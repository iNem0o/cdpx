"""Navigation primitives.

Agent usecase: move around the app under active development (Symfony
front-end, Shopware/PrestaShop back-office) and KNOW when the page is
actually loaded before observing anything — otherwise the agent reads
intermediate states.
"""

from __future__ import annotations

import json
import time
from typing import Any

from cdpx.client import CDPClient, CDPTimeout
from cdpx.option_types import NavigationWait

WAIT_EVENTS = {
    "load": "Page.loadEventFired",
    "domcontentloaded": "Page.domContentEventFired",
}


class NavigationError(ValueError):
    """A Page.navigate failure with its normalized CDP result attached."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        detail = result.get("errorText") or result.get("url") or "unknown error"
        super().__init__(f"navigation failed: {detail}")


def raise_for_navigation_error(
    response: dict[str, Any],
    url: str,
    *,
    wait: NavigationWait,
) -> None:
    """Normalize and raise any CDP navigation failure."""
    error_text = response.get("errorText")
    if error_text:
        raise NavigationError(
            {
                "url": url,
                "frameId": response.get("frameId"),
                "loaderId": response.get("loaderId"),
                "errorText": error_text,
                "waited": wait,
                "ok": False,
            }
        )


def navigate(
    client: CDPClient,
    url: str,
    wait: NavigationWait = "load",
    timeout: float = 30.0,
) -> dict:
    """Navigates and waits for the requested lifecycle event (load|domcontentloaded|none)."""
    if wait not in {*WAIT_EVENTS, "none"}:
        raise ValueError(f"unknown navigation wait: {wait}")
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
    raise_for_navigation_error(res, url, wait=wait)
    if wait in WAIT_EVENTS:
        client.wait_event(WAIT_EVENTS[wait], timeout=timeout)
    out["ok"] = True
    out["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    return out


def wait_for(client: CDPClient, selector: str, timeout: float = 10.0, poll: float = 0.05) -> dict:
    """Waits for a CSS selector to exist in the DOM (Runtime.evaluate polling).

    Why polling rather than an injected MutationObserver: zero residual
    state left in the page, identical behavior no matter when we arrive.
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
            raise CDPTimeout(f"selector not found after {timeout}s: {selector}")
        time.sleep(poll)


def wait_for_visible(
    client: CDPClient,
    selector: str,
    timeout: float = 10.0,
    poll: float = 0.05,
) -> dict:
    """Waits for an element that is attached, rendered, and has a non-zero box."""
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
            raise CDPTimeout(f"selector not visible after {timeout}s: {selector}")
        time.sleep(poll)
