"""Deterministic recording and replay of composed actions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cdpx import journal
from cdpx.action_model import BrowserAction, EvalAction, GotoAction, TypeAction
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.orchestration import OrchestrationContext
from cdpx.policy import assert_url_allowed
from cdpx.primitives import actions, inputs, js
from cdpx.security import MASK, RedactionContext, redact_tree

ACTION_ERRORS = (
    ValueError,
    TimeoutError,
    CDPError,
    CDPTimeout,
    js.JSException,
    inputs.ElementNotFound,
)


@dataclass(frozen=True)
class ReplayEntry:
    event: dict[str, Any]
    action: BrowserAction


@dataclass(frozen=True)
class ReplayLoadFailure:
    events: int
    divergence: str


def record(
    client: CDPClient,
    path: str,
    action: BrowserAction,
    *,
    run_id: str | None = None,
    context: OrchestrationContext,
) -> dict[str, Any]:
    """Executes the action then journals it (including the result) in NDJSON."""
    redaction = context.redaction
    stored_action, replayable = journal.serialize_action(action, context=redaction)
    execution_action = action
    if isinstance(stored_action, dict) and stored_action.get("verb") == "type":
        input_spec = stored_action.get("input", {})
        if isinstance(input_spec, dict) and input_spec.get("secret_ref"):
            execution_action = journal.materialize_action(stored_action)
            if not isinstance(execution_action, TypeAction):
                raise journal.JournalError("invalid materialized type action")
            redaction.register_secret(execution_action.text)
    allowed = context.origins
    if isinstance(execution_action, GotoAction):
        assert_url_allowed(execution_action.url, allowed)
    else:
        assert_url_allowed(
            actions.require_current_http_url(client, "before record action"),
            allowed,
        )
    error: Exception | None = None
    try:
        result: dict[str, Any] = actions.run_action(client, execution_action)
        assert_url_allowed(
            actions.require_current_http_url(client, "after record action"),
            allowed,
        )
        ok = True
    except ACTION_ERRORS as caught:
        result = {"error": str(caught)}
        ok = False
        error = caught
    safe_result = _persistable_action_result(
        execution_action,
        result,
        ok=ok,
        context=redaction,
    )
    event = {
        "schema": journal.SCHEMA,
        "run_id": run_id,
        "action": stored_action,
        "replayable": replayable,
        "ok": ok,
        "result": safe_result,
        "ts": round(time.time(), 3),
    }
    output = Path(path)
    journal.append_event(output, event)
    if error is not None:
        raise error
    return {
        "schema": journal.SCHEMA,
        "path": str(output),
        "recorded": 1,
        "replayable": replayable,
        "ok": ok,
    }


def _persistable_action_result(
    action: BrowserAction,
    result: dict[str, Any],
    *,
    ok: bool,
    context: RedactionContext,
) -> dict[str, Any]:
    """Never persists an arbitrary value or error coming from ``eval``."""
    if not isinstance(action, EvalAction):
        safe = redact_tree(result, context=context)
        return safe if isinstance(safe, dict) else {"redacted": True}
    field = "value" if ok else "error"
    context.mark(f"$.result.{field}")
    return {field: MASK, f"{field}_masked": True}


def replay(
    client: CDPClient,
    path: str,
    max_actions: int | None = None,
    *,
    context: OrchestrationContext,
) -> dict[str, Any]:
    """Replays an NDJSON journal, stopping at the first divergence."""
    redaction = context.redaction
    loaded = _load_replay_events(path, redaction)
    if isinstance(loaded, ReplayLoadFailure):
        return {
            "path": path,
            "ok": False,
            "events": loaded.events,
            "played": 0,
            "divergence": loaded.divergence,
        }
    entries = loaded
    if max_actions is not None and len(entries) > max_actions:
        raise ValueError(f"--max-actions budget exceeded: {len(entries)} > {max_actions}")
    for index, entry in enumerate(entries):
        if entry.event["ok"] is not True:
            return {
                "path": path,
                "events": len(entries),
                "played": 0,
                "ok": False,
                "divergence": f"event {index}: ok=false journaled",
            }
    origin_patterns = context.origins
    played = 0
    for index, entry in enumerate(entries):
        event, action = entry.event, entry.action
        if isinstance(action, GotoAction):
            try:
                assert_url_allowed(action.url, origin_patterns)
            except ACTION_ERRORS as error:
                return _replay_error(path, len(entries), played, index, error)
        if not isinstance(action, GotoAction):
            try:
                assert_url_allowed(
                    actions.require_current_http_url(client, "before action"),
                    origin_patterns,
                )
            except ACTION_ERRORS as error:
                return _replay_error(path, len(entries), played, index, error)
        try:
            actual = redact_tree(actions.run_action(client, action), context=redaction)
        except ACTION_ERRORS as error:
            return _replay_error(path, len(entries), played, index, error)
        played += 1
        try:
            current_url = actions.require_current_http_url(
                client,
                "after navigation" if isinstance(action, GotoAction) else "after action",
            )
            assert_url_allowed(current_url, origin_patterns)
        except ACTION_ERRORS as error:
            prefix = "destination after action: " if not isinstance(action, GotoAction) else ""
            return _replay_error(path, len(entries), played, index, error, prefix=prefix)
        if "result" in event:
            differences = _semantic_differences(event["result"], actual)
            if differences:
                return {
                    "path": path,
                    "events": len(entries),
                    "played": played,
                    "ok": False,
                    "divergence": {
                        "event": index,
                        "kind": "result_mismatch",
                        "differences": differences,
                    },
                }
    return {"path": path, "events": len(entries), "played": played, "ok": True}


def _load_replay_events(
    path: str,
    redaction: RedactionContext,
) -> list[ReplayEntry] | ReplayLoadFailure:
    """Decode, validate and normalize the complete journal before execution."""
    entries: list[ReplayEntry] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            return ReplayLoadFailure(len(entries), f"line {line_number}: {error.msg}")
        if not isinstance(event, dict) or not isinstance(event.get("action"), list | dict):
            return ReplayLoadFailure(len(entries), f"line {line_number}: missing action")
        if event.get("schema") not in {None, journal.SCHEMA}:
            return ReplayLoadFailure(len(entries) + 1, f"line {line_number}: unknown record schema")
        if event.get("replayable") is False:
            return ReplayLoadFailure(
                len(entries) + 1,
                f"line {line_number}: redacted action not replayable",
            )
        try:
            materialized = journal.materialize_action(event["action"])
        except (ValueError, journal.JournalError) as error:
            return ReplayLoadFailure(len(entries) + 1, f"line {line_number}: {error}")
        if isinstance(materialized, TypeAction):
            redaction.register_secret(materialized.text)
        if not isinstance(event.get("ok"), bool):
            return ReplayLoadFailure(
                len(entries) + 1,
                f"line {line_number}: boolean ok required",
            )
        if "result" in event and not isinstance(event["result"], dict):
            return ReplayLoadFailure(
                len(entries) + 1,
                f"line {line_number}: result must be an object",
            )
        if "result" in event:
            event = {**event, "result": redact_tree(event["result"], context=redaction)}
            event = _normalize_legacy_event(event, materialized)
        entries.append(ReplayEntry(event, materialized))
    return entries


def _normalize_legacy_event(
    event: dict[str, Any],
    action: BrowserAction,
) -> dict[str, Any]:
    """Normalize the only pre-schema result contract still accepted."""
    result = event.get("result")
    if (
        event.get("schema") is None
        and isinstance(action, TypeAction)
        and isinstance(result, dict)
        and isinstance(result.get("typed"), str)
    ):
        return {**event, "result": {**result, "typed": True}}
    return event


def _replay_error(
    path: str,
    event_count: int,
    played: int,
    index: int,
    error: Exception,
    *,
    prefix: str = "",
) -> dict[str, Any]:
    return {
        "path": path,
        "events": event_count,
        "played": played,
        "ok": False,
        "divergence": f"event {index}: {prefix}{error}",
    }


_VOLATILE_RESULT_KEYS = {"elapsed_ms", "frameId", "loaderId", "x", "y"}


def _semantic_differences(expected: Any, actual: Any, path: str = "$") -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, value in expected.items():
            if key in _VOLATILE_RESULT_KEYS:
                continue
            child = f"{path}.{key}"
            if key not in actual:
                differences.append({"path": child, "expected": value, "actual": "<missing>"})
            else:
                differences.extend(_semantic_differences(value, actual[key], child))
        return differences
    if expected != actual:
        differences.append({"path": path, "expected": expected, "actual": actual})
    return differences
