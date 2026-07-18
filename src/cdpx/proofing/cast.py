"""Native terminal recordings for the proof (asciicast v2).

Stdlib producer (pty + select): no asciinema/agg dependency. Recorded
commands are cheap secondary demonstration proofs — never the verdict
commands (pytest/ruff/mypy), whose duplicated execution would be
prohibitive. Recording is systematic and part of the gate: a missing or
degraded cast fails ``make proof`` (see ``cast_failures`` in ``cdpx.proof``).
"""

from __future__ import annotations

import codecs
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import Any

from cdpx.proofing.private_io import _write_private_text
from cdpx.security.redaction import RedactionContext, redact_text

MAX_CAST_BYTES = 2 * 1024 * 1024
CAST_WIDTH = 100
CAST_HEIGHT = 30
CAST_COMMANDS: tuple[tuple[str, list[str]], ...] = (
    ("cli-help", [sys.executable, "-m", "cdpx.cli", "--help"]),
    ("mock-session-demo", [sys.executable, "-m", "cdpx.proofing.demo"]),
)


def _spawn_on_pty(argv: list[str], env: dict[str, str]) -> tuple[subprocess.Popen[bytes], int]:
    """Start ``argv`` attached to a pseudo-terminal sized for the player."""

    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", CAST_HEIGHT, CAST_WIDTH, 0, 0))
        proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env={**env, "TERM": "xterm-256color", "COLUMNS": str(CAST_WIDTH)},
            close_fds=True,
        )
    except OSError:
        os.close(master)
        raise
    finally:
        os.close(slave)
    return proc, master


def record_cast(
    id: str,
    argv: list[str],
    cast_path: Path,
    *,
    env: dict[str, str],
    timeout: float = 120.0,
    redaction_context: RedactionContext | None = None,
) -> dict[str, Any]:
    """Record ``argv`` as a redacted .cast, or a degraded status without raising."""

    context = redaction_context or RedactionContext()
    cast_path.unlink(missing_ok=True)
    try:
        proc, master = _spawn_on_pty(argv, env)
    except OSError:
        return {"id": id, "path": "", "status": "unavailable"}
    events: list[tuple[float, str]] = []
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    started = time.monotonic()
    deadline = started + timeout
    total_bytes = 0
    status = ""
    try:
        while True:
            if time.monotonic() > deadline:
                status = "unavailable"
                break
            ready, _, _ = select.select([master], [], [], 0.25)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break  # slave closed and buffer drained: end of recording
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > MAX_CAST_BYTES:
                    status = "too-large"
                    break
                text = decoder.decode(chunk)
                if text:
                    events.append((time.monotonic() - started, text))
            elif proc.poll() is not None:
                break
    finally:
        os.close(master)
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if not status and proc.returncode != 0:
        status = "unavailable"
    if status:
        cast_path.unlink(missing_ok=True)
        return {"id": id, "path": "", "status": status}
    header = {
        "version": 2,
        "width": CAST_WIDTH,
        "height": CAST_HEIGHT,
        "env": {"TERM": "xterm-256color"},
    }
    lines = [json.dumps(header, ensure_ascii=False)]
    for offset, text in events:
        # Redaction per event (before JSON encoding) then on the final
        # content: a secret fragmented across events remains the known
        # limit — hence upload_allowed=False on every cast artifact.
        clean = redact_text(text, context=context, path=f"$.casts.{id}")
        lines.append(json.dumps([round(offset, 6), "o", clean], ensure_ascii=False))
    content = redact_text("\n".join(lines) + "\n", context=context, path=f"$.casts.{id}")
    if len(content.encode("utf-8")) > MAX_CAST_BYTES:
        return {"id": id, "path": "", "status": "too-large"}
    _write_private_text(cast_path, content)
    return {
        "id": id,
        "path": str(cast_path),
        "bytes": len(content.encode("utf-8")),
        "status": "generated",
    }


def collect_cast_evidence(
    root: Path,
    *,
    env: dict[str, str],
    redaction_context: RedactionContext | None = None,
) -> list[dict[str, Any]]:
    """Record each demonstration command; the gate judges the statuses."""

    return [
        record_cast(
            cast_id,
            argv,
            root / f"{cast_id}.cast",
            env=env,
            redaction_context=redaction_context,
        )
        for cast_id, argv in CAST_COMMANDS
    ]
