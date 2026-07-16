"""Primitives de boucle dev Symfony/Shopware.

Ces commandes ferment la boucle agentique côté dev: lire le profiler Symfony,
suivre la console en stream et comparer un DOM avant/après action.
"""

from __future__ import annotations

import difflib
import json
import urllib.parse

from cdpx.action_model import BrowserAction, action_argv
from cdpx.cdp_types import CDPEvent
from cdpx.client import CDPClient, validate_time_budget
from cdpx.orchestration import OrchestrationContext
from cdpx.policy import assert_url_allowed
from cdpx.primitives import actions, js, nav
from cdpx.primitives import profiler as profiler_api

PROFILER_HEADER = "x-debug-token-link"
TOKEN_HEADER = "x-debug-token"
# requestWillBeSent est nécessaire pour les redirections: Chrome n'émet PAS de
# responseReceived pour une 302, elle n'existe que dans redirectResponse.
NET_EVENTS = ("Network.responseReceived", "Network.requestWillBeSent")

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


def find_profiler_hit(events: list[CDPEvent], fallback_url: str) -> dict | None:
    """Première réponse réseau portant X-Debug-Token-Link (ou repli
    X-Debug-Token, dont on reconstruit le lien /_profiler/{token}).

    Couvre les réponses normales (Network.responseReceived) ET les
    redirections (Network.requestWillBeSent.redirectResponse), invisibles
    autrement.
    """
    for ev in events:
        params = ev.get("params", {})
        response = params.get("response") or params.get("redirectResponse") or {}
        headers = {str(k).lower(): v for k, v in response.get("headers", {}).items()}
        if PROFILER_HEADER not in headers and TOKEN_HEADER not in headers:
            continue
        link = headers.get(PROFILER_HEADER)
        if not link:
            parsed = urllib.parse.urlparse(response.get("url") or fallback_url)
            link = f"{parsed.scheme}://{parsed.netloc}/_profiler/{headers[TOKEN_HEADER]}"
        return {
            "url": response.get("url"),
            "status": response.get("status"),
            "link": link,
            "headers": headers,
        }
    return None


def profiler(
    client: CDPClient,
    url: str,
    timeout: float = 30.0,
    settle: float = 0.2,
    panels: list[str] | None = None,
    context: OrchestrationContext | None = None,
) -> dict:
    """Navigue, trouve X-Debug-Token-Link et parse les panels du Web Profiler.

    `panels=None` = tous les panels connus; `panels=[]` = sonde token seule.
    """
    timeout = validate_time_budget(timeout, "timeout profiler")
    settle = validate_time_budget(settle, "stabilisation profiler")
    keys = profiler_api.normalize_panels(panels)
    policy = context or OrchestrationContext.for_url(url)
    assert_url_allowed(url, policy.origins)
    client.send("Network.enable")
    client.send("Page.enable")
    navigation = client.send("Page.navigate", {"url": url}, timeout=timeout)
    nav.raise_for_navigation_error(navigation, url, wait="load")
    client.wait_event("Page.loadEventFired", timeout=timeout)
    events = client.collect_events(settle, NET_EVENTS)

    final_url = js.evaluate(client, "window.location.href")
    if not isinstance(final_url, str):
        raise ValueError("URL finale du profiler indéterminable")
    assert_url_allowed(final_url, policy.origins)

    hit = find_profiler_hit(events, url)
    if not hit:
        raise ValueError("header X-Debug-Token-Link/X-Debug-Token introuvable")
    return profiler_api.collect_profiler_report(
        client,
        hit,
        panels=keys,
        timeout=timeout,
        context=policy,
        page_url=final_url,
    )


def dom_diff(client: CDPClient, action: BrowserAction) -> dict:
    before = _snapshot(client)
    actions.run_action(client, action)
    after = _snapshot(client)
    diff = list(difflib.unified_diff(before, after, fromfile="before", tofile="after", lineterm=""))
    return {
        "action": action_argv(action),
        "changed": before != after,
        "diff": diff,
        "lines": len(diff),
    }


def _snapshot(client: CDPClient) -> list[str]:
    return json.loads(js.evaluate(client, DOM_SNAPSHOT_JS))
