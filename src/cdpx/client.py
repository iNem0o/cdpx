"""Synchronous CDP client.

A client = one WebSocket connection to ONE Chrome target (page).
Model: JSON-RPC-like. Commands {id, method, params} -> response {id, result|error}.
Events ({method, params} without id) arriving in the meantime are buffered
in `self.events` and consumable via wait_event()/collect_events().

Deliberate choices (see docs/CONTEXT.md):
- sync (websockets.sync): a CLI is sequential by nature, no asyncio to drag along.
- no sessionId/flatten: we connect directly to the webSocketDebuggerUrl
  of the page target provided by HTTP discovery (/json), like a human with
  chrome --remote-debugging-port.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, TypeGuard

from websockets.sync.client import connect

from cdpx.cdp_types import (
    CDPCommand,
    CDPErrorPayload,
    CDPEvent,
    CDPParams,
    CDPResponse,
    CDPResult,
)

DEFAULT_TIMEOUT = 15.0
type InboundMessage = CDPEvent | CDPResponse


def validate_time_budget(value: float, label: str) -> float:
    """Return a finite, non-negative CDP time budget."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} numeric value required")
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} finite and non-negative required")
    return rendered


class CDPError(RuntimeError):
    """Error returned by Chrome for a command."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"CDP error {code}: {message}" + (f" ({data})" if data else ""))
        self.code = code
        self.data = data


class CDPTimeout(TimeoutError):
    pass


class CDPTransportError(RuntimeError):
    """WebSocket receive or CDP message decoding failed before a valid message."""


class CDPClient:
    def __init__(self, ws_url: str, timeout: float = DEFAULT_TIMEOUT):
        self._validate_timeout(timeout)
        self.ws_url = ws_url
        self.timeout = timeout
        self._id = 0
        self.events: list[CDPEvent] = []
        self._responses: dict[int, CDPResponse] = {}
        try:
            self._ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=timeout)
        except Exception as error:
            raise CDPTransportError(f"CDP connection failed to {ws_url}: {error}") from error

    # -- context --------------------------------------------------------------
    def __enter__(self) -> CDPClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    # -- core ------------------------------------------------------------------
    def send(
        self,
        method: str,
        params: CDPParams | None = None,
        timeout: float | None = None,
    ) -> CDPResult:
        """Send a command, buffer events, return `result`."""
        timeout = self._timeout_seconds(timeout)
        cmd_id = self.send_nowait(method, params)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"response to {method}")
            if _is_response(msg):
                if msg["id"] == cmd_id:
                    return self._response_result(msg)
                self._responses[msg["id"]] = msg
            elif _is_event(msg):
                self.events.append(msg)

    def send_nowait(self, method: str, params: CDPParams | None = None) -> int:
        """Send a command without waiting for its response.

        Useful when the command immediately triggers blocking events that must
        be answered before Chrome sends back the command response itself,
        typically Fetch.requestPaused on the main request.
        """
        self._id += 1
        cmd_id = self._id
        command: CDPCommand = {"id": cmd_id, "method": method, "params": params or {}}
        payload = json.dumps(command)
        try:
            self._ws.send(payload)
        except Exception as error:
            raise CDPTransportError(
                f"transport interrupted while sending {method}: {error}"
            ) from error
        return cmd_id

    def wait_response(self, cmd_id: int, timeout: float | None = None) -> CDPResult:
        """Wait for the response to a command sent with :meth:`send_nowait`.

        Blocking events can be handled with ``next_event`` between
        sending and this call; cross responses are kept instead of
        being lost.
        """
        timeout = self._timeout_seconds(timeout)
        buffered = self._responses.pop(cmd_id, None)
        if buffered is not None:
            return self._response_result(buffered)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"response to command {cmd_id}")
            if _is_response(msg):
                if msg["id"] == cmd_id:
                    return self._response_result(msg)
                self._responses[msg["id"]] = msg
            elif _is_event(msg):
                self.events.append(msg)

    def wait_event(self, name: str, timeout: float | None = None) -> CDPEvent:
        """Wait for (or find in the buffer) the next `name` event."""
        timeout = self._timeout_seconds(timeout)
        for i, ev in enumerate(self.events):
            if ev["method"] == name:
                return self.events.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"event {name}")
            if _is_event(msg):
                if msg["method"] == name:
                    return msg
                self.events.append(msg)
            elif _is_response(msg):
                self._responses[msg["id"]] = msg

    def next_event(self, timeout: float | None = None) -> CDPEvent:
        """Return the next CDP event, whatever its name."""
        timeout = self._timeout_seconds(timeout)
        if self.events:
            return self.events.pop(0)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, "next event")
            if _is_event(msg):
                return msg
            if _is_response(msg):
                self._responses[msg["id"]] = msg

    def collect_events(
        self, duration: float, names: tuple[str, ...] | None = None
    ) -> list[CDPEvent]:
        """Passively collect events for `duration` seconds.

        A short polling timeout is expected and restarts the collection. Any
        transport failure or invalid frame raises ``CDPTransportError``
        instead of turning an incomplete collection into a partial success.
        """
        duration = validate_time_budget(duration, "collection duration")
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self._receive_message(
                    min(remaining, 0.25),
                    "passive event collection",
                )
            except TimeoutError:
                continue
            if _is_event(msg):
                self.events.append(msg)
            elif _is_response(msg):
                self._responses[msg["id"]] = msg
        out = [ev for ev in self.events if names is None or ev["method"] in names]
        if names is not None:
            self.events = [ev for ev in self.events if ev["method"] not in names]
        else:
            self.events = []
        return out

    def drain_events(self, names: tuple[str, ...] | None = None) -> list[CDPEvent]:
        """Empty the event buffer (without network read)."""
        return self.collect_events(0, names)

    # -- internal --------------------------------------------------------------
    @staticmethod
    def _validate_timeout(timeout: float) -> None:
        validate_time_budget(timeout, "timeout")

    def _timeout_seconds(self, timeout: float | None) -> float:
        value = self.timeout if timeout is None else timeout
        self._validate_timeout(value)
        return value

    def _recv(self, deadline: float, waiting_for: str) -> InboundMessage:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CDPTimeout(f"timeout waiting for {waiting_for}")
        try:
            return self._receive_message(remaining, waiting_for)
        except TimeoutError as e:
            raise CDPTimeout(f"timeout waiting for {waiting_for}") from e

    def _receive_message(self, timeout: float, waiting_for: str) -> InboundMessage:
        try:
            raw = self._ws.recv(timeout=timeout)
        except TimeoutError:
            raise
        except Exception as error:
            raise CDPTransportError(
                f"transport interrupted during {waiting_for}: {error}"
            ) from error
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as error:
            raise CDPTransportError(f"invalid CDP message during {waiting_for}") from error
        if not isinstance(message, dict):
            raise CDPTransportError(f"CDP object required during {waiting_for}")
        return _decode_envelope(message, waiting_for)

    @staticmethod
    def _response_result(msg: CDPResponse) -> CDPResult:
        if "error" in msg:
            err = msg["error"]
            raise CDPError(err.get("code", -1), err.get("message", "?"), err.get("data"))
        return msg.get("result", {})


def _is_event(message: InboundMessage) -> TypeGuard[CDPEvent]:
    return "method" in message


def _is_response(message: InboundMessage) -> TypeGuard[CDPResponse]:
    return "id" in message


def _decode_envelope(message: dict[str, Any], waiting_for: str) -> InboundMessage:
    if "method" in message:
        if any(key in message for key in ("id", "result", "error")) or not isinstance(
            message["method"], str
        ):
            raise CDPTransportError(f"invalid CDP event during {waiting_for}")
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            raise CDPTransportError(f"invalid CDP params during {waiting_for}")
        event: CDPEvent = {"method": message["method"]}
        if params is not None:
            event["params"] = params
        return event

    command_id = message.get("id")
    if isinstance(command_id, bool) or not isinstance(command_id, int):
        raise CDPTransportError(f"CDP response without valid id during {waiting_for}")
    has_result = "result" in message
    has_error = "error" in message
    if has_result == has_error:
        raise CDPTransportError(f"ambiguous CDP response during {waiting_for}")
    response: CDPResponse = {"id": command_id}
    if has_result:
        result = message["result"]
        if not isinstance(result, dict):
            raise CDPTransportError(f"invalid CDP result during {waiting_for}")
        response["result"] = result
        return response
    error = message["error"]
    if not isinstance(error, dict):
        raise CDPTransportError(f"invalid CDP error during {waiting_for}")
    code = error.get("code")
    error_message = error.get("message")
    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(error_message, str):
        raise CDPTransportError(f"malformed CDP error during {waiting_for}")
    payload: CDPErrorPayload = {"code": code, "message": error_message}
    if "data" in error:
        payload["data"] = error["data"]
    response["error"] = payload
    return response
