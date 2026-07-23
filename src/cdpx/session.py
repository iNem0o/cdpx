"""Disposable, attested, and exclusive browser sessions.

The manifest is the local capability assigned to a run. It binds a profile,
a target and an authority level. Private files stay under a 0700 directory
and each command takes a non-blocking lock on the session.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cdpx import discovery
from cdpx.cdp_types import DiscoveryTarget
from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    assert_loopback_endpoint,
    is_loopback_host,
    validate_target,
)
from cdpx.private_files import PrivateFileError, atomic_write_text
from cdpx.security.redaction import (
    RedactionContext,
    redact_text,
    secret_values_from_environment,
)
from cdpx.sessions.process import abort_supervisor as _abort_supervisor
from cdpx.sessions.process import argv_has_markers as _argv_has_markers
from cdpx.sessions.process import process_identity as _process_identity
from cdpx.sessions.process import remove_tree as _remove_tree

SCHEMA = "cdpx.session/v3"
MANIFEST_NAME = "manifest.json"
LOCK_NAME = "command.lock"
STOP_NAME = "stop"
CHROME_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)
BROWSER_KINDS = {"chrome", "mock"}
_SESSION_ID_RE = re.compile(r"[0-9a-f]{24}\Z")
_PROFILE_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
_TARGET_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,256}\Z")
_RUNTIME_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_MAX_PID = 2_147_483_647
DEFAULT_STARTUP_TIMEOUT = 60.0
MAX_STARTUP_TIMEOUT = 300.0
MAX_SESSION_TTL = 86_400.0
_STARTUP_RESULT_GRACE = 2.0
_DIAGNOSTIC_TAIL_BYTES = 2000
_BOOTSTRAP_FIELDS = {
    "session_id",
    "run_id",
    "profile_id",
    "browser_kind",
    "authority",
    "origins",
    "owner_pid",
    "owner_start_time",
    "chrome_bin",
    "startup_timeout",
    "session_dir",
    "profile_dir",
    "artifacts_dir",
    "created_at",
    "expires_at",
    "runtime_id",
    "ignore_tls_errors",
    "trust_ca_dir",
}
_ATTESTED_POLICY_FIELDS = (
    "session_id",
    "run_id",
    "profile_id",
    "browser_kind",
    "authority",
    "origins",
    "owner_pid",
    "owner_start_time",
    "session_dir",
    "profile_dir",
    "artifacts_dir",
    "created_at",
    "expires_at",
    "runtime_id",
    "ignore_tls_errors",
    "trust_ca_dir",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _secure_mkdir(path: Path) -> None:
    if path.is_symlink():
        raise PolicyError(f"symbolic session directory forbidden: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise PolicyError(f"session directory required: {path}")
    path.chmod(0o700)


def _write_private(path: Path, data: str) -> None:
    try:
        atomic_write_text(path, data)
    except PrivateFileError as error:
        raise PolicyError(str(error)) from error


@dataclass(frozen=True)
class SessionManifest:
    session_id: str
    run_id: str
    profile_id: str
    browser_kind: str
    authority: str
    origins: tuple[str, ...]
    host: str
    port: int
    target_id: str
    websocket_url: str
    browser_pid: int
    browser_start_time: str
    supervisor_pid: int
    supervisor_start_time: str
    owner_pid: int | None
    owner_start_time: str | None
    session_dir: str
    profile_dir: str
    artifacts_dir: str
    created_at: str
    expires_at: str
    ignore_tls_errors: bool = False
    trust_ca_dir: str | None = None
    runtime_id: str = "standalone"
    schema: str = SCHEMA

    @property
    def manifest_path(self) -> Path:
        return Path(self.session_dir) / MANIFEST_NAME

    def execution_context(self) -> ExecutionContext:
        return ExecutionContext.create(
            run_id=self.run_id,
            target_id=self.target_id,
            authority=self.authority,
            origins=",".join(self.origins),
            session_id=self.session_id,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "profile": {"id": self.profile_id, "ephemeral": True},
            "browser_kind": self.browser_kind,
            "authority": self.authority,
            "origins": list(self.origins),
            "host": self.host,
            "port": self.port,
            "target_id": self.target_id,
            "runtime_id": self.runtime_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "ignore_tls_errors": self.ignore_tls_errors,
            "trust_ca": self.trust_ca_dir is not None,
        }


def export_lines(manifest: SessionManifest, manifest_path: str | Path) -> list[str]:
    """Shell ``export`` lines installing a session's identity triple.

    The output is meant for ``eval``: only quoted assignments, no data beyond
    what the manifest's public view already exposes.
    """
    values = (
        ("CDPX_SESSION", str(manifest_path)),
        ("CDPX_RUN_ID", manifest.run_id),
        ("CDPX_TARGET", manifest.target_id),
    )
    return [f"export {name}={shlex.quote(value)}" for name, value in values]


@dataclass(frozen=True)
class SupervisorBootstrap:
    """Fully validated input consumed by the private supervisor process."""

    session_id: str
    run_id: str
    profile_id: str
    browser_kind: str
    authority: str
    origins: tuple[str, ...]
    owner_pid: int | None
    owner_start_time: str | None
    chrome_bin: str
    startup_timeout: float
    session_dir: str
    profile_dir: str
    artifacts_dir: str
    created_at: str
    expires_at: str
    runtime_id: str
    ignore_tls_errors: bool = False
    trust_ca_dir: str | None = None


def _policy_attestation(
    source: SessionManifest | SupervisorBootstrap | dict[str, Any],
) -> str:
    payload: dict[str, Any] = {}
    for field in _ATTESTED_POLICY_FIELDS:
        if isinstance(source, dict):
            if field in source:
                value = source[field]
            elif field == "runtime_id":
                value = "standalone"
            else:
                raise KeyError(field)
        else:
            value = getattr(source, field)
        payload[field] = list(value) if field == "origins" else value
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _supervisor_markers(manifest: SessionManifest) -> tuple[str, ...]:
    return (
        "-m",
        "cdpx.session",
        "_supervise",
        str(Path(manifest.session_dir) / "bootstrap.json"),
        f"--attestation={_policy_attestation(manifest)}",
    )


def write_manifest(manifest: SessionManifest) -> Path:
    _validate_manifest_fields(manifest)
    session_dir = Path(manifest.session_dir)
    _secure_mkdir(session_dir)
    _secure_mkdir(Path(manifest.profile_dir))
    _secure_mkdir(Path(manifest.artifacts_dir))
    path = session_dir / MANIFEST_NAME
    payload = asdict(manifest)
    payload["origins"] = list(manifest.origins)
    _write_private(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def load_manifest(
    path: str | Path,
    *,
    run_id: str | None = None,
    target_id: str | None = None,
) -> SessionManifest:
    manifest_path = Path(path)
    if not manifest_path.is_absolute():
        manifest_path = Path.cwd() / manifest_path
    try:
        info = manifest_path.lstat()
    except OSError as e:
        raise PolicyError(f"unreadable session manifest: {manifest_path}: {e}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PolicyError("regular session manifest required")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError("session manifest owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError("manifest permissions too open; 0600 required")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PolicyError(f"invalid session manifest: {e}") from e
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PolicyError("unknown or stale session schema; run `cdpx runtime reset --force`")
    manifest = _manifest_from_payload(payload)
    _validate_manifest_paths(manifest_path, manifest)
    if run_id is not None and manifest.run_id != run_id:
        raise PolicyError(f"run not the session owner: expected {manifest.run_id}, got {run_id}")
    if target_id is not None and manifest.target_id != target_id:
        raise PolicyError(
            f"target not assigned to the session: expected {manifest.target_id}, got {target_id}"
        )
    assert_loopback_endpoint(manifest.host, manifest.websocket_url)
    expected_runtime = os.environ.get("CDPX_RUNTIME_ID")
    if expected_runtime and manifest.runtime_id != expected_runtime:
        raise PolicyError("session belongs to a replaced cdpx runtime")
    return manifest


def _manifest_from_payload(payload: dict[str, Any]) -> SessionManifest:
    origins = payload.get("origins")
    if (
        not isinstance(origins, list)
        or not origins
        or not all(isinstance(item, str) and item for item in origins)
    ):
        raise PolicyError("session manifest: origins must be a non-empty list of strings")
    normalized = {**payload, "origins": tuple(origins)}
    try:
        manifest = SessionManifest(**normalized)
    except (TypeError, ValueError) as e:
        raise PolicyError(f"incomplete session manifest: {e}") from e
    _validate_manifest_fields(manifest)
    return manifest


def _strict_int(value: Any, label: str, *, minimum: int = 1, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError(f"session manifest: invalid {label} integer")
    return value


def _aware_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise PolicyError(f"session manifest: {label} must be a date")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise PolicyError(f"session manifest: invalid {label}") from e
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyError(f"session manifest: {label} must include a timezone")
    return parsed


def _validate_trust_ca_dir(value: Any, label: str, *, require_dir: bool = False) -> None:
    """Shared strict validation for a trust_ca_dir field.

    ``None`` means the option is unused. Any other value must be a non-empty
    absolute path string free of NUL and newline; when ``require_dir`` it must
    additionally resolve to an existing directory (fail closed).
    """
    if value is None:
        return
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\n" in value
        or not Path(value).is_absolute()
    ):
        raise PolicyError(f"{label}: invalid trust_ca_dir")
    if require_dir and not Path(value).is_dir():
        raise PolicyError(f"{label}: trust_ca_dir is not an existing directory")


def _validate_manifest_fields(manifest: SessionManifest) -> None:
    _validate_manifest_identity(manifest)
    if manifest.schema != SCHEMA:
        raise PolicyError("unknown session schema")
    if not isinstance(manifest.run_id, str) or not isinstance(manifest.target_id, str):
        raise PolicyError("session manifest: run/target must be strings")
    if not _TARGET_ID_RE.fullmatch(manifest.target_id):
        raise PolicyError("session manifest: invalid target")
    if not isinstance(manifest.runtime_id, str) or not _RUNTIME_ID_RE.fullmatch(
        manifest.runtime_id
    ):
        raise PolicyError("session manifest: invalid runtime_id")
    if not isinstance(manifest.authority, str) or not isinstance(manifest.origins, tuple):
        raise PolicyError("session manifest: invalid authority/origins")
    if manifest.browser_kind not in {"chrome", "mock"}:
        raise PolicyError("session manifest: invalid browser_kind")
    context = ExecutionContext.create(
        run_id=manifest.run_id,
        target_id=manifest.target_id,
        authority=manifest.authority,
        origins=",".join(manifest.origins),
        session_id=manifest.session_id,
    )
    if context.origins != manifest.origins:
        raise PolicyError("session manifest: non-canonical origins")
    if not isinstance(manifest.host, str) or not is_loopback_host(manifest.host):
        raise PolicyError("session manifest: loopback host required")
    _strict_int(manifest.port, "port", maximum=65535)
    _strict_int(manifest.browser_pid, "browser_pid", maximum=_MAX_PID)
    _strict_int(manifest.supervisor_pid, "supervisor_pid", maximum=_MAX_PID)
    if not isinstance(manifest.browser_start_time, str) or not manifest.browser_start_time:
        raise PolicyError("session manifest: invalid browser_start_time")
    if not isinstance(manifest.supervisor_start_time, str) or not manifest.supervisor_start_time:
        raise PolicyError("session manifest: invalid supervisor_start_time")
    if (manifest.owner_pid is None) != (manifest.owner_start_time is None):
        raise PolicyError("session manifest: owner_pid/start_time must be provided together")
    if manifest.owner_pid is not None:
        _strict_int(manifest.owner_pid, "owner_pid", maximum=_MAX_PID)
        if not isinstance(manifest.owner_start_time, str) or not manifest.owner_start_time:
            raise PolicyError("session manifest: invalid owner_start_time")
    for label, value in (
        ("session_dir", manifest.session_dir),
        ("profile_dir", manifest.profile_dir),
        ("artifacts_dir", manifest.artifacts_dir),
        ("websocket_url", manifest.websocket_url),
    ):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise PolicyError(f"session manifest: invalid {label}")
    created = _aware_timestamp(manifest.created_at, "created_at")
    expires = _aware_timestamp(manifest.expires_at, "expires_at")
    if expires <= created:
        raise PolicyError("session manifest: expires_at must follow created_at")
    if type(manifest.ignore_tls_errors) is not bool:
        raise PolicyError("session manifest: ignore_tls_errors must be a boolean")
    _validate_trust_ca_dir(manifest.trust_ca_dir, "session manifest")
    _validate_websocket_binding(manifest.websocket_url, manifest.port, manifest.target_id)


def _validate_websocket_binding(websocket_url: str, port: int, target_id: str) -> None:
    assert_loopback_endpoint("127.0.0.1", websocket_url)
    try:
        parsed = urllib.parse.urlsplit(websocket_url)
        actual_port = parsed.port
    except ValueError as e:
        raise PolicyError("session manifest: invalid CDP WebSocket") from e
    if parsed.username is not None or parsed.password is not None:
        raise PolicyError("session manifest: WebSocket credentials forbidden")
    if actual_port != port or parsed.path != f"/devtools/page/{target_id}":
        raise PolicyError("session manifest: WebSocket not bound to the assigned port/target")


def _validate_manifest_paths(path: Path, manifest: SessionManifest) -> None:
    raw_session_dir = Path(manifest.session_dir)
    if not raw_session_dir.is_absolute() or not path.is_absolute():
        raise PolicyError("absolute session paths required")
    if raw_session_dir.is_symlink() or path.parent.is_symlink():
        raise PolicyError("symbolic session directory forbidden")
    session_dir = raw_session_dir.resolve()
    if path.resolve().parent != session_dir or raw_session_dir.name != manifest.session_id:
        raise PolicyError("manifest outside the declared session directory")
    if hasattr(os, "getuid") and raw_session_dir.stat().st_uid != os.getuid():
        raise PolicyError("session directory owned by another user")
    if stat.S_IMODE(raw_session_dir.stat().st_mode) & 0o077:
        raise PolicyError("session directory permissions too open; 0700 required")
    expected = {
        Path(manifest.profile_dir): raw_session_dir / "profile",
        Path(manifest.artifacts_dir): raw_session_dir / "artifacts",
    }
    for candidate, assigned in expected.items():
        if (
            candidate != assigned
            or candidate.is_symlink()
            or candidate.resolve().parent != session_dir
        ):
            raise PolicyError("session path outside the assigned directory")
        try:
            info = candidate.stat()
        except OSError as e:
            raise PolicyError(f"session directory not found: {candidate}") from e
        if not stat.S_ISDIR(info.st_mode):
            raise PolicyError(f"session directory required: {candidate}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PolicyError("session directory owned by another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PolicyError("session directory permissions too open; 0700 required")


def _validate_manifest_identity(manifest: SessionManifest) -> None:
    if not isinstance(manifest.session_id, str) or not _SESSION_ID_RE.fullmatch(
        manifest.session_id
    ):
        raise PolicyError("invalid session identifier")
    if not isinstance(manifest.profile_id, str) or not _PROFILE_ID_RE.fullmatch(
        manifest.profile_id
    ):
        raise PolicyError("invalid profile identifier")


class SessionLease:
    """Exclusive, fail-fast command lock, bound to the run and target."""

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        run_id: str,
        target_id: str,
        require_active: bool = True,
    ):
        self.manifest_path = Path(manifest_path)
        if not self.manifest_path.is_absolute():
            self.manifest_path = Path.cwd() / self.manifest_path
        self.run_id = run_id
        self.target_id = target_id
        self.require_active = require_active
        self.manifest: SessionManifest | None = None
        self._stream: Any = None

    def __enter__(self) -> SessionManifest:
        manifest = load_manifest(
            self.manifest_path,
            run_id=self.run_id,
            target_id=self.target_id,
        )
        lock_path = self.manifest_path.parent / LOCK_NAME
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as e:
            raise PolicyError(f"session lock not openable: {lock_path}") from e
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise PolicyError(f"regular session lock required: {lock_path}")
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "r+")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            stream.close()
            raise PolicyError(
                f"session already in use by another command: {manifest.session_id}"
            ) from e
        stream.seek(0)
        stream.truncate()
        stream.write(f"run_id={self.run_id}\npid={os.getpid()}\n")
        stream.flush()
        if self.require_active:
            try:
                assert_session_active(manifest)
            except Exception:
                with contextlib.suppress(OSError):
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                stream.close()
                raise
        self.manifest = manifest
        self._stream = stream
        return manifest

    def __exit__(self, *exc: object) -> None:
        if self._stream is None:
            return
        with contextlib.suppress(OSError):
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        self._stream.close()
        self._stream = None


def runtime_root() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    root = Path(base) / "cdpx" if base else Path("/tmp") / f"cdpx-{os.getuid()}"
    _secure_mkdir(root)
    return root


def find_chrome(explicit: str | None = None) -> str:
    if explicit is None:
        explicit = os.environ.get("CDPX_BUNDLED_CHROME")
    if explicit:
        resolved = shutil.which(explicit) if os.sep not in explicit else explicit
        if resolved and Path(resolved).is_file():
            return str(Path(resolved).resolve())
        raise PolicyError(f"Chrome/Chromium not found: {explicit}")
    for candidate in CHROME_CANDIDATES:
        if resolved := shutil.which(candidate):
            return str(Path(resolved).resolve())
    raise PolicyError("Chrome/Chromium required for cdpx session start")


def _ci_environment() -> bool:
    value = os.environ.get("CI", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _sandbox_must_be_disabled() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(
        (callable(geteuid) and geteuid() == 0)
        or _ci_environment()
        or os.environ.get("CDPX_CONTAINERIZED") == "1"
    )


def build_chrome_command(
    chrome_bin: str,
    profile_dir: Path,
    *,
    ignore_tls_errors: bool = False,
) -> list[str]:
    command = [
        chrome_bin,
        "--headless=new",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        # crashpad daemonizes (it is not a child of the main Chrome process,
        # so killing Chrome does not reach it) and keeps writing crash dumps
        # into the profile after the kill — the exact writer that makes the
        # supervisor's rmtree fail with ENOTEMPTY on loaded CI runners. A
        # disposable profile never wants crash reporting.
        "--disable-crash-reporter",
        "--disable-breakpad",
        "about:blank",
    ]
    if _sandbox_must_be_disabled():
        command.insert(-2, "--no-sandbox")
    if _ci_environment():
        # Runners and containers often expose a very bounded /dev/shm.
        # Using the private on-disk profile avoids a Chrome cold start
        # staying alive without ever publishing DevToolsActivePort.
        command.insert(-2, "--disable-dev-shm-usage")
    if ignore_tls_errors:
        # A disposable development Chrome trusting a mounted CA still needs an
        # explicit bypass for local self-signed HTTPS not covered by that CA.
        # Inserted like --no-sandbox so about:blank stays the final argument.
        command.insert(-2, "--ignore-certificate-errors")
    return command


def build_mock_command(profile_dir: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cdpx.testing.mock_cdp",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
    ]


def _browser_markers(browser_kind: str, profile_dir: str | Path) -> tuple[str, ...]:
    profile_marker = f"--user-data-dir={profile_dir}"
    if browser_kind == "mock":
        return ("-m", "cdpx.testing.mock_cdp", profile_marker)
    return (profile_marker,)


def _positive_finite(value: float, label: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise PolicyError(f"{label} numeric value required")
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0:
        raise PolicyError(f"{label} finite and strictly positive value required")
    return rendered


def start_session(
    *,
    run_id: str,
    authority: str | Authority,
    origins: str,
    ttl: float = 3600,
    owner_pid: int | None = None,
    chrome_bin: str | None = None,
    browser_kind: str = "chrome",
    root: str | Path | None = None,
    timeout: float = DEFAULT_STARTUP_TIMEOUT,
    ignore_tls_errors: bool = False,
    trust_ca_dir: str | Path | None = None,
) -> tuple[SessionManifest, Path]:
    if browser_kind not in BROWSER_KINDS:
        raise PolicyError(f"unknown browser backend: {browser_kind}")
    if type(ignore_tls_errors) is not bool:
        raise PolicyError("ignore_tls_errors must be a boolean")
    # Resolve and vet the trust store before any file/process is created so a
    # misconfigured CDPX_TRUST_CA_DIR fails closed without leaving residue.
    resolved_trust_ca: str | None = None
    if trust_ca_dir is not None:
        raw_trust = str(trust_ca_dir)
        if not raw_trust or "\x00" in raw_trust or "\n" in raw_trust:
            raise PolicyError("CDPX_TRUST_CA_DIR must be a clean, non-empty path")
        resolved = Path(trust_ca_dir).resolve()
        if not resolved.is_dir():
            raise PolicyError(f"CDPX_TRUST_CA_DIR is not an existing directory: {resolved}")
        if not any(
            child.suffix in {".pem", ".crt"} and child.is_file() for child in resolved.iterdir()
        ):
            raise PolicyError(
                f"CDPX_TRUST_CA_DIR contains no *.pem or *.crt certificate: {resolved}"
            )
        resolved_trust_ca = str(resolved)
    # Validate grants before creating any file/process.
    preliminary = ExecutionContext.create(
        run_id=run_id,
        target_id="pending",
        authority=authority,
        origins=origins,
        session_id="pending",
    )
    ttl = _positive_finite(ttl, "session TTL")
    if ttl < 60 or ttl > MAX_SESSION_TTL:
        raise PolicyError(f"session TTL out of range; expected 60..{MAX_SESSION_TTL:g}s")
    timeout = _positive_finite(timeout, "startup timeout")
    if timeout > MAX_STARTUP_TIMEOUT:
        raise PolicyError(f"startup timeout out of range; maximum={MAX_STARTUP_TIMEOUT:g}s")
    owner = owner_pid
    owner_start_time: str | None = None
    if owner is not None:
        _strict_int(owner, "owner-pid", maximum=_MAX_PID)
        try:
            owner_start_time, _ = _process_identity(owner)
        except PolicyError as e:
            raise PolicyError(f"owner-pid not found or unverifiable: {owner}") from e
    if browser_kind == "mock" and chrome_bin is not None:
        raise PolicyError("the mock backend does not accept --chrome")
    chrome = find_chrome(chrome_bin) if browser_kind == "chrome" else sys.executable
    created = _now()
    try:
        expires = created + timedelta(seconds=ttl)
    except (OverflowError, ValueError) as e:
        raise PolicyError("session TTL out of range") from e
    parent = (Path(root) if root is not None else runtime_root()).resolve()
    _secure_mkdir(parent)
    session_id = secrets.token_hex(12)
    session_dir = parent / session_id
    profile_dir = session_dir / "profile"
    artifacts_dir = session_dir / "artifacts"
    supervisor: subprocess.Popen[Any] | None = None
    try:
        _secure_mkdir(profile_dir)
        _secure_mkdir(artifacts_dir)
        bootstrap = {
            "session_id": session_id,
            "run_id": run_id,
            "profile_id": secrets.token_hex(8),
            "browser_kind": browser_kind,
            "authority": preliminary.authority.value,
            "origins": list(preliminary.origins),
            "owner_pid": owner,
            "owner_start_time": owner_start_time,
            "chrome_bin": chrome,
            "startup_timeout": timeout,
            "session_dir": str(session_dir),
            "profile_dir": str(profile_dir),
            "artifacts_dir": str(artifacts_dir),
            "created_at": _iso(created),
            "expires_at": _iso(expires),
            "runtime_id": os.environ.get("CDPX_RUNTIME_ID", "standalone"),
            "ignore_tls_errors": ignore_tls_errors,
            "trust_ca_dir": resolved_trust_ca,
        }
        bootstrap_path = session_dir / "bootstrap.json"
        _write_private(bootstrap_path, json.dumps(bootstrap, ensure_ascii=False) + "\n")
        attestation = _policy_attestation(bootstrap)
        supervisor_log = session_dir / "supervisor.log"
        log_fd = os.open(supervisor_log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(log_fd, "w", encoding="utf-8") as log:
            supervisor = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "cdpx.session",
                    "_supervise",
                    str(bootstrap_path),
                    f"--attestation={attestation}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        manifest_path = session_dir / MANIFEST_NAME
        error_path = parent / f"{session_id}.error"
        # The supervisor owns the `timeout` budget. The parent keeps a
        # short margin only to read its manifest or its error, to avoid
        # the race where both expire at the same microsecond.
        deadline = time.monotonic() + timeout + _STARTUP_RESULT_GRACE
        while time.monotonic() < deadline:
            if manifest_path.exists():
                manifest = load_manifest(manifest_path, run_id=run_id)
                return manifest, manifest_path
            if error_path.exists():
                message = error_path.read_text(encoding="utf-8", errors="replace").strip()
                error_path.unlink(missing_ok=True)
                raise PolicyError(f"session startup failed: {message}")
            if supervisor.poll() is not None:
                detail = _startup_diagnostic_tails(session_dir)
                raise PolicyError(f"session supervisor stopped prematurely: {detail}")
            time.sleep(0.05)
        detail = _startup_diagnostic_tails(session_dir)
        raise PolicyError(f"browser session not ready after {timeout}s\n{detail}")
    except Exception:
        if supervisor is not None:
            _abort_supervisor(supervisor, session_dir)
        else:
            _remove_tree(session_dir)
        raise


def stop_session(
    manifest_path: str | Path,
    *,
    run_id: str,
    target_id: str | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    timeout = _positive_finite(timeout, "stop timeout")
    manifest = load_manifest(manifest_path, run_id=run_id, target_id=target_id)
    with SessionLease(
        manifest_path,
        run_id=run_id,
        target_id=manifest.target_id,
        require_active=False,
    ):
        stop_path = Path(manifest.session_dir) / STOP_NAME
        _write_private(stop_path, f"requested_by={os.getpid()}\n")
        deadline = time.monotonic() + timeout
        while Path(manifest.session_dir).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if Path(manifest.session_dir).exists():
            _terminate_owned_pid(
                manifest.browser_pid,
                manifest.browser_start_time,
                _browser_markers(manifest.browser_kind, manifest.profile_dir),
            )
            _terminate_owned_pid(
                manifest.supervisor_pid,
                manifest.supervisor_start_time,
                _supervisor_markers(manifest),
            )
            if Path(manifest_path).exists():
                remove_session_files(manifest_path)
    return {"session_id": manifest.session_id, "run_id": manifest.run_id, "stopped": True}


def session_status(
    manifest_path: str | Path,
    *,
    run_id: str,
    target_id: str | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path, run_id=run_id, target_id=target_id)
    return {
        **manifest.public_dict(),
        "browser_running": _process_matches(
            manifest.browser_pid,
            manifest.browser_start_time,
            _browser_markers(manifest.browser_kind, manifest.profile_dir),
        ),
        "supervisor_running": _process_matches(
            manifest.supervisor_pid,
            manifest.supervisor_start_time,
            _supervisor_markers(manifest),
        ),
    }


def assert_session_active(manifest: SessionManifest) -> None:
    expires = _aware_timestamp(manifest.expires_at, "expires_at")
    if _now() >= expires:
        raise PolicyError(f"expired session: {manifest.session_id}")
    _assert_process_identity(
        manifest.browser_pid,
        manifest.browser_start_time,
        _browser_markers(manifest.browser_kind, manifest.profile_dir),
        "browser",
    )
    _assert_process_identity(
        manifest.supervisor_pid,
        manifest.supervisor_start_time,
        _supervisor_markers(manifest),
        "supervisor",
    )
    if manifest.owner_pid is not None and manifest.owner_start_time is not None:
        _assert_process_identity(
            manifest.owner_pid,
            manifest.owner_start_time,
            None,
            "owner",
        )
    _assert_devtools_port_binding(manifest)
    _assert_exact_target(manifest)


def assert_manifest_target_binding(
    manifest: SessionManifest,
    target: DiscoveryTarget,
) -> DiscoveryTarget:
    """Validate that a discovered target is the exact endpoint in the manifest."""

    validated = validate_target(target, manifest.target_id)
    websocket_url = validated.get("webSocketDebuggerUrl")
    assert_loopback_endpoint(manifest.host, websocket_url)
    if websocket_url != manifest.websocket_url:
        raise PolicyError("session: target WebSocket differs from manifest")
    _validate_websocket_binding(manifest.websocket_url, manifest.port, manifest.target_id)
    return validated


def _assert_devtools_port_binding(manifest: SessionManifest) -> None:
    active_port = Path(manifest.profile_dir) / "DevToolsActivePort"
    try:
        lines = active_port.read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
    except (OSError, IndexError, ValueError) as e:
        raise PolicyError("session profile: invalid DevToolsActivePort") from e
    if port != manifest.port:
        raise PolicyError(
            f"session profile not bound to the assigned port: expected={manifest.port}, got={port}"
        )


def remove_session_files(manifest_path: str | Path) -> None:
    manifest = load_manifest(manifest_path)
    session_dir = Path(manifest.session_dir).resolve()
    if session_dir != Path(manifest_path).resolve().parent:
        raise PolicyError("refusing to remove a directory outside the session")
    shutil.rmtree(session_dir)


# Live implementations of process identity/termination: tests monkeypatch
# them through this namespace (session._process_identity, ...) and the
# supervisor consumes them through the facade. sessions/process.py must not
# carry a duplicate.
def _assert_process_identity(
    pid: int,
    expected_start_time: str,
    marker: str | tuple[str, ...] | None,
    label: str,
) -> None:
    actual_start_time, argv = _process_identity(pid)
    if actual_start_time != expected_start_time:
        raise PolicyError(f"session {label} reused or not assigned: pid={pid}")
    if marker is not None and not _argv_has_markers(argv, marker):
        raise PolicyError(f"session {label} without expected marker: pid={pid}")


def _process_matches(
    pid: int,
    start_time: str,
    marker: str | tuple[str, ...] | None,
) -> bool:
    try:
        _assert_process_identity(pid, start_time, marker, "process")
    except PolicyError:
        return False
    return True


def _terminate_pid(pid: int, start_time: str, timeout: float = 5) -> None:
    if not _process_matches(pid, start_time, None):
        return
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while _process_matches(pid, start_time, None) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_matches(pid, start_time, None):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _terminate_owned_pid(
    pid: int,
    start_time: str,
    marker: str | tuple[str, ...],
) -> None:
    if not _process_matches(pid, start_time, None):
        return
    _assert_process_identity(pid, start_time, marker, "processus")
    _terminate_pid(pid, start_time)


def _startup_diagnostic_tails(session_dir: Path) -> str:
    """Return bounded, cleaned tails before the private teardown."""

    context = RedactionContext.from_secrets(secret_values_from_environment())
    sections = []
    for filename in ("supervisor.log", "chrome-stderr.log"):
        path = session_dir / filename
        raw = _read_private_diagnostic_tail(path)
        tail = raw[-_DIAGNOSTIC_TAIL_BYTES:].strip() or "<empty or unavailable>"
        cleaned = redact_text(
            tail,
            context=context,
            path=f"$.session_start.{filename}",
        )
        sections.append(f"{filename}:\n{cleaned}")
    return "\n".join(sections)


def _read_private_diagnostic_tail(path: Path) -> str:
    """Read at most the allowed tail without following a link or a path race."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return ""
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            return ""
        if stat.S_IMODE(info.st_mode) & 0o077:
            return ""
        os.lseek(fd, max(0, info.st_size - _DIAGNOSTIC_TAIL_BYTES), os.SEEK_SET)
        return os.read(fd, _DIAGNOSTIC_TAIL_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        if fd >= 0:
            os.close(fd)


def _remaining_startup_timeout(deadline: float, stage: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PolicyError(f"startup timeout during {stage}")
    return remaining


def _read_devtools_port(profile_dir: Path, proc: subprocess.Popen[Any], timeout: float = 30) -> int:
    active_port = profile_dir / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise PolicyError(f"Chrome stopped before readiness (exit {proc.returncode})")
        try:
            first = active_port.read_text(encoding="utf-8").splitlines()[0]
            port = int(first)
            if 1 <= port <= 65535:
                return port
        except OSError, IndexError, ValueError:
            pass
        time.sleep(0.05)
    raise PolicyError("DevToolsActivePort not found")


def _wait_discovery(port: int, proc: subprocess.Popen[Any], timeout: float = 30) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise PolicyError(f"Chrome stopped before discovery (exit {proc.returncode})")
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return
        except OSError, urllib.error.URLError:
            pass
        time.sleep(0.05)
    raise PolicyError("Chrome discovery endpoint unavailable")


def _page_targets(host: str, port: int) -> list[DiscoveryTarget]:
    try:
        targets = discovery.list_targets(host, port)
    except discovery.DiscoveryError as e:
        raise PolicyError(f"session discovery unavailable: {e}") from e
    if not isinstance(targets, list):
        raise PolicyError("session discovery: list of targets required")
    return [target for target in targets if target.get("type") == "page"]


def _assert_exact_target(manifest: SessionManifest) -> DiscoveryTarget:
    pages = _page_targets(manifest.host, manifest.port)
    if len(pages) != 1:
        raise PolicyError(
            f"session {manifest.session_id}: exactly one page target required, found={len(pages)}"
        )
    return assert_manifest_target_binding(manifest, pages[0])


def _enforce_single_page_target(
    manifest: SessionManifest,
    *,
    close_timeout: float = 2.0,
) -> None:
    pages = _page_targets(manifest.host, manifest.port)
    for target in pages:
        target_id = target.get("id")
        if target_id != manifest.target_id:
            if not isinstance(target_id, str) or not target_id:
                raise PolicyError("extra page target without identifier")
            try:
                discovery.close_tab(manifest.host, manifest.port, target_id)
            except discovery.DiscoveryError as e:
                raise PolicyError(f"closing the extra target failed: {target_id}") from e
    # Chrome responds before /json/list always stops exposing the target
    # being closed. Waiting for this transition in a bounded way avoids a
    # false rejection while still failing closed if an extra target persists.
    deadline = time.monotonic() + close_timeout
    while True:
        pages = _page_targets(manifest.host, manifest.port)
        if len(pages) == 1:
            _assert_exact_target(manifest)
            return
        if time.monotonic() >= deadline:
            _assert_exact_target(manifest)
        time.sleep(0.05)


def _secure_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as e:
        raise PolicyError(f"unreadable {label}: {path}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PolicyError(f"regular, non-symbolic {label} required")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError(f"{label} owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError(f"{label} permissions too open; 0600 required")
    return info


def _secure_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as e:
        raise PolicyError(f"unreadable {label}: {path}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PolicyError(f"regular, non-symbolic {label} required")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError(f"{label} owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError(f"{label} permissions too open; 0700 required")
    return info


def _read_bootstrap(bootstrap_path: Path) -> SupervisorBootstrap:
    if not bootstrap_path.is_absolute():
        bootstrap_path = Path.cwd() / bootstrap_path
    if bootstrap_path.name != "bootstrap.json":
        raise PolicyError("session bootstrap: name bootstrap.json required")
    session_dir = bootstrap_path.parent
    if not _SESSION_ID_RE.fullmatch(session_dir.name):
        raise PolicyError("session bootstrap: invalid session directory")
    _secure_directory(session_dir, "bootstrap directory")
    expected = _secure_regular_file(bootstrap_path, "session bootstrap")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(bootstrap_path, flags)
    except OSError as e:
        raise PolicyError("session bootstrap not openable") from e
    try:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise PolicyError("session bootstrap replaced during validation")
        if actual.st_size > 64 * 1024:
            raise PolicyError("session bootstrap too large")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise PolicyError(f"invalid session bootstrap: {e}") from e
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict) or set(payload) != _BOOTSTRAP_FIELDS:
        raise PolicyError("session bootstrap: invalid strict fields")
    return _validate_bootstrap_payload(payload, bootstrap_path)


def _validate_bootstrap_payload(
    payload: dict[str, Any],
    bootstrap_path: Path,
) -> SupervisorBootstrap:
    session_id = payload["session_id"]
    profile_id = payload["profile_id"]
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise PolicyError("session bootstrap: invalid session_id")
    if session_id != bootstrap_path.parent.name:
        raise PolicyError("session bootstrap: session_id not bound to directory")
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise PolicyError("session bootstrap: invalid profile_id")
    origins = payload["origins"]
    if (
        not isinstance(origins, list)
        or not origins
        or not all(isinstance(item, str) and item for item in origins)
    ):
        raise PolicyError("session bootstrap: invalid origins")
    if not all(
        isinstance(payload[key], str)
        for key in ("run_id", "authority", "browser_kind", "runtime_id")
    ):
        raise PolicyError("session bootstrap: invalid run/authority/browser_kind")
    if not _RUNTIME_ID_RE.fullmatch(payload["runtime_id"]):
        raise PolicyError("session bootstrap: invalid runtime_id")
    if payload["browser_kind"] not in BROWSER_KINDS:
        raise PolicyError("session bootstrap: invalid browser_kind")
    context = ExecutionContext.create(
        run_id=payload["run_id"],
        target_id="pending",
        authority=payload["authority"],
        origins=",".join(origins),
        session_id=session_id,
    )
    if tuple(origins) != context.origins:
        raise PolicyError("session bootstrap: non-canonical origins")
    session_dir = bootstrap_path.parent.resolve()
    expected_paths = {
        "session_dir": session_dir,
        "profile_dir": session_dir / "profile",
        "artifacts_dir": session_dir / "artifacts",
    }
    for key, expected in expected_paths.items():
        raw = payload[key]
        if not isinstance(raw, str) or not Path(raw).is_absolute() or Path(raw) != expected:
            raise PolicyError(f"session bootstrap: {key} outside session")
    _secure_directory(expected_paths["profile_dir"], "session profile")
    _secure_directory(expected_paths["artifacts_dir"], "session artifacts")
    chrome_bin = payload["chrome_bin"]
    if not isinstance(chrome_bin, str) or not chrome_bin or "\x00" in chrome_bin:
        raise PolicyError("session bootstrap: invalid chrome_bin")
    startup_timeout = _positive_finite(
        payload["startup_timeout"],
        "session bootstrap: startup_timeout",
    )
    if startup_timeout > MAX_STARTUP_TIMEOUT:
        raise PolicyError("session bootstrap: startup_timeout out of range")
    created = _aware_timestamp(payload["created_at"], "created_at")
    expires = _aware_timestamp(payload["expires_at"], "expires_at")
    if expires <= created:
        raise PolicyError("session bootstrap: invalid expiration")
    owner_pid = payload["owner_pid"]
    owner_start_time = payload["owner_start_time"]
    if (owner_pid is None) != (owner_start_time is None):
        raise PolicyError("session bootstrap: incomplete owner")
    if owner_pid is not None:
        _strict_int(owner_pid, "owner_pid", maximum=_MAX_PID)
        if not isinstance(owner_start_time, str) or not owner_start_time:
            raise PolicyError("session bootstrap: invalid owner_start_time")
        _assert_process_identity(owner_pid, owner_start_time, None, "owner")
    ignore_tls_errors = payload["ignore_tls_errors"]
    if type(ignore_tls_errors) is not bool:
        raise PolicyError("session bootstrap: ignore_tls_errors must be a boolean")
    trust_ca_dir = payload["trust_ca_dir"]
    _validate_trust_ca_dir(trust_ca_dir, "session bootstrap", require_dir=True)
    return SupervisorBootstrap(
        session_id=session_id,
        run_id=payload["run_id"],
        profile_id=profile_id,
        browser_kind=payload["browser_kind"],
        authority=payload["authority"],
        origins=tuple(origins),
        owner_pid=owner_pid,
        owner_start_time=owner_start_time,
        chrome_bin=chrome_bin,
        startup_timeout=startup_timeout,
        session_dir=str(expected_paths["session_dir"]),
        profile_dir=str(expected_paths["profile_dir"]),
        artifacts_dir=str(expected_paths["artifacts_dir"]),
        created_at=payload["created_at"],
        expires_at=payload["expires_at"],
        runtime_id=payload["runtime_id"],
        ignore_tls_errors=ignore_tls_errors,
        trust_ca_dir=trust_ca_dir,
    )


def _supervise(bootstrap_path: Path, attestation: str) -> int:
    from cdpx.sessions.supervisor import supervise

    return supervise(bootstrap_path, attestation)


def _build_private_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdpx.session")
    parser.add_argument("command", choices=["_supervise"])
    parser.add_argument("bootstrap")
    parser.add_argument("--attestation", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_private_parser().parse_args(argv)
    return _supervise(Path(args.bootstrap), args.attestation)


if __name__ == "__main__":
    raise SystemExit(main())
