"""Private process identity and termination primitives for supervised sessions."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from cdpx.policy import PolicyError

MAX_PID = 2_147_483_647


def _validated_pid(pid: int) -> int:
    if not isinstance(pid, int) or isinstance(pid, bool) or not 1 <= pid <= MAX_PID:
        raise PolicyError(f"pid entier hors plage: {pid!r}")
    return pid


def process_identity(pid: int) -> tuple[str, tuple[str, ...]]:
    pid = _validated_pid(pid)
    if Path("/proc/self/stat").exists():
        return _linux_process_identity(pid)
    return _ps_process_identity(pid)


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
    except (OSError, IndexError, ValueError) as error:
        raise PolicyError(f"identité du processus {pid} invérifiable") from error
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
    except (OSError, subprocess.SubprocessError) as error:
        raise PolicyError(f"identité du processus {pid} invérifiable") from error
    if not started or not command:
        raise PolicyError(f"identité du processus {pid} invalide")
    return f"ps:{started}", (command,)


def argv_has_marker(argv: tuple[str, ...], marker: str) -> bool:
    return marker in argv or (len(argv) == 1 and marker in argv[0])


def argv_has_markers(argv: tuple[str, ...], markers: str | tuple[str, ...]) -> bool:
    expected = (markers,) if isinstance(markers, str) else markers
    return all(argv_has_marker(argv, marker) for marker in expected)


def abort_supervisor(supervisor: subprocess.Popen[Any], session_dir: Path) -> None:
    if supervisor.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(supervisor.pid, signal.SIGTERM)
        try:
            supervisor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(supervisor.pid, signal.SIGKILL)
            supervisor.wait(timeout=5)
    remove_tree(session_dir)


def remove_tree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
