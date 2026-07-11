"""Shared helpers for real-Chrome end-to-end tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from cdpx.client import CDPClient
from cdpx.primitives import capture
from cdpx.testing.evidence import EvidenceCase, slugify


def free_loopback_port() -> int:
    """Reserve an ephemeral loopback port long enough to discover its number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_chrome(proc: subprocess.Popen, port: int, log_path: Path, timeout: float = 30) -> None:
    """Fail fast when Chrome exits or never exposes its discovery endpoint."""
    from urllib.request import ProxyHandler, build_opener

    deadline = time.monotonic() + timeout
    last_error = "discovery endpoint unavailable"
    direct_opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            details = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Chrome exited with {proc.returncode} before readiness:\n{details}")
        try:
            with direct_opener.open(
                f"http://127.0.0.1:{port}/json/version", timeout=1.0
            ) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.1)
    details = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise RuntimeError(
        f"Chrome did not expose CDP on 127.0.0.1:{port} after {timeout:.1f}s: "
        f"{last_error}\n{details}"
    )


def stop_process(proc: subprocess.Popen, timeout: float = 5) -> None:
    """Terminate a child deterministically, escalating to kill when necessary."""
    if proc.poll() is not None:
        proc.wait()
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=timeout)


def run_cli(
    port: int,
    *args: str,
    target: str | None = None,
    timeout: float = 15,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the installed cdpx CLI as a black box and capture its full contract."""
    command = [sys.executable, "-m", "cdpx.cli", "--port", str(port)]
    if target is not None:
        command.extend(["--target", target])
    command.extend(args)
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def successful_json(proc: subprocess.CompletedProcess[str]) -> dict | list:
    """Decode a successful one-object CLI response with an empty diagnostic stream."""
    if proc.returncode != 0 or proc.stderr:
        raise AssertionError(
            f"CLI failed: exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    return json.loads(proc.stdout)


def attach_screenshot(
    evidence_case: EvidenceCase | None,
    client: CDPClient,
    label: str = "final",
    *,
    full_page: bool = False,
) -> dict | None:
    if evidence_case is None:
        return None
    filename = f"{slugify(label)}.png"
    path = Path(evidence_case.artifact_dir) / filename
    result = capture.screenshot(client, str(path), full_page=full_page)
    artifact = evidence_case.attach_screenshot(result["path"], label)
    artifact["screenshot"] = result
    return artifact
