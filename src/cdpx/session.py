"""Sessions navigateur jetables, attestées et exclusives.

Le manifest est la capacité locale attribuée à un run. Il lie un profil,
un target et un niveau d'autorité. Les fichiers privés restent sous un dossier
0700 et chaque commande prend un verrou non bloquant sur la session.
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
from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    assert_loopback_endpoint,
    is_loopback_host,
    validate_target,
)

SCHEMA = "cdpx.session/v1"
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
_MAX_PID = 2_147_483_647
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
    "session_dir",
    "profile_dir",
    "artifacts_dir",
    "created_at",
    "expires_at",
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
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _secure_mkdir(path: Path) -> None:
    if path.is_symlink():
        raise PolicyError(f"dossier de session symbolique interdit: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise PolicyError(f"dossier de session requis: {path}")
    path.chmod(0o700)


def _write_private(path: Path, data: str) -> None:
    _secure_mkdir(path.parent)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


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
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


def _policy_attestation(source: SessionManifest | dict[str, Any]) -> str:
    payload: dict[str, Any] = {}
    for field in _ATTESTED_POLICY_FIELDS:
        value = source[field] if isinstance(source, dict) else getattr(source, field)
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
        raise PolicyError(f"manifest de session illisible: {manifest_path}: {e}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PolicyError("manifest de session régulier requis")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError("manifest de session appartenant à un autre utilisateur")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError("permissions du manifest trop ouvertes; 0600 requis")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise PolicyError(f"manifest de session invalide: {e}") from e
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PolicyError("schema de session inconnu")
    manifest = _manifest_from_payload(payload)
    _validate_manifest_paths(manifest_path, manifest)
    if run_id is not None and manifest.run_id != run_id:
        raise PolicyError(
            f"run non propriétaire de la session: attendu {manifest.run_id}, reçu {run_id}"
        )
    if target_id is not None and manifest.target_id != target_id:
        raise PolicyError(
            f"target non attribué à la session: attendu {manifest.target_id}, reçu {target_id}"
        )
    assert_loopback_endpoint(manifest.host, manifest.websocket_url)
    return manifest


def _manifest_from_payload(payload: dict[str, Any]) -> SessionManifest:
    origins = payload.get("origins")
    if (
        not isinstance(origins, list)
        or not origins
        or not all(isinstance(item, str) and item for item in origins)
    ):
        raise PolicyError("manifest de session: origins doit être une liste non vide de chaînes")
    normalized = {**payload, "origins": tuple(origins)}
    try:
        manifest = SessionManifest(**normalized)
    except (TypeError, ValueError) as e:
        raise PolicyError(f"manifest de session incomplet: {e}") from e
    _validate_manifest_fields(manifest)
    return manifest


def _strict_int(value: Any, label: str, *, minimum: int = 1, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise PolicyError(f"manifest de session: {label} entier invalide")
    return value


def _aware_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise PolicyError(f"manifest de session: {label} doit être une date")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as e:
        raise PolicyError(f"manifest de session: {label} invalide") from e
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PolicyError(f"manifest de session: {label} doit inclure un fuseau")
    return parsed


def _validate_manifest_fields(manifest: SessionManifest) -> None:
    _validate_manifest_identity(manifest)
    if manifest.schema != SCHEMA:
        raise PolicyError("schema de session inconnu")
    if not isinstance(manifest.run_id, str) or not isinstance(manifest.target_id, str):
        raise PolicyError("manifest de session: run/target doivent être des chaînes")
    if not _TARGET_ID_RE.fullmatch(manifest.target_id):
        raise PolicyError("manifest de session: target invalide")
    if not isinstance(manifest.authority, str) or not isinstance(manifest.origins, tuple):
        raise PolicyError("manifest de session: autorité/origines invalides")
    if manifest.browser_kind not in {"chrome", "mock"}:
        raise PolicyError("manifest de session: browser_kind invalide")
    context = ExecutionContext.create(
        run_id=manifest.run_id,
        target_id=manifest.target_id,
        authority=manifest.authority,
        origins=",".join(manifest.origins),
        session_id=manifest.session_id,
    )
    if context.origins != manifest.origins:
        raise PolicyError("manifest de session: origins non canoniques")
    if not isinstance(manifest.host, str) or not is_loopback_host(manifest.host):
        raise PolicyError("manifest de session: host loopback requis")
    _strict_int(manifest.port, "port", maximum=65535)
    _strict_int(manifest.browser_pid, "browser_pid", maximum=_MAX_PID)
    _strict_int(manifest.supervisor_pid, "supervisor_pid", maximum=_MAX_PID)
    if not isinstance(manifest.browser_start_time, str) or not manifest.browser_start_time:
        raise PolicyError("manifest de session: browser_start_time invalide")
    if not isinstance(manifest.supervisor_start_time, str) or not manifest.supervisor_start_time:
        raise PolicyError("manifest de session: supervisor_start_time invalide")
    if (manifest.owner_pid is None) != (manifest.owner_start_time is None):
        raise PolicyError("manifest de session: owner_pid/start_time doivent être fournis ensemble")
    if manifest.owner_pid is not None:
        _strict_int(manifest.owner_pid, "owner_pid", maximum=_MAX_PID)
        if not isinstance(manifest.owner_start_time, str) or not manifest.owner_start_time:
            raise PolicyError("manifest de session: owner_start_time invalide")
    for label, value in (
        ("session_dir", manifest.session_dir),
        ("profile_dir", manifest.profile_dir),
        ("artifacts_dir", manifest.artifacts_dir),
        ("websocket_url", manifest.websocket_url),
    ):
        if not isinstance(value, str) or not value or "\x00" in value:
            raise PolicyError(f"manifest de session: {label} invalide")
    created = _aware_timestamp(manifest.created_at, "created_at")
    expires = _aware_timestamp(manifest.expires_at, "expires_at")
    if expires <= created:
        raise PolicyError("manifest de session: expires_at doit suivre created_at")
    _validate_websocket_binding(manifest.websocket_url, manifest.port, manifest.target_id)


def _validate_websocket_binding(websocket_url: str, port: int, target_id: str) -> None:
    assert_loopback_endpoint("127.0.0.1", websocket_url)
    try:
        parsed = urllib.parse.urlsplit(websocket_url)
        actual_port = parsed.port
    except ValueError as e:
        raise PolicyError("manifest de session: WebSocket CDP invalide") from e
    if parsed.username is not None or parsed.password is not None:
        raise PolicyError("manifest de session: credentials WebSocket interdits")
    if actual_port != port or parsed.path != f"/devtools/page/{target_id}":
        raise PolicyError("manifest de session: WebSocket non lié au port/target attribué")


def _validate_manifest_paths(path: Path, manifest: SessionManifest) -> None:
    raw_session_dir = Path(manifest.session_dir)
    if not raw_session_dir.is_absolute() or not path.is_absolute():
        raise PolicyError("chemins de session absolus requis")
    if raw_session_dir.is_symlink() or path.parent.is_symlink():
        raise PolicyError("dossier de session symbolique interdit")
    session_dir = raw_session_dir.resolve()
    if path.resolve().parent != session_dir or raw_session_dir.name != manifest.session_id:
        raise PolicyError("manifest hors du dossier de session déclaré")
    if hasattr(os, "getuid") and raw_session_dir.stat().st_uid != os.getuid():
        raise PolicyError("dossier de session appartenant à un autre utilisateur")
    if stat.S_IMODE(raw_session_dir.stat().st_mode) & 0o077:
        raise PolicyError("permissions du dossier de session trop ouvertes; 0700 requis")
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
            raise PolicyError("chemin de session hors du dossier attribué")
        try:
            info = candidate.stat()
        except OSError as e:
            raise PolicyError(f"dossier de session introuvable: {candidate}") from e
        if not stat.S_ISDIR(info.st_mode):
            raise PolicyError(f"dossier de session requis: {candidate}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PolicyError("dossier de session appartenant à un autre utilisateur")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PolicyError("permissions du dossier de session trop ouvertes; 0700 requis")


def _validate_manifest_identity(manifest: SessionManifest) -> None:
    if not isinstance(manifest.session_id, str) or not _SESSION_ID_RE.fullmatch(
        manifest.session_id
    ):
        raise PolicyError("identifiant de session invalide")
    if not isinstance(manifest.profile_id, str) or not _PROFILE_ID_RE.fullmatch(
        manifest.profile_id
    ):
        raise PolicyError("identifiant de profil invalide")


class SessionLease:
    """Verrou de commande exclusif, fail-fast, lié au run et au target."""

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
            raise PolicyError(f"verrou de session non ouvrable: {lock_path}") from e
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise PolicyError(f"verrou de session régulier requis: {lock_path}")
        os.fchmod(fd, 0o600)
        stream = os.fdopen(fd, "r+")
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            stream.close()
            raise PolicyError(
                f"session déjà utilisée par une autre commande: {manifest.session_id}"
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
    if explicit:
        resolved = shutil.which(explicit) if os.sep not in explicit else explicit
        if resolved and Path(resolved).is_file():
            return str(Path(resolved).resolve())
        raise PolicyError(f"Chrome/Chromium introuvable: {explicit}")
    for candidate in CHROME_CANDIDATES:
        if resolved := shutil.which(candidate):
            return str(Path(resolved).resolve())
    raise PolicyError("Chrome/Chromium obligatoire pour cdpx session start")


def _ci_environment() -> bool:
    value = os.environ.get("CI", "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def _sandbox_must_be_disabled() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool((callable(geteuid) and geteuid() == 0) or _ci_environment())


def build_chrome_command(chrome_bin: str, profile_dir: Path) -> list[str]:
    command = [
        chrome_bin,
        "--headless=new",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "about:blank",
    ]
    if _sandbox_must_be_disabled():
        command.insert(-2, "--no-sandbox")
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
        raise PolicyError(f"{label} numérique requis")
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0:
        raise PolicyError(f"{label} fini et strictement positif requis")
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
    timeout: float = 30,
) -> tuple[SessionManifest, Path]:
    if browser_kind not in BROWSER_KINDS:
        raise PolicyError(f"backend navigateur inconnu: {browser_kind}")
    # Valide les grants avant de créer le moindre fichier/processus.
    preliminary = ExecutionContext.create(
        run_id=run_id,
        target_id="pending",
        authority=authority,
        origins=origins,
        session_id="pending",
    )
    ttl = _positive_finite(ttl, "TTL de session")
    timeout = _positive_finite(timeout, "timeout de démarrage")
    owner = owner_pid
    owner_start_time: str | None = None
    if owner is not None:
        _strict_int(owner, "owner-pid", maximum=_MAX_PID)
        try:
            owner_start_time, _ = _process_identity(owner)
        except PolicyError as e:
            raise PolicyError(f"owner-pid introuvable ou invérifiable: {owner}") from e
    if browser_kind == "mock" and chrome_bin is not None:
        raise PolicyError("le backend mock n'accepte pas --chrome")
    chrome = find_chrome(chrome_bin) if browser_kind == "chrome" else sys.executable
    created = _now()
    try:
        expires = created + timedelta(seconds=ttl)
    except (OverflowError, ValueError) as e:
        raise PolicyError("TTL de session hors plage") from e
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
            "session_dir": str(session_dir),
            "profile_dir": str(profile_dir),
            "artifacts_dir": str(artifacts_dir),
            "created_at": _iso(created),
            "expires_at": _iso(expires),
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
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if manifest_path.exists():
                manifest = load_manifest(manifest_path, run_id=run_id)
                return manifest, manifest_path
            if error_path.exists():
                message = error_path.read_text(encoding="utf-8", errors="replace").strip()
                error_path.unlink(missing_ok=True)
                raise PolicyError(f"démarrage de session échoué: {message}")
            if supervisor.poll() is not None:
                detail = supervisor_log.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise PolicyError(f"supervisor de session arrêté prématurément: {detail}")
            time.sleep(0.05)
        raise PolicyError(f"session navigateur non prête après {timeout}s")
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
    timeout = _positive_finite(timeout, "timeout d'arrêt")
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
        raise PolicyError(f"session expirée: {manifest.session_id}")
    _assert_process_identity(
        manifest.browser_pid,
        manifest.browser_start_time,
        _browser_markers(manifest.browser_kind, manifest.profile_dir),
        "navigateur",
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


def _assert_devtools_port_binding(manifest: SessionManifest) -> None:
    active_port = Path(manifest.profile_dir) / "DevToolsActivePort"
    try:
        lines = active_port.read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
    except (OSError, IndexError, ValueError) as e:
        raise PolicyError("profil de session: DevToolsActivePort invalide") from e
    if port != manifest.port:
        raise PolicyError(
            f"profil de session non lié au port attribué: attendu={manifest.port}, reçu={port}"
        )


def remove_session_files(manifest_path: str | Path) -> None:
    manifest = load_manifest(manifest_path)
    session_dir = Path(manifest.session_dir).resolve()
    if session_dir != Path(manifest_path).resolve().parent:
        raise PolicyError("refus de supprimer un dossier hors session")
    shutil.rmtree(session_dir)


def _linux_process_identity(pid: int) -> tuple[str, tuple[str, ...]]:
    proc = Path("/proc") / str(pid)
    try:
        stat_line = (proc / "stat").read_text(encoding="utf-8")
        end = stat_line.rfind(")")
        fields = stat_line[end + 2 :].split()
        start_ticks = fields[19]
        argv = tuple(
            item.decode("utf-8", "surrogateescape")
            for item in (proc / "cmdline").read_bytes().split(b"\0")
            if item
        )
    except (OSError, IndexError, ValueError) as e:
        raise PolicyError(f"identité du processus {pid} invérifiable") from e
    if end < 0 or not start_ticks.isdigit() or not argv:
        raise PolicyError(f"identité du processus {pid} invalide")
    return f"linux:{start_ticks}", argv


def _ps_process_identity(pid: int) -> tuple[str, tuple[str, ...]]:
    try:
        started = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        raise PolicyError(f"identité du processus {pid} invérifiable") from e
    if not started or not command:
        raise PolicyError(f"identité du processus {pid} invalide")
    # Le fallback POSIX ne peut pas reconstruire argv sans ambiguïté. Le
    # marqueur est donc recherché dans la commande complète.
    return f"ps:{started}", (command,)


def _process_identity(pid: int) -> tuple[str, tuple[str, ...]]:
    _strict_int(pid, "pid", maximum=_MAX_PID)
    if Path("/proc/self/stat").exists():
        return _linux_process_identity(pid)
    return _ps_process_identity(pid)


def _argv_has_marker(argv: tuple[str, ...], marker: str) -> bool:
    return marker in argv or (len(argv) == 1 and marker in argv[0])


def _argv_has_markers(argv: tuple[str, ...], markers: str | tuple[str, ...]) -> bool:
    expected = (markers,) if isinstance(markers, str) else markers
    return all(_argv_has_marker(argv, marker) for marker in expected)


def _assert_process_identity(
    pid: int,
    expected_start_time: str,
    marker: str | tuple[str, ...] | None,
    label: str,
) -> None:
    actual_start_time, argv = _process_identity(pid)
    if actual_start_time != expected_start_time:
        raise PolicyError(f"{label} de session réutilisé ou non attribué: pid={pid}")
    if marker is not None and not _argv_has_markers(argv, marker):
        raise PolicyError(f"{label} de session sans marqueur attendu: pid={pid}")


def _process_matches(
    pid: int,
    start_time: str,
    marker: str | tuple[str, ...] | None,
) -> bool:
    try:
        _assert_process_identity(pid, start_time, marker, "processus")
    except PolicyError:
        return False
    return True


def _pid_alive(pid: int) -> bool:
    try:
        _process_identity(pid)
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


def _abort_supervisor(supervisor: subprocess.Popen[Any], session_dir: Path) -> None:
    if supervisor.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(supervisor.pid, signal.SIGTERM)
        try:
            supervisor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(supervisor.pid, signal.SIGKILL)
            supervisor.wait(timeout=5)
    _remove_tree(session_dir)


def _remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass


def _read_devtools_port(profile_dir: Path, proc: subprocess.Popen[Any], timeout: float = 30) -> int:
    active_port = profile_dir / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise PolicyError(f"Chrome arrêté avant readiness (exit {proc.returncode})")
        try:
            first = active_port.read_text(encoding="utf-8").splitlines()[0]
            port = int(first)
            if 1 <= port <= 65535:
                return port
        except (OSError, IndexError, ValueError):
            pass
        time.sleep(0.05)
    raise PolicyError("DevToolsActivePort introuvable")


def _wait_discovery(port: int, proc: subprocess.Popen[Any], timeout: float = 30) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise PolicyError(f"Chrome arrêté avant discovery (exit {proc.returncode})")
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.05)
    raise PolicyError("endpoint discovery Chrome indisponible")


def _page_targets(host: str, port: int) -> list[dict[str, Any]]:
    try:
        targets = discovery.list_targets(host, port)
    except discovery.DiscoveryError as e:
        raise PolicyError(f"discovery de session indisponible: {e}") from e
    if not isinstance(targets, list):
        raise PolicyError("discovery de session: liste de targets requise")
    return [
        target for target in targets if isinstance(target, dict) and target.get("type") == "page"
    ]


def _assert_exact_target(manifest: SessionManifest) -> dict[str, Any]:
    pages = _page_targets(manifest.host, manifest.port)
    if len(pages) != 1:
        raise PolicyError(
            f"session {manifest.session_id}: un seul target page requis, trouvé={len(pages)}"
        )
    target = validate_target(pages[0], manifest.target_id)
    websocket_url = target.get("webSocketDebuggerUrl")
    assert_loopback_endpoint(manifest.host, websocket_url)
    if websocket_url != manifest.websocket_url:
        raise PolicyError("target de session: WebSocket différent du manifest")
    _validate_websocket_binding(manifest.websocket_url, manifest.port, manifest.target_id)
    return target


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
                raise PolicyError("target page supplémentaire sans identifiant")
            try:
                discovery.close_tab(manifest.host, manifest.port, target_id)
            except discovery.DiscoveryError as e:
                raise PolicyError(f"fermeture du target supplémentaire échouée: {target_id}") from e
    # Chrome répond avant que /json/list cesse toujours d'exposer le target en
    # cours de fermeture. Attendre cette transition de façon bornée évite un
    # faux refus tout en échouant fermé si un target supplémentaire persiste.
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
        raise PolicyError(f"{label} illisible: {path}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PolicyError(f"{label} régulier non symbolique requis")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError(f"{label} appartenant à un autre utilisateur")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError(f"permissions de {label} trop ouvertes; 0600 requis")
    return info


def _secure_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as e:
        raise PolicyError(f"{label} illisible: {path}") from e
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PolicyError(f"{label} régulier non symbolique requis")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise PolicyError(f"{label} appartenant à un autre utilisateur")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise PolicyError(f"permissions de {label} trop ouvertes; 0700 requis")
    return info


def _read_bootstrap(bootstrap_path: Path) -> dict[str, Any]:
    if not bootstrap_path.is_absolute():
        bootstrap_path = Path.cwd() / bootstrap_path
    if bootstrap_path.name != "bootstrap.json":
        raise PolicyError("bootstrap de session: nom bootstrap.json requis")
    session_dir = bootstrap_path.parent
    if not _SESSION_ID_RE.fullmatch(session_dir.name):
        raise PolicyError("bootstrap de session: dossier de session invalide")
    _secure_directory(session_dir, "dossier bootstrap")
    expected = _secure_regular_file(bootstrap_path, "bootstrap de session")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(bootstrap_path, flags)
    except OSError as e:
        raise PolicyError("bootstrap de session non ouvrable") from e
    try:
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise PolicyError("bootstrap de session remplacé pendant la validation")
        if actual.st_size > 64 * 1024:
            raise PolicyError("bootstrap de session trop volumineux")
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            fd = -1
            payload = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        raise PolicyError(f"bootstrap de session invalide: {e}") from e
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(payload, dict) or set(payload) != _BOOTSTRAP_FIELDS:
        raise PolicyError("bootstrap de session: champs stricts invalides")
    return _validate_bootstrap_payload(payload, bootstrap_path)


def _validate_bootstrap_payload(
    payload: dict[str, Any],
    bootstrap_path: Path,
) -> dict[str, Any]:
    session_id = payload["session_id"]
    profile_id = payload["profile_id"]
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise PolicyError("bootstrap de session: session_id invalide")
    if session_id != bootstrap_path.parent.name:
        raise PolicyError("bootstrap de session: session_id non lié au dossier")
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
        raise PolicyError("bootstrap de session: profile_id invalide")
    origins = payload["origins"]
    if (
        not isinstance(origins, list)
        or not origins
        or not all(isinstance(item, str) and item for item in origins)
    ):
        raise PolicyError("bootstrap de session: origins invalides")
    if not all(isinstance(payload[key], str) for key in ("run_id", "authority", "browser_kind")):
        raise PolicyError("bootstrap de session: run/authority/browser_kind invalides")
    if payload["browser_kind"] not in BROWSER_KINDS:
        raise PolicyError("bootstrap de session: browser_kind invalide")
    context = ExecutionContext.create(
        run_id=payload["run_id"],
        target_id="pending",
        authority=payload["authority"],
        origins=",".join(origins),
        session_id=session_id,
    )
    if tuple(origins) != context.origins:
        raise PolicyError("bootstrap de session: origins non canoniques")
    session_dir = bootstrap_path.parent.resolve()
    expected_paths = {
        "session_dir": session_dir,
        "profile_dir": session_dir / "profile",
        "artifacts_dir": session_dir / "artifacts",
    }
    for key, expected in expected_paths.items():
        raw = payload[key]
        if not isinstance(raw, str) or not Path(raw).is_absolute() or Path(raw) != expected:
            raise PolicyError(f"bootstrap de session: {key} hors session")
    _secure_directory(expected_paths["profile_dir"], "profil de session")
    _secure_directory(expected_paths["artifacts_dir"], "artefacts de session")
    chrome_bin = payload["chrome_bin"]
    if not isinstance(chrome_bin, str) or not chrome_bin or "\x00" in chrome_bin:
        raise PolicyError("bootstrap de session: chrome_bin invalide")
    created = _aware_timestamp(payload["created_at"], "created_at")
    expires = _aware_timestamp(payload["expires_at"], "expires_at")
    if expires <= created:
        raise PolicyError("bootstrap de session: expiration invalide")
    owner_pid = payload["owner_pid"]
    owner_start_time = payload["owner_start_time"]
    if (owner_pid is None) != (owner_start_time is None):
        raise PolicyError("bootstrap de session: owner incomplet")
    if owner_pid is not None:
        _strict_int(owner_pid, "owner_pid", maximum=_MAX_PID)
        if not isinstance(owner_start_time, str) or not owner_start_time:
            raise PolicyError("bootstrap de session: owner_start_time invalide")
        _assert_process_identity(owner_pid, owner_start_time, None, "owner")
    return payload


def _supervise(bootstrap_path: Path, attestation: str) -> int:
    try:
        data = _read_bootstrap(bootstrap_path)
        expected_attestation = _policy_attestation(data)
        if not secrets.compare_digest(attestation, expected_attestation):
            raise PolicyError("bootstrap de session: attestation invalide")
    except Exception as e:  # noqa: BLE001 - aucun effet avant validation
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    session_dir = Path(data["session_dir"])
    parent = session_dir.parent
    error_path = parent / f"{data['session_id']}.error"
    chrome: subprocess.Popen[Any] | None = None
    chrome_log = None
    manifest: SessionManifest | None = None
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        profile_dir = Path(data["profile_dir"])
        log_path = session_dir / "chrome-stderr.log"
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        chrome_log = os.fdopen(log_fd, "w", encoding="utf-8")
        browser_command = (
            build_chrome_command(data["chrome_bin"], profile_dir)
            if data["browser_kind"] == "chrome"
            else build_mock_command(profile_dir)
        )
        chrome = subprocess.Popen(
            browser_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=chrome_log,
            close_fds=True,
        )
        port = _read_devtools_port(profile_dir, chrome)
        _wait_discovery(port, chrome)
        target = discovery.new_tab("127.0.0.1", port, "about:blank")
        target_id = str(target["id"])
        ws_url = str(target["webSocketDebuggerUrl"])
        assert_loopback_endpoint("127.0.0.1", ws_url)
        browser_start_time, browser_argv = _process_identity(chrome.pid)
        if not _argv_has_markers(
            browser_argv,
            _browser_markers(data["browser_kind"], profile_dir),
        ):
            raise PolicyError("navigateur démarré sans les marqueurs attribués")
        supervisor_start_time, supervisor_argv = _process_identity(os.getpid())
        expected_bootstrap = str(session_dir / "bootstrap.json")
        expected_supervisor_markers = (
            "-m",
            "cdpx.session",
            "_supervise",
            expected_bootstrap,
            f"--attestation={attestation}",
        )
        if not _argv_has_markers(supervisor_argv, expected_supervisor_markers):
            raise PolicyError("supervisor sans marqueur bootstrap attribué")
        manifest = SessionManifest(
            session_id=data["session_id"],
            run_id=data["run_id"],
            profile_id=data["profile_id"],
            browser_kind=data["browser_kind"],
            authority=data["authority"],
            origins=tuple(data["origins"]),
            host="127.0.0.1",
            port=port,
            target_id=target_id,
            websocket_url=ws_url,
            browser_pid=chrome.pid,
            browser_start_time=browser_start_time,
            supervisor_pid=os.getpid(),
            supervisor_start_time=supervisor_start_time,
            owner_pid=int(data["owner_pid"]) if data["owner_pid"] is not None else None,
            owner_start_time=data["owner_start_time"],
            session_dir=data["session_dir"],
            profile_dir=data["profile_dir"],
            artifacts_dir=data["artifacts_dir"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )
        _validate_manifest_fields(manifest)
        _enforce_single_page_target(manifest)
        write_manifest(manifest)
        bootstrap_path.unlink(missing_ok=True)
        expires = _aware_timestamp(manifest.expires_at, "expires_at")
        while True:
            if stop_requested:
                break
            if chrome.poll() is not None:
                break
            if (session_dir / STOP_NAME).exists():
                break
            if (
                manifest.owner_pid is not None
                and manifest.owner_start_time is not None
                and not _process_matches(
                    manifest.owner_pid,
                    manifest.owner_start_time,
                    None,
                )
            ):
                break
            if _now() >= expires:
                break
            _enforce_single_page_target(manifest)
            time.sleep(0.25)
        return 0
    except Exception as e:  # noqa: BLE001 - erreur transmise au processus appelant
        _write_private(error_path, f"{type(e).__name__}: {e}\n")
        return 1
    finally:
        if manifest is not None:
            with contextlib.suppress(Exception):
                discovery.close_tab(manifest.host, manifest.port, manifest.target_id)
        if chrome is not None:
            if chrome.poll() is None:
                chrome.terminate()
                try:
                    chrome.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome.kill()
                    chrome.wait(timeout=5)
            else:
                chrome.wait()
        if chrome_log is not None:
            chrome_log.close()
        try:
            shutil.rmtree(session_dir)
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            _write_private(
                error_path,
                f"{type(cleanup_error).__name__}: cleanup session échoué: {cleanup_error}\n",
            )
            raise


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
