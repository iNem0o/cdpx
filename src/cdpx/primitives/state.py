"""Primitives d'état: cookies, localStorage/sessionStorage.

Sécurité (voir HARNESS.md): les valeurs d'état sont MASQUÉES par défaut
dans les sorties. Un agent qui recopie ses sorties dans un ticket, un commit
ou un log ne doit pas pouvoir exfiltrer une session par accident. Le flag
show_values est un acte volontaire de l'humain.
"""

from __future__ import annotations

import json

from cdpx.client import CDPClient, CDPError
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
        # Chrome historique sans Storage.clearCookies: méthode dépréciée en repli.
        client.send("Network.clearBrowserCookies")
        return {"cleared": True, "method": "Network.clearBrowserCookies"}


def get_storage(client: CDPClient, kind: str = "local", show_values: bool = False) -> dict:
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
