"""Enregistrements asciinema opt-in pour la preuve (dégradation propre).

Activés par ``CDPX_PROOF_CAST=1`` quand ``asciinema`` est dans le PATH; export
GIF si ``agg`` est présent. Les commandes enregistrées sont des preuves
secondaires bon marché ré-exécutées pour la démonstration — jamais les
commandes de verdict (pytest/ruff/mypy): leur exit code sous ``asciinema rec``
n'est pas contractuel et doubler leur exécution serait prohibitif. Tout échec
retourne un statut dégradé, jamais une exception: l'absence d'un cast ne doit
pas bloquer le portail de preuve.
"""

from __future__ import annotations

import os
import secrets
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cdpx.security.redaction import RedactionContext, redact_text

CAST_ENV = "CDPX_PROOF_CAST"
MAX_CAST_BYTES = 2 * 1024 * 1024
MAX_GIF_BYTES = 5 * 1024 * 1024
CAST_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("cli-help", [sys.executable, "-m", "cdpx.cli", "--help"]),
)


def _write_private_text(path: Path, value: str) -> None:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def cast_enabled(environ: Mapping[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    return values.get(CAST_ENV) == "1" and shutil.which("asciinema") is not None


def record_cast(
    id: str,
    argv: list[str],
    cast_path: Path,
    *,
    env: dict[str, str],
    timeout: float = 120.0,
    redaction_context: RedactionContext | None = None,
) -> dict[str, Any] | None:
    """Enregistre ``argv`` en .cast redacté, ou un statut dégradé sans lever."""

    if shutil.which("asciinema") is None:
        return None
    context = redaction_context or RedactionContext()
    cast_path.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            [
                "asciinema",
                "rec",
                "--quiet",
                "--overwrite",
                "--command",
                shlex.join(argv),
                str(cast_path),
            ],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        cast_path.unlink(missing_ok=True)
        return {"id": id, "path": "", "status": "unavailable"}
    if proc.returncode != 0 or cast_path.is_symlink() or not cast_path.is_file():
        cast_path.unlink(missing_ok=True)
        return {"id": id, "path": "", "status": "unavailable"}
    if cast_path.stat().st_size > MAX_CAST_BYTES:
        cast_path.unlink(missing_ok=True)
        return {"id": id, "path": "", "status": "too-large"}
    cleaned = redact_text(
        cast_path.read_text(encoding="utf-8", errors="replace"),
        context=context,
        path=f"$.casts.{id}",
    )
    _write_private_text(cast_path, cleaned)
    return {
        "id": id,
        "path": str(cast_path),
        "bytes": len(cleaned.encode("utf-8")),
        "status": "generated",
    }


def export_gif(
    cast_path: Path,
    gif_path: Path,
    *,
    env: dict[str, str],
    timeout: float = 180.0,
) -> dict[str, Any] | None:
    """Exporte un GIF compagnon via ``agg``, ou un statut dégradé sans lever."""

    if shutil.which("agg") is None:
        return None
    gif_path.unlink(missing_ok=True)
    try:
        proc = subprocess.run(
            ["agg", str(cast_path), str(gif_path)],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        gif_path.unlink(missing_ok=True)
        return {"path": "", "status": "unavailable"}
    if proc.returncode != 0 or gif_path.is_symlink() or not gif_path.is_file():
        gif_path.unlink(missing_ok=True)
        return {"path": "", "status": "unavailable"}
    if gif_path.stat().st_size > MAX_GIF_BYTES:
        gif_path.unlink(missing_ok=True)
        return {"path": "", "status": "too-large"}
    gif_path.chmod(0o600)
    return {"path": str(gif_path), "status": "generated"}


def collect_cast_evidence(
    root: Path,
    *,
    env: dict[str, str],
    redaction_context: RedactionContext | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Boucle opt-in: un .cast (+ GIF si possible) par commande de démonstration."""

    if not cast_enabled(environ):
        return []
    entries: list[dict[str, Any]] = []
    for cast_id, argv in CAST_COMMANDS:
        entry = record_cast(
            cast_id,
            argv,
            root / f"{cast_id}.cast",
            env=env,
            redaction_context=redaction_context,
        )
        if entry is None:
            continue
        if entry["status"] == "generated":
            gif = export_gif(Path(entry["path"]), root / f"{cast_id}.gif", env=env)
            entry["gif"] = gif or {"path": "", "status": "agg-missing"}
        entries.append(entry)
    return entries
