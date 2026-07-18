"""Private subprocess entry point for the supervised browser lifecycle."""

from __future__ import annotations

import contextlib
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from cdpx import discovery, session
from cdpx.policy import PolicyError, assert_loopback_endpoint


@dataclass
class SupervisorRuntime:
    session_dir: Path
    error_path: Path
    chrome: subprocess.Popen[Any] | None = None
    chrome_log: TextIO | None = None
    manifest: session.SessionManifest | None = None


def supervise(bootstrap_path: Path, attestation: str) -> int:
    try:
        data = session._read_bootstrap(bootstrap_path)
        expected_attestation = session._policy_attestation(data)
        if not secrets.compare_digest(attestation, expected_attestation):
            raise PolicyError("session bootstrap: invalid attestation")
    except Exception as error:  # noqa: BLE001 - no effect before validation
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1

    session_dir = Path(data.session_dir)
    error_path = session_dir.parent / f"{data.session_id}.error"
    runtime = SupervisorRuntime(session_dir, error_path)
    stop_requested = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    try:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        runtime = _start_runtime(runtime, data, bootstrap_path, attestation)
        _poll_runtime(runtime, lambda: stop_requested)
        return 0
    except Exception as error:  # noqa: BLE001 - error forwarded to the parent
        diagnostics = session._startup_diagnostic_tails(runtime.session_dir)
        session._write_private(
            runtime.error_path,
            f"{type(error).__name__}: {error}\n{diagnostics}\n",
        )
        return 1
    finally:
        _teardown_runtime(runtime)


def _start_runtime(
    runtime: SupervisorRuntime,
    data: session.SupervisorBootstrap,
    bootstrap_path: Path,
    attestation: str,
) -> SupervisorRuntime:
    startup_deadline = time.monotonic() + data.startup_timeout
    print("startup_stage=spawn_chrome", flush=True)
    profile_dir = Path(data.profile_dir)
    log_path = runtime.session_dir / "chrome-stderr.log"
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    runtime.chrome_log = os.fdopen(log_fd, "w", encoding="utf-8")
    browser_command = (
        session.build_chrome_command(data.chrome_bin, profile_dir)
        if data.browser_kind == "chrome"
        else session.build_mock_command(profile_dir)
    )
    runtime.chrome = subprocess.Popen(
        browser_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=runtime.chrome_log,
        close_fds=True,
    )
    chrome = runtime.chrome
    print("startup_stage=wait_devtools_port", flush=True)
    port = session._read_devtools_port(
        profile_dir,
        chrome,
        timeout=session._remaining_startup_timeout(startup_deadline, "DevToolsActivePort"),
    )
    print(f"startup_stage=wait_discovery port={port}", flush=True)
    session._wait_discovery(
        port,
        chrome,
        timeout=session._remaining_startup_timeout(startup_deadline, "discovery Chrome"),
    )
    print("startup_stage=create_target", flush=True)
    target = discovery.new_tab("127.0.0.1", port, "about:blank")
    target_id = str(target["id"])
    ws_url = str(target["webSocketDebuggerUrl"])
    assert_loopback_endpoint("127.0.0.1", ws_url)
    browser_start_time, browser_argv = session._process_identity(chrome.pid)
    if not session._argv_has_markers(
        browser_argv,
        session._browser_markers(data.browser_kind, profile_dir),
    ):
        raise PolicyError("browser started without the assigned markers")
    supervisor_start_time, supervisor_argv = session._process_identity(os.getpid())
    expected_supervisor_markers = (
        "-m",
        "cdpx.session",
        "_supervise",
        str(runtime.session_dir / "bootstrap.json"),
        f"--attestation={attestation}",
    )
    if not session._argv_has_markers(supervisor_argv, expected_supervisor_markers):
        raise PolicyError("supervisor without assigned bootstrap marker")
    runtime.manifest = session.SessionManifest(
        session_id=data.session_id,
        run_id=data.run_id,
        profile_id=data.profile_id,
        browser_kind=data.browser_kind,
        authority=data.authority,
        origins=data.origins,
        host="127.0.0.1",
        port=port,
        target_id=target_id,
        websocket_url=ws_url,
        browser_pid=chrome.pid,
        browser_start_time=browser_start_time,
        supervisor_pid=os.getpid(),
        supervisor_start_time=supervisor_start_time,
        owner_pid=data.owner_pid,
        owner_start_time=data.owner_start_time,
        session_dir=data.session_dir,
        profile_dir=data.profile_dir,
        artifacts_dir=data.artifacts_dir,
        created_at=data.created_at,
        expires_at=data.expires_at,
    )
    manifest = runtime.manifest
    session._validate_manifest_fields(manifest)
    print("startup_stage=attest_target", flush=True)
    session._enforce_single_page_target(
        manifest,
        close_timeout=min(
            2.0,
            session._remaining_startup_timeout(startup_deadline, "attestation target"),
        ),
    )
    session.write_manifest(manifest)
    print("startup_stage=ready", flush=True)
    bootstrap_path.unlink(missing_ok=True)
    return runtime


def _poll_runtime(runtime: SupervisorRuntime, stop_requested: Callable[[], bool]) -> None:
    chrome = runtime.chrome
    manifest = runtime.manifest
    if chrome is None or manifest is None:
        raise RuntimeError("supervisor runtime not started")
    expires = session._aware_timestamp(manifest.expires_at, "expires_at")
    while True:
        if stop_requested() or chrome.poll() is not None:
            return
        if (runtime.session_dir / session.STOP_NAME).exists():
            return
        if (
            manifest.owner_pid is not None
            and manifest.owner_start_time is not None
            and not session._process_matches(
                manifest.owner_pid,
                manifest.owner_start_time,
                None,
            )
        ):
            return
        if session._now() >= expires:
            return
        session._enforce_single_page_target(manifest)
        time.sleep(0.25)


def _teardown_runtime(runtime: SupervisorRuntime) -> None:
    manifest, runtime.manifest = runtime.manifest, None
    if manifest is not None:
        with contextlib.suppress(Exception):
            discovery.close_tab(manifest.host, manifest.port, manifest.target_id)
    chrome, runtime.chrome = runtime.chrome, None
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
    chrome_log, runtime.chrome_log = runtime.chrome_log, None
    if chrome_log is not None:
        chrome_log.close()
    # Bounded retry: Chrome child processes (crashpad, renderers) can outlive
    # the killed main process for a moment and recreate files inside the
    # profile while rmtree walks it (ENOTEMPTY under CI load). The contract
    # stays fail-closed: a tree still stuck after the deadline raises.
    deadline = time.monotonic() + 15
    while True:
        try:
            shutil.rmtree(runtime.session_dir)
            break
        except FileNotFoundError:
            break
        except OSError as cleanup_error:
            if time.monotonic() >= deadline:
                session._write_private(
                    runtime.error_path,
                    f"{type(cleanup_error).__name__}: session cleanup failed: {cleanup_error}\n",
                )
                raise
            time.sleep(0.2)
