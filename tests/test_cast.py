"""Native cast producer (pty -> asciicast v2) — blocking gate, never an exception."""

import json
import sys

from cdpx.proofing import cast
from cdpx.security.redaction import RedactionContext


def _events(cast_path):
    lines = cast_path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    return header, [json.loads(line) for line in lines[1:]]


def test_record_cast_produces_valid_asciicast_v2(tmp_path, evidence_case):
    """The native recorder produces a .cast v2 readable by the player, without
    an external binary: compliant header, 'o' events with increasing timestamps."""
    cast_path = tmp_path / "demo.cast"

    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "print('line one'); print('line two')"],
        cast_path,
        env={"PATH": "/usr/bin"},
    )

    #: the recording succeeds and points to the written file
    assert entry["status"] == "generated"
    assert entry["path"] == str(cast_path)
    header, events = _events(cast_path)
    #: the header follows the asciicast v2 contract expected by the xterm player
    assert header["version"] == 2
    assert header["width"] == cast.CAST_WIDTH and header["height"] == cast.CAST_HEIGHT
    #: every event is an 'o' output and time never goes backwards
    assert events and all(event[1] == "o" for event in events)
    times = [event[0] for event in events]
    assert times == sorted(times)
    assert "line one" in "".join(event[2] for event in events)

    if evidence_case is not None:
        evidence_case.attach_cast(cast_path, "asciicast v2 recording (demo)")


def test_record_cast_runs_on_a_real_pty(tmp_path):
    """The recorded command sees a real TTY: that is what makes the cast
    faithful (colors, width) unlike a pipe capture."""
    cast_path = tmp_path / "tty.cast"

    entry = cast.record_cast(
        "tty",
        [sys.executable, "-c", "import sys; print(sys.stdout.isatty())"],
        cast_path,
        env={"PATH": "/usr/bin"},
    )

    _header, events = _events(cast_path)
    #: the subprocess did run attached to a pseudo-terminal
    assert entry["status"] == "generated"
    assert "True" in "".join(event[2] for event in events)


def test_record_cast_degrades_on_failure_size_and_timeout(tmp_path, monkeypatch):
    """Any failure returns a degraded status without raising or leaving a file
    behind: the gate judges statuses, the recorder stays harmless."""
    cast_path = tmp_path / "demo.cast"

    #: a command that exits with an error produces no evidence
    entry = cast.record_cast(
        "demo", [sys.executable, "-c", "raise SystemExit(3)"], cast_path, env={}
    )
    assert entry["status"] == "unavailable"
    assert not cast_path.exists()

    monkeypatch.setattr(cast, "MAX_CAST_BYTES", 10)
    #: a cast that is too large is deleted rather than weighing down the evidence
    entry = cast.record_cast("demo", [sys.executable, "-c", "print('x' * 64)"], cast_path, env={})
    assert entry["status"] == "too-large"
    assert not cast_path.exists()
    monkeypatch.setattr(cast, "MAX_CAST_BYTES", 2 * 1024 * 1024)

    #: the timeout kills the recording and marks it unavailable
    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cast_path,
        env={},
        timeout=0.5,
    )
    assert entry["status"] == "unavailable"
    assert not cast_path.exists()


def test_record_cast_degrades_when_command_cannot_start(tmp_path):
    """A missing binary is a degraded status, not an exception."""
    entry = cast.record_cast("demo", ["/nonexistent/binary"], tmp_path / "demo.cast", env={})

    #: OSError at startup => unavailable, no file left behind
    assert entry["status"] == "unavailable"
    assert not (tmp_path / "demo.cast").exists()


def test_record_cast_redacts_and_secures_the_recording(tmp_path):
    """The .cast written to disk is redacted before any read by the
    cockpit and stays private (0600)."""
    cast_path = tmp_path / "demo.cast"
    context = RedactionContext.from_secrets(["proof-canary-42"])

    entry = cast.record_cast(
        "demo",
        [sys.executable, "-c", "print('token proof-canary-42')"],
        cast_path,
        env={},
        redaction_context=context,
    )

    assert entry["status"] == "generated"
    content = cast_path.read_text(encoding="utf-8")
    #: the canary never reaches disk, the redaction marker does
    assert "proof-canary-42" not in content
    assert "***" in content
    assert oct(cast_path.stat().st_mode & 0o777) == "0o600"


def test_collect_cast_evidence_records_every_demo_command(tmp_path, monkeypatch):
    """Collection is no longer opt-in: every command in CAST_COMMANDS produces
    an entry judged by the gate, without an environment variable."""
    monkeypatch.setattr(
        cast,
        "CAST_COMMANDS",
        (
            ("one", [sys.executable, "-c", "print('one')"]),
            ("two", [sys.executable, "-c", "print('two')"]),
        ),
    )

    entries = cast.collect_cast_evidence(tmp_path, env={})

    #: one entry per demo command, all generated
    assert [entry["id"] for entry in entries] == ["one", "two"]
    assert [entry["status"] for entry in entries] == ["generated", "generated"]
    assert (tmp_path / "one.cast").is_file() and (tmp_path / "two.cast").is_file()


def test_cast_commands_include_cli_help_and_mock_demo():
    """The embedded demos stay cheap and browser-free: the CLI help
    and the supervised mock session."""
    ids = [cast_id for cast_id, _argv in cast.CAST_COMMANDS]

    #: the contract of the recorded demos is explicit
    assert ids == ["cli-help", "mock-session-demo"]
    for _cast_id, argv in cast.CAST_COMMANDS:
        assert argv[0] == sys.executable
