"""Browser diagnostics: Web Vitals, accessibility, and coverage."""

from __future__ import annotations

import json
from typing import Any

from cdpx.client import CDPClient, validate_time_budget
from cdpx.policy import assert_url_allowed, parse_origins
from cdpx.primitives import actions, inputs, js, nav


def vitals(
    client: CDPClient,
    url: str,
    timeout: float = 30.0,
    click_selector: str | None = None,
    settle: float = 0.5,
    origins: str | None = None,
) -> dict[str, Any]:
    timeout = validate_time_budget(timeout, "vitals timeout")
    settle = validate_time_budget(settle, "vitals settle")
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
        if origins:
            current_url = actions.require_current_http_url(client, "before vitals interaction")
            assert_url_allowed(current_url, parse_origins(origins, required=True))
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


def a11y(client: CDPClient) -> dict[str, Any]:
    response = client.send("Accessibility.getFullAXTree")
    nodes = [
        {
            "role": _ax_value(node.get("role")),
            "name": _ax_value(node.get("name")),
            "ignored": node.get("ignored", False),
        }
        for node in response.get("nodes", [])
        if not node.get("ignored", False)
    ]
    return {"nodes": nodes, "count": len(nodes)}


def coverage(client: CDPClient, url: str, timeout: float = 30.0) -> dict[str, Any]:
    client.send("DOM.enable")
    client.send("CSS.enable")
    client.send("Profiler.enable")
    client.send("CSS.startRuleUsageTracking")
    client.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
    nav.navigate(client, url, timeout=timeout)
    js_response = client.send("Profiler.takePreciseCoverage")
    css_response = client.send("CSS.stopRuleUsageTracking")
    client.send("Profiler.stopPreciseCoverage")
    files = []
    for item in js_response.get("result", []):
        byte_counts = _coverage_bytes(item.get("functions", []))
        total = byte_counts["total_bytes"]
        files.append(
            {
                "url": item.get("url"),
                "functions": len(item.get("functions", [])),
                "used_ranges": sum(
                    1
                    for function in item.get("functions", [])
                    for byte_range in function.get("ranges", [])
                    if (byte_range.get("count") or 0) > 0
                ),
                **byte_counts,
                "coverage_percent": (
                    round(byte_counts["used_bytes"] * 100 / total, 1) if total else None
                ),
            }
        )
    css_rules = css_response.get("ruleUsage", [])
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


def _coverage_bytes(functions: list[dict[str, Any]]) -> dict[str, int]:
    ranges = [byte_range for function in functions for byte_range in function.get("ranges", [])]
    boundaries = sorted(
        {
            offset
            for byte_range in ranges
            for offset in (
                byte_range.get("startOffset", 0),
                byte_range.get("endOffset", 0),
            )
        }
    )
    used = unused = 0
    for start, end in zip(boundaries, boundaries[1:], strict=False):
        covering = [
            byte_range
            for byte_range in ranges
            if byte_range.get("startOffset", 0) <= start and byte_range.get("endOffset", 0) >= end
        ]
        if not covering:
            continue
        most_specific = min(
            covering,
            key=lambda byte_range: (
                byte_range.get("endOffset", 0) - byte_range.get("startOffset", 0)
            ),
        )
        if (most_specific.get("count") or 0) > 0:
            used += end - start
        else:
            unused += end - start
    return {"total_bytes": used + unused, "used_bytes": used, "unused_bytes": unused}


def _ax_value(value: dict[str, Any] | None) -> str | None:
    if isinstance(value, dict):
        result = value.get("value")
        return result if isinstance(result, str) else None
    return None
