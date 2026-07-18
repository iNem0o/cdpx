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
from cdpx.security import redact_headers, redact_text, redact_url

from .catalog import ALL_PANELS, PANEL_SOURCES
from .parsers import parse_panel

# The __cdpx_profiler_panels marker is used for scripting the mock CDP (on_eval).
PANEL_FETCH_JS = """
(async () => { const __cdpx_profiler_panels = 1;
  const targets = %s;
  const one = async ([panel, url]) => {
    try {
      const res = await fetch(url, {
        headers: {Accept: 'text/html'},
        credentials: 'same-origin',
        signal: AbortSignal.timeout(%d),
      });
      const html = await res.text();
      return {panel, status: res.status, html};
    } catch (e) {
      return {panel, status: 0, html: '', error: String(e)};
    }
  };
  return JSON.stringify(await Promise.all(targets.map(one)));
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
    """Fetches the HTML of the requested panels via fetch() in the page."""
    base = profiler_url.split("?", 1)[0].split("#", 1)[0]
    targets = [[key, f"{base}?panel={PANEL_SOURCES[key]}"] for key in panels]
    expr = PANEL_FETCH_JS % (json.dumps(targets), int(timeout * 1000))
    raw = js.evaluate(client, expr, await_promise=True)
    if not isinstance(raw, str):
        return []
    fetched = json.loads(raw)
    return fetched if isinstance(fetched, list) else []


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
        "panels": {},
    }
    if not keys:
        return out
    fetched = {item.get("panel"): item for item in fetch_panels(client, link, keys, timeout)}
    first = fetched.get(keys[0])
    if first is not None:
        out["profiler_status"] = first.get("status")
    for key in keys:
        item = fetched.get(key) or {"status": 0, "html": ""}
        out["panels"][key] = parse_panel(key, int(item.get("status") or 0), item.get("html") or "")
    return out


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
