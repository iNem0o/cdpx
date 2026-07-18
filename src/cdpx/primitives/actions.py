"""Composed action interpreter.

An "action" is a compact argv (["click", "#sel"]) executed on an
already-open CDP connection. It's the common language of composed commands
(dom-diff, record, replay, emulate): one action = one named primitive,
never a shell escape hatch. The centralized policy then classifies the verb
to set the authority and the allowed origins.
"""

from __future__ import annotations

import urllib.parse

from cdpx.action_model import (
    BrowserAction,
    ClickAction,
    EvalAction,
    GotoAction,
    KeyAction,
    TypeAction,
    WaitAction,
    parse_action,
)
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.primitives import inputs, js, nav


def run_action_argv(client: CDPClient, argv: list[str], timeout: float = 30.0) -> dict:
    """Compatibility adapter for external callers that still hold action argv."""
    return run_action(client, parse_action(argv), timeout=timeout)


def run_action(client: CDPClient, action: BrowserAction, timeout: float = 30.0) -> dict:
    """Executes an action and returns the output of the underlying primitive."""
    if isinstance(action, GotoAction):
        return nav.navigate(client, action.url, timeout=timeout)
    if isinstance(action, WaitAction):
        return nav.wait_for(client, action.selector, timeout=min(timeout, 10.0))
    if isinstance(action, ClickAction):
        return inputs.click(client, action.selector)
    if isinstance(action, TypeAction):
        return inputs.type_text(client, action.selector, action.text, clear=action.clear)
    if isinstance(action, KeyAction):
        return inputs.press_key(client, action.key)
    if isinstance(action, EvalAction):
        return {"value": js.evaluate(client, action.expression, await_promise=True)}
    raise AssertionError(f"unhandled action: {action!r}")


def current_http_url(client: CDPClient) -> str | None:
    """Returns the page's real HTTP(S) URL, or ``None`` otherwise."""
    value = js.evaluate(client, "window.location.href")
    parsed = urllib.parse.urlparse(value if isinstance(value, str) else "")
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def require_current_http_url(client: CDPClient, phase: str) -> str:
    """Reads the current URL and fails closed if the browser cannot provide it."""
    try:
        current_url = current_http_url(client)
    except (ValueError, CDPError, CDPTimeout, js.JSException) as error:
        raise ValueError(f"unable to determine the current URL {phase}: {error}") from error
    if current_url is None:
        raise ValueError(f"unable to determine the current URL {phase}")
    return current_url
