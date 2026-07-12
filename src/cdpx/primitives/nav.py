"""Primitives de navigation.

Usecase agent: se déplacer dans l'app en cours de dev (front Symfony, back
Shopware/PrestaShop) et SAVOIR quand la page est réellement chargée avant
d'observer quoi que ce soit — sinon l'agent lit des états intermédiaires.
"""

from __future__ import annotations

import json
import time

from cdpx.client import CDPClient, CDPTimeout

WAIT_EVENTS = {
    "load": "Page.loadEventFired",
    "domcontentloaded": "Page.domContentEventFired",
}


def navigate(client: CDPClient, url: str, wait: str = "load", timeout: float = 30.0) -> dict:
    """Navigue et attend l'évènement de cycle de vie demandé (load|domcontentloaded|none)."""
    client.send("Page.enable")
    started = time.monotonic()
    res = client.send("Page.navigate", {"url": url}, timeout=timeout)
    out = {
        "url": url,
        "frameId": res.get("frameId"),
        "loaderId": res.get("loaderId"),
        "errorText": res.get("errorText"),
        "waited": wait,
    }
    if res.get("errorText"):
        out["ok"] = False
        return out
    if wait in WAIT_EVENTS:
        client.wait_event(WAIT_EVENTS[wait], timeout=timeout)
    out["ok"] = True
    out["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return out


def wait_for(client: CDPClient, selector: str, timeout: float = 10.0, poll: float = 0.05) -> dict:
    """Attend qu'un sélecteur CSS existe dans le DOM (polling Runtime.evaluate).

    Pourquoi polling plutôt que MutationObserver injecté: zéro état résiduel
    dans la page, comportement identique quel que soit le moment où on arrive.
    """
    expr = f"!!document.querySelector({json.dumps(selector)})"
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while True:
        res = client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if res.get("result", {}).get("value") is True:
            return {
                "found": True,
                "selector": selector,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }
        if time.monotonic() >= deadline:
            raise CDPTimeout(f"sélecteur introuvable après {timeout}s: {selector}")
        time.sleep(poll)


def wait_for_visible(
    client: CDPClient,
    selector: str,
    timeout: float = 10.0,
    poll: float = 0.05,
) -> dict:
    """Attend un élément attaché, rendu et doté d'une boîte non nulle."""
    expr = (
        "(() => {"
        f"const el = document.querySelector({json.dumps(selector)});"
        "if (!el || !el.isConnected) return false;"
        "const style = window.getComputedStyle(el);"
        'if (style.display === "none" || '
        'style.visibility === "hidden" || '
        'style.visibility === "collapse") return false;'
        "const rect = el.getBoundingClientRect();"
        "return rect.width > 0 && rect.height > 0;"
        "})() /* __cdpx_visible */"
    )
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    while True:
        res = client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        if res.get("result", {}).get("value") is True:
            return {
                "visible": True,
                "selector": selector,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            }
        if time.monotonic() >= deadline:
            raise CDPTimeout(f"sélecteur non visible après {timeout}s: {selector}")
        time.sleep(poll)
