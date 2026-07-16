"""Interpréteur d'actions composées.

Une "action" est un argv compact (["click", "#sel"]) exécuté dans une
connexion CDP déjà ouverte. C'est le langage commun des commandes composées
(dom-diff, record, replay, emulate): une action = une primitive nommée,
jamais d'échappatoire shell. La politique centralisée classe ensuite le verbe
pour fixer l'autorité et les origines autorisées.
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
    """Exécute une action et retourne la sortie de la primitive sous-jacente."""
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
    raise AssertionError(f"action non gérée: {action!r}")


def current_http_url(client: CDPClient) -> str | None:
    """Retourne l'URL HTTP(S) réelle de la page, sinon ``None``."""
    value = js.evaluate(client, "window.location.href")
    parsed = urllib.parse.urlparse(value if isinstance(value, str) else "")
    return value if parsed.scheme in {"http", "https"} and parsed.netloc else None


def require_current_http_url(client: CDPClient, phase: str) -> str:
    """Lit l'URL courante et échoue fermé si le navigateur ne peut la fournir."""
    try:
        current_url = current_http_url(client)
    except (ValueError, CDPError, CDPTimeout, js.JSException) as error:
        raise ValueError(f"URL courante indéterminable {phase}: {error}") from error
    if current_url is None:
        raise ValueError(f"URL courante indéterminable {phase}")
    return current_url
