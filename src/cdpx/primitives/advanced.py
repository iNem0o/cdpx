"""Primitives avancées M3-M5: interception, émulation, mesure et orchestration."""

from __future__ import annotations

import base64
import fnmatch
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

from cdpx import journal
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.policy import assert_url_allowed, parse_origins
from cdpx.primitives import actions, inputs, js, nav
from cdpx.security import MASK, RedactionContext, redact_tree

# Garde d'origine (CDPX_ORIGINS): mutations refusées hors liste, lectures
# permises. Les commandes composées (dom-diff, record, emulate) sont classées
# par le VERBE de leur action; replay est mutant en bloc (le journal peut
# contenir n'importe quelle action).
ALWAYS_MUTATING = {"click", "type", "key", "eval", "intercept", "replay"}
COMPOSED_COMMANDS = {"dom-diff", "record", "emulate", "vitals"}

ACTION_ERRORS = (
    ValueError,
    TimeoutError,
    CDPError,
    CDPTimeout,
    js.JSException,
    inputs.ElementNotFound,
)

PRESETS: dict[str, dict[str, Any]] = {
    "mobile": {
        "metrics": {
            "width": 390,
            "height": 844,
            "deviceScaleFactor": 3,
            "mobile": True,
        },
        "ua": "cdpx-mobile/1.0",
    },
    "slow-3g": {
        "network": {
            "offline": False,
            "latency": 400,
            "downloadThroughput": 50 * 1024,
            "uploadThroughput": 50 * 1024,
        }
    },
    "cpu-4x": {"cpu": 4},
}


def command_mutates(command: str, action: list[str] | None = None) -> bool:
    if command == "cookies":
        return bool(action and action[0] in {"set", "clear"})
    if command in ALWAYS_MUTATING:
        return True
    if command in COMPOSED_COMMANDS:
        return bool(action and action[0] in actions.MUTATING_VERBS)
    return False


def assert_origin_allowed(
    command: str,
    current_url: str | None,
    origins: str | None,
    action: list[str] | None = None,
) -> None:
    if not origins or not command_mutates(command, action):
        return
    parsed = urllib.parse.urlparse(current_url or "")
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    allowed = [item.strip() for item in origins.split(",") if item.strip()]
    if not any(fnmatch.fnmatch(origin, pattern) for pattern in allowed):
        raise ValueError(f"mutation refusée hors CDPX_ORIGINS: {origin or current_url}")


def intercept_goto(
    client: CDPClient,
    rules: list[str],
    url: str,
    timeout: float = 30.0,
    settle: float = 0.5,
) -> dict:
    parsed_rules = [parse_intercept_rule(rule) for rule in rules]
    client.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
    client.send("Page.enable")
    navigation_id = client.send_nowait("Page.navigate", {"url": url})

    started = time.monotonic()
    last_event = time.monotonic()
    load_seen = False
    hits: list[dict] = []
    while True:
        if time.monotonic() - started > timeout:
            raise TimeoutError(f"timeout interception après {timeout}s")
        if load_seen and time.monotonic() - last_event >= settle:
            break
        try:
            ev = client.next_event(timeout=min(0.25, timeout))
        except CDPTimeout:
            continue
        last_event = time.monotonic()
        if ev["method"] == "Page.loadEventFired":
            load_seen = True
            continue
        if ev["method"] != "Fetch.requestPaused":
            continue
        params = ev.get("params", {})
        request = params.get("request", {})
        req_url = request.get("url", "")
        rule = _match_rule(parsed_rules, req_url)
        action = rule["action"] if rule else "continue"
        if action == "continue":
            client.send("Fetch.continueRequest", {"requestId": params["requestId"]})
        elif action == "block":
            client.send(
                "Fetch.failRequest",
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
            )
        elif (
            action.isascii() and len(action) == 3 and action.isdigit() and 200 <= int(action) <= 599
        ):
            status = int(action)
            body = json.dumps({"cdpx": "intercept", "status": status}).encode()
            client.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": params["requestId"],
                    "responseCode": status,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": base64.b64encode(body).decode(),
                },
            )
        else:
            raise AssertionError(f"action d'interception non validée: {action}")
        hits.append({"url": req_url, "action": action})
    navigation = client.wait_response(
        navigation_id, timeout=max(0.1, timeout - (time.monotonic() - started))
    )
    if navigation.get("errorText"):
        raise ValueError(f"navigation échouée: {navigation['errorText']}")
    return {"url": url, "rules": rules, "hits": hits, "count": len(hits), "settle": settle}


def emulate(client: CDPClient, preset: str | None = None, reset: bool = False) -> dict:
    if reset:
        client.send("Emulation.clearDeviceMetricsOverride")
        # userAgent vide = Chrome restaure l'UA par défaut (vérifié e2e); sans
        # cet appel, l'UA du preset mobile survivait au reset.
        client.send("Emulation.setUserAgentOverride", {"userAgent": ""})
        client.send(
            "Network.emulateNetworkConditions",
            {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1},
        )
        client.send("Emulation.setCPUThrottlingRate", {"rate": 1})
        return {"reset": True}
    if preset not in PRESETS:
        raise ValueError(f"preset inconnu: {preset}")
    spec = PRESETS[preset]
    client.send("Network.enable")
    if "metrics" in spec:
        client.send("Emulation.setDeviceMetricsOverride", spec["metrics"])
    if "ua" in spec:
        client.send("Emulation.setUserAgentOverride", {"userAgent": spec["ua"]})
    if "network" in spec:
        client.send("Network.emulateNetworkConditions", spec["network"])
    if "cpu" in spec:
        client.send("Emulation.setCPUThrottlingRate", {"rate": spec["cpu"]})
    return {"preset": preset, "applied": True}


def vitals(
    client: CDPClient,
    url: str,
    timeout: float = 30.0,
    click_selector: str | None = None,
    settle: float = 0.5,
    origins: str | None = None,
) -> dict:
    script = """
window.__cdpxVitals = {lcp: 0, cls: 0, inp: 0};
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    window.__cdpxVitals.lcp = Math.max(window.__cdpxVitals.lcp, e.startTime || 0);
  }
}).observe({type: 'largest-contentful-paint', buffered: true});
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (!e.hadRecentInput) window.__cdpxVitals.cls += e.value || 0;
  }
}).observe({type: 'layout-shift', buffered: true});
try {
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (e.name === 'click') {
        window.__cdpxVitals.inp = Math.max(window.__cdpxVitals.inp, e.duration || 0);
      }
    }
  }).observe({type: 'event', buffered: true, durationThreshold: 0});
} catch (e) {}
"""
    client.send("Page.addScriptToEvaluateOnNewDocument", {"source": script})
    navigation = nav.navigate(client, url, timeout=timeout)
    if navigation.get("ok") is False:
        raise ValueError(f"navigation échouée: {navigation.get('errorText') or url}")
    if click_selector:
        from cdpx.primitives import inputs

        # Une redirection peut avoir changé l'origine depuis l'URL demandée.
        # Revalider juste avant l'interaction trusted évite de cliquer ailleurs.
        current_url = (
            _require_current_http_url(client, "avant interaction vitals")
            if origins
            else (_current_http_url(client) or url)
        )
        assert_origin_allowed("click", current_url, origins)
        inputs.click(client, click_selector)
    client.collect_events(settle)
    value = js.evaluate(client, "JSON.stringify(window.__cdpxVitals || {})")
    data = json.loads(value or "{}")
    return {
        "url": url,
        "lcp": data.get("lcp", 0),
        "cls": data.get("cls", 0),
        "inp": data.get("inp", 0),
    }


def a11y(client: CDPClient) -> dict:
    res = client.send("Accessibility.getFullAXTree")
    nodes = [
        {
            "role": _ax_value(node.get("role")),
            "name": _ax_value(node.get("name")),
            "ignored": node.get("ignored", False),
        }
        for node in res.get("nodes", [])
        if not node.get("ignored", False)
    ]
    return {"nodes": nodes, "count": len(nodes)}


def coverage(client: CDPClient, url: str, timeout: float = 30.0) -> dict:
    client.send("DOM.enable")
    client.send("CSS.enable")
    client.send("Profiler.enable")
    client.send("CSS.startRuleUsageTracking")
    client.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
    navigation = nav.navigate(client, url, timeout=timeout)
    if navigation.get("ok") is False:
        raise ValueError(f"navigation échouée: {navigation.get('errorText') or url}")
    js_res = client.send("Profiler.takePreciseCoverage")
    css_res = client.send("CSS.stopRuleUsageTracking")
    client.send("Profiler.stopPreciseCoverage")
    files = []
    for item in js_res.get("result", []):
        byte_counts = _coverage_bytes(item.get("functions", []))
        total = byte_counts["total_bytes"]
        files.append(
            {
                "url": item.get("url"),
                "functions": len(item.get("functions", [])),
                "used_ranges": sum(
                    1
                    for fn in item.get("functions", [])
                    for rng in fn.get("ranges", [])
                    if (rng.get("count") or 0) > 0
                ),
                **byte_counts,
                "coverage_percent": round(byte_counts["used_bytes"] * 100 / total, 1)
                if total
                else None,
            }
        )
    css_rules = css_res.get("ruleUsage", [])
    js_totals = {
        key: sum(item[key] for item in files)
        for key in ("total_bytes", "used_bytes", "unused_bytes")
    }
    return {
        "url": url,
        "files": files,
        "count": len(files),
        "js": js_totals,
        "css": {
            "rules": len(css_rules),
            "used": sum(1 for rule in css_rules if rule.get("used")),
            "unused": sum(1 for rule in css_rules if not rule.get("used")),
        },
    }


def frame_text(client: CDPClient, selector: str) -> dict:
    expr = (
        "(() => Array.from(document.querySelectorAll('iframe')).map(f => "
        "f.contentDocument && f.contentDocument.querySelector("
        f"{json.dumps(selector)})?.innerText).find(Boolean) || null)()"
    )
    return {"selector": selector, "text": js.evaluate(client, expr)}


def record(
    client: CDPClient,
    path: str,
    action: list[str],
    *,
    run_id: str | None = None,
    redaction_context: RedactionContext | None = None,
    origins: str,
) -> dict:
    """Exécute l'action puis la journalise (résultat compris) en NDJSON.

    L'échec est journalisé (ok:false + erreur) AVANT d'être relancé: le journal
    reste la trace fidèle de ce qui s'est réellement passé.
    """
    context = redaction_context or RedactionContext()
    stored_action, replayable = journal.serialize_action(action, context=context)
    execution_action = action
    if isinstance(stored_action, dict) and stored_action.get("verb") == "type":
        input_spec = stored_action.get("input", {})
        if isinstance(input_spec, dict) and input_spec.get("secret_ref"):
            execution_action = journal.materialize_action(stored_action)
            context.register_secret(execution_action[2])
    allowed = parse_origins(origins, required=True)
    if execution_action[0] == "goto":
        assert_url_allowed(execution_action[1], allowed)
    else:
        assert_url_allowed(
            _require_current_http_url(client, "avant action record"),
            allowed,
        )
    error: Exception | None = None
    try:
        result: dict = actions.run_action(client, execution_action)
        assert_url_allowed(
            _require_current_http_url(client, "après action record"),
            allowed,
        )
        ok = True
    except ACTION_ERRORS as e:
        result = {"error": str(e)}
        ok = False
        error = e
    safe_result = _persistable_action_result(
        execution_action,
        result,
        ok=ok,
        context=context,
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
    out = Path(path)
    journal.append_event(out, event)
    if error is not None:
        raise error
    return {
        "schema": journal.SCHEMA,
        "path": str(out),
        "recorded": 1,
        "replayable": replayable,
        "ok": ok,
    }


def _persistable_action_result(
    action: list[str],
    result: dict[str, Any],
    *,
    ok: bool,
    context: RedactionContext,
) -> dict[str, Any]:
    """Ne persiste jamais une valeur ou erreur arbitraire issue de ``eval``."""
    if action[0] != "eval":
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
    origins: str,
    redaction_context: RedactionContext | None = None,
) -> dict:
    """Rejoue un journal NDJSON action par action, arrêt à la première divergence.

    Toute la validation (syntaxe, actions présentes, budget) se fait AVANT la
    première exécution: un journal invalide ne touche jamais le navigateur.
    """
    context = redaction_context or RedactionContext()
    events: list[dict] = []
    materialized_actions: list[list[str]] = []
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as e:
            return {
                "path": path,
                "ok": False,
                "events": len(events),
                "played": 0,
                "divergence": f"line {lineno}: {e.msg}",
            }
        if not isinstance(event, dict) or not isinstance(event.get("action"), list | dict):
            return {
                "path": path,
                "ok": False,
                "events": len(events),
                "played": 0,
                "divergence": f"line {lineno}: action manquante",
            }
        if event.get("schema") not in {None, journal.SCHEMA}:
            return {
                "path": path,
                "ok": False,
                "events": len(events) + 1,
                "played": 0,
                "divergence": f"line {lineno}: schema record inconnu",
            }
        if event.get("replayable") is False:
            return {
                "path": path,
                "ok": False,
                "events": len(events) + 1,
                "played": 0,
                "divergence": f"line {lineno}: action redacted non rejouable",
            }
        try:
            materialized = journal.materialize_action(event["action"])
        except (ValueError, journal.JournalError) as e:
            return {
                "path": path,
                "ok": False,
                "events": len(events) + 1,
                "played": 0,
                "divergence": f"line {lineno}: {e}",
            }
        if materialized[0] == "type":
            context.register_secret(materialized[2])
        if not isinstance(event.get("ok"), bool):
            return {
                "path": path,
                "ok": False,
                "events": len(events) + 1,
                "played": 0,
                "divergence": f"line {lineno}: ok booléen requis",
            }
        if "result" in event and not isinstance(event["result"], dict):
            return {
                "path": path,
                "ok": False,
                "events": len(events) + 1,
                "played": 0,
                "divergence": f"line {lineno}: result doit être un objet",
            }
        if "result" in event:
            event = {**event, "result": redact_tree(event["result"], context=context)}
        events.append(event)
        materialized_actions.append(materialized)
    if max_actions is not None and len(events) > max_actions:
        raise ValueError(f"budget --max-actions dépassé: {len(events)} > {max_actions}")
    for index, event in enumerate(events):
        if event["ok"] is not True:
            return {
                "path": path,
                "events": len(events),
                "played": 0,
                "ok": False,
                "divergence": f"event {index}: ok=false journalisé",
            }
    origin_patterns = parse_origins(origins, required=True)
    played = 0
    for index, (event, action) in enumerate(zip(events, materialized_actions, strict=True)):
        if action[0] == "goto":
            try:
                assert_url_allowed(action[1], origin_patterns)
            except ACTION_ERRORS as e:
                return {
                    "path": path,
                    "events": len(events),
                    "played": played,
                    "ok": False,
                    "divergence": f"event {index}: {e}",
                }
        if action[0] != "goto":
            try:
                assert_url_allowed(
                    _require_current_http_url(client, "avant action"),
                    origin_patterns,
                )
            except ACTION_ERRORS as e:
                return {
                    "path": path,
                    "events": len(events),
                    "played": played,
                    "ok": False,
                    "divergence": f"event {index}: {e}",
                }
        try:
            actual = redact_tree(actions.run_action(client, action), context=context)
        except ACTION_ERRORS as e:
            return {
                "path": path,
                "events": len(events),
                "played": played,
                "ok": False,
                "divergence": f"event {index}: {e}",
            }
        played += 1
        if action[0] == "goto":
            try:
                current_url = _require_current_http_url(client, "après navigation")
                assert_url_allowed(current_url, origin_patterns)
            except ACTION_ERRORS as e:
                return {
                    "path": path,
                    "events": len(events),
                    "played": played,
                    "ok": False,
                    "divergence": f"event {index}: {e}",
                }
        else:
            try:
                assert_url_allowed(
                    _require_current_http_url(client, "après action"),
                    origin_patterns,
                )
            except ACTION_ERRORS as e:
                return {
                    "path": path,
                    "events": len(events),
                    "played": played,
                    "ok": False,
                    "divergence": f"event {index}: destination après action: {e}",
                }
        if "result" in event:
            expected = _normalized_replay_result(event, action)
            differences = _semantic_differences(expected, actual)
            if differences:
                return {
                    "path": path,
                    "events": len(events),
                    "played": played,
                    "ok": False,
                    "divergence": {
                        "event": index,
                        "kind": "result_mismatch",
                        "differences": differences,
                    },
                }
    return {"path": path, "events": len(events), "played": played, "ok": True}


def _current_http_url(client: CDPClient) -> str | None:
    value = js.evaluate(client, "window.location.href")
    parsed = urllib.parse.urlparse(value if isinstance(value, str) else "")
    return value if parsed.scheme and parsed.netloc else None


def _require_current_http_url(client: CDPClient, phase: str) -> str:
    try:
        current_url = _current_http_url(client)
    except (ValueError, CDPError, CDPTimeout, js.JSException) as e:
        raise ValueError(f"URL courante indéterminable {phase}: {e}") from e
    if current_url is None:
        raise ValueError(f"URL courante indéterminable {phase}")
    return current_url


_VOLATILE_RESULT_KEYS = {"elapsed_ms", "frameId", "loaderId", "x", "y"}


def _normalized_replay_result(event: dict[str, Any], action: list[str]) -> Any:
    """Adapte le seul ancien contrat devenu volontairement non sensible.

    Les records v1 stockaient le texte saisi sous ``result.typed``. Depuis le
    journal v2 ce champ est un booléen et la valeur ne quitte plus le process.
    La comparaison conserve donc la compatibilité sans réintroduire le secret.
    """
    expected = event["result"]
    if (
        event.get("schema") is None
        and action[0] == "type"
        and isinstance(expected, dict)
        and isinstance(expected.get("typed"), str)
    ):
        return {**expected, "typed": True}
    return expected


def _semantic_differences(expected: Any, actual: Any, path: str = "$") -> list[dict]:
    differences: list[dict] = []
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


def _coverage_bytes(functions: list[dict]) -> dict[str, int]:
    ranges = [rng for fn in functions for rng in fn.get("ranges", [])]
    boundaries = sorted(
        {
            offset
            for rng in ranges
            for offset in (rng.get("startOffset", 0), rng.get("endOffset", 0))
        }
    )
    used = unused = 0
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        covering = [
            rng
            for rng in ranges
            if rng.get("startOffset", 0) <= start and rng.get("endOffset", 0) >= end
        ]
        if not covering:
            continue
        most_specific = min(
            covering, key=lambda rng: rng.get("endOffset", 0) - rng.get("startOffset", 0)
        )
        if (most_specific.get("count") or 0) > 0:
            used += end - start
        else:
            unused += end - start
    return {"total_bytes": used + unused, "used_bytes": used, "unused_bytes": unused}


def parse_intercept_rule(rule: str) -> dict:
    if "=>" not in rule:
        raise ValueError("règle attendue: PATTERN => ACTION")
    pattern, action = [part.strip() for part in rule.split("=>", 1)]
    if not pattern:
        raise ValueError("motif d'interception vide")
    if action not in {"continue", "block"}:
        is_status = action.isascii() and len(action) == 3 and action.isdigit()
        if not is_status or not 200 <= int(action) <= 599:
            raise ValueError("action d'interception attendue: continue, block ou statut 200..599")
    return {"pattern": pattern, "action": action}


def _match_rule(rules: list[dict], url: str) -> dict | None:
    for rule in rules:
        pattern = rule["pattern"]
        if fnmatch.fnmatch(url, pattern) or pattern in url:
            return rule
    return None


def _ax_value(value: dict | None) -> str | None:
    if isinstance(value, dict):
        return value.get("value")
    return None
