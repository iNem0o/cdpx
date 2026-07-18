"""Cross-cutting execution adapter shared by CLI command families."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cdpx import discovery, journal, output, scenarios, session
from cdpx.action_model import BrowserAction, GotoAction, TypeAction, parse_action
from cdpx.cli_context import CommandInvocation, CommandOptions
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    assert_authorized,
    assert_grant,
    assert_url_allowed,
    command_semantics,
    max_authority,
)
from cdpx.primitives import js
from cdpx.security import (
    RedactionContext,
    redact_tree,
    secret_values_from_environment,
)


def action(args: CommandOptions | CommandInvocation) -> BrowserAction | None:
    """Parse a composed REMAINDER action after its optional ``--`` separator."""
    options = args.options if isinstance(args, CommandInvocation) else args
    argv = options.action or []
    if not isinstance(argv, list):
        return None
    normalized = argv[1:] if argv and argv[0] == "--" else argv
    return parse_action(normalized) if normalized else None


def require_action(args: CommandOptions | CommandInvocation) -> BrowserAction:
    parsed = action(args)
    return parse_action([]) if parsed is None else parsed


def execution(args: CommandInvocation) -> ExecutionContext:
    return args.require_execution()


def origins(args: CommandInvocation) -> str:
    return ",".join(execution(args).origins)


def orchestration(args: CommandInvocation) -> OrchestrationContext:
    return OrchestrationContext(execution(args).origins, args.redaction)


def current_http_url(client: CDPClient) -> str:
    current = js.evaluate(client, "window.location.href")
    if not isinstance(current, str):
        raise PolicyError("session: current URL undeterminable")
    return current


def assert_session_current(args: CommandInvocation, client: CDPClient) -> None:
    context = execution(args)
    assert_url_allowed(current_http_url(client), context.origins)


def artifact_path(
    args: CommandInvocation,
    requested: str,
    category: str,
    *,
    must_exist: bool = False,
) -> str:
    return args.require_artifacts().path(requested, category, must_exist=must_exist)


def artifact_metadata(
    args: CommandInvocation, data: dict[str, Any], classification: str
) -> dict[str, Any]:
    return args.require_artifacts().metadata(data, classification)


def artifact_ttl(args: CommandInvocation) -> float:
    return args.require_artifacts().remaining_ttl()


def _destination(args: CommandInvocation) -> str | None:
    source = command_semantics(args.options.command).destination_source
    if source == "url":
        return args.options.url
    if source == "cookie-url":
        return args.options.url if args.options.action == "set" else None
    if source == "action-goto":
        parsed = action(args)
        if isinstance(parsed, GotoAction):
            return parsed.url
    return None


def _requires_current_origin(args: CommandInvocation) -> bool:
    policy = command_semantics(args.options.command).current_origin
    if policy == "never":
        return False
    if policy == "unless-destination":
        return _destination(args) is None
    if policy == "action-non-navigation":
        parsed = action(args)
        return parsed is not None and not isinstance(parsed, GotoAction)
    return policy == "always"


@contextmanager
def browser_client(
    args: CommandInvocation, *, required_authority: Authority | None = None
) -> Iterator[CDPClient]:
    context = execution(args)
    if required_authority is not None:
        assert_grant(context, required_authority, args.options.command)
    else:
        assert_authorized(context, args.options.command)
    destination = _destination(args)
    if destination:
        assert_url_allowed(destination, context.origins)

    session_path = args.options.session
    run_id = args.options.run_id
    target_id = args.options.target
    if session_path is None or run_id is None or target_id is None:
        raise RuntimeError("session identity not prepared")
    lease: Any = session.SessionLease(
        session_path,
        run_id=run_id,
        target_id=target_id,
    )
    with lease as manifest:
        target = discovery.pick_page(args.options.host, args.options.port, target_id)
        target = session.assert_manifest_target_binding(manifest, target)
        with CDPClient(target["webSocketDebuggerUrl"], timeout=args.options.timeout) as client:
            if _requires_current_origin(args):
                assert_url_allowed(current_http_url(client), context.origins)
            yield client


def build_redaction_context(args: CommandOptions) -> RedactionContext:
    context = RedactionContext()
    for env_secret in secret_values_from_environment():
        context.register_secret(env_secret)
    try:
        parsed = action(args)
    except ValueError:
        # Built before main()'s try: an invalid action argv must
        # remain diagnosed by the command's preflight (exit 1/2 +
        # cdpx message), never by a traceback at context construction.
        return context
    if isinstance(parsed, TypeAction):
        context.register_secret(parsed.text)
    return context


def safe_output(args: CommandInvocation, data: Any) -> Any:
    safe = redact_tree(data, context=args.redaction)
    context = execution(args)
    if isinstance(safe, dict):
        safe = {**safe, "_cdpx": context.metadata()}
    return safe


def emit_json(args: CommandInvocation, data: Any) -> None:
    shaped = output.bound(safe_output(args, data), full=args.options.full, limit=args.options.limit)
    if args.options.pretty:
        print(json.dumps(shaped, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(shaped, ensure_ascii=False, separators=(",", ":")))


def emit_ndjson(args: CommandInvocation, data: Any) -> None:
    print(
        json.dumps(safe_output(args, data), ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def preflight_actions(args: CommandInvocation, actions: list[BrowserAction]) -> Authority:
    context = execution(args)
    required = max_authority(actions)
    for parsed in actions:
        if isinstance(parsed, GotoAction):
            assert_url_allowed(parsed.url, context.origins)
        if isinstance(parsed, TypeAction):
            if args.options.command == "record":
                stored, replayable = journal.serialize_action(parsed, context=args.redaction)
                if not replayable:
                    raise PolicyError("session: record type requires @env:NAME")
                materialized = journal.materialize_action(stored)
                if not isinstance(materialized, TypeAction):
                    raise PolicyError("session: invalid record type")
                args.redaction.register_secret(materialized.text)
            else:
                args.redaction.register_secret(parsed.text)
    assert_grant(context, required, args.options.command)
    return required


def preflight_replay(args: CommandInvocation, path: str) -> Authority:
    parsed_actions: list[BrowserAction] = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PolicyError(f"unreadable replay journal: {error}") from error
    for lineno, line in enumerate(lines, start=1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise PolicyError(f"invalid replay journal at line {lineno}: {error.msg}") from error
        stored_action = event.get("action") if isinstance(event, dict) else None
        if not isinstance(stored_action, list | dict):
            raise PolicyError(f"invalid replay journal at line {lineno}: action required")
        if event.get("replayable") is False:
            raise PolicyError(f"non-replayable replay journal at line {lineno}")
        try:
            parsed = journal.materialize_action(stored_action)
        except journal.JournalError as error:
            raise PolicyError(f"invalid replay journal at line {lineno}: {error}") from error
        if isinstance(parsed, TypeAction):
            args.redaction.register_secret(parsed.text)
        parsed_actions.append(parsed)
    return preflight_actions(args, parsed_actions)


def preflight_scenario(args: CommandInvocation, prepared: scenarios.PreparedScenario) -> Authority:
    context = execution(args)
    scenario_spec = prepared.scenario
    assert_url_allowed(scenario_spec.base_url, context.origins)
    scenario_actions = [
        operation.action for operation in prepared.operations if operation.action is not None
    ]
    required = preflight_actions(args, scenario_actions)
    if (
        scenario_spec.emulation
        or "profiler" in scenario_spec.artifacts
        or any("profiler" in step.capture for step in scenario_spec.steps)
    ):
        required = Authority.PRIVILEGED
        assert_grant(context, required, "scenario")
    return required


def resolve_sensitive_value(
    args: CommandInvocation,
    *,
    literal: str | None,
    env_name: str | None,
    label: str,
) -> str:
    if literal is not None and env_name is not None:
        raise scenarios.ScenarioUsageError(
            f"{label}: literal value and env reference are mutually exclusive"
        )
    if literal is None and env_name is None:
        raise scenarios.ScenarioUsageError(f"{label}: --value/text or env reference required")
    if literal is not None:
        raise PolicyError(f"session: {label} requires an environment secret reference")
    if not env_name or env_name not in os.environ:
        raise PolicyError(f"{label}: secret variable not found: {env_name}")
    value = os.environ[env_name]
    args.redaction.register_secret(value)
    return value
