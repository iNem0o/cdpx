"""Primitives d'interaction (Input domain).

Pourquoi Input.dispatch* plutôt que el.click() en JS: les évènements passent
par le pipeline navigateur réel (hover, focus, trusted events). C'est ce qui
fait la différence sur les frameworks front qui filtrent isTrusted, et c'est
plus proche de ce que verra un vrai utilisateur.
"""

from __future__ import annotations

import json

from cdpx.client import CDPClient

_RECT_EXPR = (
    "(() => {{ const el = document.querySelector({sel});"
    " if (!el) return null;"
    " el.scrollIntoView({{block: 'center', inline: 'center'}});"
    " const r = el.getBoundingClientRect();"
    " return JSON.stringify({{x: r.x, y: r.y, width: r.width, height: r.height}}); }})()"
)

KEY_MAP = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
}


class ElementNotFound(RuntimeError):
    pass


def _center(client: CDPClient, selector: str) -> tuple[float, float]:
    expr = _RECT_EXPR.format(sel=json.dumps(selector))
    res = client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    raw = res.get("result", {}).get("value")
    if not raw:
        raise ElementNotFound(f"sélecteur introuvable: {selector}")
    rect = json.loads(raw)
    return rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2


def click(client: CDPClient, selector: str, button: str = "left") -> dict:
    x, y = _center(client, selector)
    base = {"x": x, "y": y, "button": button, "clickCount": 1}
    client.send("Input.dispatchMouseEvent", {"type": "mouseMoved", **base})
    client.send("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
    client.send("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})
    return {"clicked": selector, "x": round(x, 1), "y": round(y, 1)}


def type_text(client: CDPClient, selector: str, text: str, clear: bool = False) -> dict:
    """Focus l'élément puis insère le texte via Input.insertText (composition IME-safe)."""
    sel = json.dumps(selector)
    clear_js = (
        "el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true}));" if clear else ""
    )
    expr = (
        f"(() => {{ const el = document.querySelector({sel});"
        f" if (!el) return false; el.focus(); {clear_js} return true; }})()"
    )
    res = client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    if res.get("result", {}).get("value") is not True:
        raise ElementNotFound(f"sélecteur introuvable: {selector}")
    client.send("Input.insertText", {"text": text})
    return {"typed": text, "selector": selector, "cleared": clear}


def press_key(client: CDPClient, key: str) -> dict:
    if key not in KEY_MAP:
        raise ValueError(f"touche non supportée: {key} (dispo: {', '.join(KEY_MAP)})")
    params = KEY_MAP[key]
    down = {"type": "rawKeyDown", **{k: v for k, v in params.items() if k != "text"}}
    client.send("Input.dispatchKeyEvent", down)
    if "text" in params:
        client.send(
            "Input.dispatchKeyEvent",
            {"type": "char", "text": params["text"], "key": params["key"]},
        )
    client.send(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", **{k: v for k, v in params.items() if k != "text"}},
    )
    return {"pressed": key}
