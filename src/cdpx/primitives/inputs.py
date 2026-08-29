"""Interaction primitives (Input domain).

Why Input.dispatch* rather than el.click() in JS: the events go through
the real browser pipeline (hover, focus, trusted events). That's what makes
the difference on front-end frameworks that filter isTrusted, and it's
closer to what a real user would see.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

from cdpx.client import CDPClient
from cdpx.policy import assert_url_allowed, origin_from_url

_ACTIONABILITY_EXPR = r"""
(() => {
  const selector = __CDPX_SELECTOR__;
  const measure = () => {
    const element = document.querySelector(selector);
    if (!element || !element.isConnected) {
      return {
        element: null,
        state: {
          attached: false,
          visible: false,
          enabled: false,
          stable: false,
          receives_events: false,
          editable: false,
          rect: null
        }
      };
    }
    const style = window.getComputedStyle(element);
    const box = element.getBoundingClientRect();
    const rect = {x: box.x, y: box.y, width: box.width, height: box.height};
    const visible = style.display !== "none"
      && style.visibility !== "hidden"
      && style.visibility !== "collapse"
      && rect.width > 0
      && rect.height > 0;
    let ariaDisabled = false;
    for (let node = element; node; node = node.parentElement) {
      if ((node.getAttribute("aria-disabled") || "").toLowerCase() === "true") {
        ariaDisabled = true;
        break;
      }
    }
    const enabled = !element.matches(":disabled")
      && !ariaDisabled
      && element.closest("[inert]") === null
      && style.pointerEvents !== "none";
    const blockedInputTypes = new Set([
      "button", "checkbox", "color", "file", "hidden", "image", "radio",
      "range", "reset", "submit"
    ]);
    const editable = !element.readOnly && (
      (element instanceof HTMLInputElement && !blockedInputTypes.has(element.type))
      || element instanceof HTMLTextAreaElement
      || element.isContentEditable
    );
    return {
      element,
      state: {
        attached: true,
        visible,
        enabled,
        stable: false,
        receives_events: false,
        editable,
        rect
      }
    };
  };

  const initial = document.querySelector(selector);
  if (initial) initial.scrollIntoView({block: "center", inline: "center"});
  return new Promise((resolve) => {
    requestAnimationFrame(() => {
      const first = measure();
      requestAnimationFrame(() => {
        const second = measure();
        const state = second.state;
        const a = first.state.rect;
        const b = state.rect;
        state.stable = first.element === second.element
          && first.state.attached
          && state.attached
          && Math.abs(a.x - b.x) <= 0.5
          && Math.abs(a.y - b.y) <= 0.5
          && Math.abs(a.width - b.width) <= 0.5
          && Math.abs(a.height - b.height) <= 0.5;
        if (state.visible && second.element) {
          const x = b.x + b.width / 2;
          const y = b.y + b.height / 2;
          const hit = document.elementFromPoint(x, y);
          state.receives_events = hit !== null
            && (hit === second.element || second.element.contains(hit));
        }
        resolve(JSON.stringify(state));
      });
    });
  });
})() /* __cdpx_actionability focus */
"""

_PREPARE_TEXT_EXPR = r"""
(() => {
  const el = document.querySelector(__CDPX_SELECTOR__);
  if (!el || !el.isConnected) return false;
  el.focus();
  if (!__CDPX_CLEAR__) return true;
  if (!el.isContentEditable && typeof el.select === "function") {
    el.select();
    return true;
  }
  if (el.isContentEditable) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const selection = window.getSelection();
    if (!selection) return false;
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }
  return false;
})() /* __cdpx_prepare_text */
"""

_ACTIONABLE_DEFAULTS = {
    "attached": True,
    "visible": True,
    "enabled": True,
    "stable": True,
    "receives_events": True,
    "editable": True,
    "rect": None,
}

_FAILURE_MESSAGES = (
    ("visible", "element not visible"),
    ("enabled", "element disabled"),
    ("stable", "element unstable"),
    ("receives_events", "element covered"),
)

KEY_MAP = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Space": {"key": " ", "code": "Space", "windowsVirtualKeyCode": 32, "text": " "},
    "PageUp": {"key": "PageUp", "code": "PageUp", "windowsVirtualKeyCode": 33},
    "PageDown": {"key": "PageDown", "code": "PageDown", "windowsVirtualKeyCode": 34},
    "End": {"key": "End", "code": "End", "windowsVirtualKeyCode": 35},
    "Home": {"key": "Home", "code": "Home", "windowsVirtualKeyCode": 36},
    "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
    "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46},
}


class ElementNotFound(RuntimeError):
    pass


class ElementNotInteractable(ElementNotFound):
    """Element present but unfit for reliable user interaction."""


def _probe_actionability(
    client: CDPClient,
    selector: str,
    *,
    remaining: Callable[[], float] | None = None,
) -> dict[str, Any]:
    expr = _ACTIONABILITY_EXPR.replace("__CDPX_SELECTOR__", json.dumps(selector))
    params = {"expression": expr, "returnByValue": True, "awaitPromise": True}
    if remaining is None:
        res = client.send("Runtime.evaluate", params)
    else:
        res = client.send("Runtime.evaluate", params, timeout=remaining())
    raw = res.get("result", {}).get("value")
    if raw is True:
        return dict(_ACTIONABLE_DEFAULTS)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, dict) and {"x", "y", "width", "height"} <= raw.keys():
        return {**_ACTIONABLE_DEFAULTS, "rect": raw}
    if not isinstance(raw, dict):
        return {**_ACTIONABLE_DEFAULTS, "attached": False}
    return {**_ACTIONABLE_DEFAULTS, **raw}


def _require_attached(state: dict[str, Any], selector: str) -> None:
    if not state["attached"]:
        raise ElementNotFound(f"selector not found: {selector}")


def _require_actionable(state: dict[str, Any], selector: str) -> None:
    _require_attached(state, selector)
    for field, message in _FAILURE_MESSAGES:
        if not state[field]:
            raise ElementNotInteractable(f"{message}: {selector}")


def _prepare_text_input(client: CDPClient, selector: str, clear: bool) -> None:
    expr = _PREPARE_TEXT_EXPR.replace("__CDPX_CLEAR__", "true" if clear else "false").replace(
        "__CDPX_SELECTOR__", json.dumps(selector)
    )
    res = client.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    if res.get("result", {}).get("value") is not True:
        raise ElementNotFound(f"selector not found or selection not possible: {selector}")


def click(
    client: CDPClient,
    selector: str,
    button: str = "left",
    *,
    remaining: Callable[[], float] | None = None,
) -> dict:
    state = _probe_actionability(client, selector, remaining=remaining)
    _require_actionable(state, selector)
    rect = state["rect"]
    if not isinstance(rect, dict):
        raise ElementNotInteractable(f"element not visible: {selector}")
    x = rect["x"] + rect["width"] / 2
    y = rect["y"] + rect["height"] / 2
    base = {"x": x, "y": y, "button": button, "clickCount": 1}
    for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
        params = {"type": event_type, **base}
        if remaining is None:
            client.send("Input.dispatchMouseEvent", params)
        else:
            client.send("Input.dispatchMouseEvent", params, timeout=remaining())
    return {"clicked": selector, "x": round(x, 1), "y": round(y, 1)}


def type_text(
    client: CDPClient,
    selector: str,
    text: str,
    clear: bool = False,
    *,
    mode: str = "insert_text",
) -> dict:
    """Focus an element and type with IME insertion or discrete trusted key events."""
    if mode not in {"insert_text", "key_events"}:
        raise ValueError(f"unsupported typing mode: {mode}")
    if mode == "key_events" and any(not char.isascii() or not char.isprintable() for char in text):
        raise ValueError("key_events typing supports printable ASCII only")
    state = _probe_actionability(client, selector)
    _require_attached(state, selector)
    for field, message in _FAILURE_MESSAGES[:2]:
        if not state[field]:
            raise ElementNotInteractable(f"{message}: {selector}")
    if not state["editable"]:
        raise ElementNotInteractable(f"element not editable: {selector}")
    _prepare_text_input(client, selector, clear)
    if clear:
        press_key(client, "Backspace")
    _insert_text(client, text, mode=mode)
    result = {
        "typed": True,
        "value_masked": True,
        "selector": selector,
        "cleared": clear,
    }
    if mode != "insert_text":
        result["mode"] = mode
    return result


def _type_printable_key(client: CDPClient, char: str) -> None:
    virtual_key = ord(char.upper()) if char.isalpha() else ord(char)
    code = f"Digit{char}" if char.isdigit() else f"Key{char.upper()}" if char.isalpha() else ""
    key = {
        "key": char,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }
    if code:
        key["code"] = code
    client.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **key})
    client.send(
        "Input.dispatchKeyEvent",
        {"type": "char", "text": char, "unmodifiedText": char, **key},
    )
    client.send("Input.dispatchKeyEvent", {"type": "keyUp", **key})


def _insert_text(client: CDPClient, text: str, *, mode: str, key_delay_ms: int = 0) -> None:
    if mode not in {"insert_text", "key_events"}:
        raise ValueError(f"unsupported typing mode: {mode}")
    if mode == "key_events" and any(not char.isascii() or not char.isprintable() for char in text):
        raise ValueError("key_events typing supports printable ASCII only")
    if (
        not isinstance(key_delay_ms, int)
        or isinstance(key_delay_ms, bool)
        or not 0 <= key_delay_ms <= 250
    ):
        raise ValueError("key_delay_ms must stay between 0 and 250")
    if mode != "key_events" and key_delay_ms:
        raise ValueError("key_delay_ms requires key_events mode")
    if mode == "insert_text":
        client.send("Input.insertText", {"text": text})
        return
    for char in text:
        _type_printable_key(client, char)
        if key_delay_ms:
            time.sleep(key_delay_ms / 1000)


def type_text_in_frame(
    client: CDPClient,
    selector: str,
    text: str,
    *,
    frame_origin: str,
    mode: str = "insert_text",
    key_delay_ms: int = 0,
) -> dict:
    """Type into a single-field cross-origin iframe without reading its DOM."""
    state = _probe_actionability(client, selector)
    _require_actionable(state, selector)
    frame_url = _frame_url(client, selector)
    assert_url_allowed(frame_url, (origin_from_url(frame_origin),))
    click(client, selector)
    _insert_text(client, text, mode=mode, key_delay_ms=key_delay_ms)
    result = {
        "typed": True,
        "value_masked": True,
        "selector": selector,
        "frame_origin": origin_from_url(frame_url),
        "cleared": False,
    }
    if mode != "insert_text":
        result["mode"] = mode
    return result


def type_text_in_candidate_frame(
    client: CDPClient,
    candidates: tuple[tuple[str, str, str], ...],
    *,
    mode: str = "insert_text",
    key_delay_ms: int = 0,
) -> dict:
    """Type into exactly one declared cross-origin iframe candidate."""
    matches: list[tuple[str, str, str]] = []
    for selector, frame_origin, text in candidates:
        try:
            frame_url = _frame_url(client, selector)
        except ElementNotInteractable:
            continue
        assert_url_allowed(frame_url, (origin_from_url(frame_origin),))
        matches.append((selector, frame_origin, text))

    if not matches:
        raise ElementNotInteractable(
            "none of the declared iframe candidates is available: "
            + ", ".join(selector for selector, _, _ in candidates)
        )
    if len(matches) > 1:
        raise ElementNotInteractable(
            "multiple declared iframe candidates are available: "
            + ", ".join(selector for selector, _, _ in matches)
        )

    selector, frame_origin, text = matches[0]
    return type_text_in_frame(
        client,
        selector,
        text,
        frame_origin=frame_origin,
        mode=mode,
        key_delay_ms=key_delay_ms,
    )


def _frame_url(client: CDPClient, selector: str) -> str:
    expression = r"""
(() => {
  const element = document.querySelector(__CDPX_SELECTOR__);
  if (!(element instanceof HTMLIFrameElement) || !element.isConnected) return null;
  return element.src;
})() /* __cdpx_frame_url */
""".replace("__CDPX_SELECTOR__", json.dumps(selector))
    response = client.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
    value = response.get("result", {}).get("value")
    if not isinstance(value, str) or not value:
        raise ElementNotInteractable(f"iframe src unavailable: {selector}")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ElementNotInteractable(f"iframe src is not HTTP(S): {selector}")
    return value


def press_key(client: CDPClient, key: str) -> dict:
    if key not in KEY_MAP:
        raise ValueError(f"unsupported key: {key} (available: {', '.join(KEY_MAP)})")
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
