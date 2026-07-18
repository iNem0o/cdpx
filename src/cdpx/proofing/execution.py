"""Bounded execution of proof commands and rewrite utilities.

Functions the `cdpx.proof` facade allows tests to monkeypatch (notably
`_stream_to_private_file`) are received here as keyword-only parameters by
their consumers: no symbol in this module reads `cdpx.proof` at runtime.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdpx.artifacts import ArtifactError
from cdpx.proofing.evidence_policy import PROOF_RETENTION_ENV
from cdpx.proofing.private_io import _secure_dir
from cdpx.security.redaction import RedactionContext, redact_text

# `CDPX_PROOF_TIMEOUT_SCALE` (strictly positive float, e.g. "2" on a slow
# machine) uniformly multiplies every deadline budget of the proof.
PROOF_TIMEOUT_SCALE_ENV = "CDPX_PROOF_TIMEOUT_SCALE"

_ALLOWED_ENV_NAMES = {
    "CI",
    "COLORTERM",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    PROOF_RETENTION_ENV,
    "SHELL",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
}

StreamToPrivateFile = Callable[..., tuple[int, bool]]


@dataclass
class CommandEvidence:
    id: str
    label: str
    argv: list[str]
    log: str
    exit_code: int
    duration_s: float
    status: str


def proof_timeout_scale(environ: dict[str, str] | None = None) -> float:
    """Deadline scale factor, validated fail-closed like the retention."""

    values = os.environ if environ is None else environ
    raw = values.get(PROOF_TIMEOUT_SCALE_ENV)
    if raw is None:
        return 1.0
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", raw) or float(raw) <= 0:
        raise ValueError(f"{PROOF_TIMEOUT_SCALE_ENV} must be a strictly positive float")
    return float(raw)


def _sanitize_argv(argv: list[str], context: RedactionContext) -> list[str]:
    return [
        redact_text(value, context=context, path=f"$.argv[{index}]")
        for index, value in enumerate(argv)
    ]


def _repo_env() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name in _ALLOWED_ENV_NAMES}
    src = str(Path("src").resolve())
    env["PYTHONPATH"] = src
    return env


def _rewrite_text_paths(value: str, rewrites: Sequence[tuple[str, str]]) -> str:
    """Rewrite paths from a physical root to their logical root.

    The rewrite is anchored: only `root/…` path prefixes and the value
    exactly equal to the root are rewritten. A bare literal (e.g.
    `.proof.new` quoted in a code excerpt captured by evidence) is preserved
    as-is — a naive replacement would corrupt these excerpts.
    """

    for physical, logical in rewrites:
        if value == physical:
            value = logical
            continue
        value = value.replace(f"{physical}/", f"{logical}/")
    return value


def _read_json_or_fail(path: Path, label: str) -> Any:
    """Read JSON, failing closed with a localized error.

    The offending file and cause are named in the ArtifactError, rather
    than an anonymous OSError/JSONDecodeError in the middle of the proof
    pipeline.
    """

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{label}: {path}: {exc}") from exc


def _rewrite_tree_paths(value: Any, rewrites: Sequence[tuple[str, str]]) -> Any:
    """Apply path rewrites to every string in a JSON tree."""

    if isinstance(value, str):
        return _rewrite_text_paths(value, rewrites)
    if isinstance(value, list):
        return [_rewrite_tree_paths(item, rewrites) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_tree_paths(item, rewrites) for key, item in value.items()}
    return value


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the entire process group of a proof command.

    ``proc.kill()`` alone would only reach the direct child: a Chrome or
    fixtures server launched by pytest would survive the deadline, keep its
    ports, and could write to evidence after the purge.
    """

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Group already gone or inaccessible: fall back to the direct child.
        proc.kill()
    proc.wait()


def _stream_to_private_file(
    argv: list[str],
    sink: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
) -> tuple[int, bool]:
    """Run ``argv``, streaming raw stdout+stderr into ``sink`` (0600).

    Output is never buffered in memory: the file grows as execution
    progresses (observable via tail -f) and the deadline is monotonic.
    Returns (exit_code, timed_out): 127 if the binary is not found, 124
    after a kill on deadline overrun.
    """

    _secure_dir(sink.parent)
    if sink.is_symlink():
        raise ArtifactError(f"lien symbolique interdit: {sink}")
    sink.unlink(missing_ok=True)
    fd = os.open(sink, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        try:
            # start_new_session isolates the command in its own process
            # group: the deadline kill also reaches its descendants.
            proc = subprocess.Popen(
                argv,
                cwd=Path.cwd(),
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            stream.write((str(exc) + "\n").encode("utf-8"))
            return 127, False
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Deadline exceeded: kill the group then drain the status. What
            # was already streamed into the file remains available for the
            # log.
            _kill_process_group(proc)
            return 124, True
        except BaseException:
            # Unexpected exception (KeyboardInterrupt, MemoryError…): never
            # return control while leaving the group running.
            _kill_process_group(proc)
            raise
    return exit_code, False


def _stream_and_collect(
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
    timeout_label: str,
    stream: StreamToPrivateFile | None = None,
) -> tuple[int, bool, str]:
    """Stream ``argv`` into a private ``*.partial`` file then re-read its output.

    The raw (NOT redacted) stream is removed under all circumstances — even
    if reading, redaction, or the final write fails: a partial staging kept
    for diagnostics must never contain raw output. Memory is bounded only
    WHILE running (disk streaming): the final re-read reloads the whole text
    for redaction — a deliberate choice, the deadline bounds the run's
    duration, not the volume of its output.

    ``stream`` receives the streaming implementation (facade contract: the
    `cdpx.proof` facade resolves it at call time to stay monkeypatchable in
    tests).
    """

    stream_impl = _stream_to_private_file if stream is None else stream
    partial = log_path.with_name(f"{log_path.name}.partial")
    try:
        exit_code, timed_out = stream_impl(argv, partial, env=env, timeout=timeout)
        raw = partial.read_text(encoding="utf-8", errors="replace")
    finally:
        partial.unlink(missing_ok=True)
    if timed_out:
        raw += f"\ntimeout: {timeout_label} after {timeout}s (exit 124)\n"
    return exit_code, timed_out, raw


def _run_text(
    argv: list[str],
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output + f"\ntimeout after {timeout}s\n"
    except FileNotFoundError as exc:
        return 127, f"{exc}\n"
    return proc.returncode, proc.stdout
