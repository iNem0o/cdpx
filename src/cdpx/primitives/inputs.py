"""Interaction primitives (Input domain).

Why Input.dispatch* rather than el.click() in JS: the events go through
the real browser pipeline (hover, focus, trusted events). That's what makes
the difference on front-end frameworks that filter isTrusted, and it's
closer to what a real user would see.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from cdpx.client import CDPClient, CDPError, CDPTimeout, CDPTransportError
from cdpx.policy import assert_url_allowed, origin_from_url, parse_exact_origin

OriginGuard = Callable[[Callable[[], float] | None], None]

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


def _prepare_text_input(
    client: CDPClient,
    selector: str,
    clear: bool,
    *,
    remaining: Callable[[], float] | None = None,
) -> None:
    expr = _PREPARE_TEXT_EXPR.replace("__CDPX_CLEAR__", "true" if clear else "false").replace(
        "__CDPX_SELECTOR__", json.dumps(selector)
    )
    res = _send(
        client,
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": True},
        remaining=remaining,
    )
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
    origin_guard: OriginGuard | None = None,
    remaining: Callable[[], float] | None = None,
) -> dict:
    """Focus an element and type with IME insertion or discrete trusted key events."""
    _validate_typing(text, mode=mode, key_delay_ms=0)
    state = _probe_actionability(client, selector, remaining=remaining)
    _require_attached(state, selector)
    for field, message in _FAILURE_MESSAGES[:2]:
        if not state[field]:
            raise ElementNotInteractable(f"{message}: {selector}")
    if not state["editable"]:
        raise ElementNotInteractable(f"element not editable: {selector}")
    _prepare_text_input(client, selector, clear, remaining=remaining)
    if clear:
        _guard_origin(origin_guard, remaining)
        press_key(client, "Backspace", remaining=remaining)
        _guard_origin(origin_guard, remaining)
    _insert_text(
        client,
        text,
        mode=mode,
        origin_guard=origin_guard,
        remaining=remaining,
    )
    result = {
        "typed": True,
        "value_masked": True,
        "selector": selector,
        "cleared": clear,
    }
    if mode != "insert_text":
        result["mode"] = mode
    return result


def _type_printable_key(
    client: CDPClient,
    char: str,
    *,
    origin_guard: OriginGuard | None = None,
    remaining: Callable[[], float] | None = None,
) -> None:
    virtual_key, code, modifiers, unmodified_text = _printable_key_layout(char)
    key = {
        "key": char,
        "code": code,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }
    if modifiers:
        key["modifiers"] = modifiers
    events = (
        {"type": "rawKeyDown", **key},
        {"type": "char", "text": char, "unmodifiedText": unmodified_text, **key},
        {"type": "keyUp", **key},
    )
    for event in events:
        _guard_origin(origin_guard, remaining)
        _send(client, "Input.dispatchKeyEvent", event, remaining=remaining)
    _guard_origin(origin_guard, remaining)


_SHIFTED_DIGITS = dict(zip("!@#$%^&*()", "1234567890", strict=True))
_PUNCTUATION_KEYS = {
    "`": (192, "Backquote"),
    "-": (189, "Minus"),
    "=": (187, "Equal"),
    "[": (219, "BracketLeft"),
    "]": (221, "BracketRight"),
    "\\": (220, "Backslash"),
    ";": (186, "Semicolon"),
    "'": (222, "Quote"),
    ",": (188, "Comma"),
    ".": (190, "Period"),
    "/": (191, "Slash"),
}
_SHIFTED_PUNCTUATION = dict(zip('~_+{}|:"<>?', _PUNCTUATION_KEYS, strict=True))


def _printable_key_layout(char: str) -> tuple[int, str, int, str]:
    """Return the US physical-key metadata for one printable ASCII character."""
    if char == " ":
        return 32, "Space", 0, char
    if char.isalpha():
        return ord(char.upper()), f"Key{char.upper()}", 8 if char.isupper() else 0, char.lower()
    if char.isdigit():
        return ord(char), f"Digit{char}", 0, char
    digit = _SHIFTED_DIGITS.get(char)
    if digit is not None:
        return ord(digit), f"Digit{digit}", 8, digit
    base = _SHIFTED_PUNCTUATION.get(char, char)
    virtual_key, code = _PUNCTUATION_KEYS[base]
    return virtual_key, code, 8 if base != char else 0, base


def _validate_typing_options(*, mode: str, key_delay_ms: int) -> None:
    if mode not in {"insert_text", "key_events"}:
        raise ValueError(f"unsupported typing mode: {mode}")
    if (
        not isinstance(key_delay_ms, int)
        or isinstance(key_delay_ms, bool)
        or not 0 <= key_delay_ms <= 250
    ):
        raise ValueError("key_delay_ms must stay between 0 and 250")
    if mode != "key_events" and key_delay_ms:
        raise ValueError("key_delay_ms requires key_events mode")


def _validate_typing(text: str, *, mode: str, key_delay_ms: int) -> None:
    _validate_typing_options(mode=mode, key_delay_ms=key_delay_ms)
    if mode == "key_events" and any(not char.isascii() or not char.isprintable() for char in text):
        raise ValueError("key_events typing supports printable ASCII only")


def _send(
    client: CDPClient,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    remaining: Callable[[], float] | None = None,
) -> dict[str, Any]:
    if remaining is None:
        return client.send(method, params)
    return client.send(method, params, timeout=remaining())


def _guard_origin(
    origin_guard: OriginGuard | None,
    remaining: Callable[[], float] | None,
) -> None:
    if origin_guard is not None:
        origin_guard(remaining)


def _insert_text(
    client: CDPClient,
    text: str,
    *,
    mode: str,
    key_delay_ms: int = 0,
    origin_guard: OriginGuard | None = None,
    remaining: Callable[[], float] | None = None,
) -> None:
    _validate_typing(text, mode=mode, key_delay_ms=key_delay_ms)
    if mode == "insert_text":
        _guard_origin(origin_guard, remaining)
        _send(client, "Input.insertText", {"text": text}, remaining=remaining)
        _guard_origin(origin_guard, remaining)
        return
    for char in text:
        _type_printable_key(
            client,
            char,
            origin_guard=origin_guard,
            remaining=remaining,
        )
        if key_delay_ms:
            delay = key_delay_ms / 1000
            if remaining is not None:
                delay = min(delay, remaining())
            time.sleep(delay)
            if remaining is not None:
                remaining()


def type_text_in_frame(
    client: CDPClient,
    selector: str,
    text: str,
    *,
    frame_origin: str,
    mode: str = "insert_text",
    key_delay_ms: int = 0,
    remaining: Callable[[], float] | None = None,
) -> dict:
    """Type into a single-field cross-origin iframe without reading its DOM."""
    _validate_typing(text, mode=mode, key_delay_ms=key_delay_ms)
    expected_origin = parse_exact_origin(frame_origin)
    state = _probe_actionability(client, selector, remaining=remaining)
    _require_actionable(state, selector)

    def guard_origin(guard_remaining: Callable[[], float] | None) -> None:
        assert_url_allowed(
            _frame_url(client, selector, remaining=guard_remaining),
            (expected_origin,),
        )

    frame_url = _frame_url(client, selector, remaining=remaining)
    assert_url_allowed(frame_url, (expected_origin,))
    click(client, selector, remaining=remaining)
    _insert_text(
        client,
        text,
        mode=mode,
        key_delay_ms=key_delay_ms,
        origin_guard=guard_origin,
        remaining=remaining,
    )
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
    remaining: Callable[[], float] | None = None,
) -> dict:
    """Type into exactly one declared cross-origin iframe candidate."""
    _validate_typing_options(mode=mode, key_delay_ms=key_delay_ms)
    validated_candidates = tuple(
        (selector, parse_exact_origin(frame_origin), text)
        for selector, frame_origin, text in candidates
    )
    matches: list[tuple[str, str, str]] = []
    for selector, frame_origin, text in validated_candidates:
        try:
            frame_url = _frame_url(client, selector, remaining=remaining)
        except ElementNotInteractable:
            continue
        assert_url_allowed(frame_url, (frame_origin,))
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
        remaining=remaining,
    )


def _frame_url(
    client: CDPClient,
    selector: str,
    *,
    remaining: Callable[[], float] | None = None,
) -> str:
    document = _send(client, "DOM.getDocument", {"depth": 0}, remaining=remaining)
    root_id = document.get("root", {}).get("nodeId")
    if not isinstance(root_id, int):
        raise ElementNotInteractable(f"iframe document unavailable: {selector}")
    query = _send(
        client,
        "DOM.querySelector",
        {"nodeId": root_id, "selector": selector},
        remaining=remaining,
    )
    node_id = query.get("nodeId")
    if not isinstance(node_id, int) or node_id <= 0:
        raise ElementNotInteractable(f"iframe unavailable: {selector}")
    description = _send(client, "DOM.describeNode", {"nodeId": node_id}, remaining=remaining)
    frame_id = description.get("node", {}).get("frameId")
    if not isinstance(frame_id, str) or not frame_id:
        raise ElementNotInteractable(f"iframe child frame unavailable: {selector}")
    frame_tree = _send(client, "Page.getFrameTree", remaining=remaining).get("frameTree")
    frame_url = _find_frame_url(frame_tree, frame_id)
    if frame_url is None:
        raise ElementNotInteractable(f"iframe current URL unavailable: {selector}")
    return frame_url


def _find_frame_url(frame_tree: Any, frame_id: str) -> str | None:
    if not isinstance(frame_tree, dict):
        return None
    frame = frame_tree.get("frame")
    if isinstance(frame, dict) and frame.get("id") == frame_id:
        url = frame.get("url")
        return url if isinstance(url, str) and url else None
    children = frame_tree.get("childFrames", [])
    if not isinstance(children, list):
        return None
    for child in children:
        url = _find_frame_url(child, frame_id)
        if url is not None:
            return url
    return None


def press_key(
    client: CDPClient,
    key: str,
    *,
    remaining: Callable[[], float] | None = None,
    after_key_down: Callable[[], None] | None = None,
    cleanup_remaining: Callable[[], float] | None = None,
    before_key_up: Callable[[], None] | None = None,
    cleanup_status: dict[str, str] | None = None,
) -> dict:
    if key not in KEY_MAP:
        raise ValueError(f"unsupported key: {key} (available: {', '.join(KEY_MAP)})")
    params = KEY_MAP[key]
    down = {"type": "rawKeyDown", **{k: v for k, v in params.items() if k != "text"}}
    cleanup_timeout = cleanup_remaining or remaining
    pending_error: BaseException | None = None
    key_down_attempted = False
    if cleanup_status is not None:
        cleanup_status["key_up"] = "not_attempted"
    try:
        # Resolve the local deadline before marking the event as attempted.
        # Once client.send starts, a response timeout can still mean Chromium
        # applied rawKeyDown, so cleanup must then dispatch keyUp.
        down_timeout = remaining() if remaining is not None else None
        key_down_attempted = True
        if down_timeout is None:
            client.send("Input.dispatchKeyEvent", down)
        else:
            client.send("Input.dispatchKeyEvent", down, timeout=down_timeout)
        if after_key_down is not None:
            after_key_down()
        if "text" in params:
            _send(
                client,
                "Input.dispatchKeyEvent",
                {"type": "char", "text": params["text"], "key": params["key"]},
                remaining=remaining,
            )
    except BaseException as error:
        pending_error = error
        raise
    finally:
        if key_down_attempted:
            try:
                if before_key_up is not None:
                    before_key_up()
                _send(
                    client,
                    "Input.dispatchKeyEvent",
                    {"type": "keyUp", **{k: v for k, v in params.items() if k != "text"}},
                    remaining=cleanup_timeout,
                )
                if cleanup_status is not None:
                    cleanup_status["key_up"] = "completed"
            except CDPError, CDPTimeout, CDPTransportError:
                if cleanup_status is not None:
                    cleanup_status["key_up"] = "failed"
                if pending_error is None:
                    raise
    return {"pressed": key}
