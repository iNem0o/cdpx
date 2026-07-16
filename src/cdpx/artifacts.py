"""Écriture privée, classification et staging explicite des artefacts cdpx."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from cdpx.private_files import PrivateFileError, atomic_write_bytes
from cdpx.security import RedactionContext, redact_text, redact_tree

SCHEMA = "cdpx.artifacts/v1"
REDACTION_POLICY_VERSION = "1"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class ArtifactError(ValueError):
    pass


class ArtifactClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    SECRET = "secret"
    OPAQUE_RESTRICTED = "opaque-restricted"


@dataclass(frozen=True)
class ArtifactEntry:
    path: str
    bytes: int
    sha256: str
    mime: str
    classification: str
    upload_allowed: bool
    created_at: str
    redaction_policy: str = REDACTION_POLICY_VERSION


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _secure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ArtifactError(f"dossier d'artefact symbolique interdit: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ArtifactError(f"dossier d'artefact requis: {path}")
    path.chmod(0o700)


def _directory_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _file_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _assert_private_owner(info: os.stat_result, relative: str) -> None:
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise ArtifactError(f"artefact appartenant à un autre utilisateur: {relative}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ArtifactError(f"permissions trop ouvertes: {relative}")


def _atomic_private_write(path: Path, data: bytes) -> None:
    try:
        atomic_write_bytes(path, data)
    except PrivateFileError as error:
        raise ArtifactError(str(error)) from error


class SecureArtifactWriter:
    def __init__(
        self,
        root: str | Path,
        run_id: str,
        *,
        ttl: float = 86400,
        redaction_context: RedactionContext | None = None,
    ) -> None:
        if not _SAFE_ID.fullmatch(run_id or ""):
            raise ArtifactError("run-id invalide pour un chemin d'artefacts")
        if ttl <= 0:
            raise ArtifactError("TTL d'artefact strictement positif requis")
        self.root = Path(root)
        _secure_dir(self.root)
        self.run_id = run_id
        self.run_dir = self.root / run_id
        _secure_dir(self.run_dir)
        self.manifest_path = self.run_dir / "manifest.json"
        self.created_at = _now()
        self.expires_at = self.created_at + timedelta(seconds=ttl)
        self.redaction_context = redaction_context or RedactionContext()
        self._entries: dict[str, ArtifactEntry] = {}
        self._write_manifest()

    def write_text(
        self,
        name: str,
        value: str,
        *,
        classification: ArtifactClassification = ArtifactClassification.INTERNAL,
        upload_allowed: bool = False,
    ) -> ArtifactEntry:
        return self.write_bytes(
            name,
            redact_text(value, context=self.redaction_context, path=f"$.artifacts.{name}").encode(
                "utf-8"
            ),
            classification=classification,
            upload_allowed=upload_allowed,
            mime="text/plain",
        )

    def write_json(
        self,
        name: str,
        value: Any,
        *,
        classification: ArtifactClassification = ArtifactClassification.INTERNAL,
        upload_allowed: bool = False,
    ) -> ArtifactEntry:
        safe = redact_tree(value, context=self.redaction_context, path=f"$.artifacts.{name}")
        data = (json.dumps(safe, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        return self.write_bytes(
            name,
            data,
            classification=classification,
            upload_allowed=upload_allowed,
            mime="application/json",
        )

    def write_bytes(
        self,
        name: str,
        data: bytes,
        *,
        classification: ArtifactClassification = ArtifactClassification.INTERNAL,
        upload_allowed: bool = False,
        mime: str | None = None,
    ) -> ArtifactEntry:
        relative = self._relative(name)
        self._assert_shareable(classification, upload_allowed)
        path = self.run_dir / relative
        _atomic_private_write(path, data)
        entry = ArtifactEntry(
            path=relative.as_posix(),
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            mime=mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            classification=classification.value,
            upload_allowed=upload_allowed,
            created_at=_iso(_now()),
        )
        self._entries[entry.path] = entry
        self._write_manifest()
        return entry

    def register_file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        classification: ArtifactClassification = ArtifactClassification.INTERNAL,
        upload_allowed: bool = False,
    ) -> ArtifactEntry:
        source = Path(path)
        if source.is_symlink():
            raise ArtifactError(f"lien symbolique interdit: {source}")
        if not source.is_file():
            raise ArtifactError(f"artefact introuvable: {source}")
        destination = name
        if destination is None:
            try:
                destination = source.resolve().relative_to(self.run_dir.resolve()).as_posix()
            except ValueError:
                destination = source.name
        data = source.read_bytes()
        mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        if _is_textual(source, mime):
            decoded = data.decode("utf-8", errors="replace")
            if source.suffix.lower() == ".json" or mime == "application/json":
                try:
                    parsed = json.loads(decoded)
                except json.JSONDecodeError:
                    decoded = redact_text(
                        decoded,
                        context=self.redaction_context,
                        path=f"$.artifacts.{destination}",
                    )
                else:
                    decoded = (
                        json.dumps(
                            redact_tree(
                                parsed,
                                context=self.redaction_context,
                                path=f"$.artifacts.{destination}",
                            ),
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    )
            else:
                decoded = redact_text(
                    decoded,
                    context=self.redaction_context,
                    path=f"$.artifacts.{destination}",
                )
            data = decoded.encode("utf-8")
        return self.write_bytes(
            destination,
            data,
            classification=classification,
            upload_allowed=upload_allowed,
            mime=mime,
        )

    def build_shareable(self, destination: str | Path) -> Path:
        self._assert_manifest_complete()
        snapshots: dict[str, bytes] = {}
        for entry in self._entries.values():
            data = self._read_verified_entry(entry)
            if entry.upload_allowed:
                snapshots[entry.path] = data
        staging = Path(destination)
        if staging.exists():
            if staging.is_symlink():
                raise ArtifactError("staging symbolique interdit")
            shutil.rmtree(staging)
        _secure_dir(staging)
        selected = [entry for entry in self._entries.values() if entry.upload_allowed]
        for entry in selected:
            target = staging / entry.path
            _atomic_private_write(target, snapshots[entry.path])
        payload = self._manifest_payload(selected)
        _atomic_private_write(
            staging / "manifest.json",
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return staging

    def _relative(self, name: str) -> Path:
        path = Path(name)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ArtifactError(f"chemin d'artefact invalide: {name}")
        candidate = (self.run_dir / path).resolve()
        try:
            candidate.relative_to(self.run_dir.resolve())
        except ValueError as e:
            raise ArtifactError(f"chemin d'artefact hors run: {name}") from e
        return path

    @staticmethod
    def _assert_shareable(
        classification: ArtifactClassification,
        upload_allowed: bool,
    ) -> None:
        if upload_allowed and classification in {
            ArtifactClassification.SECRET,
            ArtifactClassification.OPAQUE_RESTRICTED,
        }:
            raise ArtifactError(f"classification non partageable: {classification.value}")

    def _assert_manifest_complete(self) -> None:
        expected = set(self._entries) | {self.manifest_path.name}
        actual = {
            path.relative_to(self.run_dir).as_posix()
            for path in self.run_dir.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        unknown = sorted(actual - expected)
        if unknown:
            raise ArtifactError(f"fichier non manifesté: {', '.join(unknown)}")
        for relative in expected:
            path = self.run_dir / relative
            if path.is_symlink():
                raise ArtifactError(f"lien symbolique non manifestable: {relative}")
            if not path.exists():
                raise ArtifactError(f"artefact manifesté introuvable: {relative}")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise ArtifactError(f"artefact régulier requis: {relative}")
            _assert_private_owner(info, relative)

    def _read_verified_entry(self, entry: ArtifactEntry) -> bytes:
        """Lit un snapshot no-follow et vérifie les métadonnées manifestées.

        Les descripteurs de chaque dossier sont ouverts relativement au dossier
        de run. Un remplacement concurrent par un lien symbolique ne peut donc
        ni sortir du run, ni changer les octets copiés après leur validation.
        """

        relative = Path(entry.path)
        root_fd: int | None = None
        run_fd: int | None = None
        current_fd: int | None = None
        file_fd: int | None = None
        try:
            root_fd = os.open(self.root, _directory_flags())
            run_fd = os.open(self.run_id, _directory_flags(), dir_fd=root_fd)
            current_fd = os.dup(run_fd)
            for part in relative.parts[:-1]:
                next_fd = os.open(part, _directory_flags(), dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(relative.name, _file_flags(), dir_fd=current_fd)
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise ArtifactError(f"artefact régulier requis: {entry.path}")
            _assert_private_owner(info, entry.path)
            with os.fdopen(file_fd, "rb") as stream:
                file_fd = None
                data = stream.read()
        except OSError as e:
            raise ArtifactError(f"artefact non lisible sans suivi: {entry.path}: {e}") from e
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if current_fd is not None:
                os.close(current_fd)
            if run_fd is not None:
                os.close(run_fd)
            if root_fd is not None:
                os.close(root_fd)

        actual_hash = hashlib.sha256(data).hexdigest()
        if len(data) != entry.bytes or actual_hash != entry.sha256:
            raise ArtifactError(f"intégrité d'artefact invalide: {entry.path}")
        return data

    def _manifest_payload(self, entries: list[ArtifactEntry] | None = None) -> dict[str, Any]:
        values = list(self._entries.values()) if entries is None else entries
        return {
            "schema": SCHEMA,
            "run_id": self.run_id,
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
            "redaction_policy": REDACTION_POLICY_VERSION,
            "artifacts": [asdict(entry) for entry in sorted(values, key=lambda item: item.path)],
        }

    def _write_manifest(self) -> None:
        _atomic_private_write(
            self.manifest_path,
            (json.dumps(self._manifest_payload(), ensure_ascii=False, indent=2) + "\n").encode(
                "utf-8"
            ),
        )


def _is_textual(path: Path, mime: str) -> bool:
    return (
        mime.startswith("text/")
        or mime
        in {
            "application/json",
            "application/javascript",
            "application/sql",
            "application/xml",
            "application/x-ndjson",
            "image/svg+xml",
        }
        or path.suffix.lower() in {".json", ".log", ".md", ".ndjson", ".txt", ".xml"}
    )


def scan_canaries(root: str | Path, values: list[str]) -> list[str]:
    base = Path(root)
    needles = [value.encode("utf-8") for value in values if value]
    matches: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if any(needle in data for needle in needles):
            matches.append(path.relative_to(base).as_posix())
    return matches


def purge_expired(root: str | Path, *, now: datetime | None = None) -> list[str]:
    base = Path(root)
    current = now or _now()
    removed: list[str] = []
    if not base.exists():
        return removed
    candidates = (path for path in base.iterdir() if path.is_dir() and not path.is_symlink())
    for run_dir in sorted(candidates):
        manifest = run_dir / "manifest.json"
        try:
            raw = manifest.read_bytes()
        except FileNotFoundError:
            # Répertoire sans manifeste (résidu partiel ou étranger): la purge
            # ne juge que ce qu'un manifeste date — conservation fail-open.
            continue
        except PermissionError:
            # Fichiers root laissés par un run Docker: propagé tel quel, le
            # consommateur (cdpx.proof) avertit avec le remède chown et
            # poursuit son run best-effort.
            raise
        except OSError as error:
            raise ArtifactError(f"manifest d'artefacts invalide: {manifest}: {error}") from error
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("objet JSON requis")
            raw_expiration = payload["expires_at"]
            if not isinstance(raw_expiration, str):
                raise ValueError("expires_at textuel requis")
            expires = datetime.fromisoformat(raw_expiration)
            if expires.tzinfo is None or expires.utcoffset() is None:
                raise ValueError("expires_at avec fuseau requis")
        except (
            UnicodeError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ArtifactError(f"manifest d'artefacts invalide: {manifest}: {error}") from error
        if current >= expires:
            shutil.rmtree(run_dir)
            removed.append(run_dir.name)
    return removed
