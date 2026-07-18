"""Real Chrome E2E for the lifecycle and isolation of supervised sessions."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import capture, state
from cdpx.session import (
    SessionLease,
    SessionManifest,
    find_chrome,
    load_manifest,
    stop_session,
)
from cdpx.testing.e2e import attach_cli_run


def run_session_cli(
    *args: str,
    timeout: float = 40,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cdpx.cli", "--timeout", "30", "session", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **(env or {})},
    )


def start_managed_session(
    *,
    run_id: str,
    authority: str,
    origins: str,
    chrome_bin: str,
    runtime_dir: Path,
    evidence_case=None,
    start_label: str | None = None,
) -> tuple[SessionManifest, Path]:
    proc = run_session_cli(
        "start",
        "--run-id",
        run_id,
        "--authority",
        authority,
        "--origins",
        origins,
        "--ttl",
        "300",
        "--owner-pid",
        str(os.getpid()),
        "--chrome",
        chrome_bin,
        env={"XDG_RUNTIME_DIR": str(runtime_dir)},
    )
    assert proc.returncode == 0 and not proc.stderr, (
        f"session start failed: exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    if start_label is not None:
        attach_cli_run(evidence_case, start_label, proc)
    payload = json.loads(proc.stdout)
    assert payload["started"] is True
    path = Path(payload["manifest"])
    manifest = load_manifest(path, run_id=run_id, target_id=payload["target_id"])
    return manifest, path


def run_browser_cli(
    manifest: SessionManifest,
    manifest_path: Path,
    *args: str,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cdpx.cli",
            "--session",
            str(manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "15",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )


def successful_session_json(
    manifest: SessionManifest,
    manifest_path: Path,
    *args: str,
) -> dict:
    proc = run_browser_cli(manifest, manifest_path, *args)
    assert proc.returncode == 0 and not proc.stderr, (
        f"session CLI failed: exit={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert payload["_cdpx"] == {
        "run_id": manifest.run_id,
        "session_id": manifest.session_id,
        "target_id": manifest.target_id,
        "authority": manifest.authority,
        "content_trust": "untrusted",
    }
    return payload


def browser_state(manifest: SessionManifest) -> tuple[dict, dict]:
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        cookies = state.get_cookies(client, show_values=True)
        local_storage = state.get_storage(client, kind="local", show_values=True)
    return cookies, local_storage


def attach_session_screenshot(
    evidence_case, manifest: SessionManifest, path: Path, label: str
) -> None:
    if evidence_case is None:
        return
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        capture.screenshot(client, str(path))
    evidence_case.attach_file(path, label, "screenshot")


def removed_within(path: Path, timeout: float = 45) -> bool:
    """Bounded wait for removal: the supervisor cleans up in its own
    process and can return control after the CLI stop command returns
    on a loaded machine (2-core CI runners)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return True
        time.sleep(0.05)
    return not path.exists()


def port_is_closed(port: int, timeout: float = 2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                pass
        except OSError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.scenario(
    feature="state-session",
    journey="isolate-session-runs",
    scenario_id="state-session.isolate-supervised-session-runs",
    proves=[
        "Managed runs use distinct Chrome profiles, targets and loopback endpoints.",
        "Cookies and localStorage do not cross session boundaries.",
        "Authority grants and the exclusive command lease are enforced by the real CLI.",
        "A popup target is closed by the supervisor before it can persist in the run.",
        "Stopping a run removes its private files and closes its CDP endpoint.",
    ],
)
def test_supervised_sessions_are_isolated_authorized_and_torn_down(
    fixtures_http,
    tmp_path,
    evidence_case,
):
    """Three concurrent supervised runs live in genuinely sealed Chrome
    instances (profiles, targets, ports, cookies, storage), each authority
    bounds what the CLI accepts, and stopping leaves neither file nor open
    port behind."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    origins = fixtures_http.base_url
    sessions: list[tuple[SessionManifest, Path]] = []
    proof: dict = {
        "sessions": [],
        "status": {},
        "isolation": {},
        "authority": {},
        "lease": {},
        "teardown": [],
    }

    try:
        for run_id, authority in (
            ("e2e-observation", "observation"),
            ("e2e-interaction", "interaction"),
            ("e2e-privileged", "privileged"),
        ):
            manifest, path = start_managed_session(
                run_id=run_id,
                authority=authority,
                origins=origins,
                chrome_bin=chrome_bin,
                runtime_dir=runtime_dir,
            )
            sessions.append((manifest, path))
            proof["sessions"].append(manifest.public_dict())

        observation, interaction, privileged = sessions
        manifests = [item[0] for item in sessions]
        #: each run gets its own identity, disk profile, target and loopback
        #: port: no resource is shared between runs
        assert len({item.session_id for item in manifests}) == 3
        assert len({item.profile_id for item in manifests}) == 3
        assert len({item.profile_dir for item in manifests}) == 3
        assert len({item.target_id for item in manifests}) == 3
        assert len({item.port for item in manifests}) == 3
        assert all(Path(item.profile_dir).is_dir() for item in manifests)

        status_proc = run_session_cli(
            "status",
            "--session",
            str(observation[1]),
            "--run-id",
            observation[0].run_id,
            "--target",
            observation[0].target_id,
            env={"XDG_RUNTIME_DIR": str(runtime_dir)},
        )
        #: the status command answers cleanly on stdout alone
        assert status_proc.returncode == 0 and not status_proc.stderr
        status = json.loads(status_proc.stdout)
        #: browser and supervisor are reported alive, but the status leaks
        #: neither the WebSocket endpoint nor the private profile path
        assert status["browser_running"] is True
        assert status["supervisor_running"] is True
        assert "websocket_url" not in status and "profile_dir" not in status
        proof["status"] = status

        observation_url = f"{fixtures_http.base_url}/storage.html"
        interaction_url = f"{fixtures_http.base_url}/form.html"
        privileged_url = f"{fixtures_http.base_url}/index.html"
        #: each session navigates to its own reference page via the real CLI,
        #: the _cdpx envelope is verified by the helper along the way
        assert successful_session_json(*observation, "goto", observation_url)["ok"] is True
        assert successful_session_json(*interaction, "goto", interaction_url)["ok"] is True
        assert successful_session_json(*privileged, "goto", privileged_url)["ok"] is True

        assigned_urls = {
            item.run_id: discovery.pick_page(item.host, item.port, item.target_id)["url"]
            for item in manifests
        }
        #: queried endpoint by endpoint, each Chrome shows its own run's URL
        #: and no other: navigations did not cross
        assert assigned_urls == {
            observation[0].run_id: observation_url,
            interaction[0].run_id: interaction_url,
            privileged[0].run_id: privileged_url,
        }
        attach_session_screenshot(
            evidence_case,
            observation[0],
            tmp_path / "managed-session.png",
            "Managed session target",
        )

        observation_cookies, observation_storage = browser_state(observation[0])
        interaction_cookies, interaction_storage = browser_state(interaction[0])
        observation_cookie_names = {item["name"] for item in observation_cookies["cookies"]}
        interaction_cookie_names = {item["name"] for item in interaction_cookies["cookies"]}
        #: the cookie and localStorage key created by the observation
        #: session's page exist there and never leaked into the interaction
        #: session: browser state is partitioned by profile
        assert "jsCookie" in observation_cookie_names
        assert observation_storage["entries"]["cdpx-key"] == "cdpx-value"
        assert "jsCookie" not in interaction_cookie_names
        assert "cdpx-key" not in interaction_storage["entries"]
        proof["isolation"] = {
            "observation_cookie_names": sorted(observation_cookie_names),
            "interaction_cookie_names": sorted(interaction_cookie_names),
            "observation_local_storage_keys": sorted(observation_storage["entries"]),
            "interaction_local_storage_keys": sorted(interaction_storage["entries"]),
            "assigned_urls": assigned_urls,
        }

        observed = successful_session_json(*observation, "text", "h1")
        #: observation authority is enough to read the DOM
        assert observed["text"] == "Storage"
        denied_interaction = run_browser_cli(*observation, "click", "h1")
        #: the click is denied with exit 1 and a diagnostic that names the
        #: missing authority — the refusal is explainable, not silent
        assert denied_interaction.returncode == 1
        assert "requires interaction" in denied_interaction.stderr
        attach_cli_run(evidence_case, "Denied click (observation authority)", denied_interaction)

        clicked = successful_session_json(*interaction, "click", "#submit-btn")
        #: interaction authority allows a real click and the DOM attests to
        #: it through the form submission result
        assert clicked["clicked"] == "#submit-btn"
        assert successful_session_json(*interaction, "text", "#result")["text"] == "OK:"
        denied_privileged = run_browser_cli(*interaction, "eval", "document.title")
        #: arbitrary JS evaluation stays out of interaction's reach:
        #: explicit refusal naming the required privileged level
        assert denied_privileged.returncode == 1
        assert "requires privileged" in denied_privileged.stderr
        attach_cli_run(evidence_case, "Denied eval (interaction authority)", denied_privileged)

        evaluated = successful_session_json(*privileged, "eval", "document.title")
        #: privileged authority unlocks JS evaluation in the real page
        assert evaluated["value"] == "cdpx fixtures — accueil"
        opened = successful_session_json(
            *privileged,
            "eval",
            "window.open('about:blank', '_blank'); true",
        )
        #: the page did execute the popup open — the danger is real
        assert opened["value"] is True
        popup_deadline = time.monotonic() + 5
        pages = []
        while time.monotonic() < popup_deadline:
            pages = [
                target
                for target in discovery.list_targets(privileged[0].host, privileged[0].port)
                if target.get("type") == "page"
            ]
            if [target.get("id") for target in pages] == [privileged[0].target_id]:
                break
            time.sleep(0.05)
        #: the supervisor closed the popup back down: only the target
        #: assigned to the run survives, no stray window can settle in
        assert [target.get("id") for target in pages] == [privileged[0].target_id]
        proof["authority"] = {
            "observation_text": "allowed",
            "observation_click": "denied",
            "interaction_click": "allowed",
            "interaction_eval": "denied",
            "privileged_eval": "allowed",
            "popup_target": "closed_by_supervisor",
        }

        with SessionLease(
            observation[1],
            run_id=observation[0].run_id,
            target_id=observation[0].target_id,
        ):
            contended = run_browser_cli(*observation, "text", "h1")
        #: while the exclusive lease is held elsewhere, the command is
        #: denied with a diagnostic explaining the contention
        assert contended.returncode == 1
        assert "session already in use" in contended.stderr
        attach_cli_run(evidence_case, "Contended command (lease held elsewhere)", contended)
        #: with the lease released, the same command goes through right
        #: away: the refusal really came from the lock, not a broken state
        assert successful_session_json(*observation, "text", "h1")["text"] == "Storage"
        proof["lease"] = {"while_held": "denied", "after_release": "allowed"}

        for manifest, path in reversed(sessions):
            profile_dir = Path(manifest.profile_dir)
            session_dir = Path(manifest.session_dir)
            stopped = run_session_cli(
                "stop",
                "--session",
                str(path),
                "--run-id",
                manifest.run_id,
                "--target",
                manifest.target_id,
                timeout=45,
                env={"XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            #: stopping each run answers cleanly, even run in series while
            #: other sessions are still alive
            assert stopped.returncode == 0 and not stopped.stderr, (
                f"session stop failed: exit={stopped.returncode}\n"
                f"stdout={stopped.stdout}\nstderr={stopped.stderr}"
            )
            result = json.loads(stopped.stdout)
            closed = port_is_closed(manifest.port)
            teardown = {
                **result,
                "manifest_removed": removed_within(path),
                "profile_removed": removed_within(profile_dir),
                "session_dir_removed": removed_within(session_dir),
                "port_closed": closed,
            }
            proof["teardown"].append(teardown)
            #: stopping leaves no trace: private manifest, profile and
            #: directory removed (bounded wait: the supervisor cleans up in
            #: its own process), loopback CDP endpoint closed
            residue = [
                key
                for key in (
                    "stopped",
                    "manifest_removed",
                    "profile_removed",
                    "session_dir_removed",
                    "port_closed",
                )
                if not teardown[key]
            ]
            assert not residue, f"teardown residue for {manifest.run_id}: {residue}"
    finally:
        for manifest, path in reversed(sessions):
            if path.exists():
                with contextlib.suppress(Exception):
                    stop_session(
                        path,
                        run_id=manifest.run_id,
                        target_id=manifest.target_id,
                        timeout=10,
                    )
        if evidence_case is not None:
            evidence_case.attach_json(
                "Supervised session isolation",
                proof,
                "managed-session-isolation.json",
            )


@pytest.mark.scenario(
    feature="state-session",
    journey="teardown-supervisor-signal",
    scenario_id="state-session.teardown-on-supervisor-signal",
    proves=[
        "A normal supervisor termination closes Chrome and removes its ephemeral profile.",
        "The assigned CDP loopback endpoint is closed after teardown.",
    ],
)
def test_supervisor_signal_still_tears_down_chrome_and_private_files(
    fixtures_http,
    tmp_path,
    evidence_case,
):
    """A plain SIGTERM to the supervisor is enough to tear everything down —
    Chrome closed, private files removed, CDP endpoint closed — without ever
    going through the CLI stop command."""
    chrome_bin = find_chrome()
    runtime_dir = tmp_path / "runtime"
    manifest, path = start_managed_session(
        run_id="e2e-signal-teardown",
        authority="observation",
        origins=fixtures_http.base_url,
        chrome_bin=chrome_bin,
        runtime_dir=runtime_dir,
        evidence_case=evidence_case,
        start_label="Session start (before SIGTERM)",
    )
    session_dir = Path(manifest.session_dir)
    profile_dir = Path(manifest.profile_dir)
    proof = {"session": manifest.public_dict()}
    try:
        #: the session is alive and drivable before the signal is sent: the
        #: teardown observed afterward cannot be a false positive
        assert (
            successful_session_json(
                manifest,
                path,
                "goto",
                f"{fixtures_http.base_url}/index.html",
            )["ok"]
            is True
        )
        attach_session_screenshot(
            evidence_case,
            manifest,
            tmp_path / "signal-teardown-session.png",
            "Session before supervisor teardown",
        )
        if evidence_case is not None:
            status_proc = run_session_cli(
                "status",
                "--session",
                str(path),
                "--run-id",
                manifest.run_id,
                "--target",
                manifest.target_id,
                env={"XDG_RUNTIME_DIR": str(runtime_dir)},
            )
            attach_cli_run(evidence_case, "Session status (alive, before SIGTERM)", status_proc)
        #: the manifest exposes the supervisor's pid, sole recipient of the
        #: termination signal
        assert manifest.supervisor_pid is not None
        os.kill(manifest.supervisor_pid, signal.SIGTERM)
        # Budget aligned on the worst case of the supervisor teardown itself:
        # close_tab HTTP + terminate (5s) + kill (5s) + rmtree of a full
        # Chrome profile, which alone can exceed 30s on a loaded 2-core CI
        # runner during make proof — each wait stays bounded.
        proof["teardown"] = {
            "manifest_removed": removed_within(path),
            "profile_removed": removed_within(profile_dir),
            "session_dir_removed": removed_within(session_dir),
            "port_closed": port_is_closed(manifest.port, timeout=10),
        }
        #: after the signal, no trace remains: manifest, profile and session
        #: directory removed, CDP port closed — the cleanup is intrinsic to
        #: the supervisor, not to the stop command
        assert all(proof["teardown"].values())
    finally:
        if path.exists():
            with contextlib.suppress(Exception):
                stop_session(
                    path,
                    run_id=manifest.run_id,
                    target_id=manifest.target_id,
                    timeout=10,
                )
        if evidence_case is not None:
            evidence_case.attach_json(
                "Supervisor signal teardown",
                proof,
                "supervisor-signal-teardown.json",
            )
