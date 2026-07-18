"""Mock CDP: a fake, deterministic Chrome, for testing cdpx without a browser.

What it simulates:
- the HTTP discovery API (/json/list, /json/new, /json/activate, /json/close,
  /json/version),
- the per-target WebSocket endpoint (ws://.../devtools/page/{id}),
- a single loopback port like Chrome, so the mock can be attested by the
  session supervisor.

What it guarantees:
- every command received is RECORDED (self.commands) -> tests validate the
  exact protocol emitted by cdpx (methods, params, order),
- responses and events are SCRIPTED -> zero randomness, zero external
  network, runnable in any CI.

What it does NOT test (and this is intentional): the real behavior of
Blink/V8. That is the role of the real-Chrome e2e (milestone M1), which
reuses the same HTML fixtures.
"""

from __future__ import annotations

import argparse
import base64
import json
import signal
import threading
import urllib.parse
import uuid
from collections import deque
from http import HTTPStatus
from pathlib import Path
from typing import Any

from websockets.datastructures import Headers
from websockets.http11 import Response
from websockets.sync.server import Server, serve

# 1x1 transparent PNG, valid. Minimal PDF, valid signature.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
TINY_PDF = b"%PDF-1.4\n%mock cdpx\n%%EOF\n"

DEFAULT_METRICS = [
    {"name": "Timestamp", "value": 123456.789},
    {"name": "Documents", "value": 1},
    {"name": "Nodes", "value": 42},
    {"name": "JSEventListeners", "value": 7},
    {"name": "LayoutCount", "value": 3},
    {"name": "JSHeapUsedSize", "value": 1048576},
]

DEFAULT_COOKIES = [
    {
        "name": "PHPSESSID",
        "value": "secret-session-token",
        "domain": "127.0.0.1",
        "path": "/",
        "httpOnly": True,
        "secure": False,
    }
]


class MockCDP:
    """Scriptable fake Chrome. See tests/conftest.py for typical usage."""

    def __init__(self, port: int = 0) -> None:
        self.targets: dict[str, dict] = {}
        self.commands: list[tuple[str, str, dict]] = []  # (target_id, method, params)
        self.eval_rules: list[tuple[str, deque]] = []  # (substring, successive values)
        self.console_script: list[dict] = []  # events emitted after Runtime.enable
        self.network_script: list[dict] = []  # events emitted after Page.navigate
        self.error_methods: set[str] = set()  # methods that respond with a CDP error
        self.cookies: list[dict] = [dict(c) for c in DEFAULT_COOKIES]
        self._server: Server | None = None
        self._requested_port = port
        self.port = 0
        self.http_port = 0
        self.ws_port = 0
        self._add_target("about:blank", "Mock Tab")

    # -- scripting -------------------------------------------------------------
    def on_eval(self, substring: str, *values: Any) -> None:
        """When Runtime.evaluate contains `substring`, reply with these values in
        sequence (the last one repeats)."""
        self.eval_rules.append((substring, deque(values)))

    def script_console(self, entries: list[dict]) -> None:
        self.console_script = entries

    def script_network(self, events: list[dict]) -> None:
        self.network_script = events

    def commands_for(self, method: str) -> list[dict]:
        return [p for (_t, m, p) in self.commands if m == method]

    def fail_on(self, method: str) -> None:
        """Script a CDP error for this method (to test fallback paths)."""
        self.error_methods.add(method)

    # -- lifecycle -----------------------------------------------------------------
    def start(self) -> MockCDP:
        self._start_server()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()

    def __enter__(self) -> MockCDP:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- targets -----------------------------------------------------------------
    def _add_target(self, url: str, title: str) -> dict:
        tid = uuid.uuid4().hex[:12].upper()
        self.targets[tid] = {"id": tid, "type": "page", "title": title, "url": url}
        return self._public_target(tid)

    def _public_target(self, tid: str) -> dict:
        t = dict(self.targets[tid])
        t["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{self.port}/devtools/page/{tid}"
        return t

    # -- discovery + WebSocket CDP server --------------------------------------------
    def _start_server(self) -> None:
        mock = self

        def reply(payload: Any, status: HTTPStatus = HTTPStatus.OK) -> Response:
            body = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
            return Response(
                status.value,
                status.phrase,
                Headers(
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                        ("Connection", "close"),
                    ]
                ),
                body,
            )

        def process_request(_connection, request):
            parsed = urllib.parse.urlparse(request.path)
            path = parsed.path
            if path.startswith("/devtools/"):
                return None
            if path in ("/json", "/json/list"):
                return reply([mock._public_target(t) for t in mock.targets])
            if path == "/json/version":
                return reply(
                    {
                        "Browser": "MockChrome/126.0",
                        "Protocol-Version": "1.3",
                        "webSocketDebuggerUrl": (f"ws://127.0.0.1:{mock.port}/devtools/browser"),
                    }
                )
            if path == "/json/new":
                url = urllib.parse.unquote(parsed.query) or "about:blank"
                return reply(mock._add_target(url, "New Tab"))
            if path.startswith("/json/activate/"):
                tid = path.rsplit("/", 1)[1]
                if tid in mock.targets:
                    return reply("Target activated")
                return reply("No such target id", HTTPStatus.NOT_FOUND)
            if path.startswith("/json/close/"):
                tid = path.rsplit("/", 1)[1]
                if mock.targets.pop(tid, None):
                    return reply("Target is closing")
                return reply("No such target id", HTTPStatus.NOT_FOUND)
            return reply({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def handler(ws):
            path = getattr(ws, "request", None)
            tid = (path.path if path else "").rsplit("/", 1)[-1]
            for raw in ws:
                msg = json.loads(raw)
                method, params = msg["method"], msg.get("params", {})
                mock.commands.append((tid, method, params))
                result, error, events = mock._respond(tid, method, params)
                if error:
                    ws.send(json.dumps({"id": msg["id"], "error": error}))
                else:
                    ws.send(json.dumps({"id": msg["id"], "result": result}))
                for ev in events:
                    ws.send(json.dumps(ev))

        server = serve(
            handler,
            "127.0.0.1",
            self._requested_port,
            process_request=process_request,
        )
        self._server = server
        self.port = int(server.socket.getsockname()[1])
        self.http_port = self.port
        self.ws_port = self.port
        threading.Thread(target=server.serve_forever, daemon=True).start()

    # -- scripted protocol ----------------------------------------------------------
    def _respond(self, tid: str, method: str, params: dict):
        events: list[dict] = []
        if method in self.error_methods:
            return None, {"code": -32000, "message": f"mock: '{method}' scripted failure"}, events
        if method == "Runtime.evaluate":
            expr = params.get("expression", "")
            for substring, values in self.eval_rules:
                if substring in expr:
                    value = values.popleft() if len(values) > 1 else values[0]
                    if isinstance(value, dict) and "raw" in value:
                        return value["raw"], None, events
                    return {"result": {"type": type(value).__name__, "value": value}}, None, events
            if "window.location.href" in expr:
                value = self.targets.get(tid, {}).get("url", "about:blank")
                return {"result": {"type": "str", "value": value}}, None, events
            return {"result": {"type": "string", "value": "mock"}}, None, events

        if method == "Runtime.enable":
            for entry in self.console_script:
                events.append({"method": "Runtime.consoleAPICalled", "params": entry})
            return {}, None, events

        if method == "Page.navigate":
            url = params.get("url", "")
            if tid in self.targets:
                self.targets[tid]["url"] = url
            events.extend(self.network_script)
            events.append({"method": "Page.domContentEventFired", "params": {"timestamp": 1.0}})
            events.append({"method": "Page.loadEventFired", "params": {"timestamp": 1.2}})
            return {"frameId": "FRAME1", "loaderId": "LOADER1"}, None, events

        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(TINY_PNG).decode()}, None, events
        if method == "Page.printToPDF":
            return {"data": base64.b64encode(TINY_PDF).decode()}, None, events
        if method == "Performance.getMetrics":
            return {"metrics": DEFAULT_METRICS}, None, events
        if method == "Accessibility.getFullAXTree":
            return (
                {
                    "nodes": [
                        {"role": {"value": "RootWebArea"}, "name": {"value": "Fixture"}},
                        {"role": {"value": "button"}, "name": {"value": "Envoyer"}},
                    ]
                },
                None,
                events,
            )
        if method == "Profiler.takePreciseCoverage":
            return (
                {
                    "result": [
                        {"url": "http://fixture/app.js", "functions": [{"functionName": "main"}]}
                    ]
                },
                None,
                events,
            )
        if method == "Network.getCookies":
            return {"cookies": self.cookies}, None, events
        if method == "Network.setCookie":
            self.cookies.append(
                {
                    "name": params.get("name"),
                    "value": params.get("value"),
                    "domain": "127.0.0.1",
                    "path": "/",
                }
            )
            return {"success": True}, None, events
        if method in ("Network.clearBrowserCookies", "Storage.clearCookies"):
            self.cookies = []
            return {}, None, events

        known_ok = (
            "Page.enable",
            "Network.enable",
            "Performance.enable",
            "DOM.enable",
            "Input.dispatchMouseEvent",
            "Input.dispatchKeyEvent",
            "Input.insertText",
            "Fetch.enable",
            "Fetch.continueRequest",
            "Fetch.failRequest",
            "Fetch.fulfillRequest",
            "Emulation.clearDeviceMetricsOverride",
            "Emulation.setDeviceMetricsOverride",
            "Emulation.setUserAgentOverride",
            "Emulation.setCPUThrottlingRate",
            "Network.emulateNetworkConditions",
            "Page.addScriptToEvaluateOnNewDocument",
            "Profiler.enable",
            "Profiler.startPreciseCoverage",
            "Profiler.stopPreciseCoverage",
            "CSS.enable",
            "CSS.startRuleUsageTracking",
        )
        if method in known_ok:
            return {}, None, events
        if method == "CSS.stopRuleUsageTracking":
            return (
                {
                    "ruleUsage": [
                        {"styleSheetId": "S1", "startOffset": 0, "endOffset": 10, "used": True},
                        {"styleSheetId": "S1", "startOffset": 11, "endOffset": 20, "used": False},
                    ]
                },
                None,
                events,
            )

        return None, {"code": -32601, "message": f"'{method}' wasn't found"}, events


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - supervised process
    parser = argparse.ArgumentParser(prog="python -m cdpx.testing.mock_cdp")
    parser.add_argument("--remote-debugging-port", type=int, default=0)
    parser.add_argument("--user-data-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    mock = MockCDP(port=args.remote_debugging_port).start()
    args.user_data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (args.user_data_dir / "DevToolsActivePort").write_text(
        f"{mock.port}\n/devtools/browser/mock\n",
        encoding="utf-8",
    )
    stopped = threading.Event()

    def request_stop(_signum, _frame):
        stopped.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stopped.wait(0.25):
            pass
    finally:
        mock.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
