from __future__ import annotations

import json
import os
import stat

import pytest

from cdpx.action_model import ClickAction, EvalAction, GotoAction, TypeAction
from cdpx.client import CDPClient
from cdpx.journal import JournalError, append_event, materialize_action, serialize_action
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import recording
from cdpx.security import RedactionContext


def orchestration(redaction: RedactionContext | None = None) -> OrchestrationContext:
    return OrchestrationContext.from_origins("http://*.test", redaction=redaction)


def test_type_literal_is_redacted_and_not_replayable():
    """A secret value typed as a literal is stored redacted and the action
    loses its replay right: the value no longer exists, replaying it
    would be a lie."""
    context = RedactionContext.from_secrets(["super-secret"])
    stored, replayable = serialize_action(
        TypeAction("#password", "super-secret", clear=True),
        context=context,
    )
    #: the stored form keeps only the action's structure,
    #: never the secret value itself
    assert replayable is False
    assert stored == {
        "verb": "type",
        "selector": "#password",
        "input": {"redacted": True},
        "clear": True,
    }
    assert "super-secret" not in json.dumps(stored)
    #: replaying an action stripped of its value would be a lie: flat refusal
    with pytest.raises(JournalError, match="not replayable"):
        materialize_action(stored)


def test_secret_env_reference_is_replayable_without_serializing_value(monkeypatch):
    """An @env: reference makes the action replayable: the journal stores
    only the variable name and the secret value is resolved at replay
    time."""
    monkeypatch.setenv("CHECKOUT_PASSWORD", "env-secret")
    stored, replayable = serialize_action(TypeAction("#password", "@env:CHECKOUT_PASSWORD"))
    #: only the reference is persisted, the value stays in the environment
    assert replayable is True
    assert stored["input"] == {"secret_ref": "CHECKOUT_PASSWORD", "source": "env"}
    assert "env-secret" not in json.dumps(stored)
    #: materialization resolves the reference at the last moment and
    #: rebuilds the complete action for execution
    assert materialize_action(stored) == TypeAction("#password", "env-secret")


def test_missing_secret_ref_fails_before_action(monkeypatch):
    """A secret reference missing from the environment fails materialization
    before any execution, naming the variable."""
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    stored, _ = serialize_action(TypeAction("#password", "@env:MISSING_SECRET"))
    #: the error cites the missing reference, immediate diagnostic without
    #: having launched a single browser action
    with pytest.raises(JournalError, match="MISSING_SECRET"):
        materialize_action(stored)


def test_eval_is_redacted_and_non_replayable():
    """An eval expression is redacted in the journal but stays correlatable
    by its SHA-256 fingerprint; the action is never replayable."""
    stored, replayable = serialize_action(EvalAction("document.cookie"))
    #: the expression disappears from the journal, only its fingerprint
    #: allows correlating it to a known execution
    assert replayable is False
    assert stored["verb"] == "eval"
    assert stored["expression"] == "***"
    assert len(stored["sha256"]) == 64
    assert "document.cookie" not in json.dumps(stored)


def test_any_redacted_action_is_stored_safely_and_marked_non_replayable():
    """Any action whose argument had to be redacted (URL credentials,
    secret path, token) loses its replay right; an untouched action keeps
    it."""
    stored, replayable = serialize_action(
        GotoAction("https://user:pass@example.test/reset/private-path?token=value#trace"),
        context=RedactionContext.from_secrets(["private-path"]),
    )

    #: the stored URL lost credentials, secret segment, and fragment:
    #: replaying it would produce a different navigation, so replay is
    #: forbidden
    assert stored == ["goto", "https://example.test/reset/***?token=***"]
    assert replayable is False

    unchanged, replayable = serialize_action(ClickAction("#submit"))
    #: an action with nothing to redact passes through untouched and stays
    #: replayable
    assert unchanged == ["click", "#submit"]
    assert replayable is True


def test_v1_sensitive_actions_are_always_rejected():
    """The v1 list format cannot distinguish a secret value from a
    reference: its sensitive actions are rejected at replay, harmless
    actions pass."""
    #: type and eval in v1 format are unrecoverable without risking a
    #: replay of a value that should have stayed secret
    with pytest.raises(JournalError, match="sensitive v1"):
        materialize_action(["type", "#password", "raw"])
    with pytest.raises(JournalError, match="sensitive v1"):
        materialize_action(["eval", "1 + 1"])
    #: a v1 action with no sensitive data stays replayable as-is
    assert materialize_action(["click", "#go"]) == ClickAction("#go")


def test_secure_append_permissions_are_enforced(tmp_path):
    """The append creates the journal's directory tree with private
    permissions and writes an ndjson event fitting on a single line."""
    path = tmp_path / "private" / "record.ndjson"
    append_event(path, {"schema": "cdpx.record/v2", "ok": True})
    #: folder and journal are born unreadable for other accounts
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    #: the event fits on a single line, ndjson contract respected
    assert os.linesep not in path.read_text(encoding="utf-8").rstrip("\n")


def test_secure_append_refuses_a_symbolic_journal(tmp_path):
    """A journal that is actually a symbolic link is refused: the append
    cannot be used to overwrite an arbitrary file on the system."""
    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_text("preserve", encoding="utf-8")
    link = tmp_path / "record.ndjson"
    link.symlink_to(sensitive)

    #: the link is detected and refused before any write
    with pytest.raises(JournalError, match="symbolic"):
        append_event(link, {"ok": True})

    #: the file targeted by the link was not touched
    assert sensitive.read_text(encoding="utf-8") == "preserve"


def test_record_v2_executes_secret_but_never_persists_it(
    mock, tmp_path, monkeypatch, evidence_case
):
    """v2 recording executes the action with the real secret value
    resolved from the environment, but persists only the reference: the
    journal stays replayable without ever containing the value."""
    path = tmp_path / "record.ndjson"
    seen = []

    def run_action(_client, action, timeout=30):
        seen.append(action)
        assert isinstance(action, TypeAction)
        return {"typed": action.text, "selector": action.selector}

    monkeypatch.setenv("CHECKOUT_PASSWORD", "runtime-canary-secret")
    monkeypatch.setattr(recording.actions, "run_action", run_action)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = recording.record(
            client,
            str(path),
            TypeAction("#password", "@env:CHECKOUT_PASSWORD"),
            run_id="R1",
            context=orchestration(),
        )
    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    #: the action actually executed did receive the resolved value: redaction
    #: does not degrade execution
    assert seen == [TypeAction("#password", "runtime-canary-secret")]
    #: on disk, only the reference remains and the result is masked
    assert "runtime-canary-secret" not in raw
    assert event["schema"] == "cdpx.record/v2"
    assert event["action"]["input"]["secret_ref"] == "CHECKOUT_PASSWORD"
    assert event["result"]["typed"] == "***"
    #: thanks to the reference, the recording stays replayable regardless
    assert result["replayable"] is True

    if evidence_case is not None:
        # Direct proof: the persisted journal carries only the @env reference.
        evidence_case.attach_file(
            path,
            "Journal record.ndjson (@env reference, no secret value)",
            excerpt=raw,
        )


@pytest.mark.parametrize("fails", [False, True])
def test_record_eval_never_persists_result_or_error(
    mock, tmp_path, monkeypatch, fails, evidence_case
):
    """Whether an eval succeeds or fails, neither its return value nor its
    error message reach the journal: only masked markers and an explicit
    flag are persisted."""
    path = tmp_path / "eval.ndjson"
    canary = "unknown-eval-result-canary-7734"

    def run_action(_client, _action, timeout=30):
        if fails:
            raise ValueError(f"eval failed with {canary}")
        return {"value": canary}

    monkeypatch.setattr(recording.actions, "run_action", run_action)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        if fails:
            #: the eval's failure propagates to the caller, the journal does
            #: not swallow it
            with pytest.raises(ValueError, match="eval failed"):
                recording.record(
                    client,
                    str(path),
                    EvalAction("window.readSecret()"),
                    context=orchestration(),
                )
        else:
            recording.record(
                client,
                str(path),
                EvalAction("window.readSecret()"),
                context=orchestration(),
            )

    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    #: the canary from the eval (result as well as error) never reaches
    #: disk
    assert canary not in raw
    #: the journal keeps only a masked marker and an honest flag, on the
    #: success side as well as the failure side
    if fails:
        assert event["result"] == {"error": "***", "error_masked": True}
    else:
        assert event["result"] == {"value": "***", "value_masked": True}

    if evidence_case is not None:
        # Same masking contract on both branches (success/failure): the
        # eval.ndjson journal shows only markers, never the canary.
        branch = "failure" if fails else "success"
        evidence_case.attach_file(
            path,
            f"Journal eval.ndjson (masked result, {branch} branch)",
            excerpt=raw,
        )


def test_replay_v2_resolves_all_refs_before_first_action(
    mock, tmp_path, monkeypatch, evidence_case
):
    """Replay validates all secret references before the first action: a
    missing reference stops everything, without the slightest side effect
    on the browser side."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"schema":"cdpx.record/v2","action":{"verb":"type",'
        '"selector":"#password","input":{"source":"env",'
        '"secret_ref":"MISSING"},"clear":false},"replayable":true,"ok":true}\n'
        '{"action":["click","#go"],"ok":true}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING", raising=False)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = recording.replay(client, str(path), context=orchestration())
    #: the missing reference interrupts replay before the first action
    #: and the divergence names it for the diagnostic
    assert result["played"] == 0 and result["ok"] is False
    assert "MISSING" in result["divergence"]
    #: no CDP order was emitted: the failure precedes any side effect
    assert mock.commands == []

    if evidence_case is not None:
        # Documents replay's fail-fast: played=0 and a divergence naming the
        # missing reference, without the slightest browser side effect.
        evidence_case.attach_json("Fail-fast replay result (played=0)", result)
