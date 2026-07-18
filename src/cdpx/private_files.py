"""Package-internal atomic write primitive for already-validated private paths."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


class PrivateFileError(ValueError):
    """A destination cannot satisfy the private-file publication contract."""


def _secure_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise PrivateFileError(f"symbolic private directory forbidden: {parent}")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent.is_dir():
        raise PrivateFileError(f"private directory required: {parent}")
    parent.chmod(0o700)
    if path.is_symlink():
        raise PrivateFileError(f"symbolic private file forbidden: {path}")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically publish bytes with mode 0600.

    Domain layers may translate ``PrivateFileError`` but don't need to repeat
    parent-directory, symlink, durability, or permission mechanics.
    """
    _secure_parent(path)
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


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))
