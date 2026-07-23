"""Compile the optional workspace configuration for the POSIX launcher.

The host launcher intentionally has no YAML implementation. It asks the
digest-pinned cdpx image to validate ``cdpx.yaml`` and emit a small,
line-oriented execution plan that a POSIX shell can consume without eval.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

SCHEMA = "cdpx/v1"
CONFIG_NAME = "cdpx.yaml"
DEFAULT_IDLE_TIMEOUT = 86_400
DEFAULT_SESSION_TTL = 3_600
DEFAULT_SHM_SIZE = 1_073_741_824
MIN_IDLE_TIMEOUT = 300
MAX_IDLE_TIMEOUT = 604_800
MIN_SESSION_TTL = 60
MAX_SESSION_TTL = 86_400
MIN_SHM_SIZE = 268_435_456
MAX_SHM_SIZE = 4_294_967_296
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
NETWORK = re.compile(r"(?:host|bridge|network:[A-Za-z0-9_.-]+|container:[A-Za-z0-9_.-]+)\Z")
HOSTNAME = re.compile(
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)
PLACEHOLDER = re.compile(r"\$(?:\$|\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\})")
CORE_ENVIRONMENT = {
    "CDPX_IGNORE_TLS_ERRORS",
    "CDPX_ORIGINS",
    "CDPX_RUN_ID",
    "CDPX_SESSION",
    "CDPX_SESSION_TTL",
    "CDPX_TARGET",
    "CDPX_TRUST_CA_DIR",
}
TRUST_CA_TARGET = "/etc/cdpx/trust"
RESERVED_TARGETS = (
    "/bin",
    "/dev",
    "/etc",
    "/lib",
    "/opt/cdpx",
    "/proc",
    "/sbin",
    "/sys",
    "/usr",
    "/var/run/docker.sock",
)
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3_600, "d": 86_400}
_SIZE_UNITS = {
    "b": 1,
    "k": 1_024,
    "kb": 1_024,
    "kib": 1_024,
    "m": 1_048_576,
    "mb": 1_048_576,
    "mib": 1_048_576,
    "g": 1_073_741_824,
    "gb": 1_073_741_824,
    "gib": 1_073_741_824,
}


class ConfigurationError(ValueError):
    """A project configuration cannot be applied safely."""


def _strict_mapping(value: Any, label: str, allowed: set[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigurationError(f"{label}: mapping required")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigurationError(f"{label}: unknown keys: {', '.join(unknown)}")
    return value


def _duration(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{label}: duration required")
    if isinstance(value, int):
        seconds = value
    elif isinstance(value, str):
        match = re.fullmatch(r"([1-9][0-9]*)([smhd])", value.strip().lower())
        if not match:
            raise ConfigurationError(f"{label}: use an integer number of seconds or 10m/1h/1d")
        seconds = int(match.group(1)) * _DURATION_UNITS[match.group(2)]
    else:
        raise ConfigurationError(f"{label}: duration required")
    if not minimum <= seconds <= maximum:
        raise ConfigurationError(f"{label}: must be between {minimum}s and {maximum}s")
    return seconds


def _size(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigurationError(f"{label}: size required")
    if isinstance(value, int):
        size = value
    elif isinstance(value, str):
        match = re.fullmatch(r"([1-9][0-9]*)\s*([kmgt]?(?:i?b)?)", value.strip().lower())
        if not match or match.group(2) not in _SIZE_UNITS:
            raise ConfigurationError(f"{label}: use bytes or a value such as 512m/1g")
        size = int(match.group(1)) * _SIZE_UNITS[match.group(2)]
    else:
        raise ConfigurationError(f"{label}: size required")
    if not MIN_SHM_SIZE <= size <= MAX_SHM_SIZE:
        raise ConfigurationError(
            f"{label}: must be between {MIN_SHM_SIZE} and {MAX_SHM_SIZE} bytes"
        )
    return size


def _environment_names(value: Any, label: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError(f"{label}: list required")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not ENVIRONMENT_NAME.fullmatch(item):
            raise ConfigurationError(f"{label}: invalid environment name: {item!r}")
        if item in CORE_ENVIRONMENT:
            raise ConfigurationError(f"{label}: reserved environment name: {item}")
        if item in names:
            raise ConfigurationError(f"{label}: duplicate environment name: {item}")
        names.append(item)
    return names


def _literal_environment(value: Any) -> dict[str, str]:
    mapping = _strict_mapping(value, "environment.set", set(value or {}))
    result: dict[str, str] = {}
    for name, literal in mapping.items():
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise ConfigurationError(f"environment.set: invalid environment name: {name!r}")
        if name in CORE_ENVIRONMENT:
            raise ConfigurationError(f"environment.set: reserved environment name: {name}")
        if not isinstance(literal, str):
            raise ConfigurationError(f"environment.set.{name}: literal string required")
        if "\n" in literal or "\x00" in literal:
            raise ConfigurationError(f"environment.set.{name}: newline/NUL forbidden")
        result[name] = literal
    return result


def _interpolate_text(text: str, label: str, environ: Mapping[str, str]) -> str:
    pieces: list[str] = []
    position = 0
    while (index := text.find("$", position)) != -1:
        pieces.append(text[position:index])
        match = PLACEHOLDER.match(text, index)
        if not match:
            raise ConfigurationError(
                f"{label}: malformed placeholder near {text[index : index + 16]!r}; "
                "use ${NAME}, ${NAME:-default} or $$ for a literal $"
            )
        if match.group(0) == "$$":
            pieces.append("$")
        else:
            name = match.group("name")
            default = match.group("default")
            value = environ.get(name)
            if value:
                pieces.append(value)
            elif default is not None:
                pieces.append(default)
            elif name in environ:
                pieces.append("")
            else:
                raise ConfigurationError(f"{label}: undefined environment variable: {name}")
        position = match.end()
    pieces.append(text[position:])
    return "".join(pieces)


def _interpolate(value: Any, label: str, environ: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return _interpolate_text(value, label, environ)
    if isinstance(value, list):
        return [
            _interpolate(item, f"{label}[{index}]", environ) for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        return {key: _interpolate(item, f"{label}.{key}", environ) for key, item in value.items()}
    return value


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _mounts(value: Any, root: Path) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError("mounts: list required")
    mounts: list[dict[str, Any]] = []
    targets: set[str] = set()
    for index, raw in enumerate(value):
        item = _strict_mapping(raw, f"mounts[{index}]", {"source", "target", "read_only"})
        source_value = item.get("source")
        target_value = item.get("target")
        if not isinstance(source_value, str) or not source_value:
            raise ConfigurationError(f"mounts[{index}].source: non-empty path required")
        if not isinstance(target_value, str) or not target_value.startswith("/"):
            raise ConfigurationError(f"mounts[{index}].target: absolute path required")
        if any(character in source_value + target_value for character in ("\n", "\x00", ",")):
            raise ConfigurationError(f"mounts[{index}]: newline, NUL and comma are forbidden")
        source = Path(source_value)
        if not source.is_absolute():
            source = root / source
        source = source.resolve()
        if not _inside(source, root):
            raise ConfigurationError(f"mounts[{index}].source: must stay inside the workspace")
        if not source.exists():
            raise ConfigurationError(f"mounts[{index}].source: path does not exist: {source}")
        target = str(Path(target_value))
        if any(
            target == reserved or target.startswith(f"{reserved}/") for reserved in RESERVED_TARGETS
        ):
            raise ConfigurationError(f"mounts[{index}].target: reserved runtime path: {target}")
        if target in targets:
            raise ConfigurationError(f"mounts[{index}].target: duplicate target: {target}")
        read_only = item.get("read_only", True)
        if not isinstance(read_only, bool):
            raise ConfigurationError(f"mounts[{index}].read_only: boolean required")
        targets.add(target)
        mounts.append({"source": str(source), "target": target, "read_only": read_only})
    return mounts


def _trust_ca(value: Any, root: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError("runtime.trust_ca: list required")
    sources: list[str] = []
    basenames: set[str] = set()
    for index, raw in enumerate(value):
        label = f"runtime.trust_ca[{index}]"
        if not isinstance(raw, str) or not raw:
            raise ConfigurationError(f"{label}: non-empty path required")
        if any(character in raw for character in ("\n", "\x00", ",")):
            raise ConfigurationError(f"{label}: newline, NUL and comma are forbidden")
        source = Path(raw)
        if not source.is_absolute():
            source = root / source
        source = source.resolve()
        if not _inside(source, root):
            raise ConfigurationError(f"{label}: must stay inside the workspace")
        if not source.exists():
            raise ConfigurationError(f"{label}: path does not exist: {source}")
        if not source.is_file():
            raise ConfigurationError(f"{label}: must be a regular file: {source}")
        basename = source.name
        if basename in basenames:
            raise ConfigurationError(
                f"{label}: duplicate certificate file name {basename!r}; "
                "trusted CAs share one target directory"
            )
        with source.open("rb") as stream:
            content = stream.read(65_536).decode("utf-8", errors="replace")
        if "-----BEGIN CERTIFICATE-----" not in content:
            raise ConfigurationError(f"{label}: no PEM CERTIFICATE block in {source}")
        if "PRIVATE KEY" in content:
            raise ConfigurationError(
                f"{label}: {source} contains a PRIVATE KEY; copy only the CA "
                "certificate, never its private key"
            )
        basenames.add(basename)
        sources.append(str(source))
    return sources


def _extra_hosts(value: Any, network: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigurationError("runtime.extra_hosts: list required")
    entries: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        label = f"runtime.extra_hosts[{index}]"
        if not isinstance(item, str):
            raise ConfigurationError(f"{label}: hostname:target string required")
        if "\n" in item or "\x00" in item:
            raise ConfigurationError(f"{label}: newline/NUL forbidden")
        hostname, separator, target = item.partition(":")
        if not separator or len(hostname) > 253 or not HOSTNAME.fullmatch(hostname):
            raise ConfigurationError(f"{label}: invalid hostname in {item!r}")
        if target != "host-gateway":
            try:
                ipaddress.ip_address(target)
            except ValueError:
                raise ConfigurationError(
                    f"{label}: target must be an IPv4/IPv6 address or host-gateway"
                ) from None
        lowered = hostname.lower()
        if lowered in seen:
            raise ConfigurationError(f"{label}: duplicate hostname: {hostname}")
        seen.add(lowered)
        entries.append(item)
    if entries and network.startswith("container:"):
        raise ConfigurationError(
            "runtime.extra_hosts: not allowed with runtime.network container:<name>"
        )
    return entries


def load_configuration(
    root: Path,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return a strict, normalized configuration with all defaults applied."""

    root = root.resolve()
    if environ is None:
        environ = os.environ
    path = config_path or root / CONFIG_NAME
    if path.exists():
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ConfigurationError(f"{path}: invalid YAML: {error}") from error
        loaded = _interpolate(loaded, str(path), environ)
        document = _strict_mapping(
            loaded,
            str(path),
            {"schema", "runtime", "environment", "mounts", "session"},
        )
    else:
        document = {}
    schema = document.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise ConfigurationError(f"schema: expected {SCHEMA}, got {schema!r}")

    runtime = _strict_mapping(
        document.get("runtime"),
        "runtime",
        {"network", "idle_timeout", "shm_size", "extra_hosts", "trust_ca"},
    )
    network = runtime.get("network", "host")
    if not isinstance(network, str) or not NETWORK.fullmatch(network):
        raise ConfigurationError(
            "runtime.network: expected host, bridge, network:<name> or container:<name>"
        )
    extra_hosts = _extra_hosts(runtime.get("extra_hosts"), network)
    idle_timeout = _duration(
        runtime.get("idle_timeout", DEFAULT_IDLE_TIMEOUT),
        "runtime.idle_timeout",
        MIN_IDLE_TIMEOUT,
        MAX_IDLE_TIMEOUT,
    )
    shm_size = _size(runtime.get("shm_size", DEFAULT_SHM_SIZE), "runtime.shm_size")
    trust_ca = _trust_ca(runtime.get("trust_ca"), root)

    environment = _strict_mapping(
        document.get("environment"),
        "environment",
        {"required", "optional", "set"},
    )
    required = _environment_names(environment.get("required"), "environment.required")
    optional = _environment_names(environment.get("optional"), "environment.optional")
    literals = _literal_environment(environment.get("set"))
    overlap = sorted((set(required) & set(optional)) | (set(required + optional) & set(literals)))
    if overlap:
        names = ", ".join(overlap)
        raise ConfigurationError(f"environment: names declared more than once: {names}")

    session = _strict_mapping(
        document.get("session"), "session", {"ttl", "origins", "ignore_tls_errors"}
    )
    ignore_tls_errors = session.get("ignore_tls_errors", False)
    if not isinstance(ignore_tls_errors, bool):
        raise ConfigurationError("session.ignore_tls_errors: boolean required")
    ttl = _duration(
        session.get("ttl", DEFAULT_SESSION_TTL),
        "session.ttl",
        MIN_SESSION_TTL,
        MAX_SESSION_TTL,
    )
    origins = session.get("origins", [])
    if not isinstance(origins, list) or not all(
        isinstance(origin, str) and origin and "\n" not in origin for origin in origins
    ):
        raise ConfigurationError("session.origins: list of non-empty strings required")

    return {
        "schema": SCHEMA,
        "runtime": {
            "network": network,
            "idle_timeout": idle_timeout,
            "shm_size": shm_size,
            "extra_hosts": extra_hosts,
            "trust_ca": trust_ca,
        },
        "environment": {
            "required": required,
            "optional": optional,
            "set": literals,
        },
        "mounts": _mounts(document.get("mounts"), root),
        "session": {
            "ttl": ttl,
            "origins": origins,
            "ignore_tls_errors": ignore_tls_errors,
        },
    }


def _atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def compile_plan(
    root: Path,
    output: Path,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    configuration = load_configuration(root, config_path, environ)
    canonical = json.dumps(configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    config = config_path or root / CONFIG_NAME
    plan = {
        "schema": "cdpx.runtime-plan/v1",
        "workspace": str(root),
        "config": str(config) if config.exists() else None,
        "config_trusted": config.exists(),
        "fingerprint": fingerprint,
        "effective": configuration,
    }
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    _atomic_text(output / "plan.json", json.dumps(plan, ensure_ascii=False) + "\n")
    _atomic_text(output / "fingerprint", fingerprint + "\n")

    docker_arguments = [
        "--network",
        configuration["runtime"]["network"].removeprefix("network:")
        if configuration["runtime"]["network"].startswith("network:")
        else configuration["runtime"]["network"],
        "--shm-size",
        str(configuration["runtime"]["shm_size"]),
    ]
    for entry in configuration["runtime"]["extra_hosts"]:
        docker_arguments.extend(("--add-host", entry))
    for mount in configuration["mounts"]:
        spec = f"type=bind,source={mount['source']},target={mount['target']}"
        if mount["read_only"]:
            spec += ",readonly"
        docker_arguments.extend(("--mount", spec))
    for source in configuration["runtime"]["trust_ca"]:
        basename = Path(source).name
        docker_arguments.extend(
            (
                "--mount",
                f"type=bind,source={source},target={TRUST_CA_TARGET}/{basename},readonly",
            )
        )
    _atomic_text(output / "docker.args", "".join(f"{argument}\n" for argument in docker_arguments))
    _atomic_text(
        output / "environment.required",
        "".join(f"{name}\n" for name in configuration["environment"]["required"]),
    )
    _atomic_text(
        output / "environment.optional",
        "".join(f"{name}\n" for name in configuration["environment"]["optional"]),
    )
    fixed = dict(configuration["environment"]["set"])
    fixed["CDPX_SESSION_TTL"] = str(configuration["session"]["ttl"])
    if configuration["session"]["origins"]:
        fixed["CDPX_ORIGINS"] = ",".join(configuration["session"]["origins"])
    if configuration["runtime"]["trust_ca"]:
        fixed["CDPX_TRUST_CA_DIR"] = TRUST_CA_TARGET
    if configuration["session"]["ignore_tls_errors"]:
        fixed["CDPX_IGNORE_TLS_ERRORS"] = "1"
    _atomic_text(
        output / "environment.set",
        "".join(f"{name}={value}\n" for name, value in sorted(fixed.items())),
    )
    _atomic_text(output / "idle-timeout", f"{configuration['runtime']['idle_timeout']}\n")
    return plan


TEMPLATE = """# cdpx workspace configuration. All fields are optional.
schema: cdpx/v1

runtime:
  network: host
  extra_hosts: []
  idle_timeout: 24h
  shm_size: 1g
  trust_ca: []

environment:
  required: []
  optional: []
  set: {}

mounts: []

session:
  ttl: 1h
  origins: []
  ignore_tls_errors: false
"""


def init_configuration(root: Path) -> Path:
    path = root.resolve() / CONFIG_NAME
    if path.exists():
        raise ConfigurationError(f"refusing to overwrite existing configuration: {path}")
    _atomic_text(path, TEMPLATE, mode=0o644)
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdpx.runtime_config")
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile")
    compile_parser.add_argument("--root", required=True)
    compile_parser.add_argument("--output", required=True)
    compile_parser.add_argument("--config")
    init_parser = sub.add_parser("init")
    init_parser.add_argument("--root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "compile":
            plan = compile_plan(
                Path(args.root),
                Path(args.output),
                Path(args.config) if args.config else None,
            )
            print(json.dumps({"compiled": True, "fingerprint": plan["fingerprint"]}))
        else:
            path = init_configuration(Path(args.root))
            print(json.dumps({"created": str(path), "schema": SCHEMA}))
    except ConfigurationError as error:
        print(f"cdpx: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
