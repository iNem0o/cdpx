from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cdpx import journal
from cdpx.action_model import TypeAction
from cdpx.artifacts import (
    ArtifactClassification,
    SecureArtifactWriter,
    scan_canaries,
)
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import capture, dev, net, recording, state
from cdpx.security import MASK, RedactionContext, redact_text, redact_tree

CANARY = "CDPX-CANARY-7d3df679f62b"
ORDINARY_TEXT = "contact=alice@example.test order=123456 status=ready"


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as cdp:
        yield cdp


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_private_tree(root: Path) -> None:
    assert _mode(root) == 0o700
    for path in root.rglob("*"):
        if path.is_dir():
            assert _mode(path) == 0o700, path
        elif path.is_file():
            assert _mode(path) == 0o600, path


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["Aggregated console, storage, network and error outputs ship canary-free."],
)
def test_observation_outputs_redact_url_console_storage_and_errors(
    mock,
    client,
    capsys,
    evidence_case,
):
    """All observation outputs (console, storage, network, errors) are
    purged of the canary before serialization and all the way to the
    real stdout/stderr streams, without impoverishing ordinary
    diagnostics."""
    context = RedactionContext.from_secrets([CANARY])
    mock.on_eval(
        "Object.entries(localStorage)",
        json.dumps({"session": CANARY, "diagnostic": ORDINARY_TEXT}),
    )
    mock.script_console(
        [
            {
                "type": "error",
                "args": [
                    {"value": f"failed Bearer {CANARY}"},
                    {"value": ORDINARY_TEXT},
                    {"value": f"https://user:{CANARY}@app.test/cb?code={CANARY}#trace"},
                ],
                "timestamp": 10.0,
            }
        ]
    )
    console_result = capture.console_capture(client, duration=0.01, context=context)
    storage_result = state.get_storage(client)

    navigation_url = f"http://user:{CANARY}@app.test/report?token={CANARY}#fragment"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": f"http://app.test/api/orders?access_token={CANARY}",
                        "method": "GET",
                    },
                },
            },
            {
                "method": "Network.loadingFailed",
                "params": {
                    "requestId": "R1",
                    "errorText": f"upstream rejected {CANARY}; {ORDINARY_TEXT}",
                },
            },
        ]
    )
    network_result = net.capture(client, navigation_url, settle=0.01, context=context)
    safe_error = redact_tree(
        {"error": f"request failed for {CANARY}; {ORDINARY_TEXT}"},
        context=context,
    )

    output = {
        "console": console_result,
        "storage": storage_result,
        "network": network_result,
        "error": safe_error,
    }
    serialized = json.dumps(output, ensure_ascii=False)

    #: the canary appears nowhere in the serialized aggregated output
    assert CANARY not in serialized
    #: ordinary diagnostics survive everywhere: redaction does not
    #: impoverish the observation value of the outputs
    assert ORDINARY_TEXT in console_result["entries"][0]["text"]
    assert ORDINARY_TEXT in network_result["requests"][0]["failed"]
    assert ORDINARY_TEXT in safe_error["error"]
    #: storage is masked key by key: it is a reservoir of sessions
    assert storage_result["entries"] == {"session": MASK, "diagnostic": MASK}
    #: network URLs lose credentials and tokens but stay correlatable
    assert network_result["url"] == "http://app.test/report?token=***"
    assert network_result["requests"][0]["url"] == ("http://app.test/api/orders?access_token=***")
    #: the browser received the URL intact: redaction only alters the
    #: output, never the requested action
    assert mock.commands_for("Page.navigate")[-1] == {"url": navigation_url}
    assert redact_text(ORDINARY_TEXT, context=context) == ORDINARY_TEXT

    print(serialized)
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    #: at the real stdout/stderr boundary, the canary is absent and the
    #: ordinary diagnostic is still present
    assert CANARY not in emitted.out
    assert CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.out and ORDINARY_TEXT in emitted.err

    if evidence_case is not None:
        evidence_case.attach_json(
            "Redacted aggregated observation output",
            output,
        )


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["The redact_tree boundary keeps the shareable profiler artifact canary-free."],
)
def test_profiler_redacts_token_headers_and_urls_before_artifact(
    mock,
    client,
    tmp_path,
    evidence_case,
):
    """The Symfony profiler token, discovered along the way, joins the
    shared context: URLs, headers, and SQL panels come out cleaned, and
    the written artifact stays private and canary-free."""
    # The token is not pre-registered: the profiler primitive must add it
    # to the shared context before the cross-cutting cleanup of the output.
    context = RedactionContext()
    navigation_url = f"http://app.test/report?session={CANARY}"
    profiler_link = f"http://app.test/_profiler/{CANARY}?transport={CANARY}#trace"
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": navigation_url,
                        "status": 200,
                        "headers": {
                            "X-Debug-Token-Link": profiler_link,
                            "Authorization": f"Bearer {CANARY}",
                            "Set-Cookie": f"sid={CANARY}; HttpOnly",
                            "X-Diagnostic": f"{ORDINARY_TEXT}; secret={CANARY}",
                        },
                    },
                },
            }
        ]
    )
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps(
            [
                {
                    "panel": "db",
                    "status": 200,
                    "html": (
                        '<div class="metric"><span class="value">1</span>'
                        '<span class="label">Database queries</span></div>'
                        "<table><tr><th>Info</th><th>Time</th></tr>"
                        f"<tr><td>SELECT '{CANARY}' /* {ORDINARY_TEXT} */</td>"
                        "<td>1 ms</td></tr></table>"
                    ),
                }
            ]
        ),
    )
    mock.on_eval("window.location.href", navigation_url)

    primitive_result = dev.profiler(
        client,
        navigation_url,
        panels=["db"],
        settle=0.01,
        context=OrchestrationContext.from_origins("http://*.test", redaction=context),
    )
    # Same boundary as the CLI before stdout or artifact persistence.
    result = redact_tree(primitive_result, context=context)
    serialized = json.dumps(result, ensure_ascii=False)

    #: the primitive's raw output still contains the token: it is indeed
    #: the redact_tree boundary that protects, not upstream luck
    assert CANARY in json.dumps(primitive_result["panels"], ensure_ascii=False)
    #: past the boundary, no more canary in the serialized output
    assert CANARY not in serialized
    #: navigation and profiler URLs lose the token discovered along the way
    assert result["url"] == "http://app.test/report?session=***"
    assert result["profiler_url"] == "http://app.test/_profiler/***?transport=***"
    #: auth headers are masked, the ordinary diagnostic survives in the
    #: free header as well as in the panel's SQL
    assert result["response_headers"]["authorization"] == MASK
    assert result["response_headers"]["set-cookie"] == MASK
    assert ORDINARY_TEXT in result["response_headers"]["x-diagnostic"]
    assert ORDINARY_TEXT in result["panels"]["db"]["list"][0]["sql"]
    #: the token is never returned, only its presence is attested
    assert "token" not in result and result["token_present"] is True
    #: on the protocol side the real token did circulate: redaction did not
    #: degrade the profiler's actual interrogation
    assert CANARY in mock.commands_for("Runtime.evaluate")[-1]["expression"]
    assert mock.commands_for("Page.navigate")[-1] == {"url": navigation_url}

    writer = SecureArtifactWriter(tmp_path / "private", "profiler-run")
    writer.write_json(
        "profiler.json",
        result,
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    shareable = writer.build_shareable(tmp_path / "shareable")
    #: neither the artifact nor its shareable copy contains the canary, and
    #: their directory trees stay private (0700/0600)
    run_dir_scan = scan_canaries(writer.run_dir, [CANARY])
    shareable_scan = scan_canaries(shareable, [CANARY])
    assert run_dir_scan == []
    assert shareable_scan == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)

    if evidence_case is not None:
        evidence_case.attach_file(
            shareable / "profiler.json",
            "Redacted shareable profiler artifact",
            "profiler",
        )
        evidence_case.attach_json(
            "Canary scan of profiler artifacts",
            {"run_dir": run_dir_scan, "shareable": shareable_scan},
        )


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["The replayable journal stores only the secret_ref, never the secret value."],
)
def test_secret_ref_record_stdout_journal_and_artifacts_are_canary_free(
    mock,
    client,
    tmp_path,
    monkeypatch,
    capsys,
    evidence_case,
):
    """A secret injected via @env: reaches the browser but never appears
    anywhere else: the journal stores only the reference, and artifacts,
    stdout, and stderr stay free of the secret value."""
    monkeypatch.setenv("CHECKOUT_PASSWORD", CANARY)
    mock.on_eval(
        "__cdpx_actionability focus",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 2, "width": 30, "height": 12},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    context = RedactionContext()
    record_path = tmp_path / "journal" / "record.ndjson"

    result = recording.record(
        client,
        str(record_path),
        TypeAction("#checkout-password", "@env:CHECKOUT_PASSWORD"),
        run_id="security-run",
        context=OrchestrationContext.from_origins("http://*.test", redaction=context),
    )
    safe_error = redact_tree(
        {"error": f"submission failed for {CANARY}; {ORDINARY_TEXT}"},
        context=context,
    )

    #: the secret value was indeed typed into the page: protection does not
    #: sacrifice input functionality
    assert mock.commands_for("Input.insertText") == [{"text": CANARY}]
    journal_text = record_path.read_text(encoding="utf-8")
    #: the replayable journal contains only the environment reference,
    #: never the secret value itself
    assert CANARY not in journal_text
    event = json.loads(journal_text)
    assert event["action"]["input"] == {
        "secret_ref": "CHECKOUT_PASSWORD",
        "source": "env",
    }
    #: the journal is created private from the moment it is written,
    #: with no after-the-fact tightening
    assert _mode(record_path.parent) == 0o700
    assert _mode(record_path) == 0o600

    writer = SecureArtifactWriter(tmp_path / "artifacts", "security-run")
    writer.write_json(
        "outputs/result.json",
        {"stdout": result, "stderr": safe_error, "ordinary": ORDINARY_TEXT},
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    writer.register_file(
        record_path,
        name="journal/record.ndjson",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    shareable = writer.build_shareable(tmp_path / "staging")

    #: no canary in the artifacts nor the shareable copy, private
    #: directory trees end to end
    assert scan_canaries(writer.run_dir, [CANARY]) == []
    assert scan_canaries(shareable, [CANARY]) == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)
    #: the non-uploadable internal journal is excluded from the shareable copy
    assert not (shareable / "journal" / "record.ndjson").exists()

    if evidence_case is not None:
        evidence_case.attach_file(
            record_path,
            "NDJSON record journal (secret_ref only)",
        )
        evidence_case.attach_json(
            "Shareable directory tree (internal journal excluded)",
            {
                "files": sorted(
                    path.relative_to(shareable).as_posix()
                    for path in shareable.rglob("*")
                    if path.is_file()
                )
            },
        )

    print(json.dumps(result, ensure_ascii=False))
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    #: at the stdout/stderr boundaries, the canary is absent but the
    #: ordinary diagnostic still gets through
    assert CANARY not in emitted.out and CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.err


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["A missing secret_ref fails the replay closed before any CDP command is emitted."],
)
def test_missing_secret_ref_is_rejected_before_any_cdp_effect(
    mock,
    client,
    tmp_path,
    monkeypatch,
    evidence_case,
):
    """A replay whose secret reference no longer exists in the
    environment stops flat before the first action: explicit divergence
    and zero CDP effect."""
    monkeypatch.delenv("MISSING_CHECKOUT_PASSWORD", raising=False)
    record_path = tmp_path / "journal" / "record.ndjson"
    journal.append_event(
        record_path,
        {
            "schema": journal.SCHEMA,
            "action": {
                "verb": "type",
                "selector": "#checkout-password",
                "input": {
                    "source": "env",
                    "secret_ref": "MISSING_CHECKOUT_PASSWORD",
                },
                "clear": False,
            },
            "replayable": True,
            "ok": True,
        },
    )
    journal.append_event(
        record_path,
        {
            "schema": journal.SCHEMA,
            "action": ["click", "#submit"],
            "replayable": True,
            "ok": True,
        },
    )

    result = recording.replay(
        client,
        str(record_path),
        context=OrchestrationContext.from_origins("http://*.test"),
    )

    #: the replay fails without playing any action and the divergence names
    #: the missing reference for an immediate fix
    assert result["ok"] is False
    assert result["played"] == 0
    assert "MISSING_CHECKOUT_PASSWORD" in result["divergence"]
    #: no command reached the browser: the refusal precedes any effect
    assert mock.commands == []

    if evidence_case is not None:
        evidence_case.attach_json(
            "Fail-closed replay divergence",
            {"replay": result, "cdp_commands": mock.commands},
        )
