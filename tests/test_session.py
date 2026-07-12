from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdpx import session as session_mod
from cdpx.policy import PolicyError
from cdpx.session import (
    SessionLease,
    SessionManifest,
    assert_session_active,
    build_chrome_command,
    find_chrome,
    load_manifest,
    remove_session_files,
    runtime_root,
    session_status,
    start_session,
    stop_session,
    write_manifest,
)

SESSION_ID = "a" * 24
PROFILE_ID = "b" * 16


def manifest_for(root: Path) -> SessionManifest:
    session_dir = root / SESSION_ID
    return SessionManifest(
        session_id=SESSION_ID,
        run_id="R1",
        profile_id=PROFILE_ID,
        authority="interaction",
        origins=("http://*.test",),
        host="127.0.0.1",
        port=9222,
        target_id="T1",
        websocket_url="ws://127.0.0.1:9222/devtools/page/T1",
        browser_pid=999_999,
        browser_start_time="linux:browser",
        supervisor_pid=999_998,
        supervisor_start_time="linux:supervisor",
        owner_pid=os.getpid(),
        owner_start_time="linux:owner",
        session_dir=str(session_dir),
        profile_dir=str(session_dir / "profile"),
        artifacts_dir=str(session_dir / "artifacts"),
        created_at="2026-07-12T00:00:00+00:00",
        expires_at="2026-07-12T01:00:00+00:00",
    )


def test_manifest_is_private_and_builds_team_context(tmp_path):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_manifest(path, run_id="R1", target_id="T1")
    assert loaded == manifest
    context = loaded.execution_context()
    assert context.team_mode is True
    assert context.authority.value == "interaction"


def test_manifest_refuses_permissions_and_assignment_mismatch(tmp_path):
    path = write_manifest(manifest_for(tmp_path))
    with pytest.raises(PolicyError, match="run"):
        load_manifest(path, run_id="OTHER", target_id="T1")
    with pytest.raises(PolicyError, match="target"):
        load_manifest(path, run_id="R1", target_id="OTHER")
    path.chmod(0o644)
    with pytest.raises(PolicyError, match="permissions"):
        load_manifest(path, run_id="R1", target_id="T1")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("authority", "admin", "autorité"),
        ("origins", "http://demo.test", "origins"),
        ("created_at", "2026-07-12T00:00:00", "fuseau"),
        ("browser_pid", True, "browser_pid"),
        (
            "websocket_url",
            "ws://127.0.0.1:9333/devtools/page/T1",
            "port/target",
        ),
    ],
)
def test_manifest_rejects_malformed_typed_or_unbound_fields(
    tmp_path,
    field,
    value,
    message,
):
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(PolicyError, match=message):
        load_manifest(path)


def test_manifest_rejects_tampered_session_paths(tmp_path):
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profile_dir"] = "/tmp/unrelated-profile"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(PolicyError, match="hors du dossier"):
        load_manifest(path, run_id="R1", target_id="T1")


def test_session_lease_is_non_blocking_and_owned_by_run(tmp_path):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
        with pytest.raises(PolicyError, match="déjà utilisée"):
            with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
                pass


def test_session_lease_reattests_fresh_manifest_by_default(tmp_path, monkeypatch):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    checked = []
    monkeypatch.setattr(session_mod, "assert_session_active", checked.append)

    with SessionLease(path, run_id="R1", target_id="T1") as leased:
        assert leased == manifest

    assert checked == [manifest]


def test_public_manifest_omits_capabilities_and_physical_profile(tmp_path):
    public = manifest_for(tmp_path).public_dict()
    assert public["run_id"] == "R1" and public["target_id"] == "T1"
    assert public["profile"] == {"id": PROFILE_ID, "ephemeral": True}
    assert "websocket_url" not in public
    assert "profile_dir" not in public
    assert "browser_pid" not in public


def test_chrome_command_forces_ephemeral_loopback_profile(tmp_path):
    profile = tmp_path / "profile"
    command = build_chrome_command("/usr/bin/chromium", profile)
    assert command[0] == "/usr/bin/chromium"
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=0" in command
    assert f"--user-data-dir={profile}" in command
    assert "--no-first-run" in command


def test_chrome_sandbox_is_disabled_only_for_root_or_ci(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("CI", raising=False)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    assert "--no-sandbox" not in command

    monkeypatch.setenv("CI", "true")
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    assert "--no-sandbox" in command

    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 0)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    assert "--no-sandbox" in command


def test_cleanup_only_removes_the_manifest_session_tree(tmp_path):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    remove_session_files(path)
    assert not Path(manifest.session_dir).exists()
    assert keep.read_text(encoding="utf-8") == "keep"


def test_manifest_cannot_name_an_arbitrary_parent_as_its_session(tmp_path):
    project = tmp_path / "project"
    project.mkdir(mode=0o700)
    payload = manifest_for(tmp_path)
    forged = {**payload.__dict__, "session_dir": str(project)}
    forged["profile_dir"] = str(project / "profile")
    forged["artifacts_dir"] = str(project / "artifacts")
    (project / "profile").mkdir()
    (project / "artifacts").mkdir()
    path = project / "manifest.json"
    path.write_text(json.dumps(forged), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(PolicyError, match="hors du dossier"):
        load_manifest(path)
    assert project.exists()


def test_stop_refuses_to_signal_a_reused_or_forged_pid(tmp_path):
    manifest = manifest_for(tmp_path)
    process_start, _ = session_mod._process_identity(os.getpid())
    forged = replace(
        manifest,
        browser_pid=os.getpid(),
        browser_start_time=process_start,
    )
    path = write_manifest(forged)

    with pytest.raises(PolicyError, match="marqueur"):
        stop_session(path, run_id=forged.run_id, target_id=forged.target_id, timeout=0.001)

    assert path.exists()


def test_stop_respects_the_exclusive_command_lease(tmp_path):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    with SessionLease(
        path,
        run_id=manifest.run_id,
        target_id=manifest.target_id,
        require_active=False,
    ):
        with pytest.raises(PolicyError, match="déjà utilisée"):
            stop_session(
                path,
                run_id=manifest.run_id,
                target_id=manifest.target_id,
                timeout=0.001,
            )


def test_stop_rejects_invalid_timeout_before_writing_stop_file(tmp_path):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    with pytest.raises(PolicyError, match="fini et strictement positif"):
        stop_session(
            path,
            run_id=manifest.run_id,
            target_id=manifest.target_id,
            timeout=float("nan"),
        )

    assert not (Path(manifest.session_dir) / session_mod.STOP_NAME).exists()


def test_start_session_bootstraps_and_returns_supervised_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    launched = []

    class FakeSupervisor:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        bootstrap_path = Path(argv[4])
        data = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        manifest = SessionManifest(
            session_id=data["session_id"],
            run_id=data["run_id"],
            profile_id=data["profile_id"],
            authority=data["authority"],
            origins=tuple(data["origins"]),
            host="127.0.0.1",
            port=9333,
            target_id="TARGET",
            websocket_url="ws://127.0.0.1:9333/devtools/page/TARGET",
            browser_pid=os.getpid(),
            browser_start_time="linux:fake-browser",
            supervisor_pid=4242,
            supervisor_start_time="linux:fake-supervisor",
            owner_pid=data["owner_pid"],
            owner_start_time=data["owner_start_time"],
            session_dir=data["session_dir"],
            profile_dir=data["profile_dir"],
            artifacts_dir=data["artifacts_dir"],
            created_at=data["created_at"],
            expires_at=data["expires_at"],
        )
        write_manifest(manifest)
        return FakeSupervisor()

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)

    manifest, path = start_session(
        run_id="run-start",
        authority="observation",
        origins="http://demo.test",
        owner_pid=os.getpid(),
        chrome_bin="ignored",
        root=tmp_path,
        timeout=1,
    )

    assert manifest.target_id == "TARGET"
    assert path == tmp_path / SESSION_ID / "manifest.json"
    assert launched[0][0][:4] == [session_mod.sys.executable, "-m", "cdpx.session", "_supervise"]
    assert launched[0][1]["start_new_session"] is True


def test_start_session_fails_closed_on_bootstrap_error_and_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    aborted = []

    class FakeSupervisor:
        pid = 5151

        def poll(self):
            return None

    def error_popen(argv, **_kwargs):
        bootstrap = Path(argv[4])
        data = json.loads(bootstrap.read_text(encoding="utf-8"))
        (bootstrap.parent.parent / f"{data['session_id']}.error").write_text(
            "synthetic bootstrap failure",
            encoding="utf-8",
        )
        return FakeSupervisor()

    monkeypatch.setattr(session_mod.subprocess, "Popen", error_popen)
    monkeypatch.setattr(
        session_mod,
        "_abort_supervisor",
        lambda supervisor, path: aborted.append((supervisor.pid, path)),
    )
    with pytest.raises(PolicyError, match="synthetic bootstrap failure"):
        start_session(
            run_id="run-error",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
            timeout=1,
        )
    assert aborted and not (tmp_path / f"{SESSION_ID}.error").exists()

    with pytest.raises(PolicyError, match="strictement positif"):
        start_session(
            run_id="run-timeout",
            authority="observation",
            origins="http://demo.test",
            ttl=0,
            chrome_bin="ignored",
            root=tmp_path,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"ttl": float("nan")},
        {"ttl": float("inf")},
        {"timeout": 0},
        {"timeout": float("nan")},
    ],
)
def test_start_session_rejects_non_finite_limits_before_creating_files(
    tmp_path,
    overrides,
):
    with pytest.raises(PolicyError, match="fini et strictement positif"):
        start_session(
            run_id="run-invalid-limits",
            authority="observation",
            origins="http://demo.test",
            root=tmp_path,
            **overrides,
        )
    assert list(tmp_path.iterdir()) == []


def test_start_session_cleans_private_tree_when_supervisor_spawn_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")

    def fail_popen(*_args, **_kwargs):
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(session_mod.subprocess, "Popen", fail_popen)

    with pytest.raises(OSError, match="synthetic spawn failure"):
        start_session(
            run_id="run-spawn-failure",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
        )
    assert not (tmp_path / SESSION_ID).exists()


def test_supervisor_builds_manifest_closes_extra_target_and_cleans_up(tmp_path, monkeypatch):
    session_dir = tmp_path / SESSION_ID
    profile_dir = session_dir / "profile"
    artifacts_dir = session_dir / "artifacts"
    for path in (session_dir, profile_dir, artifacts_dir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    bootstrap = session_dir / "bootstrap.json"
    now = datetime.now(UTC)
    bootstrap.write_text(
        json.dumps(
            {
                "session_id": SESSION_ID,
                "run_id": "run-supervisor",
                "profile_id": PROFILE_ID,
                "authority": "interaction",
                "origins": ["http://demo.test"],
                "owner_pid": None,
                "owner_start_time": None,
                "chrome_bin": "/fake/chrome",
                "session_dir": str(session_dir),
                "profile_dir": str(profile_dir),
                "artifacts_dir": str(artifacts_dir),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    bootstrap.chmod(0o600)
    attestation = session_mod._policy_attestation(json.loads(bootstrap.read_text(encoding="utf-8")))
    handlers = {}
    monkeypatch.setattr(
        session_mod.signal,
        "signal",
        lambda signum, handler: handlers.setdefault(signum, handler),
    )

    class FakeChrome:
        pid = 6262
        killed = False
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired("chrome", timeout)
            return 0

        def kill(self):
            self.killed = True

    chrome = FakeChrome()

    def fake_popen(*_args, **_kwargs):
        handlers[session_mod.signal.SIGTERM](session_mod.signal.SIGTERM, None)
        return chrome

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_mod,
        "_process_identity",
        lambda pid: (
            ("linux:chrome", (f"--user-data-dir={profile_dir}",))
            if pid == chrome.pid
            else (
                "linux:supervisor",
                (
                    "-m",
                    "cdpx.session",
                    "_supervise",
                    str(bootstrap),
                    f"--attestation={attestation}",
                ),
            )
        ),
    )
    monkeypatch.setattr(session_mod, "_read_devtools_port", lambda *_args, **_kwargs: 9444)
    monkeypatch.setattr(session_mod, "_wait_discovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        session_mod.discovery,
        "new_tab",
        lambda *_args: {
            "id": "ASSIGNED",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
        },
    )
    target_lists = iter(
        (
            [
                {"id": "OLD", "type": "page"},
                {
                    "id": "ASSIGNED",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
                },
                {"id": "WORKER", "type": "worker"},
            ],
            [
                {
                    "id": "ASSIGNED",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9444/devtools/page/ASSIGNED",
                }
            ],
        )
    )
    monkeypatch.setattr(session_mod.discovery, "list_targets", lambda *_args: next(target_lists))
    closed = []
    monkeypatch.setattr(
        session_mod.discovery,
        "close_tab",
        lambda _host, _port, target: closed.append(target),
    )
    real_rmtree = session_mod.shutil.rmtree
    removed = []
    monkeypatch.setattr(
        session_mod.shutil,
        "rmtree",
        lambda path, ignore_errors=False: removed.append((Path(path), ignore_errors)),
    )

    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    assert bootstrap.exists() and session_dir.exists()
    result = session_mod._supervise(bootstrap, attestation)

    assert result == 0
    manifest = load_manifest(session_dir / "manifest.json", run_id="run-supervisor")
    assert manifest.target_id == "ASSIGNED" and manifest.port == 9444
    assert closed == ["OLD", "ASSIGNED"]
    assert chrome.terminated is True and chrome.killed is True
    assert removed == [(session_dir, False)]
    real_rmtree(session_dir)


def test_supervisor_rejects_invalid_bootstrap_without_writing_or_cleanup(tmp_path):
    session_dir = tmp_path / SESSION_ID
    session_dir.mkdir(mode=0o700)
    bootstrap = session_dir / "bootstrap.json"
    bootstrap.write_text("not-json", encoding="utf-8")
    bootstrap.chmod(0o600)

    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    error = tmp_path / f"{SESSION_ID}.error"
    assert not error.exists()
    assert session_dir.exists()
    assert bootstrap.read_text(encoding="utf-8") == "not-json"


def test_supervisor_arbitrary_path_never_removes_or_chmods_its_parent(tmp_path):
    victim = tmp_path / "project"
    victim.mkdir(mode=0o755)
    keep = victim / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    arbitrary = victim / "README.md"
    arbitrary.write_text("not a bootstrap", encoding="utf-8")
    before_mode = stat.S_IMODE(victim.stat().st_mode)

    assert session_mod._supervise(arbitrary, "0" * 64) == 1

    assert keep.read_text(encoding="utf-8") == "keep"
    assert arbitrary.exists()
    assert stat.S_IMODE(victim.stat().st_mode) == before_mode
    assert not (tmp_path / "project.error").exists()


def test_single_target_enforcement_fails_closed_when_popup_cannot_close(
    tmp_path,
    monkeypatch,
):
    manifest = manifest_for(tmp_path)
    assigned = {
        "id": manifest.target_id,
        "type": "page",
        "webSocketDebuggerUrl": manifest.websocket_url,
    }
    popup = {
        "id": "POPUP",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/POPUP",
    }
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [assigned, popup],
    )

    def refuse_close(*_args):
        raise session_mod.discovery.DiscoveryError("synthetic close refusal")

    monkeypatch.setattr(session_mod.discovery, "close_tab", refuse_close)

    with pytest.raises(PolicyError, match="fermeture.*échouée"):
        session_mod._enforce_single_page_target(manifest)


def test_exact_target_attestation_rejects_extra_page(tmp_path, monkeypatch):
    manifest = manifest_for(tmp_path)
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [
            {
                "id": manifest.target_id,
                "type": "page",
                "webSocketDebuggerUrl": manifest.websocket_url,
            },
            {
                "id": "POPUP",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/POPUP",
            },
        ],
    )

    with pytest.raises(PolicyError, match="un seul target page"):
        session_mod._assert_exact_target(manifest)


def test_session_status_activity_runtime_root_and_chrome_discovery(tmp_path, monkeypatch):
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    status = session_status(path, run_id=manifest.run_id, target_id=manifest.target_id)
    assert status["browser_running"] is False and status["supervisor_running"] is False

    active = replace(
        manifest,
        browser_pid=os.getpid(),
        browser_start_time="active-start",
        supervisor_pid=os.getpid(),
        supervisor_start_time="active-start",
        owner_start_time="active-start",
        expires_at=(datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    )
    markers = (
        f"--user-data-dir={active.profile_dir}",
        *session_mod._supervisor_markers(active),
    )

    def process_identity(pid):
        if pid != os.getpid():
            return "wrong-start", ("unrelated",)
        return "active-start", markers

    monkeypatch.setattr(session_mod, "_process_identity", process_identity)
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: [
            {
                "id": active.target_id,
                "type": "page",
                "webSocketDebuggerUrl": active.websocket_url,
            }
        ],
    )
    (Path(active.profile_dir) / "DevToolsActivePort").write_text(
        f"{active.port}\n/devtools/browser/id\n",
        encoding="utf-8",
    )
    assert_session_active(active)
    active_port = Path(active.profile_dir) / "DevToolsActivePort"
    active_port.write_text("1\n/devtools/browser/id\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="non lié au port"):
        assert_session_active(active)
    active_port.write_text(f"{active.port}\n/devtools/browser/id\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="réutilisé"):
        assert_session_active(replace(active, browser_start_time="stale-start"))
    with pytest.raises(PolicyError, match="marqueur"):
        assert_session_active(
            replace(active, profile_dir=str(Path(active.session_dir) / "other-profile"))
        )
    with pytest.raises(PolicyError, match="supervisor.*marqueur"):
        assert_session_active(replace(active, authority="privileged"))
    with pytest.raises(PolicyError, match="expires_at"):
        assert_session_active(replace(active, expires_at="invalid"))
    with pytest.raises(PolicyError, match="expirée"):
        assert_session_active(
            replace(active, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        )
    with pytest.raises(PolicyError, match="Chrome"):
        assert_session_active(replace(active, browser_pid=999_999))
    with pytest.raises(PolicyError, match="supervisor"):
        assert_session_active(replace(active, supervisor_pid=999_998))

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    assert runtime_root() == runtime / "cdpx"
    executable = tmp_path / "chromium"
    executable.write_text("binary", encoding="utf-8")
    assert find_chrome(str(executable)) == str(executable)
    with pytest.raises(PolicyError, match="introuvable"):
        find_chrome(str(tmp_path / "missing"))


def test_devtools_port_and_discovery_readiness_are_bounded(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()

    class Running:
        returncode = None

        def poll(self):
            return None

    class Stopped:
        returncode = 7

        def poll(self):
            return 7

    (profile / "DevToolsActivePort").write_text("9555\n/devtools/browser/id\n", encoding="utf-8")
    assert session_mod._read_devtools_port(profile, Running(), timeout=1) == 9555

    (profile / "DevToolsActivePort").write_text("invalid\n", encoding="utf-8")
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(session_mod.time, "sleep", lambda _delay: None)
    with pytest.raises(PolicyError, match="introuvable"):
        session_mod._read_devtools_port(profile, Running(), timeout=0.5)
    ticks = iter((0.0, 0.0))
    with pytest.raises(PolicyError, match="readiness"):
        session_mod._read_devtools_port(profile, Stopped(), timeout=1)

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class Opener:
        def open(self, url, timeout):
            assert url == "http://127.0.0.1:9555/json/version"
            assert timeout == 0.5
            return Response()

    monkeypatch.setattr(
        session_mod.urllib.request,
        "build_opener",
        lambda *_handlers: Opener(),
    )
    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    session_mod._wait_discovery(9555, Running(), timeout=1)

    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    with pytest.raises(PolicyError, match="discovery"):
        session_mod._wait_discovery(9555, Stopped(), timeout=1)
