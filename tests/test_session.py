from __future__ import annotations

import json
import os
import shlex
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
    export_lines,
    find_chrome,
    load_manifest,
    remove_session_files,
    runtime_root,
    session_status,
    start_session,
    stop_session,
    write_manifest,
)
from cdpx.sessions import supervisor as supervisor_mod

SESSION_ID = "a" * 24
PROFILE_ID = "b" * 16


def test_supervisor_teardown_is_idempotent(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    runtime = supervisor_mod.SupervisorRuntime(
        session_dir=session_dir,
        error_path=tmp_path / "session.error",
    )

    supervisor_mod._teardown_runtime(runtime)
    supervisor_mod._teardown_runtime(runtime)

    assert not session_dir.exists()


def manifest_for(root: Path) -> SessionManifest:
    session_dir = root / SESSION_ID
    return SessionManifest(
        session_id=SESSION_ID,
        run_id="R1",
        profile_id=PROFILE_ID,
        browser_kind="chrome",
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


def test_manifest_is_private_and_builds_execution_context(tmp_path):
    """Writing the manifest enforces private permissions and its attested
    reread restores the same content, ready to produce an execution context
    carrying the declared authority."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    #: directory and file are unreadable to other users, the admission
    #: condition for reloading
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_manifest(path, run_id="R1", target_id="T1")
    #: no field is lost or transformed by the round trip to disk
    assert loaded == manifest
    context = loaded.execution_context()
    #: the context inherits the authority and session identity from the manifest
    assert context.authority.value == "interaction"
    assert context.session_id == SESSION_ID


@pytest.mark.scenario(
    feature="state-session",
    journey="exercise-session-without-chrome",
    scenario_id="state-session.run-supervised-mock-session",
    proves=[
        "The packaged mock uses the same attested manifest and loopback endpoint contract.",
        "Stopping the mock session removes its private runtime tree.",
    ],
)
def test_mock_backend_uses_supervised_session_contract(tmp_path):
    """The mock backend shipped with the package honors the same supervised
    session contract as Chrome: attested manifest, consistent loopback
    endpoint, stop that erases the entire private tree."""
    manifest, path = start_session(
        run_id="mock-contract",
        authority="privileged",
        origins="http://*.test",
        browser_kind="mock",
        owner_pid=os.getpid(),
        root=tmp_path,
        timeout=10,
    )
    session_dir = Path(manifest.session_dir)
    try:
        #: the manifest describes the mock backend with a websocket URL whose
        #: port matches the announced one, just like a real Chrome
        assert manifest.browser_kind == "mock"
        assert manifest.port == int(manifest.websocket_url.split(":")[2].split("/")[0])
        #: HTTP discovery responds on this port identifying itself as the
        #: mock, and the activity attestation passes with the same code as
        #: for Chrome
        assert session_mod.discovery.version(manifest.host, manifest.port)["Browser"].startswith(
            "MockChrome/"
        )
        assert_session_active(manifest)
    finally:
        stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)

    #: stopping removes the private runtime without leaving a trace
    assert not session_dir.exists()


def test_manifest_refuses_permissions_and_assignment_mismatch(tmp_path):
    """Loading the manifest fails closed when the assignment identity
    (run, target) does not match, or when the file has become readable by
    other users."""
    path = write_manifest(manifest_for(tmp_path))
    #: a foreign run cannot appropriate another's session
    with pytest.raises(PolicyError, match="run"):
        load_manifest(path, run_id="OTHER", target_id="T1")
    #: a target not assigned to this session is refused the same way
    with pytest.raises(PolicyError, match="target"):
        load_manifest(path, run_id="R1", target_id="OTHER")
    path.chmod(0o644)
    #: widened permissions invalidate the manifest, even with intact content
    with pytest.raises(PolicyError, match="permissions"):
        load_manifest(path, run_id="R1", target_id="T1")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("authority", "admin", "unknown authority level"),
        ("origins", "http://demo.test", "origins"),
        ("created_at", "2026-07-12T00:00:00", "timezone"),
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
    """Every critical manifest field is validated on load: out-of-domain
    value, unexpected type, naive datetime, or a websocket URL inconsistent
    with the declared port/target are all rejected."""
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)

    #: whatever corruption is injected, the faulty field is named in the
    #: error and no manifest is ever returned
    with pytest.raises(PolicyError, match=message):
        load_manifest(path)


def test_manifest_rejects_tampered_session_paths(tmp_path):
    """A manifest whose internal path points outside the session directory
    is rejected: impossible to redirect cdpx to an arbitrary directory by
    editing the file."""
    path = write_manifest(manifest_for(tmp_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profile_dir"] = "/tmp/unrelated-profile"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    #: the profile moved outside the session tree blocks loading
    with pytest.raises(PolicyError, match="outside the assigned directory"):
        load_manifest(path, run_id="R1", target_id="T1")


def test_session_lease_is_non_blocking_and_owned_by_run(tmp_path):
    """The session lease is exclusive and non-blocking: a second attempt to
    acquire the same session fails immediately with PolicyError instead of
    waiting for the lock to be released."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
        #: the second acquisition fails right away instead of blocking the
        #: concurrent command
        with pytest.raises(PolicyError, match="already in use by another command"):
            with SessionLease(path, run_id="R1", target_id="T1", require_active=False):
                pass


def test_session_lease_reattests_fresh_manifest_by_default(tmp_path, monkeypatch):
    """By default, taking the lease re-attests that the session is alive and
    provides the manifest freshly reread from disk."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    checked = []
    monkeypatch.setattr(session_mod, "assert_session_active", checked.append)

    with SessionLease(path, run_id="R1", target_id="T1") as leased:
        #: the lease exposes the manifest reread from disk, not a stale copy
        assert leased == manifest

    #: the activity attestation was called exactly once, on this manifest
    assert checked == [manifest]


@pytest.mark.scenario(
    feature="state-session",
    journey="isolate-session-runs",
    scenario_id="state-session.public-manifest-hides-control-levers",
    proves=[
        "The public manifest view keeps run/target identity readable.",
        "The websocket endpoint, profile path and browser PID never leak by default.",
    ],
)
def test_public_manifest_omits_capabilities_and_physical_profile(tmp_path, evidence_case):
    """The public view of the manifest exposes the logical identity (run,
    target, ephemeral profile) but never the control-taking levers: websocket
    endpoint, physical profile path, browser PID."""
    public = manifest_for(tmp_path).public_dict()
    #: the logical identity remains readable by the caller
    assert public["run_id"] == "R1" and public["target_id"] == "T1"
    assert public["profile"] == {"id": PROFILE_ID, "ephemeral": True}
    #: no capability that would allow attacking the browser or its profile
    #: leaks into the default output
    assert "websocket_url" not in public
    assert "profile_dir" not in public
    assert "browser_pid" not in public

    # Secondary proof: the serialized public output contract, evidence for
    # the report documenting invariant 5 (no capability/PID/path leaks).
    if evidence_case is not None:
        evidence_case.attach_json(
            "Public view of the session manifest",
            public,
            filename="public-manifest.json",
        )


def test_export_lines_quote_hostile_values_for_eval(tmp_path):
    """The `export` lines for the identity triple quote every value hostile
    to the shell: a path with a space or apostrophe survives a shlex round
    trip without injection or truncation."""
    manifest = replace(
        manifest_for(tmp_path),
        run_id="run 'quoted'",
    )
    hostile_path = tmp_path / "folder with spaces" / "manifest.json"

    lines = export_lines(manifest, hostile_path)

    #: exactly the three contract variables, in the documented order
    assert [line.split("=", 1)[0] for line in lines] == [
        "export CDPX_SESSION",
        "export CDPX_RUN_ID",
        "export CDPX_TARGET",
    ]
    #: each line yields back the exact value after shell interpretation:
    #: quoting neutralizes spaces and apostrophes instead of emitting them raw
    parsed = dict(shlex.split(line)[1].split("=", 1) for line in lines)
    assert parsed == {
        "CDPX_SESSION": str(hostile_path),
        "CDPX_RUN_ID": manifest.run_id,
        "CDPX_TARGET": manifest.target_id,
    }


def test_chrome_command_forces_ephemeral_loopback_profile(tmp_path):
    """The constructed Chrome command line enforces confinement: debug
    reachable only on loopback on a port chosen by the OS, and a dedicated
    disposable profile — never the user's personal Chrome."""
    profile = tmp_path / "profile"
    command = build_chrome_command("/usr/bin/chromium", profile)
    assert command[0] == "/usr/bin/chromium"
    #: debug is confined to loopback with an ephemeral port assigned by the
    #: OS, so it is not predictable by a third party
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=0" in command
    #: the profile used is the disposable one from the supervised session
    assert f"--user-data-dir={profile}" in command
    assert "--no-first-run" in command
    #: crash reporting is disabled: crashpad outlives a killed Chrome and
    #: would write dumps into the profile while the supervisor removes it
    assert "--disable-crash-reporter" in command
    assert "--disable-breakpad" in command


def test_chrome_sandbox_is_disabled_only_when_required(tmp_path, monkeypatch):
    """The Chrome sandbox is sacrificed only where it cannot work: a normal
    host user outside CI keeps it, while CI, root and containers disable it."""
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("CDPX_CONTAINERIZED", raising=False)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: normal user outside CI: the sandbox stays active by default
    assert "--no-sandbox" not in command

    monkeypatch.setenv("CDPX_CONTAINERIZED", "1")
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: the OCI runtime already provides the isolation boundary; Chromium's
    #: namespace sandbox is unavailable inside the hardened container
    assert "--no-sandbox" in command
    assert "--disable-dev-shm-usage" not in command

    monkeypatch.delenv("CDPX_CONTAINERIZED")
    monkeypatch.setenv("CI", "true")
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: in CI, sandbox disabled and /dev/shm bypassed (containers with
    #: reduced shared memory)
    assert "--no-sandbox" in command
    assert "--disable-dev-shm-usage" in command

    monkeypatch.setenv("CI", "false")
    monkeypatch.setattr(session_mod.os, "geteuid", lambda: 0)
    command = build_chrome_command("/usr/bin/chromium", tmp_path / "profile")
    #: root forces the sandbox to be disabled even outside CI, but without
    #: the /dev/shm accommodation specific to containers
    assert "--no-sandbox" in command
    assert "--disable-dev-shm-usage" not in command


def test_cleanup_only_removes_the_manifest_session_tree(tmp_path):
    """Session cleanup is surgical: only the tree described by the manifest
    disappears, sibling files under the same parent remain intact."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    remove_session_files(path)
    #: the session tree is removed but the sibling file survives intact,
    #: proof that removal does not propagate up to the parent
    assert not Path(manifest.session_dir).exists()
    assert keep.read_text(encoding="utf-8") == "keep"


def test_manifest_cannot_name_an_arbitrary_parent_as_its_session(tmp_path):
    """A forged manifest declaring any existing folder as session_dir is
    rejected at load time, before any cleanup could target that folder."""
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

    #: the forgery is refused: the targeted folder does not belong to the
    #: cdpx runtime
    with pytest.raises(PolicyError, match="outside the declared session directory"):
        load_manifest(path)
    #: the directory targeted by the forgery underwent no destruction
    assert project.exists()


def test_stop_refuses_to_signal_a_reused_or_forged_pid(tmp_path):
    """stop_session checks the process identity markers before any signal: a
    live PID that is not the session's Chrome (recycled or forged PID) is
    never killed."""
    manifest = manifest_for(tmp_path)
    process_start, _ = session_mod._process_identity(os.getpid())
    forged = replace(
        manifest,
        browser_pid=os.getpid(),
        browser_start_time=process_start,
    )
    path = write_manifest(forged)

    #: the current process, real but without a Chrome marker, is refused
    #: before any signal is sent
    with pytest.raises(PolicyError, match="marker"):
        stop_session(path, run_id=forged.run_id, target_id=forged.target_id, timeout=0.001)

    #: the refusal leaves the manifest in place for diagnosis
    assert path.exists()


def test_stop_respects_the_exclusive_command_lease(tmp_path):
    """stop_session goes through the same exclusive lease as other commands:
    impossible to stop a session while a command holds it."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    with SessionLease(
        path,
        run_id=manifest.run_id,
        target_id=manifest.target_id,
        require_active=False,
    ):
        #: the concurrent stop fails closed as long as the lease is held by
        #: the current command
        with pytest.raises(PolicyError, match="already in use by another command"):
            stop_session(
                path,
                run_id=manifest.run_id,
                target_id=manifest.target_id,
                timeout=0.001,
            )


def test_stop_rejects_invalid_timeout_before_writing_stop_file(tmp_path):
    """A non-finite timeout is rejected by stop_session before any side
    effect: no stop order is deposited into the session."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)

    #: parameter validation precedes any write into the session
    with pytest.raises(PolicyError, match="finite and strictly positive value required"):
        stop_session(
            path,
            run_id=manifest.run_id,
            target_id=manifest.target_id,
            timeout=float("nan"),
        )

    #: no stop file was deposited despite the refused call
    assert not (Path(manifest.session_dir) / session_mod.STOP_NAME).exists()


def test_start_session_bootstraps_and_returns_supervised_manifest(tmp_path, monkeypatch):
    """start_session delegates launching to a detached supervisor via a
    private bootstrap file, then returns the manifest that supervisor wrote,
    with the requested timeout propagated as-is."""
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
            browser_kind=data["browser_kind"],
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
            ignore_tls_errors=data["ignore_tls_errors"],
            trust_ca_dir=data["trust_ca_dir"],
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

    #: the manifest returned to the caller is the one produced by the
    #: supervisor, with the target it assigned, at the session's canonical
    #: path
    assert manifest.target_id == "TARGET"
    assert path == tmp_path / SESSION_ID / "manifest.json"
    #: the supervisor is launched as a cdpx module in its own process
    #: session, a survival condition after the parent dies
    assert launched[0][0][:4] == [session_mod.sys.executable, "-m", "cdpx.session", "_supervise"]
    assert launched[0][1]["start_new_session"] is True
    bootstrap = json.loads(Path(launched[0][0][4]).read_text(encoding="utf-8"))
    #: the requested timeout arrives intact in the supervisor's bootstrap
    assert bootstrap["startup_timeout"] == 1.0


def test_start_session_fails_closed_on_bootstrap_error_and_timeout(tmp_path, monkeypatch):
    """A failure written by the supervisor during bootstrap propagates as-is
    to the caller, the supervisor is aborted and the error file consumed; a
    zero TTL is meanwhile rejected before any launch."""
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
    #: the message deposited by the supervisor is relayed to the caller, who
    #: thus knows why startup failed
    with pytest.raises(PolicyError, match="synthetic bootstrap failure"):
        start_session(
            run_id="run-error",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
            timeout=1,
        )
    #: the faulty supervisor was aborted and the error file consumed, so no
    #: zombie process and no residue on disk
    assert aborted and not (tmp_path / f"{SESSION_ID}.error").exists()

    #: a zero TTL is refused outright, without even attempting a launch
    with pytest.raises(PolicyError, match="finite and strictly positive value required"):
        start_session(
            run_id="run-timeout",
            authority="observation",
            origins="http://demo.test",
            ttl=0,
            chrome_bin="ignored",
            root=tmp_path,
        )


@pytest.mark.scenario(
    feature="state-session",
    journey="exercise-session-without-chrome",
    scenario_id="state-session.report-redacted-startup-diagnostics",
    proves=[
        "A stalled startup names both log tails and the readiness stage reached.",
        "The environment secret is redacted before it can reach the diagnostic.",
    ],
)
def test_start_session_timeout_reports_redacted_log_tails_before_cleanup(
    tmp_path,
    monkeypatch,
    evidence_case,
):
    """When the session is not ready in time, the diagnostic surfaces the
    tail of the supervisor and Chrome logs — the secret value is redacted
    there — and cleanup happens only after they are read."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")
    secret = "diagnostic-secret-value"
    monkeypatch.setenv("CI_SECRET_TOKEN", secret)

    class FakeSupervisor:
        pid = 5151

        def poll(self):
            return None

    def stalled_popen(argv, **_kwargs):
        session_dir = Path(argv[4]).parent
        (session_dir / "supervisor.log").write_text(
            f"startup_stage=wait_devtools\nAuthorization: Bearer {secret}\n",
            encoding="utf-8",
        )
        (session_dir / "chrome-stderr.log").write_text(
            f"Chrome could not start with token={secret}\n",
            encoding="utf-8",
        )
        (session_dir / "chrome-stderr.log").chmod(0o600)
        return FakeSupervisor()

    clock = iter((0.0, 0.0, 4.0))
    monkeypatch.setattr(session_mod.subprocess, "Popen", stalled_popen)
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(session_mod.time, "sleep", lambda _seconds: None)

    cleanup_observation = {}

    def abort(supervisor, session_dir):
        cleanup_observation["pid"] = supervisor.pid
        cleanup_observation["logs_present"] = (session_dir / "supervisor.log").exists() and (
            session_dir / "chrome-stderr.log"
        ).exists()
        session_mod.shutil.rmtree(session_dir)

    monkeypatch.setattr(session_mod, "_abort_supervisor", abort)

    with pytest.raises(PolicyError) as caught:
        start_session(
            run_id="run-timeout",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
            timeout=1,
        )

    message = str(caught.value)
    #: the diagnostic names the readiness failure and quotes both logs with
    #: the startup stage reached, enough to investigate without the session
    assert "browser session not ready" in message
    assert "supervisor.log" in message and "chrome-stderr.log" in message
    assert "startup_stage=wait_devtools" in message
    #: the secret value coming from the environment never reaches the
    #: message, only the redaction marker appears there
    assert secret not in message
    assert "***" in message
    #: cleanup did happen after reading the logs, then removed everything
    assert cleanup_observation == {"pid": 5151, "logs_present": True}
    assert not (tmp_path / SESSION_ID).exists()

    # Secondary proof: the error message already redacted by the session,
    # the only usable diagnostic once the private runtime has been removed.
    if evidence_case is not None:
        evidence_case.attach_text(
            "Redacted startup diagnostic (PolicyError)",
            message,
            filename="startup-timeout-diagnostic.txt",
        )


def test_startup_diagnostics_refuse_symlinked_logs(tmp_path):
    """Startup diagnostics do not follow symlinks: a log pointing outside the
    session is treated as unavailable, never read."""
    session_dir = tmp_path / "session"
    session_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.log"
    outside.write_text("must-not-be-read", encoding="utf-8")
    (session_dir / "supervisor.log").symlink_to(outside)

    diagnostics = session_mod._startup_diagnostic_tails(session_dir)

    #: the outside file's content was not exfiltrated via the symlink
    assert "must-not-be-read" not in diagnostics
    #: the hijacked log is presented as unavailable, not as an error
    assert "supervisor.log:\n<empty or unavailable>" in diagnostics


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
    """Non-finite or zero TTL and timeout are rejected before any file
    creation: the runtime stays pristine regardless of the faulty limit."""
    #: each invalid limit triggers the same explicit policy refusal
    with pytest.raises(PolicyError, match="finite and strictly positive value required"):
        start_session(
            run_id="run-invalid-limits",
            authority="observation",
            origins="http://demo.test",
            root=tmp_path,
            **overrides,
        )
    #: the refusal precedes the creation of any session file
    assert list(tmp_path.iterdir()) == []


def test_start_session_rejects_unbounded_startup_timeout(tmp_path):
    """The startup timeout is also bounded from above: a wait beyond the
    ceiling is refused before any effect on disk."""
    #: exceeding the ceiling is a policy refusal, not an infinite wait
    with pytest.raises(PolicyError, match="startup timeout out of range"):
        start_session(
            run_id="run-invalid-timeout",
            authority="observation",
            origins="http://demo.test",
            root=tmp_path,
            timeout=session_mod.MAX_STARTUP_TIMEOUT + 1,
        )
    #: nothing was created on disk before the refusal
    assert list(tmp_path.iterdir()) == []


def test_start_session_cleans_private_tree_when_supervisor_spawn_fails(tmp_path, monkeypatch):
    """If the supervisor spawn fails, start_session propagates the original
    system error but first removes the private tree already created for the
    bootstrap."""
    monkeypatch.setattr(
        session_mod.secrets,
        "token_hex",
        lambda size: SESSION_ID if size == 12 else PROFILE_ID,
    )
    monkeypatch.setattr(session_mod, "find_chrome", lambda _explicit=None: "/fake/chrome")

    def fail_popen(*_args, **_kwargs):
        raise OSError("synthetic spawn failure")

    monkeypatch.setattr(session_mod.subprocess, "Popen", fail_popen)

    #: the original system error is propagated without being swallowed or
    #: disguised
    with pytest.raises(OSError, match="synthetic spawn failure"):
        start_session(
            run_id="run-spawn-failure",
            authority="observation",
            origins="http://demo.test",
            chrome_bin="ignored",
            root=tmp_path,
        )
    #: the tree created before the spawn was entirely cleaned up
    assert not (tmp_path / SESSION_ID).exists()


@pytest.mark.scenario(
    feature="state-session",
    journey="exercise-session-without-chrome",
    scenario_id="state-session.supervise-lifecycle-without-chrome",
    proves=[
        "An invalid attestation fails the supervisor without touching the session.",
        "The supervisor writes a reloadable manifest, closes extra targets and tears down on stop.",
    ],
)
def test_supervisor_builds_manifest_closes_extra_target_and_cleans_up(tmp_path, monkeypatch):
    """The supervisor requires a valid attestation then runs the full cycle:
    manifest written and reloadable, extra targets closed, Chrome terminated
    (then killed if it resists) and session removed on SIGTERM."""
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
                "browser_kind": "chrome",
                "authority": "interaction",
                "origins": ["http://demo.test"],
                "owner_pid": None,
                "owner_start_time": None,
                "chrome_bin": "/fake/chrome",
                "startup_timeout": 60.0,
                "session_dir": str(session_dir),
                "profile_dir": str(profile_dir),
                "artifacts_dir": str(artifacts_dir),
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
                "runtime_id": "standalone",
                "ignore_tls_errors": False,
                "trust_ca_dir": None,
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

    #: an invalid attestation makes the supervisor fail without touching the
    #: bootstrap or the session directory
    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    assert bootstrap.exists() and session_dir.exists()
    result = session_mod._supervise(bootstrap, attestation)

    #: with the correct attestation, the supervised cycle finishes cleanly
    assert result == 0
    manifest = load_manifest(session_dir / "manifest.json", run_id="run-supervisor")
    #: the written manifest is reloadable for this run and points to the
    #: assigned target on the actually discovered port
    assert manifest.target_id == "ASSIGNED" and manifest.port == 9444
    #: the superfluous initial tab is closed at startup, the assigned target
    #: is closed at stop — the worker is never touched
    assert closed == ["OLD", "ASSIGNED"]
    #: Chrome receives terminate then kill when it ignores the stop deadline
    assert chrome.terminated is True and chrome.killed is True
    #: the session is removed exactly once, with no swallowed errors
    assert removed == [(session_dir, False)]
    real_rmtree(session_dir)


def test_teardown_runtime_retries_rmtree_against_dying_chrome_children(tmp_path, monkeypatch):
    """A Chrome child process (crashpad, renderer) can outlive the killed
    main process and recreate files while rmtree walks the profile: the
    supervisor retries within a bounded deadline instead of leaving the
    session directory behind forever."""
    from cdpx.sessions import supervisor as supervisor_mod

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    runtime = supervisor_mod.SupervisorRuntime(
        session_dir=session_dir,
        error_path=session_dir / "error.log",
    )
    attempts = []
    real_rmtree = supervisor_mod.shutil.rmtree

    def flaky_rmtree(path, *args, **kwargs):
        attempts.append(Path(path))
        #: the first two walks fail like an ENOTEMPTY race, the third wins
        if len(attempts) < 3:
            raise OSError(39, "Directory not empty")
        real_rmtree(path)

    monkeypatch.setattr(supervisor_mod.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(supervisor_mod.time, "sleep", lambda _s: None)

    supervisor_mod._teardown_runtime(runtime)

    #: the transient failures are absorbed and the directory ends up removed
    assert len(attempts) == 3
    assert not session_dir.exists()


def test_supervisor_rejects_invalid_bootstrap_without_writing_or_cleanup(tmp_path):
    """An unreadable bootstrap makes the supervisor fail without publishing
    an error file or destroying anything: nothing is cleaned up for an input
    that has not proven to be a cdpx session."""
    session_dir = tmp_path / SESSION_ID
    session_dir.mkdir(mode=0o700)
    bootstrap = session_dir / "bootstrap.json"
    bootstrap.write_text("not-json", encoding="utf-8")
    bootstrap.chmod(0o600)

    #: the supervisor refuses the unparsable input with a failure code
    assert session_mod._supervise(bootstrap, "0" * 64) == 1
    error = tmp_path / f"{SESSION_ID}.error"
    #: no error file is published for an unattested input
    assert not error.exists()
    #: the folder and its content remain exactly as they were before the call
    assert session_dir.exists()
    assert bootstrap.read_text(encoding="utf-8") == "not-json"


def test_supervisor_error_preserves_redacted_readiness_tails(tmp_path, monkeypatch, evidence_case):
    """When readiness fails on the supervisor side, the published error file
    keeps the cause and the log tails — the secret value is redacted there —
    then the session is entirely destroyed."""
    session_dir = tmp_path / SESSION_ID
    profile_dir = session_dir / "profile"
    artifacts_dir = session_dir / "artifacts"
    for path in (session_dir, profile_dir, artifacts_dir):
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    now = datetime.now(UTC)
    bootstrap = session_dir / "bootstrap.json"
    payload = {
        "session_id": SESSION_ID,
        "run_id": "run-readiness-error",
        "profile_id": PROFILE_ID,
        "browser_kind": "chrome",
        "authority": "observation",
        "origins": ["http://demo.test"],
        "owner_pid": None,
        "owner_start_time": None,
        "chrome_bin": "/fake/chrome",
        "startup_timeout": 60.0,
        "session_dir": str(session_dir),
        "profile_dir": str(profile_dir),
        "artifacts_dir": str(artifacts_dir),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "runtime_id": "standalone",
        "ignore_tls_errors": False,
        "trust_ca_dir": None,
    }
    bootstrap.write_text(json.dumps(payload), encoding="utf-8")
    bootstrap.chmod(0o600)
    attestation = session_mod._policy_attestation(payload)
    secret = "readiness-secret-value"
    monkeypatch.setenv("CI_SECRET_TOKEN", secret)
    monkeypatch.setattr(session_mod.signal, "signal", lambda *_args: None)

    class FakeChrome:
        pid = 6262

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    def fake_popen(*_args, **_kwargs):
        (session_dir / "chrome-stderr.log").write_text(
            f"cold start blocked token={secret}\n",
            encoding="utf-8",
        )
        return FakeChrome()

    monkeypatch.setattr(session_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        session_mod,
        "_read_devtools_port",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PolicyError("synthetic readiness timeout")),
    )

    #: the readiness failure makes the supervisor exit with an error
    assert session_mod._supervise(bootstrap, attestation) == 1

    error = tmp_path / f"{SESSION_ID}.error"
    message = error.read_text(encoding="utf-8")
    #: the original cause and excerpts from both logs survive in the error
    #: file, the only witness after the session's destruction
    assert "synthetic readiness timeout" in message
    assert "supervisor.log" in message and "chrome-stderr.log" in message
    #: the secret value is redacted before reaching the error file
    assert secret not in message and "***" in message
    #: the session folder, though, is indeed cleaned up despite the failure
    assert not session_dir.exists()

    # Secondary proof: the redacted content of the .error file, the only
    # post-mortem witness once the session is destroyed.
    if evidence_case is not None:
        evidence_case.attach_text(
            "Redacted supervisor error file",
            message,
            filename="supervisor-readiness-error.txt",
        )


def test_supervisor_arbitrary_path_never_removes_or_chmods_its_parent(tmp_path):
    """Pointing the supervisor at any arbitrary project file neither destroys
    nor re-chmods the parent folder: the failure has absolutely no side
    effect."""
    victim = tmp_path / "project"
    victim.mkdir(mode=0o755)
    keep = victim / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    arbitrary = victim / "README.md"
    arbitrary.write_text("not a bootstrap", encoding="utf-8")
    before_mode = stat.S_IMODE(victim.stat().st_mode)

    #: the supervisor refuses the arbitrary file with a plain failure code
    assert session_mod._supervise(arbitrary, "0" * 64) == 1

    #: the victim folder is intact: content preserved and permissions
    #: unchanged, no 0700 hardening applied to a foreign folder
    assert keep.read_text(encoding="utf-8") == "keep"
    assert arbitrary.exists()
    assert stat.S_IMODE(victim.stat().st_mode) == before_mode
    #: no error file is deposited next to a foreign folder
    assert not (tmp_path / "project.error").exists()


def test_single_target_enforcement_fails_closed_when_popup_cannot_close(
    tmp_path,
    monkeypatch,
):
    """If an extra popup refuses to close, the single-target rule fails
    closed instead of letting the session continue with two pages."""
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

    #: the refusal to close becomes an explicit policy error, not a silence
    #: that would leave an unsupervised page open
    with pytest.raises(PolicyError, match="closing.*failed"):
        session_mod._enforce_single_page_target(manifest)


def test_single_target_enforcement_waits_for_async_close(tmp_path, monkeypatch):
    """Closing a popup is asynchronous on the Chrome side: enforcement waits,
    in a bounded way, for the target list to converge instead of re-closing
    in a loop or failing too early."""
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
    discoveries = iter(([assigned, popup], [assigned, popup], [assigned], [assigned]))
    monkeypatch.setattr(
        session_mod.discovery,
        "list_targets",
        lambda *_args: next(discoveries),
    )
    closed: list[str] = []
    monkeypatch.setattr(
        session_mod.discovery,
        "close_tab",
        lambda _host, _port, target_id: closed.append(target_id),
    )
    monkeypatch.setattr(session_mod.time, "sleep", lambda _seconds: None)

    session_mod._enforce_single_page_target(manifest, close_timeout=0.1)

    #: only one close order is issued despite several readings where the
    #: popup still lingers: waiting replaces re-issuing
    assert closed == ["POPUP"]


def test_exact_target_attestation_rejects_extra_page(tmp_path, monkeypatch):
    """Strict target attestation fails as soon as an extra page coexists with
    the assigned target: the session never works in a shared browser."""
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

    #: the presence of a second page violates the target's exclusivity
    #: contract and invalidates the attestation
    with pytest.raises(PolicyError, match="exactly one page target"):
        session_mod._assert_exact_target(manifest)


def test_session_status_activity_runtime_root_and_chrome_discovery(tmp_path, monkeypatch):
    """session_status reflects the real state of the processes and
    assert_session_active checks every link of the chain (bound port,
    markers, PID identity, expiration) failing closed on any drift;
    runtime_root and find_chrome complete local discovery."""
    manifest = manifest_for(tmp_path)
    path = write_manifest(manifest)
    status = session_status(path, run_id=manifest.run_id, target_id=manifest.target_id)
    #: on dead PIDs, the status reports browser and supervisor stopped
    #: without raising an error
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
    #: the coherent session (live PIDs, markers, bound port, not expired) is
    #: attested active without error
    assert_session_active(active)
    active_port = Path(active.profile_dir) / "DevToolsActivePort"
    active_port.write_text("1\n/devtools/browser/id\n", encoding="utf-8")
    #: a divergent DevToolsActivePort proves that the profile is no longer
    #: served by the manifest's port
    with pytest.raises(PolicyError, match="not bound to the assigned port"):
        assert_session_active(active)
    active_port.write_text(f"{active.port}\n/devtools/browser/id\n", encoding="utf-8")
    #: a different start_time betrays a PID recycled by another process
    with pytest.raises(PolicyError, match="reused"):
        assert_session_active(replace(active, browser_start_time="stale-start"))
    #: a moved profile no longer carries the expected user-data-dir marker
    with pytest.raises(PolicyError, match="marker"):
        assert_session_active(
            replace(active, profile_dir=str(Path(active.session_dir) / "other-profile"))
        )
    #: changing the authority breaks the supervisor's attested markers
    with pytest.raises(PolicyError, match="supervisor.*marker"):
        assert_session_active(replace(active, authority="privileged"))
    #: an unreadable expiration is a refusal, never an eternal session
    with pytest.raises(PolicyError, match="expires_at"):
        assert_session_active(replace(active, expires_at="invalid"))
    #: an expired session is refused even if all processes are running
    with pytest.raises(PolicyError, match="expired"):
        assert_session_active(
            replace(active, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
        )
    #: the disappearance of either the browser or the supervisor is enough
    #: to invalidate the session
    with pytest.raises(PolicyError, match="browser"):
        assert_session_active(replace(active, browser_pid=999_999))
    with pytest.raises(PolicyError, match="supervisor"):
        assert_session_active(replace(active, supervisor_pid=999_998))

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    #: the runtime root follows XDG_RUNTIME_DIR, isolated per user
    assert runtime_root() == runtime / "cdpx"
    executable = tmp_path / "chromium"
    executable.write_text("binary", encoding="utf-8")
    #: an existing explicit binary is accepted as-is, a missing path is a
    #: policy error, not a silent fallback
    assert find_chrome(str(executable)) == str(executable)
    with pytest.raises(PolicyError, match="not found"):
        find_chrome(str(tmp_path / "missing"))


def test_chrome_command_ignore_tls_errors_is_opt_in_and_keeps_about_blank_last(tmp_path):
    """The certificate-error bypass is off by default and, when enabled, adds
    exactly the documented flag while keeping about:blank as the final
    argument so the launch target is never displaced."""
    profile = tmp_path / "profile"
    #: without the option the disposable Chrome keeps validating certificates
    default = build_chrome_command("/usr/bin/chromium", profile)
    assert "--ignore-certificate-errors" not in default
    assert default[-1] == "about:blank"
    #: opting in adds the bypass but about:blank stays the last argument
    with_bypass = build_chrome_command("/usr/bin/chromium", profile, ignore_tls_errors=True)
    assert "--ignore-certificate-errors" in with_bypass
    assert with_bypass[-1] == "about:blank"


def test_ignore_tls_errors_changes_the_policy_attestation(tmp_path):
    """The TLS-bypass flag is an attested policy field: two otherwise identical
    manifests attest differently, so the option cannot be flipped after
    startup without breaking the attestation."""
    base = manifest_for(tmp_path)
    flipped = replace(base, ignore_tls_errors=True)
    #: the attested digest depends on the option, closing a downgrade path
    assert session_mod._policy_attestation(base) != session_mod._policy_attestation(flipped)


@pytest.mark.scenario(
    feature="state-session",
    journey="exercise-session-without-chrome",
    scenario_id="state-session.record-tls-bypass-option",
    proves=[
        "The TLS-bypass option is recorded in the manifest and its public view.",
        "The public view never leaks the raw trust store path.",
    ],
)
def test_mock_session_records_ignore_tls_errors_and_public_view(tmp_path):
    """The mock backend accepts the TLS options as recorded no-ops: the
    manifest carries ignore_tls_errors, the public view exposes it together
    with a boolean trust_ca marker, never the raw path."""
    manifest, path = start_session(
        run_id="mock-tls",
        authority="observation",
        origins="http://*.test",
        browser_kind="mock",
        owner_pid=os.getpid(),
        root=tmp_path,
        timeout=10,
        ignore_tls_errors=True,
    )
    try:
        #: the option is persisted in the attested manifest
        assert manifest.ignore_tls_errors is True
        assert manifest.trust_ca_dir is None
        public = manifest.public_dict()
        #: the public view surfaces the boolean options only
        assert public["ignore_tls_errors"] is True
        assert public["trust_ca"] is False
        assert "trust_ca_dir" not in public
    finally:
        stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)


def _valid_bootstrap_payload(session_dir: Path, **overrides: object) -> dict:
    now = datetime.now(UTC)
    payload = {
        "session_id": session_dir.name,
        "run_id": "run-bootstrap",
        "profile_id": PROFILE_ID,
        "browser_kind": "chrome",
        "authority": "observation",
        "origins": ["http://demo.test"],
        "owner_pid": None,
        "owner_start_time": None,
        "chrome_bin": "/fake/chrome",
        "startup_timeout": 60.0,
        "session_dir": str(session_dir),
        "profile_dir": str(session_dir / "profile"),
        "artifacts_dir": str(session_dir / "artifacts"),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "runtime_id": "standalone",
        "ignore_tls_errors": False,
        "trust_ca_dir": None,
    }
    drop = overrides.pop("_drop", None)
    payload.update(overrides)
    if drop is not None:
        payload.pop(drop)
    return payload


def _write_bootstrap(tmp_path: Path, **overrides: object) -> Path:
    session_dir = tmp_path / SESSION_ID
    for sub in (session_dir, session_dir / "profile", session_dir / "artifacts"):
        sub.mkdir(parents=True, exist_ok=True)
        sub.chmod(0o700)
    bootstrap = session_dir / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(_valid_bootstrap_payload(session_dir, **overrides)),
        encoding="utf-8",
    )
    bootstrap.chmod(0o600)
    return bootstrap


def test_bootstrap_accepts_valid_tls_options(tmp_path):
    """A well-formed bootstrap carrying the two TLS fields is accepted and its
    values reach the validated SupervisorBootstrap."""
    ca_dir = tmp_path / "ca"
    ca_dir.mkdir()
    (ca_dir / "root.pem").write_text("cert", encoding="utf-8")
    bootstrap = _write_bootstrap(
        tmp_path,
        ignore_tls_errors=True,
        trust_ca_dir=str(ca_dir),
    )
    data = session_mod._read_bootstrap(bootstrap)
    #: the validated bootstrap threads both options through unchanged
    assert data.ignore_tls_errors is True
    assert data.trust_ca_dir == str(ca_dir)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"ignore_tls_errors": "yes"}, "ignore_tls_errors"),
        ({"_drop": "ignore_tls_errors"}, "strict fields"),
        ({"_drop": "trust_ca_dir"}, "strict fields"),
        ({"trust_ca_dir": "relative/ca"}, "trust_ca_dir"),
    ],
)
def test_bootstrap_fails_closed_on_malformed_tls_fields(tmp_path, overrides, message):
    """The supervisor bootstrap rejects a non-boolean flag, a missing new key
    (strict field set) or a non-absolute trust path before any launch."""
    bootstrap = _write_bootstrap(tmp_path, **overrides)
    with pytest.raises(PolicyError, match=message):
        session_mod._read_bootstrap(bootstrap)


def test_bootstrap_rejects_trust_ca_dir_that_is_not_a_directory(tmp_path):
    """An absolute but non-existent trust path fails closed: the supervisor
    never trusts a directory it cannot see."""
    bootstrap = _write_bootstrap(tmp_path, trust_ca_dir=str(tmp_path / "absent-ca"))
    with pytest.raises(PolicyError, match="trust_ca_dir"):
        session_mod._read_bootstrap(bootstrap)


@pytest.mark.parametrize("missing", ["empty", "absent"])
def test_start_session_rejects_unusable_trust_dir_before_any_file(tmp_path, missing):
    """start_session vets the trust directory before creating a session: an
    empty directory or a missing path is a policy refusal naming the
    environment variable, and nothing is written under the runtime root."""
    root = tmp_path / "root"
    root.mkdir()
    if missing == "empty":
        trust_dir = tmp_path / "empty-ca"
        trust_dir.mkdir()
    else:
        trust_dir = tmp_path / "absent-ca"
    #: the misconfigured trust store is refused, naming CDPX_TRUST_CA_DIR
    with pytest.raises(PolicyError, match="CDPX_TRUST_CA_DIR"):
        start_session(
            run_id="run-trust",
            authority="observation",
            origins="http://demo.test",
            browser_kind="mock",
            root=root,
            timeout=10,
            trust_ca_dir=str(trust_dir),
        )
    #: the refusal precedes any session file creation
    assert list(root.iterdir()) == []


def test_devtools_port_and_discovery_readiness_are_bounded(tmp_path, monkeypatch):
    """Reading DevToolsActivePort and waiting for discovery are bounded:
    immediate success when the port is valid, a clean timeout otherwise, and
    an early stop as soon as the Chrome process is already dead."""
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
    #: a valid port file is read on the very first pass, without waiting
    assert session_mod._read_devtools_port(profile, Running(), timeout=1) == 9555

    (profile / "DevToolsActivePort").write_text("invalid\n", encoding="utf-8")
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(session_mod.time, "sleep", lambda _delay: None)
    #: invalid content exhausts the timeout without ever returning a port
    with pytest.raises(PolicyError, match="not found"):
        session_mod._read_devtools_port(profile, Running(), timeout=0.5)
    ticks = iter((0.0, 0.0))
    #: an already-terminated Chrome short-circuits the wait instead of
    #: exhausting it
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
    #: discovery ready (HTTP 200 on the expected loopback endpoint, checked
    #: by the fake opener) returns without error
    session_mod._wait_discovery(9555, Running(), timeout=1)

    ticks = iter((0.0, 0.0))
    monkeypatch.setattr(session_mod.time, "monotonic", lambda: next(ticks))
    #: a process dead during the discovery wait is an immediate failure
    with pytest.raises(PolicyError, match="discovery"):
        session_mod._wait_discovery(9555, Stopped(), timeout=1)
