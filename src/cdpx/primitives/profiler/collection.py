"""CDP collection and security boundary for Symfony profiler panels."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.policy import assert_url_allowed, origin_from_url
from cdpx.primitives import js
from cdpx.security import RedactionContext, redact_headers, redact_text, redact_url

from .adapters import detect_extensions
from .catalog import ALL_PANELS, LIST_LIMIT, PANEL_SOURCE_CANDIDATES, PANEL_SOURCES
from .parsers import parse_panel

# The __cdpx_profiler_panels marker is used for scripting the mock CDP (on_eval).
PANEL_FETCH_JS = """
(async () => { const __cdpx_profiler_panels = 1;
  const base = %s;
  const targets = %s;
  const deadline = performance.now() + %d;
  const fetchOne = async (panel, source) => {
    const url = new URL(base);
    url.search = '';
    url.hash = '';
    url.searchParams.set('panel', source);
    if (panel === 'db') url.searchParams.set('group', 'true');
    const remaining = Math.ceil(deadline - performance.now());
    if (remaining <= 0) {
      return {panel, source, status: 0, html: '', error: 'panel fetch timeout'};
    }
    try {
      const res = await fetch(url, {
        headers: {Accept: 'text/html'},
        credentials: 'same-origin',
        signal: AbortSignal.timeout(remaining),
      });
      return {panel, source, status: res.status, html: await res.text()};
    } catch (e) {
      return {panel, source, status: 0, html: '', error: String(e)};
    }
  };
  const probe = await fetchOne('router', 'request');
  let collectors = [];
  if (probe.status === 200 && probe.html) {
    const doc = new DOMParser().parseFromString(probe.html, 'text/html');
    collectors = Array.from(doc.querySelectorAll('a[href]'))
      .map((link) => {
        try {
          return new URL(link.getAttribute('href'), base).searchParams.get('panel');
        } catch (_) {
          return null;
        }
      })
      .filter((value, index, values) => value && values.indexOf(value) === index);
  }
  const probeUsable = probe.status === 200 && collectors.length > 0;
  const one = async ([panel, candidates]) => {
    const advertised = candidates.filter((source) => collectors.includes(source));
    const sources = probeUsable ? advertised : candidates;
    if (panel === 'router' && sources[0] === 'request' && probe.status === 200) {
      return {...probe, panel, source: 'request'};
    }
    if (sources.length === 0) return {panel, status: 0, html: ''};
    let result = {panel, status: 0, html: ''};
    for (const source of sources) {
      result = await fetchOne(panel, source);
      if (result.status === 200) return result;
    }
    return result;
  };
  return JSON.stringify({
    probe: {status: probe.status, usable: probeUsable, collectors},
    panels: await Promise.all(targets.map(one)),
  });
})()
"""


def normalize_panels(panels: Sequence[str] | None) -> list[str]:
    """Validates a list of requested panels (None -> all)."""
    if panels is None:
        return list(ALL_PANELS)
    unknown = [p for p in panels if p not in PANEL_SOURCES]
    if unknown:
        raise ValueError(
            f"unknown panel(s): {', '.join(unknown)} (choices: {', '.join(ALL_PANELS)})"
        )
    return list(panels)


def fetch_panels(
    client: CDPClient, profiler_url: str, panels: list[str], timeout: float = 30.0
) -> list[dict[str, Any]]:
    """Compatibility wrapper returning only fetched panel envelopes."""
    return fetch_profiler(client, profiler_url, panels, timeout)["panels"]


def fetch_profiler(
    client: CDPClient, profiler_url: str, panels: list[str], timeout: float = 30.0
) -> dict[str, Any]:
    """Probes collector IDs and fetches selected HTML via the assigned page."""
    base = profiler_url.split("?", 1)[0].split("#", 1)[0]
    targets = [[key, list(PANEL_SOURCE_CANDIDATES[key])] for key in panels]
    expr = PANEL_FETCH_JS % (
        json.dumps(base),
        json.dumps(targets, separators=(",", ":")),
        int(timeout * 1000),
    )
    raw = js.evaluate(client, expr, await_promise=True)
    if not isinstance(raw, str):
        return _empty_fetch()
    fetched = json.loads(raw)
    # Existing scripted mocks may still return the pre-probe list envelope.
    if isinstance(fetched, list):
        return {"probe": _empty_probe(), "panels": fetched}
    if not isinstance(fetched, dict):
        return _empty_fetch()
    probe = fetched.get("probe")
    panel_items = fetched.get("panels")
    return {
        "probe": probe if isinstance(probe, dict) else _empty_probe(),
        "panels": panel_items if isinstance(panel_items, list) else [],
    }


def _empty_probe() -> dict[str, Any]:
    return {"status": 0, "usable": False, "collectors": []}


def _empty_fetch() -> dict[str, Any]:
    return {"probe": _empty_probe(), "panels": []}


def collect_profiler_report(
    client: CDPClient,
    hit: dict[str, Any],
    *,
    context: OrchestrationContext,
    panels: list[str] | None = None,
    timeout: float = 30.0,
    page_url: str | None = None,
) -> dict[str, Any]:
    """Complete `cdpx profiler` contract built from an X-Debug-Token(-Link) hit.

    `hit` comes from dev.find_profiler_hit: {url, status, link, headers}.
    """
    keys = normalize_panels(panels)
    link = _validated_profiler_link(
        hit,
        allowed_origins=context.origins,
        page_url=page_url,
    )
    token = link.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    redaction = context.redaction
    redaction.register_secret(token)
    profiler_url = redact_text(
        redact_url(link, context=redaction, path="$.profiler_url"),
        context=redaction,
        path="$.profiler_url",
    )
    hit_url = hit.get("url")
    if isinstance(hit_url, str):
        hit_url = redact_text(
            redact_url(hit_url, context=redaction, path="$.url"),
            context=redaction,
            path="$.url",
        )
    headers = hit.get("headers")
    out: dict[str, Any] = {
        "token_present": bool(token),
        "url": hit_url,
        "status": hit["status"],
        "profiler_url": profiler_url,
        "profiler_status": None,
        "response_headers": redact_headers(
            headers if isinstance(headers, Mapping) else {},
            context=redaction,
            path="$.response_headers",
        ),
        "profile": _profile([], probed=False, context=redaction),
        "panels": {},
    }
    if not keys:
        return out
    collection = fetch_profiler(client, link, keys, timeout)
    probe = collection["probe"]
    collector_ids = [item for item in probe.get("collectors", []) if isinstance(item, str)]
    out["profile"] = _profile(
        collector_ids,
        probed=bool(probe.get("usable")),
        context=redaction,
    )
    fetched = {item.get("panel"): item for item in collection["panels"]}
    first = fetched.get(keys[0])
    if first is not None:
        out["profiler_status"] = first.get("status")
    for key in keys:
        item = fetched.get(key) or {"status": 0, "html": ""}
        out["panels"][key] = parse_panel(key, int(item.get("status") or 0), item.get("html") or "")
    return out


def _profile(
    collector_ids: list[str], *, probed: bool, context: RedactionContext
) -> dict[str, Any]:
    total = len(collector_ids) if probed else None
    cleaned = [
        redact_text(item, context=context, path=f"$.profile.collectors.items[{index}]")
        for index, item in enumerate(collector_ids[:LIST_LIMIT])
    ]
    return {
        "engine": "symfony_web_profiler",
        "probed": probed,
        "extensions": detect_extensions(collector_ids) if probed else [],
        "collectors": {
            "items": cleaned,
            "total": total,
            "truncated": bool(probed and total is not None and total > len(cleaned)),
        },
    }


def _validated_profiler_link(
    hit: Mapping[str, Any],
    *,
    allowed_origins: tuple[str, ...] | None,
    page_url: str | None,
) -> str:
    raw_link = hit.get("link")
    if not isinstance(raw_link, str) or not raw_link.strip():
        raise ValueError("missing or invalid profiler link")
    hit_url = hit.get("url")
    base_url = hit_url if isinstance(hit_url, str) and hit_url else page_url
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("unable to determine the profiler's trusted origin")
    trust_url = page_url or base_url
    origins = allowed_origins or (origin_from_url(trust_url),)
    assert_url_allowed(trust_url, origins)
    resolved = urllib.parse.urljoin(base_url, raw_link)
    assert_url_allowed(resolved, origins)
    return resolved
