"""CLI cdpx — interface agent/humain vers les primitives CDP.

Contrat de sortie (voir HARNESS.md):
- stdout = UN objet JSON compact par défaut (parsable machine, sobre en tokens).
- --pretty = JSON indenté pour lecture humaine.
- stderr = diagnostics humains.
- exit 0 = succès, 1 = erreur d'exécution (CDP/JS/timeout), 2 = erreur d'usage.

Connexion: --port/--host ciblent un Chrome lancé avec
  chrome --remote-debugging-port=9222 --user-data-dir=/tmp/cdpx-profile
Le target est la première page, ou --target <id> (voir `cdpx tabs list`).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import stat
import sys
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cdpx import __version__, discovery, journal, output, scenarios, session
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.policy import (
    Authority,
    ExecutionContext,
    PolicyError,
    action_authority,
    assert_authorized,
    assert_grant,
    assert_loopback_endpoint,
    assert_url_allowed,
    validate_target,
)
from cdpx.primitives import (
    actions,
    advanced,
    audit,
    capture,
    dev,
    inputs,
    js,
    nav,
    net,
    profiler_panels,
    state,
)
from cdpx.security import (
    RedactionContext,
    redact_text,
    redact_tree,
    secret_values_from_environment,
)


def _action(args) -> list[str]:
    """Action composée (REMAINDER), débarrassée du séparateur `--` initial."""
    action = getattr(args, "action", None) or []
    if not isinstance(action, list):
        return []
    return action[1:] if action and action[0] == "--" else action


def _execution(args) -> ExecutionContext:
    context = getattr(args, "_execution_context", None)
    if not isinstance(context, ExecutionContext):
        raise RuntimeError("contexte d'exécution non préparé")
    return context


def _origins(args) -> str | None:
    context = _execution(args)
    return ",".join(context.origins) if context.team_mode else os.environ.get("CDPX_ORIGINS")


def _policy_action(args) -> list[str] | None:
    if args.command in {"tabs", "cookies"}:
        return [args.action]
    if args.command == "vitals" and getattr(args, "click", None):
        return ["click", args.click]
    action = _action(args)
    return action or None


def _destination(args) -> str | None:
    if args.command in {"goto", "network", "profiler", "coverage", "vitals"}:
        return args.url
    if args.command == "seo":
        return args.url
    if args.command == "cookies" and args.action == "set":
        return args.url
    action = _action(args)
    if args.command in {"intercept", "emulate", "record", "dom-diff"}:
        if len(action) >= 2 and action[0] == "goto":
            return action[1]
    return None


def _requires_current_origin(args) -> bool:
    if args.command in {"goto", "network", "profiler", "coverage", "vitals", "intercept"}:
        return False
    if args.command == "seo" and args.url:
        return False
    if args.command == "cookies":
        return False
    if args.command in {"replay", "scenario"}:
        return False
    if args.command == "emulate" and not _action(args):
        return False
    if args.command in {"emulate", "record"} and _destination(args):
        return False
    return True


def _current_http_url(client: CDPClient) -> str:
    current = js.evaluate(client, "window.location.href")
    if not isinstance(current, str):
        raise PolicyError("mode équipe: URL courante indéterminable")
    return current


def _assert_team_current(args, client: CDPClient) -> None:
    context = _execution(args)
    if context.team_mode:
        assert_url_allowed(_current_http_url(client), context.origins)


def _team_artifact_path(
    args,
    requested: str,
    category: str,
    *,
    must_exist: bool = False,
) -> str:
    """Confine les fichiers d'un run équipe dans son dossier de rétention."""
    if not _execution(args).team_mode:
        return requested
    manifest = getattr(args, "_session_manifest", None)
    if not isinstance(manifest, session.SessionManifest):
        raise PolicyError("mode équipe: manifest requis pour les artefacts")
    name = Path(requested).name
    if (
        not name
        or len(name) > 128
        or not name[0].isascii()
        or not name[0].isalnum()
        or any(not char.isascii() or not (char.isalnum() or char in "._-") for char in name)
    ):
        raise PolicyError(f"mode équipe: nom d'artefact invalide: {name or requested}")
    root = Path(manifest.artifacts_dir) / category
    if root.is_symlink():
        raise PolicyError(f"mode équipe: dossier d'artefact symbolique interdit: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    destination = root / name
    if destination.is_symlink():
        raise PolicyError(f"mode équipe: artefact symbolique interdit: {destination}")
    if must_exist:
        try:
            info = destination.lstat()
        except OSError as e:
            raise PolicyError(f"mode équipe: artefact introuvable: {destination}") from e
        if not stat.S_ISREG(info.st_mode):
            raise PolicyError(f"mode équipe: artefact régulier requis: {destination}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PolicyError("mode équipe: artefact appartenant à un autre utilisateur")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PolicyError("mode équipe: permissions d'artefact trop ouvertes; 0600 requis")
    return str(destination)


def _team_artifact_metadata(args, data: dict[str, Any], classification: str) -> dict[str, Any]:
    if not _execution(args).team_mode:
        return data
    return {
        **data,
        "classification": classification,
        "upload_allowed": False,
        "retention": "session",
    }


def _artifact_ttl(args, default: float = 86400) -> float:
    if not _execution(args).team_mode:
        return default
    manifest = getattr(args, "_session_manifest", None)
    if not isinstance(manifest, session.SessionManifest):
        raise PolicyError("mode équipe: manifest requis pour la rétention")
    try:
        remaining = (
            datetime.fromisoformat(manifest.expires_at) - datetime.now(UTC)
        ).total_seconds()
    except ValueError as e:
        raise PolicyError("mode équipe: expiration de session invalide") from e
    if remaining <= 0:
        raise PolicyError(f"session expirée: {manifest.session_id}")
    return remaining


@contextmanager
def _client(args) -> Iterator[CDPClient]:
    context = _execution(args)
    action = _action(args)
    policy_action = _policy_action(args)
    required = getattr(args, "_required_authority", None)
    if isinstance(required, Authority):
        assert_grant(context, required, args.command)
    else:
        assert_authorized(context, args.command, policy_action)
    destination = _destination(args)
    if context.team_mode and destination:
        assert_url_allowed(destination, context.origins)

    lease: Any = contextlib.nullcontext(None)
    if context.team_mode:
        lease = session.SessionLease(args.session, run_id=args.run_id, target_id=args.target)
    with lease as manifest:
        target = discovery.pick_page(args.host, args.port, args.target)
        if context.team_mode:
            validate_target(target, context.target_id or "")
            assert_loopback_endpoint(args.host, target.get("webSocketDebuggerUrl"))
        guard_url = target.get("url")
        guard_action = action
        if args.command == "intercept" and len(action) == 2 and action[0] == "goto":
            guard_url = action[1]
        elif args.command == "vitals" and getattr(args, "click", None):
            guard_url = args.url
            guard_action = ["click", args.click]
        elif args.command == "cookies":
            guard_action = [args.action]
            if args.action == "set":
                guard_url = args.url
        # replay applique sa garde après chaque goto et avant/après mutation.
        if not context.team_mode and args.command != "replay":
            advanced.assert_origin_allowed(
                args.command,
                guard_url,
                _origins(args),
                action=guard_action,
            )
        with CDPClient(target["webSocketDebuggerUrl"], timeout=args.timeout) as client:
            if context.team_mode and _requires_current_origin(args):
                assert_url_allowed(_current_http_url(client), context.origins)
            # Garder la référence vivante durant toute la connexion/lease.
            _ = manifest
            yield client


def _redaction_context(args) -> RedactionContext:
    context = getattr(args, "_redaction_context", None)
    if isinstance(context, RedactionContext):
        return context
    context = RedactionContext()
    execution = getattr(args, "_execution_context", None)
    if (isinstance(execution, ExecutionContext) and execution.team_mode) or bool(
        getattr(args, "session", None)
    ):
        for env_secret in secret_values_from_environment():
            context.register_secret(env_secret)
    for name in ("text", "value"):
        value = getattr(args, name, None)
        if isinstance(value, str) and value:
            context.register_secret(value)
    action = _action(args)
    if len(action) >= 3 and action[0] == "type":
        context.register_secret(action[2])
    args._redaction_context = context
    return context


def _safe_output(args, data: Any) -> Any:
    safe = redact_tree(data, context=_redaction_context(args))
    context = _execution(args)
    if context.team_mode and isinstance(safe, dict):
        safe = {**safe, "_cdpx": context.metadata()}
    return safe


def _out(args, data) -> None:
    shaped = output.bound(_safe_output(args, data), full=args.full, limit=args.limit)
    if args.pretty:
        print(json.dumps(shaped, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(shaped, ensure_ascii=False, separators=(",", ":")))


def _ndjson(args, data) -> None:
    print(
        json.dumps(_safe_output(args, data), ensure_ascii=False, separators=(",", ":")),
        flush=True,
    )


def _higher_authority(left: Authority, right: Authority) -> Authority:
    order = (Authority.OBSERVATION, Authority.INTERACTION, Authority.PRIVILEGED)
    return order[max(order.index(left), order.index(right))]


def _preflight_actions(args, action_list: list[list[str]]) -> Authority:
    context = _execution(args)
    required = Authority.OBSERVATION
    for action in action_list:
        actions.validate_action(action)
        required = _higher_authority(required, action_authority(action))
        if context.team_mode and action[0] == "goto":
            assert_url_allowed(action[1], context.origins)
        if action[0] == "type" and len(action) >= 3:
            if context.team_mode and args.command == "record":
                stored, replayable = journal.serialize_action(
                    action,
                    context=_redaction_context(args),
                )
                if not replayable:
                    raise PolicyError("mode équipe: record type exige @env:NOM")
                materialized = journal.materialize_action(stored, team_mode=True)
                _redaction_context(args).register_secret(materialized[2])
            else:
                _redaction_context(args).register_secret(action[2])
    assert_grant(context, required, args.command)
    args._required_authority = required
    return required


def _preflight_replay(args) -> None:
    if not _execution(args).team_mode:
        return
    parsed: list[list[str]] = []
    try:
        lines = Path(args.path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise PolicyError(f"journal replay illisible: {e}") from e
    for lineno, line in enumerate(lines, start=1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            raise PolicyError(f"journal replay invalide ligne {lineno}: {e.msg}") from e
        stored_action = event.get("action") if isinstance(event, dict) else None
        if not isinstance(stored_action, list | dict):
            raise PolicyError(f"journal replay invalide ligne {lineno}: action requise")
        if event.get("replayable") is False:
            raise PolicyError(f"journal replay non rejouable ligne {lineno}")
        try:
            action = journal.materialize_action(stored_action, team_mode=True)
        except journal.JournalError as e:
            raise PolicyError(f"journal replay invalide ligne {lineno}: {e}") from e
        if len(action) >= 3 and action[0] == "type":
            _redaction_context(args).register_secret(action[2])
        parsed.append(action)
    _preflight_actions(args, parsed)


def _scenario_action(scenario: scenarios.Scenario, step: scenarios.ScenarioStep) -> list[str]:
    if step.verb == "goto":
        return ["goto", urllib.parse.urljoin(scenario.base_url.rstrip("/") + "/", step.value)]
    if step.verb in {"wait_visible", "wait_text"}:
        return ["wait", step.value if isinstance(step.value, str) else step.value[0]]
    if step.verb == "type":
        if isinstance(step.value, dict):
            if "secret_ref" not in step.value:
                raise PolicyError("mode équipe: scenario type exige secret_ref")
            name = step.value["secret_ref"]
            if name not in os.environ:
                raise PolicyError(f"scenario: variable de secret introuvable: {name}")
            action = ["type", step.value["selector"], os.environ[name]]
            if step.value.get("clear"):
                action.append("--clear")
            return action
        raise PolicyError("mode équipe: scenario type exige secret_ref")
    return [step.verb, step.value]


def _preflight_scenario(args, scenario_spec: scenarios.Scenario) -> None:
    context = _execution(args)
    if not context.team_mode:
        return
    assert_url_allowed(scenario_spec.base_url, context.origins)
    scenario_actions = [_scenario_action(scenario_spec, step) for step in scenario_spec.steps]
    required = _preflight_actions(args, scenario_actions)
    if (
        scenario_spec.emulation
        or "profiler" in scenario_spec.artifacts
        or any("profiler" in step.capture for step in scenario_spec.steps)
    ):
        required = Authority.PRIVILEGED
        assert_grant(context, required, "scenario")
    args._required_authority = required


def _resolve_sensitive_value(
    args,
    *,
    literal: str | None,
    env_name: str | None,
    label: str,
) -> str:
    if literal is not None and env_name is not None:
        raise scenarios.ScenarioUsageError(f"{label}: valeur littérale et référence env exclusives")
    if literal is None and env_name is None:
        raise scenarios.ScenarioUsageError(f"{label}: --value/texte ou référence env requis")
    if _execution(args).team_mode and literal is not None:
        raise PolicyError(f"mode équipe: {label} exige une référence de secret en environnement")
    if env_name is not None:
        if not env_name or env_name not in os.environ:
            raise PolicyError(f"{label}: variable de secret introuvable: {env_name}")
        value = os.environ[env_name]
    else:
        value = literal or ""
    _redaction_context(args).register_secret(value)
    return value


# -- commandes -----------------------------------------------------------------


def cmd_tabs(args) -> None:
    if args.action in {"activate", "close"} and not args.id:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --id requis")
    if args.action != "new" and args.url is not None:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --url non supporté")
    if args.action not in {"activate", "close"} and args.id is not None:
        raise scenarios.ScenarioUsageError(f"tabs {args.action}: --id non supporté")
    context = _execution(args)
    assert_authorized(context, "tabs", [args.action])
    if context.team_mode and args.action != "list":
        raise PolicyError("mode équipe: lifecycle des targets réservé au supervisor de session")
    scope: Any = contextlib.nullcontext(None)
    if context.team_mode:
        scope = session.SessionLease(args.session, run_id=args.run_id, target_id=args.target)
    with scope as leased_manifest:
        if args.action == "list":
            targets = discovery.list_targets(args.host, args.port)
            if context.team_mode:
                assigned = [target for target in targets if target.get("id") == context.target_id]
                if len(assigned) != 1:
                    raise PolicyError("mode équipe: target attribué unique introuvable")
                target = validate_target(assigned[0], context.target_id or "")
                assert_loopback_endpoint(args.host, target.get("webSocketDebuggerUrl"))
                if not isinstance(leased_manifest, session.SessionManifest):
                    raise PolicyError("mode équipe: lease de session invalide")
                if target.get("webSocketDebuggerUrl") != leased_manifest.websocket_url:
                    raise PolicyError("mode équipe: WebSocket du target différent du manifest")
                with CDPClient(target["webSocketDebuggerUrl"], timeout=args.timeout) as client:
                    current_url = _current_http_url(client)
                    assert_url_allowed(current_url, context.origins)
                targets = [{**target, "url": current_url}]
            tabs = [_public_target(target) for target in targets]
            _out(args, {"tabs": tabs, "count": len(tabs)})
        elif args.action == "new":
            _out(args, _public_target(discovery.new_tab(args.host, args.port, args.url)))
        elif args.action == "activate":
            discovery.activate_tab(args.host, args.port, args.id)
            _out(args, {"activated": args.id})
        elif args.action == "close":
            discovery.close_tab(args.host, args.port, args.id)
            _out(args, {"closed": args.id})


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": target.get("id"),
        "type": target.get("type"),
        "title": target.get("title"),
        "url": target.get("url"),
    }


def cmd_version(args) -> None:
    context = _execution(args)
    assert_authorized(context, "version")
    scope: Any = contextlib.nullcontext(None)
    if context.team_mode:
        scope = session.SessionLease(args.session, run_id=args.run_id, target_id=args.target)
    with scope:
        data = discovery.version(args.host, args.port)
    data.pop("webSocketDebuggerUrl", None)
    _out(args, data)


def cmd_goto(args) -> None:
    with _client(args) as c:
        result = nav.navigate(c, args.url, wait=args.wait, timeout=args.timeout)
        _require_navigation(result)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_wait(args) -> None:
    with _client(args) as c:
        result = nav.wait_for(c, args.selector, timeout=args.timeout)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_eval(args) -> None:
    with _client(args) as c:
        value = js.evaluate(c, args.expression, await_promise=args.await_promise)
        _assert_team_current(args, c)
        _out(args, {"value": value})


def cmd_text(args) -> None:
    with _client(args) as c:
        result = js.get_text(c, args.selector)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_html(args) -> None:
    with _client(args) as c:
        result = js.get_html(c, args.selector)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_count(args) -> None:
    with _client(args) as c:
        result = js.count(c, args.selector)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_click(args) -> None:
    with _client(args) as c:
        result = inputs.click(c, args.selector)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_type(args) -> None:
    text = _resolve_sensitive_value(
        args,
        literal=args.text,
        env_name=args.secret_env,
        label="type",
    )
    with _client(args) as c:
        result = inputs.type_text(c, args.selector, text, clear=args.clear)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_key(args) -> None:
    with _client(args) as c:
        result = inputs.press_key(c, args.key)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_screenshot(args) -> None:
    with _client(args) as c:
        path = _team_artifact_path(args, args.output, "captures")
        result = capture.screenshot(c, path, full_page=args.full_page, fmt=args.fmt)
        try:
            _assert_team_current(args, c)
        except PolicyError:
            if _execution(args).team_mode:
                Path(path).unlink(missing_ok=True)
            raise
        _out(args, _team_artifact_metadata(args, result, "opaque-restricted"))


def cmd_pdf(args) -> None:
    with _client(args) as c:
        path = _team_artifact_path(args, args.output, "captures")
        result = capture.pdf(c, path)
        try:
            _assert_team_current(args, c)
        except PolicyError:
            if _execution(args).team_mode:
                Path(path).unlink(missing_ok=True)
            raise
        _out(args, _team_artifact_metadata(args, result, "opaque-restricted"))


def cmd_console(args) -> None:
    with _client(args) as c:
        if args.follow:
            try:
                for entry in capture.console_follow(
                    c,
                    max_entries=args.max,
                    context=_redaction_context(args),
                ):
                    _assert_team_current(args, c)
                    _ndjson(args, entry)
                _assert_team_current(args, c)
            except KeyboardInterrupt:
                return
        else:
            result = capture.console_capture(
                c,
                duration=args.duration,
                context=_redaction_context(args),
            )
            _assert_team_current(args, c)
            _out(
                args,
                result,
            )


def cmd_network(args) -> None:
    with _client(args) as c:
        result = net.capture(
            c,
            args.url,
            timeout=args.timeout,
            settle=args.settle,
            context=_redaction_context(args),
        )
        _assert_team_current(args, c)
        _out(args, result)


def cmd_cookies(args) -> None:
    if args.action == "set":
        value = _resolve_sensitive_value(
            args,
            literal=args.value,
            env_name=args.value_env,
            label="cookies set",
        )
        missing = [
            flag for flag, item in (("--name", args.name), ("--url", args.url)) if item is None
        ]
        if missing:
            raise scenarios.ScenarioUsageError(f"cookies set: {', '.join(missing)} requis")
        if args.show_values:
            raise scenarios.ScenarioUsageError("cookies set: --show-values non supporté")
    elif any(value is not None for value in (args.name, args.value, args.value_env, args.url)):
        raise scenarios.ScenarioUsageError(
            f"cookies {args.action}: --name/--value/--url non supportés"
        )
    elif args.action == "clear" and args.show_values:
        raise scenarios.ScenarioUsageError("cookies clear: --show-values non supporté")
    with _client(args) as c:
        if args.action == "get":
            _out(args, state.get_cookies(c, show_values=args.show_values))
        elif args.action == "set":
            _out(args, state.set_cookie(c, args.name, value, args.url))
        elif args.action == "clear":
            _out(args, state.clear_cookies(c))


def cmd_storage(args) -> None:
    with _client(args) as c:
        result = state.get_storage(c, kind=args.kind, show_values=args.show_values)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_seo(args) -> None:
    with _client(args) as c:
        if args.url:
            _require_navigation(nav.navigate(c, args.url, wait="load", timeout=args.timeout))
            _assert_team_current(args, c)
        result = audit.seo(c)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_metrics(args) -> None:
    with _client(args) as c:
        result = audit.metrics(c)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_profiler(args) -> None:
    with _client(args) as c:
        result = dev.profiler(
            c,
            args.url,
            timeout=args.timeout,
            settle=args.settle,
            panels=args.panels,
            context=_redaction_context(args),
            allowed_origins=_execution(args).origins or None,
        )
        _assert_team_current(args, c)
        _out(args, result)


def cmd_dom_diff(args) -> None:
    with _client(args) as c:
        result = dev.dom_diff(c, _action(args))
        _assert_team_current(args, c)
        _out(args, result)


def cmd_intercept(args) -> None:
    action = _action(args)
    if len(action) != 2 or action[0] != "goto":
        raise ValueError("intercept supporte: -- goto <url>")
    with _client(args) as c:
        result = advanced.intercept_goto(
            c,
            args.rule,
            action[1],
            timeout=args.timeout,
            settle=args.settle,
        )
        _assert_team_current(args, c)
        _out(args, result)


def cmd_emulate(args) -> None:
    action = _action(args)
    with _client(args) as c:
        res = advanced.emulate(c, preset=args.preset, reset=args.reset)
        if action:
            # Les overrides meurent avec la connexion: agir sous émulation
            # exige d'exécuter l'action DANS cette connexion (cf. e2e).
            res["action"] = {
                "argv": action,
                "result": actions.run_action(c, action, timeout=args.timeout),
            }
            _assert_team_current(args, c)
        _out(args, res)


def cmd_vitals(args) -> None:
    with _client(args) as c:
        result = advanced.vitals(
            c,
            args.url,
            timeout=args.timeout,
            click_selector=args.click,
            settle=args.settle,
            origins=_origins(args),
        )
        _assert_team_current(args, c)
        _out(args, result)


def cmd_a11y(args) -> None:
    with _client(args) as c:
        result = advanced.a11y(c)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_coverage(args) -> None:
    with _client(args) as c:
        result = advanced.coverage(c, args.url, timeout=args.timeout)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_frame(args) -> None:
    with _client(args) as c:
        result = advanced.frame_text(c, args.selector)
        _assert_team_current(args, c)
        _out(args, result)


def cmd_record(args) -> None:
    _preflight_actions(args, [_action(args)])
    with _client(args) as c:
        path = _team_artifact_path(args, args.output, "journals")
        _out(
            args,
            _team_artifact_metadata(
                args,
                advanced.record(
                    c,
                    path,
                    _action(args),
                    run_id=_execution(args).run_id,
                    redaction_context=_redaction_context(args),
                    origins=_origins(args),
                    strict_origins=_execution(args).team_mode,
                ),
                "internal",
            ),
        )


def cmd_replay(args) -> int:
    args.path = _team_artifact_path(args, args.path, "journals", must_exist=True)
    _preflight_replay(args)
    with _client(args) as c:
        res = advanced.replay(
            c,
            args.path,
            max_actions=args.max_actions,
            origins=_origins(args),
            team_mode=_execution(args).team_mode,
            strict_origins=_execution(args).team_mode,
            redaction_context=_redaction_context(args),
        )
    _out(args, res)
    # divergence = erreur d'exécution: JSON structuré sur stdout, exit 1
    return 0 if res.get("ok") else 1


def cmd_scenario(args) -> int:
    if args.scenario_action != "run":
        raise scenarios.ScenarioUsageError("scenario supporte: run <path>")
    scenario = scenarios.load(args.path)
    _preflight_scenario(args, scenario)
    with _client(args) as c:
        res = scenarios.run(
            c,
            scenario,
            evidence_root=args.evidence_dir,
            timeout=args.timeout,
            settle=args.settle,
            origins=_origins(args),
            strict_origins=_execution(args).team_mode,
            redaction_context=_redaction_context(args),
            run_id=_execution(args).run_id,
            artifact_ttl=_artifact_ttl(args),
        )
    _out(args, res)
    return 0 if res["verdict"] == "pass" else 1


def cmd_session(args) -> None:
    if args.session_action == "start":
        manifest, path = session.start_session(
            run_id=args.session_run_id,
            authority=args.authority,
            origins=args.origins,
            ttl=args.ttl,
            owner_pid=args.owner_pid,
            chrome_bin=args.chrome,
            timeout=args.timeout,
        )
        _out(args, {**manifest.public_dict(), "manifest": str(path), "started": True})
        return
    if args.session_action == "status":
        _out(
            args,
            session.session_status(
                args.manifest,
                run_id=args.session_run_id,
                target_id=args.session_target,
            ),
        )
        return
    if args.session_action == "stop":
        _out(
            args,
            session.stop_session(
                args.manifest,
                run_id=args.session_run_id,
                target_id=args.session_target,
                timeout=args.timeout,
            ),
        )
        return
    raise scenarios.ScenarioUsageError("session supporte: start, status, stop")


# -- parseur ---------------------------------------------------------------------


def _panels_arg(value: str) -> list[str] | None:
    """--panels: all (défaut) -> tous, none -> sonde token seule, sinon liste CSV."""
    if value == "all":
        return None
    if value == "none":
        return []
    panels = [item.strip() for item in value.split(",") if item.strip()]
    try:
        return profiler_panels.normalize_panels(panels)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def _intercept_rule_arg(value: str) -> str:
    try:
        advanced.parse_intercept_rule(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return value


def _require_navigation(result: dict) -> None:
    if result.get("ok") is False:
        raise ValueError(f"navigation échouée: {result.get('errorText') or result.get('url')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cdpx", description=__doc__)
    p.add_argument("--version", action="version", version=f"cdpx {__version__}")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--target", default=None, help="id du target (défaut: première page)")
    p.add_argument("--session", default=None, help="manifest d'une session équipe gérée")
    p.add_argument("--run-id", default=None, help="identifiant du run propriétaire")
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--pretty", action="store_true", help="JSON indenté pour lecture humaine")
    p.add_argument("--full", action="store_true", help="ne pas borner les sorties volumineuses")
    p.add_argument("--max-actions", type=int, default=None, help="budget d'actions agentiques")
    p.add_argument(
        "--limit",
        type=int,
        default=output.DEFAULT_LIMIT,
        help="nombre max d'items par liste volumineuse (défaut: 50)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("tabs", help="gestion des onglets")
    s.add_argument("action", choices=["list", "new", "activate", "close"])
    s.add_argument("--url", default=None)
    s.add_argument("--id", default=None)
    s.set_defaults(func=cmd_tabs)

    s = sub.add_parser("version", help="infos du navigateur")
    s.set_defaults(func=cmd_version)

    s = sub.add_parser("goto", help="naviguer vers une URL")
    s.add_argument("url")
    s.add_argument("--wait", choices=["load", "domcontentloaded", "none"], default="load")
    s.set_defaults(func=cmd_goto)

    s = sub.add_parser("wait", help="attendre un sélecteur CSS")
    s.add_argument("selector")
    s.set_defaults(func=cmd_wait)

    s = sub.add_parser("eval", help="évaluer du JS dans la page")
    s.add_argument("expression")
    s.add_argument("--await", dest="await_promise", action="store_true")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("text", help="innerText (élément ou body)")
    s.add_argument("selector", nargs="?", default=None)
    s.set_defaults(func=cmd_text)

    s = sub.add_parser("html", help="outerHTML (élément ou document)")
    s.add_argument("selector", nargs="?", default=None)
    s.set_defaults(func=cmd_html)

    s = sub.add_parser("count", help="compter les éléments d'un sélecteur")
    s.add_argument("selector")
    s.set_defaults(func=cmd_count)

    s = sub.add_parser("click", help="cliquer un élément (Input domain)")
    s.add_argument("selector")
    s.set_defaults(func=cmd_click)

    s = sub.add_parser("type", help="taper du texte dans un champ")
    s.add_argument("selector")
    s.add_argument("text", nargs="?", default=None)
    s.add_argument("--secret-env", default=None, help="lire le texte depuis cette variable")
    s.add_argument("--clear", action="store_true", help="vider le champ avant")
    s.set_defaults(func=cmd_type)

    s = sub.add_parser(
        "key",
        help="presser une touche nommée (navigation, édition, Enter/Tab/Escape/Space)",
    )
    s.add_argument("key")
    s.set_defaults(func=cmd_key)

    s = sub.add_parser("screenshot", help="capture d'écran PNG ou JPEG")
    s.add_argument("-o", "--output", default="screenshot.png")
    s.add_argument("--full-page", action="store_true")
    s.add_argument("--format", dest="fmt", choices=["png", "jpeg"], default="png")
    s.set_defaults(func=cmd_screenshot)

    s = sub.add_parser("pdf", help="imprimer la page en PDF")
    s.add_argument("-o", "--output", default="page.pdf")
    s.set_defaults(func=cmd_pdf)

    s = sub.add_parser("console", help="capturer logs et exceptions JS")
    s.add_argument("--duration", type=float, default=2.0)
    s.add_argument("--follow", action="store_true", help="stream NDJSON jusqu'à Ctrl-C ou --max")
    s.add_argument("--max", type=int, default=None, help="nombre max d'entrées en mode follow")
    s.set_defaults(func=cmd_console)

    s = sub.add_parser("network", help="naviguer en capturant l'activité réseau")
    s.add_argument("url")
    s.add_argument("--settle", type=float, default=0.5)
    s.set_defaults(func=cmd_network)

    s = sub.add_parser("cookies", help="cookies (valeurs masquées par défaut)")
    s.add_argument("action", choices=["get", "set", "clear"])
    s.add_argument("--show-values", action="store_true")
    s.add_argument("--name", default=None)
    s.add_argument("--value", default=None)
    s.add_argument("--value-env", default=None, help="lire la valeur depuis cette variable")
    s.add_argument("--url", default=None)
    s.set_defaults(func=cmd_cookies)

    s = sub.add_parser("storage", help="localStorage / sessionStorage")
    s.add_argument("--kind", choices=["local", "session"], default="local")
    s.add_argument("--show-values", action="store_true")
    s.set_defaults(func=cmd_storage)

    s = sub.add_parser("seo", help="audit SEO on-page du DOM rendu")
    s.add_argument("url", nargs="?", default=None, help="naviguer d'abord (optionnel)")
    s.set_defaults(func=cmd_seo)

    s = sub.add_parser("metrics", help="métriques de performance du renderer")
    s.set_defaults(func=cmd_metrics)

    s = sub.add_parser("profiler", help="parser les panels du Web Profiler Symfony")
    s.add_argument("url")
    s.add_argument("--settle", type=float, default=0.2)
    s.add_argument(
        "--panels",
        type=_panels_arg,
        default="all",
        help=f"all | none | liste: {','.join(profiler_panels.ALL_PANELS)}",
    )
    s.set_defaults(func=cmd_profiler)

    s = sub.add_parser("dom-diff", help="diff DOM stable autour d'une action")
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_dom_diff)

    s = sub.add_parser("intercept", help="intercepter des requêtes pendant une commande")
    s.add_argument(
        "--rule",
        action="append",
        required=True,
        type=_intercept_rule_arg,
        help="PATTERN => 200..599|block|continue",
    )
    s.add_argument("--settle", type=float, default=0.5)
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_intercept)

    s = sub.add_parser("emulate", help="émulation mobile/réseau/CPU (+ action composée)")
    s.add_argument("preset", nargs="?", choices=["mobile", "slow-3g", "cpu-4x"])
    s.add_argument("--reset", action="store_true")
    s.add_argument("action", nargs=argparse.REMAINDER, help="-- goto <url> | click <sel> | ...")
    s.set_defaults(func=cmd_emulate)

    s = sub.add_parser("vitals", help="Core Web Vitals basiques")
    s.add_argument("url")
    s.add_argument("--click", default=None, help="sélecteur à cliquer pour mesurer INP")
    s.add_argument("--settle", type=float, default=0.5)
    s.set_defaults(func=cmd_vitals)

    s = sub.add_parser("a11y", help="arbre d'accessibilité compact")
    s.set_defaults(func=cmd_a11y)

    s = sub.add_parser("coverage", help="coverage JS par fichier")
    s.add_argument("url")
    s.set_defaults(func=cmd_coverage)

    s = sub.add_parser("frame", help="lire du texte dans une iframe")
    s.add_argument("selector")
    s.set_defaults(func=cmd_frame)

    s = sub.add_parser("record", help="exécuter une action et la journaliser en NDJSON")
    s.add_argument("-o", "--output", default="cdpx-record.ndjson")
    s.add_argument("action", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_record)

    s = sub.add_parser("replay", help="rejouer un journal NDJSON, stop à la divergence")
    s.add_argument("path")
    s.set_defaults(func=cmd_replay)

    s = sub.add_parser("scenario", help="exécuter un scénario métier déclaratif")
    scenario_sub = s.add_subparsers(dest="scenario_action", required=True)
    r = scenario_sub.add_parser("run", help="exécuter un fichier scénario YAML")
    r.add_argument("path")
    r.add_argument("--evidence-dir", default=".cdpx-evidence")
    r.add_argument("--settle", type=float, default=0.5)
    r.set_defaults(func=cmd_scenario)

    s = sub.add_parser("session", help="profil Chrome jetable et exclusif pour un run")
    session_sub = s.add_subparsers(dest="session_action", required=True)
    start = session_sub.add_parser("start", help="démarrer une session Chrome supervisée")
    start.add_argument("--run-id", dest="session_run_id", required=True)
    start.add_argument(
        "--authority",
        choices=["observation", "interaction", "privileged"],
        required=True,
    )
    start.add_argument("--origins", default=os.environ.get("CDPX_ORIGINS", ""))
    start.add_argument("--ttl", type=float, default=3600.0)
    start.add_argument("--owner-pid", type=int, default=None)
    start.add_argument("--chrome", default=None)
    start.set_defaults(func=cmd_session)
    for action_name in ("status", "stop"):
        child = session_sub.add_parser(action_name, help=f"{action_name} une session gérée")
        child.add_argument("--manifest", required=True)
        child.add_argument("--run-id", dest="session_run_id", required=True)
        child.add_argument("--target", dest="session_target", default=None)
        child.set_defaults(func=cmd_session)

    return p


def _prepare_args(args) -> None:
    if args.command == "session":
        if args.session is not None or args.run_id is not None or args.target is not None:
            raise scenarios.ScenarioUsageError(
                "session start/status/stop utilise ses options propres, "
                "sans --session/--target globaux"
            )
        args.host = args.host or os.environ.get("CDPX_HOST", "127.0.0.1")
        args.port = args.port or int(os.environ.get("CDPX_PORT", "9222"))
        args._execution_context = ExecutionContext.legacy()
        args._redaction_context = RedactionContext()
        return
    if args.session:
        missing = [
            name
            for name, value in (("--run-id", args.run_id), ("--target", args.target))
            if not value
        ]
        if missing:
            raise scenarios.ScenarioUsageError(
                f"mode équipe: {', '.join(missing)} explicite(s) requis"
            )
        if args.host is not None or args.port is not None:
            raise scenarios.ScenarioUsageError(
                "mode équipe: --host/--port viennent du manifest et ne sont pas surchargeables"
            )
        manifest = session.load_manifest(args.session, run_id=args.run_id, target_id=args.target)
        session.assert_session_active(manifest)
        args.host = manifest.host
        args.port = manifest.port
        args._session_manifest = manifest
        args._execution_context = manifest.execution_context()
        if args.command == "scenario":
            args.evidence_dir = str(Path(manifest.artifacts_dir) / "scenarios")
        if args.full and manifest.authority != "privileged":
            raise PolicyError("mode équipe: --full requiert privileged")
    else:
        args.host = args.host or os.environ.get("CDPX_HOST", "127.0.0.1")
        args.port = args.port or int(os.environ.get("CDPX_PORT", "9222"))
        args._execution_context = ExecutionContext.legacy(
            target_id=args.target,
            origins=os.environ.get("CDPX_ORIGINS"),
        )
    _redaction_context(args)


def _error_text(args, error: Exception) -> str:
    return redact_text(str(error), context=_redaction_context(args))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _prepare_args(args)
        code = args.func(args)
        return code or 0
    except scenarios.ScenarioUsageError as e:
        print(f"cdpx: {_error_text(args, e)}", file=sys.stderr)
        return 2
    except (
        CDPError,
        CDPTimeout,
        discovery.DiscoveryError,
        js.JSException,
        inputs.ElementNotFound,
        ValueError,
        TimeoutError,
    ) as e:
        print(f"cdpx: {_error_text(args, e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
