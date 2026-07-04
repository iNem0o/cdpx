"""Primitives JS / lecture DOM.

`evaluate` est LA primitive racine: tout ce que l'agent ne sait pas faire en
protocole pur, il peut le faire en JS. Les autres primitives de ce module sont
des raccourcis stables (contrat de sortie fixe) pour éviter que l'agent
réinvente du JS fragile à chaque session.
"""

from __future__ import annotations

import json
from typing import Any

from cdpx.client import CDPClient


class JSException(RuntimeError):
    pass


def evaluate(
    client: CDPClient,
    expression: str,
    await_promise: bool = False,
    return_by_value: bool = True,
) -> Any:
    res = client.send(
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": return_by_value,
            "awaitPromise": await_promise,
        },
    )
    if "exceptionDetails" in res:
        details = res["exceptionDetails"]
        text = details.get("exception", {}).get("description") or details.get("text", "JS error")
        raise JSException(text)
    return res.get("result", {}).get("value")


def get_text(client: CDPClient, selector: str | None = None) -> dict:
    """innerText d'un élément (ou du body). Vision 'sémantique' low-cost de la page."""
    if selector:
        expr = (
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f" return el ? el.innerText : null; }})()"
        )
    else:
        expr = "document.body ? document.body.innerText : ''"
    value = evaluate(client, expr)
    return {"selector": selector, "text": value}


def get_html(client: CDPClient, selector: str | None = None) -> dict:
    """outerHTML d'un élément (ou du document). Pour inspection structurelle fine."""
    if selector:
        expr = (
            f"(() => {{ const el = document.querySelector({json.dumps(selector)});"
            f" return el ? el.outerHTML : null; }})()"
        )
    else:
        expr = "document.documentElement.outerHTML"
    value = evaluate(client, expr)
    return {"selector": selector, "html": value}


def count(client: CDPClient, selector: str) -> dict:
    """Nombre d'éléments matchant un sélecteur. Assertion cheap pour l'agent."""
    expr = f"document.querySelectorAll({json.dumps(selector)}).length"
    return {"selector": selector, "count": evaluate(client, expr)}
