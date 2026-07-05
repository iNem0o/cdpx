"""Primitives avancées M3-M5: interception, émulation, mesure et orchestration."""

from __future__ import annotations

import base64
import fnmatch
import json
import time
import urllib.parse
from pathlib import Path

from cdpx.client import CDPClient, CDPTimeout
from cdpx.primitives import js, nav

# Commandes qui mutent la page: refusées hors CDPX_ORIGINS. dom-diff exécute
# de vraies actions (click/type/key/eval). record/replay rejoindront le set
# quand leur rejeu ouvrira une connexion navigateur.
MUTATING_COMMANDS = {"click", "type", "key", "eval", "intercept", "dom-diff"}

PRESETS = {
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


def assert_origin_allowed(command: str, current_url: str | None, origins: str | None) -> None:
    if command not in MUTATING_COMMANDS or not origins:
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
    parsed_rules = [_parse_rule(rule) for rule in rules]
    client.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
    client.send("Page.enable")
    client.send_nowait("Page.navigate", {"url": url})

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
        if action == "block":
            client.send(
                "Fetch.failRequest",
                {"requestId": params["requestId"], "errorReason": "BlockedByClient"},
            )
        elif action.isdigit():
            body = json.dumps({"cdpx": "intercept", "status": int(action)}).encode()
            client.send(
                "Fetch.fulfillRequest",
                {
                    "requestId": params["requestId"],
                    "responseCode": int(action),
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": base64.b64encode(body).decode(),
                },
            )
        else:
            client.send("Fetch.continueRequest", {"requestId": params["requestId"]})
        hits.append({"url": req_url, "action": action})
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
    nav.navigate(client, url, timeout=timeout)
    if click_selector:
        from cdpx.primitives import inputs

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
    nav.navigate(client, url, timeout=timeout)
    js_res = client.send("Profiler.takePreciseCoverage")
    css_res = client.send("CSS.stopRuleUsageTracking")
    client.send("Profiler.stopPreciseCoverage")
    files = [
        {
            "url": item.get("url"),
            "functions": len(item.get("functions", [])),
            "used_ranges": sum(
                1
                for fn in item.get("functions", [])
                for rng in fn.get("ranges", [])
                if (rng.get("count") or 0) > 0
            ),
        }
        for item in js_res.get("result", [])
    ]
    css_rules = css_res.get("ruleUsage", [])
    return {
        "url": url,
        "files": files,
        "count": len(files),
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


def record(path: str, action: list[str]) -> dict:
    event = {"action": action, "ok": True, "ts": round(time.time(), 3)}
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"path": str(out), "recorded": 1}


def replay(path: str, max_actions: int | None = None) -> dict:
    events = []
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
                "divergence": f"line {lineno}: {e.msg}",
            }
        if not isinstance(event.get("action"), list):
            return {
                "path": path,
                "ok": False,
                "events": len(events),
                "divergence": f"line {lineno}: action manquante",
            }
        events.append(event)
    if max_actions is not None and len(events) > max_actions:
        raise ValueError(f"budget --max-actions dépassé: {len(events)} > {max_actions}")
    for index, event in enumerate(events):
        if event.get("ok") is not True:
            return {
                "path": path,
                "events": len(events),
                "ok": False,
                "divergence": f"event {index}: ok=false",
            }
    return {"path": path, "events": len(events), "ok": True}


def _parse_rule(rule: str) -> dict:
    if "=>" not in rule:
        raise ValueError("règle attendue: PATTERN => ACTION")
    pattern, action = [part.strip() for part in rule.split("=>", 1)]
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
