"""Écritures privées du pipeline de preuve (0600/0700, atomiques, fail-closed).

Aucun symbole de ce module ne lit `cdpx.proof` à l'exécution: la façade
`cdpx.proof` ré-exporte ces primitives pour le contrat des tests.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from cdpx.artifacts import ArtifactError


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _secure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ArtifactError(f"dossier de preuve symbolique interdit: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ArtifactError(f"dossier de preuve requis: {path}")
    path.chmod(0o700)


def _write_private_bytes(path: Path, data: bytes) -> None:
    _secure_dir(path.parent)
    if path.is_symlink():
        raise ArtifactError(f"lien symbolique interdit: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _write_private_text(path: Path, value: str) -> None:
    _write_private_bytes(path, value.encode("utf-8"))


def _harden_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ArtifactError(f"lien symbolique interdit dans les preuves: {path}")
        path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)


@contextmanager
def _private_umask() -> Iterator[None]:
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)
