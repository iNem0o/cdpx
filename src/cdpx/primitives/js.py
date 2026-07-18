"""JS primitives / DOM reading.

`evaluate` is THE root primitive: anything the agent can't do in pure
protocol, it can do in JS. The other primitives in this module are
stable shortcuts (fixed output contract) to keep the agent from
reinventing fragile JS every session.
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
    """innerText of an element (or the body). Low-cost 'semantic' view of the page."""
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
    """outerHTML of an element (or the document). For fine-grained structural inspection."""
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
    """Number of elements matching a selector. Cheap assertion for the agent."""
    expr = f"document.querySelectorAll({json.dumps(selector)}).length"
    return {"selector": selector, "count": evaluate(client, expr)}
