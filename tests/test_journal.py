from __future__ import annotations

import json
import os
import stat

import pytest

from cdpx.client import CDPClient
from cdpx.journal import JournalError, append_event, materialize_action, serialize_action
from cdpx.primitives import advanced
from cdpx.security import RedactionContext


def test_type_literal_is_redacted_and_not_replayable():
    context = RedactionContext.from_secrets(["super-secret"])
    stored, replayable = serialize_action(
        ["type", "#password", "super-secret", "--clear"],
        context=context,
    )
    assert replayable is False
    assert stored == {
        "verb": "type",
        "selector": "#password",
        "input": {"redacted": True},
        "clear": True,
    }
    assert "super-secret" not in json.dumps(stored)
    with pytest.raises(JournalError, match="non rejouable"):
        materialize_action(stored)


def test_secret_env_reference_is_replayable_without_serializing_value(monkeypatch):
    monkeypatch.setenv("CHECKOUT_PASSWORD", "env-secret")
    stored, replayable = serialize_action(["type", "#password", "@env:CHECKOUT_PASSWORD"])
    assert replayable is True
    assert stored["input"] == {"secret_ref": "CHECKOUT_PASSWORD", "source": "env"}
    assert "env-secret" not in json.dumps(stored)
    assert materialize_action(stored) == ["type", "#password", "env-secret"]


def test_missing_secret_ref_fails_before_action(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    stored, _ = serialize_action(["type", "#password", "@env:MISSING_SECRET"])
    with pytest.raises(JournalError, match="MISSING_SECRET"):
        materialize_action(stored)


def test_eval_is_redacted_and_non_replayable():
    stored, replayable = serialize_action(["eval", "document.cookie"])
    assert replayable is False
    assert stored["verb"] == "eval"
    assert stored["expression"] == "***"
    assert len(stored["sha256"]) == 64
    assert "document.cookie" not in json.dumps(stored)


def test_any_redacted_action_is_stored_safely_and_marked_non_replayable():
    stored, replayable = serialize_action(
        ["goto", "https://user:pass@example.test/reset/private-path?token=value#trace"],
        context=RedactionContext.from_secrets(["private-path"]),
    )

    assert stored == ["goto", "https://example.test/reset/***?token=***"]
    assert replayable is False

    unchanged, replayable = serialize_action(["click", "#submit"])
    assert unchanged == ["click", "#submit"]
    assert replayable is True


def test_v1_sensitive_actions_are_rejected_in_team_mode():
    with pytest.raises(JournalError, match="v1 sensible"):
        materialize_action(["type", "#password", "raw"], team_mode=True)
    with pytest.raises(JournalError, match="v1 sensible"):
        materialize_action(["eval", "1 + 1"], team_mode=True)
    assert materialize_action(["click", "#go"], team_mode=True) == ["click", "#go"]


def test_secure_append_permissions_are_enforced(tmp_path):
    path = tmp_path / "private" / "record.ndjson"
    append_event(path, {"schema": "cdpx.record/v2", "ok": True})
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert os.linesep not in path.read_text(encoding="utf-8").rstrip("\n")


def test_secure_append_refuses_a_symbolic_journal(tmp_path):
    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_text("preserve", encoding="utf-8")
    link = tmp_path / "record.ndjson"
    link.symlink_to(sensitive)

    with pytest.raises(JournalError, match="symbolique"):
        append_event(link, {"ok": True})

    assert sensitive.read_text(encoding="utf-8") == "preserve"


def test_record_v2_executes_secret_but_never_persists_it(mock, tmp_path, monkeypatch):
    path = tmp_path / "record.ndjson"
    seen = []

    def run_action(_client, action, timeout=30):
        seen.append(action)
        return {"typed": action[2], "selector": action[1]}

    monkeypatch.setenv("CHECKOUT_PASSWORD", "runtime-canary-secret")
    monkeypatch.setattr(advanced.actions, "run_action", run_action)
    target = mock._public_target(next(iter(mock.targets)))
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = advanced.record(
            client,
            str(path),
            ["type", "#password", "@env:CHECKOUT_PASSWORD"],
            run_id="R1",
        )
    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert seen == [["type", "#password", "runtime-canary-secret"]]
    assert "runtime-canary-secret" not in raw
    assert event["schema"] == "cdpx.record/v2"
    assert event["action"]["input"]["secret_ref"] == "CHECKOUT_PASSWORD"
    assert event["result"]["typed"] == "***"
    assert result["replayable"] is True


@pytest.mark.parametrize("fails", [False, True])
def test_record_eval_never_persists_result_or_error(mock, tmp_path, monkeypatch, fails):
    path = tmp_path / "eval.ndjson"
    canary = "unknown-eval-result-canary-7734"

    def run_action(_client, _action, timeout=30):
        if fails:
            raise ValueError(f"eval failed with {canary}")
        return {"value": canary}

    monkeypatch.setattr(advanced.actions, "run_action", run_action)
    target = mock._public_target(next(iter(mock.targets)))
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        if fails:
            with pytest.raises(ValueError, match="eval failed"):
                advanced.record(client, str(path), ["eval", "window.readSecret()"])
        else:
            advanced.record(client, str(path), ["eval", "window.readSecret()"])

    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert canary not in raw
    if fails:
        assert event["result"] == {"error": "***", "error_masked": True}
    else:
        assert event["result"] == {"value": "***", "value_masked": True}


def test_replay_v2_resolves_all_refs_before_first_action(mock, tmp_path, monkeypatch):
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"schema":"cdpx.record/v2","action":{"verb":"type",'
        '"selector":"#password","input":{"source":"env",'
        '"secret_ref":"MISSING"},"clear":false},"replayable":true,"ok":true}\n'
        '{"action":["click","#go"],"ok":true}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING", raising=False)
    target = mock._public_target(next(iter(mock.targets)))
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = advanced.replay(client, str(path), team_mode=True)
    assert result["played"] == 0 and result["ok"] is False
    assert "MISSING" in result["divergence"]
    assert mock.commands == []
