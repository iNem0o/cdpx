from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cdpx import journal
from cdpx.artifacts import (
    ArtifactClassification,
    SecureArtifactWriter,
    scan_canaries,
)
from cdpx.client import CDPClient
from cdpx.primitives import advanced, capture, dev, net, state
from cdpx.security import MASK, RedactionContext, redact_text, redact_tree

CANARY = "CDPX-CANARY-7d3df679f62b"
ORDINARY_TEXT = "contact=alice@example.test order=123456 status=ready"


@pytest.fixture()
def client(mock):
    target = mock._public_target(next(iter(mock.targets)))
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


def test_observation_outputs_redact_url_console_storage_and_errors(
    mock,
    client,
    capsys,
):
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

    assert CANARY not in serialized
    assert ORDINARY_TEXT in console_result["entries"][0]["text"]
    assert ORDINARY_TEXT in network_result["requests"][0]["failed"]
    assert ORDINARY_TEXT in safe_error["error"]
    assert storage_result["entries"] == {"session": MASK, "diagnostic": MASK}
    assert network_result["url"] == "http://app.test/report?token=***"
    assert network_result["requests"][0]["url"] == ("http://app.test/api/orders?access_token=***")
    assert mock.commands_for("Page.navigate")[-1] == {"url": navigation_url}
    assert redact_text(ORDINARY_TEXT, context=context) == ORDINARY_TEXT

    print(serialized)
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    assert CANARY not in emitted.out
    assert CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.out and ORDINARY_TEXT in emitted.err


def test_profiler_redacts_token_headers_and_urls_before_artifact(
    mock,
    client,
    tmp_path,
):
    # Le token n'est pas pré-enregistré: la primitive profiler doit l'ajouter
    # au contexte partagé avant le nettoyage transversal de la sortie.
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
        context=context,
    )
    # Même frontière que le CLI avant stdout ou persistance d'un artefact.
    result = redact_tree(primitive_result, context=context)
    serialized = json.dumps(result, ensure_ascii=False)

    assert CANARY in json.dumps(primitive_result["panels"], ensure_ascii=False)
    assert CANARY not in serialized
    assert result["url"] == "http://app.test/report?session=***"
    assert result["profiler_url"] == "http://app.test/_profiler/***?transport=***"
    assert result["response_headers"]["authorization"] == MASK
    assert result["response_headers"]["set-cookie"] == MASK
    assert ORDINARY_TEXT in result["response_headers"]["x-diagnostic"]
    assert ORDINARY_TEXT in result["panels"]["db"]["list"][0]["sql"]
    assert "token" not in result and result["token_present"] is True
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
    assert scan_canaries(writer.run_dir, [CANARY]) == []
    assert scan_canaries(shareable, [CANARY]) == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)


def test_secret_ref_record_stdout_journal_and_artifacts_are_canary_free(
    mock,
    client,
    tmp_path,
    monkeypatch,
    capsys,
):
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

    result = advanced.record(
        client,
        str(record_path),
        ["type", "#checkout-password", "@env:CHECKOUT_PASSWORD"],
        run_id="security-run",
        redaction_context=context,
    )
    safe_error = redact_tree(
        {"error": f"submission failed for {CANARY}; {ORDINARY_TEXT}"},
        context=context,
    )

    assert mock.commands_for("Input.insertText") == [{"text": CANARY}]
    journal_text = record_path.read_text(encoding="utf-8")
    assert CANARY not in journal_text
    event = json.loads(journal_text)
    assert event["action"]["input"] == {
        "secret_ref": "CHECKOUT_PASSWORD",
        "source": "env",
    }
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

    assert scan_canaries(writer.run_dir, [CANARY]) == []
    assert scan_canaries(shareable, [CANARY]) == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)
    assert not (shareable / "journal" / "record.ndjson").exists()

    print(json.dumps(result, ensure_ascii=False))
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    assert CANARY not in emitted.out and CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.err


def test_missing_secret_ref_is_rejected_before_any_cdp_effect(
    mock,
    client,
    tmp_path,
    monkeypatch,
):
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

    result = advanced.replay(client, str(record_path), team_mode=True)

    assert result["ok"] is False
    assert result["played"] == 0
    assert "MISSING_CHECKOUT_PASSWORD" in result["divergence"]
    assert mock.commands == []
