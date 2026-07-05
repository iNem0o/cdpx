"""Primitives de boucle dev Symfony/Shopware.

Ces commandes ferment la boucle agentique côté dev: lire le profiler Symfony,
suivre la console en stream et comparer un DOM avant/après action.
"""

from __future__ import annotations

import difflib
import json
import urllib.parse
import urllib.request

from cdpx.client import CDPClient
from cdpx.primitives import inputs, js

PROFILER_HEADER = "x-debug-token-link"
TOKEN_HEADER = "x-debug-token"
NET_EVENTS = ("Network.responseReceived",)
SCENARIO_SIGNAL_HEADERS = {
    "x-cdpx-scenario": ("scenario", str),
    "x-cdpx-profiler-time-ms": ("time_ms", int),
    "x-cdpx-profiler-memory-kb": ("memory_kb", int),
    "x-cdpx-profiler-db-queries": ("db_queries", int),
    "x-cdpx-profiler-db-duplicate-queries": ("db_duplicate_queries", int),
    "x-cdpx-profiler-cache-hit": ("cache_hit", lambda value: value == "1"),
    "x-cdpx-profiler-cache-state": ("cache_state", str),
    "x-cdpx-profiler-payload-bytes": ("payload_bytes", int),
    "x-cdpx-profiler-twig-renders": ("twig_renders", int),
    "x-cdpx-profiler-twig-render-ms": ("twig_render_ms", int),
    "x-cdpx-profiler-stopwatch-sections": ("stopwatch_sections", int),
    "x-cdpx-profiler-http-client": ("http_client", str),
    "x-cdpx-profiler-http-client-ms": ("http_client_ms", int),
    "x-cdpx-profiler-messenger": ("messenger", str),
    "x-cdpx-profiler-queue-depth": ("queue_depth", int),
    "x-cdpx-profiler-route-outcome": ("route_outcome", str),
    "x-cdpx-profiler-response-status": ("response_status", int),
    "x-cdpx-profiler-expected": ("expected", str),
}

DOM_SNAPSHOT_JS = r"""
(() => { const __cdpx_dom_snapshot = 1;
  const attrs = (el) => {
    const out = [];
    if (el.id) out.push(`#${el.id}`);
    if (el.classList && el.classList.length) {
      out.push('.' + Array.from(el.classList).sort().join('.'));
    }
    Array.from(el.attributes || [])
      .filter(a => a.name.startsWith('data-'))
      .sort((a, b) => a.name.localeCompare(b.name))
      .forEach(a => out.push(`[${a.name}="${a.value}"]`));
    return out.join('');
  };
  const line = (el, depth) => `${'  '.repeat(depth)}<${el.tagName.toLowerCase()}${attrs(el)}>`;
  const walk = (el, depth, acc) => {
    acc.push(line(el, depth));
    Array.from(el.children).forEach(child => walk(child, depth + 1, acc));
    const text = Array.from(el.childNodes)
      .filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent.trim())
      .filter(Boolean)
      .join(' ');
    if (text) acc.push(`${'  '.repeat(depth + 1)}"${text}"`);
    return acc;
  };
  return JSON.stringify(walk(document.body || document.documentElement, 0, []));
})()
"""


def profiler(client: CDPClient, url: str, timeout: float = 30.0, settle: float = 0.2) -> dict:
    """Navigue, trouve X-Debug-Token-Link et récupère le profiler côté cdpx."""
    client.send("Network.enable")
    client.send("Page.enable")
    client.send("Page.navigate", {"url": url}, timeout=timeout)
    client.wait_event("Page.loadEventFired", timeout=timeout)
    events = client.collect_events(settle, NET_EVENTS)

    hit = None
    for ev in events:
        response = ev.get("params", {}).get("response", {})
        headers = {str(k).lower(): v for k, v in response.get("headers", {}).items()}
        if PROFILER_HEADER in headers or TOKEN_HEADER in headers:
            link = headers.get(PROFILER_HEADER)
            if not link:
                parsed = urllib.parse.urlparse(response.get("url") or url)
                link = f"{parsed.scheme}://{parsed.netloc}/_profiler/{headers[TOKEN_HEADER]}"
            hit = {
                "url": response.get("url"),
                "status": response.get("status"),
                "link": link,
                "headers": headers,
            }
            break
    if not hit:
        raise ValueError("header X-Debug-Token-Link/X-Debug-Token introuvable")

    req = urllib.request.Request(hit["link"], headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read()
        content_type = res.headers.get("Content-Type", "")

    token = hit["link"].rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
    out = {
        "token": token,
        "url": hit["url"],
        "status": hit["status"],
        "profiler_url": hit["link"],
        "profiler_status": 200,
        "profiler_bytes": len(body),
        "response_headers": hit["headers"],
        "signals": _scenario_signals(hit["headers"]),
    }
    if "json" in content_type:
        out["panels"] = json.loads(body.decode("utf-8"))
    else:
        out["panels"] = {"raw": {"content_type": content_type, "bytes": len(body)}}
    return out


def _scenario_signals(headers: dict[str, object]) -> dict:
    signals = {}
    for header, (name, caster) in SCENARIO_SIGNAL_HEADERS.items():
        if header not in headers:
            continue
        raw = str(headers[header])
        try:
            signals[name] = caster(raw)
        except ValueError:
            signals[name] = raw
    return signals


def dom_diff(client: CDPClient, action: list[str]) -> dict:
    before = _snapshot(client)
    _run_action(client, action)
    after = _snapshot(client)
    diff = list(difflib.unified_diff(before, after, fromfile="before", tofile="after", lineterm=""))
    return {
        "action": action,
        "changed": before != after,
        "diff": diff,
        "lines": len(diff),
    }


def _snapshot(client: CDPClient) -> list[str]:
    return json.loads(js.evaluate(client, DOM_SNAPSHOT_JS))


def _run_action(client: CDPClient, action: list[str]) -> None:
    if not action:
        raise ValueError("action dom-diff manquante")
    name, rest = action[0], action[1:]
    if name == "click" and len(rest) == 1:
        inputs.click(client, rest[0])
    elif name == "type" and len(rest) >= 2:
        clear = "--clear" in rest[2:]
        inputs.type_text(client, rest[0], rest[1], clear=clear)
    elif name == "key" and len(rest) == 1:
        inputs.press_key(client, rest[0])
    elif name == "eval" and rest:
        js.evaluate(client, " ".join(rest), await_promise=True)
    else:
        raise ValueError(
            "action dom-diff supportée: click <sel>, type <sel> <txt>, key <k>, eval <js>"
        )
