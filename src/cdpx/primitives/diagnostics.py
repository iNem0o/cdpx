"""Browser diagnostics: Web Vitals, accessibility, and coverage."""

from __future__ import annotations

import json
from typing import Any

from cdpx.client import CDPClient, validate_time_budget
from cdpx.policy import assert_url_allowed, parse_origins
from cdpx.primitives import actions, inputs, js, nav

MAX_CLS_ENTRIES = 50
MAX_CLS_SOURCES_PER_ENTRY = 5

VITALS_OBSERVER_SCRIPT = f"""
(() => {{
  const MAX_ENTRIES = {MAX_CLS_ENTRIES};
  const MAX_SOURCES = {MAX_CLS_SOURCES_PER_ENTRY};
  const number = (value) => Number.isFinite(value) ? value : 0;
  const rect = (value) => value ? {{
    x: number(value.x),
    y: number(value.y),
    width: number(value.width),
    height: number(value.height)
  }} : null;
  const node = (value) => {{
    if (!value || value.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = String(value.tagName || '').toLowerCase().slice(0, 32);
    const id = String(value.id || '').slice(0, 120);
    const classes = Array.from(value.classList || [])
      .slice(0, 5)
      .map((item) => String(item).slice(0, 80));
    const selector = (id ? `#${{CSS.escape(id)}}` : [tag, ...classes.map(
      (item) => `.${{CSS.escape(item)}}`
    )].join('')).slice(0, 240);
    return {{tag, id, classes, selector}};
  }};
  const source = (value) => ({{
    node: node(value && value.node),
    previous_rect: rect(value && value.previousRect),
    current_rect: rect(value && value.currentRect)
  }});
  const shift = (entry) => {{
    const sources = Array.from(entry.sources || []);
    return {{
      value: number(entry.value),
      start_time: number(entry.startTime),
      duration: number(entry.duration),
      had_recent_input: Boolean(entry.hadRecentInput),
      sources: sources.slice(0, MAX_SOURCES).map(source),
      source_count: sources.length,
      sources_truncated: sources.length > MAX_SOURCES
    }};
  }};
  const snapshot = (value) => value ? {{
    value: value.value,
    start_time: value.start_time,
    end_time: value.last_time,
    duration: Math.max(0, value.last_time - value.start_time),
    entry_count: value.entry_count,
    entries: value.entries.slice(),
    entries_truncated: value.entry_count > value.entries.length
  }} : null;

  const state = window.__cdpxVitals = {{
    lcp: 0,
    cls: 0,
    raw_sum: 0,
    inp: 0,
    total_entries: 0,
    ignored_recent_input: 0,
    winning_window: null
  }};
  let current = null;

  new PerformanceObserver((list) => {{
    for (const entry of list.getEntries()) {{
      state.lcp = Math.max(state.lcp, number(entry.startTime));
    }}
  }}).observe({{type: 'largest-contentful-paint', buffered: true}});

  new PerformanceObserver((list) => {{
    for (const entry of list.getEntries()) {{
      if (entry.hadRecentInput) {{
        state.ignored_recent_input += 1;
        continue;
      }}
      const value = number(entry.value);
      state.raw_sum += value;
      state.total_entries += 1;
      if (
        current &&
        entry.startTime - current.last_time < 1000 &&
        entry.startTime - current.start_time < 5000
      ) {{
        current.value += value;
        current.last_time = number(entry.startTime);
      }} else {{
        current = {{
          value,
          start_time: number(entry.startTime),
          last_time: number(entry.startTime),
          entry_count: 0,
          entries: []
        }};
      }}
      current.entry_count += 1;
      if (current.entries.length < MAX_ENTRIES) current.entries.push(shift(entry));
      if (!state.winning_window || current.value > state.winning_window.value) {{
        state.cls = current.value;
        state.winning_window = snapshot(current);
      }}
    }}
  }}).observe({{type: 'layout-shift', buffered: true}});

  try {{
    new PerformanceObserver((list) => {{
      for (const entry of list.getEntries()) {{
        if (entry.name === 'click') {{
          state.inp = Math.max(state.inp, number(entry.duration));
        }}
      }}
    }}).observe({{type: 'event', buffered: true, durationThreshold: 0}});
  }} catch (error) {{}}
}})();
"""


def install_vitals_observer(client: CDPClient) -> None:
    """Install the bounded collector before the next document is created."""
    client.send(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": VITALS_OBSERVER_SCRIPT},
    )


def collect_vitals(client: CDPClient, settle: float = 0.5) -> dict[str, Any]:
    """Read the current document's attributed Web Vitals snapshot."""
    settle = validate_time_budget(settle, "vitals settle")
    client.collect_events(settle)
    value = js.evaluate(client, "JSON.stringify(window.__cdpxVitals || {})")
    try:
        data = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("vitals collector returned invalid JSON") from error
    return _normalize_vitals(data)


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
    install_vitals_observer(client)
    nav.navigate(client, url, timeout=timeout)
    if click_selector:
        if origins:
            current_url = actions.require_current_http_url(client, "before vitals interaction")
            assert_url_allowed(current_url, parse_origins(origins, required=True))
        inputs.click(client, click_selector)
    return {"url": url, **collect_vitals(client, settle)}


def _normalize_vitals(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {
        "lcp": _number(data.get("lcp")),
        "cls": _number(data.get("cls")),
        "raw_sum": _number(data.get("raw_sum", data.get("cls"))),
        "inp": _number(data.get("inp")),
        "total_entries": _integer(data.get("total_entries")),
        "ignored_recent_input": _integer(data.get("ignored_recent_input")),
        "winning_window": _normalize_cls_window(data.get("winning_window")),
    }
    return result


def _normalize_cls_window(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_entries = value.get("entries")
    entries = raw_entries if isinstance(raw_entries, list) else []
    normalized = [_normalize_cls_entry(entry) for entry in entries[:MAX_CLS_ENTRIES]]
    return {
        "value": _number(value.get("value")),
        "start_time": _number(value.get("start_time")),
        "end_time": _number(value.get("end_time")),
        "duration": _number(value.get("duration")),
        "entry_count": _integer(value.get("entry_count")),
        "entries": normalized,
        "entries_truncated": bool(value.get("entries_truncated")) or len(entries) > MAX_CLS_ENTRIES,
    }


def _normalize_cls_entry(value: Any) -> dict[str, Any]:
    entry = value if isinstance(value, dict) else {}
    raw_sources = entry.get("sources")
    sources = raw_sources if isinstance(raw_sources, list) else []
    return {
        "value": _number(entry.get("value")),
        "start_time": _number(entry.get("start_time")),
        "duration": _number(entry.get("duration")),
        "had_recent_input": bool(entry.get("had_recent_input")),
        "sources": [
            _normalize_cls_source(source) for source in sources[:MAX_CLS_SOURCES_PER_ENTRY]
        ],
        "source_count": _integer(entry.get("source_count")),
        "sources_truncated": bool(entry.get("sources_truncated"))
        or len(sources) > MAX_CLS_SOURCES_PER_ENTRY,
    }


def _normalize_cls_source(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "node": _normalize_cls_node(source.get("node")),
        "previous_rect": _normalize_rect(source.get("previous_rect")),
        "current_rect": _normalize_rect(source.get("current_rect")),
    }


def _normalize_cls_node(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    classes = value.get("classes")
    return {
        "tag": _bounded_string(value.get("tag"), 32),
        "id": _bounded_string(value.get("id"), 120),
        "classes": [
            _bounded_string(item, 80) for item in (classes if isinstance(classes, list) else [])[:5]
        ],
        "selector": _bounded_string(value.get("selector"), 240),
    }


def _normalize_rect(value: Any) -> dict[str, float | int] | None:
    if not isinstance(value, dict):
        return None
    return {key: _number(value.get(key)) for key in ("x", "y", "width", "height")}


def _number(value: Any) -> float | int:
    return value if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _bounded_string(value: Any, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


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
