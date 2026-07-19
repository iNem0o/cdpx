"""State primitives: cookies, localStorage/sessionStorage.

Security (see HARNESS.md): state values are REDACTED by default in
outputs. An agent that copies its outputs into a ticket, a commit, or a
log must not be able to exfiltrate a session by accident. The
show_values flag is a deliberate human act.
"""

from __future__ import annotations

import json

from cdpx.client import CDPClient, CDPError
from cdpx.option_types import StorageKind
from cdpx.primitives.js import evaluate
from cdpx.security import MASK


def get_cookies(client: CDPClient, show_values: bool = False) -> dict:
    res = client.send("Network.getCookies")
    cookies = []
    for c in res.get("cookies", []):
        cookies.append(
            {
                "name": c.get("name"),
                "value": c.get("value") if show_values else MASK,
                "domain": c.get("domain"),
                "path": c.get("path"),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
            }
        )
    return {"cookies": cookies, "count": len(cookies), "values_masked": not show_values}


def set_cookie(client: CDPClient, name: str, value: str, url: str) -> dict:
    res = client.send("Network.setCookie", {"name": name, "value": value, "url": url})
    return {"name": name, "url": url, "success": bool(res.get("success", True))}


def clear_cookies(client: CDPClient) -> dict:
    try:
        client.send("Storage.clearCookies")
        return {"cleared": True, "method": "Storage.clearCookies"}
    except CDPError:
        # Some supported endpoints expose cookie clearing through Network.
        client.send("Network.clearBrowserCookies")
        return {"cleared": True, "method": "Network.clearBrowserCookies"}


def get_storage(
    client: CDPClient,
    kind: StorageKind = "local",
    show_values: bool = False,
) -> dict:
    if kind not in {"local", "session"}:
        raise ValueError(f"unknown storage: {kind}")
    store = "localStorage" if kind == "local" else "sessionStorage"
    expr = f"JSON.stringify(Object.fromEntries(Object.entries({store})))"
    raw = evaluate(client, expr)
    data = json.loads(raw) if raw else {}
    entries = data if show_values else {name: MASK for name in data}
    return {
        "kind": kind,
        "entries": entries,
        "count": len(data),
        "values_masked": not show_values,
    }
