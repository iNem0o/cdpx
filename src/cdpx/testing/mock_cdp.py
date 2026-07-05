"""Mock CDP: un faux Chrome, déterministe, pour tester cdpx sans navigateur.

Ce que ça simule:
- l'API HTTP de découverte (/json/list, /json/new, /json/activate, /json/close,
  /json/version) sur un port,
- l'endpoint WebSocket par target (ws://.../devtools/page/{id}) sur un autre
  port (Chrome n'en utilise qu'un, mais le client suit le webSocketDebuggerUrl
  publié par la découverte, donc la compat est totale).

Ce que ça garantit:
- chaque commande reçue est ENREGISTRÉE (self.commands) -> les tests valident
  le protocole exact émis par cdpx (méthodes, params, ordre),
- les réponses et les évènements sont SCRIPTÉS -> zéro aléa, zéro réseau
  externe, exécutable dans n'importe quel CI.

Ce que ça ne teste PAS (et c'est assumé): le comportement réel de Blink/V8.
C'est le rôle du e2e Chrome réel (milestone M1) qui réutilise les mêmes
fixtures HTML.
"""

from __future__ import annotations

import base64
import http.server
import json
import threading
import urllib.parse
import uuid
from collections import deque
from typing import Any

from websockets.sync.server import serve

# PNG 1x1 transparent, valide. PDF minimal, valide en signature.
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
    """Faux Chrome scriptable. Voir tests/conftest.py pour l'usage type."""

    def __init__(self) -> None:
        self.targets: dict[str, dict] = {}
        self.commands: list[tuple[str, str, dict]] = []  # (target_id, method, params)
        self.eval_rules: list[tuple[str, deque]] = []  # (substring, valeurs successives)
        self.console_script: list[dict] = []  # évènements émis après Runtime.enable
        self.network_script: list[dict] = []  # évènements émis après Page.navigate
        self.error_methods: set[str] = set()  # méthodes qui répondent une erreur CDP
        self.cookies: list[dict] = [dict(c) for c in DEFAULT_COOKIES]
        self._http: http.server.ThreadingHTTPServer | None = None
        self._ws_server = None
        self.http_port = 0
        self.ws_port = 0
        self._add_target("about:blank", "Mock Tab")

    # -- scripting -------------------------------------------------------------
    def on_eval(self, substring: str, *values: Any) -> None:
        """Quand Runtime.evaluate contient `substring`, répondre ces valeurs en séquence
        (la dernière se répète)."""
        self.eval_rules.append((substring, deque(values)))

    def script_console(self, entries: list[dict]) -> None:
        self.console_script = entries

    def script_network(self, events: list[dict]) -> None:
        self.network_script = events

    def commands_for(self, method: str) -> list[dict]:
        return [p for (_t, m, p) in self.commands if m == method]

    def fail_on(self, method: str) -> None:
        """Scripter une erreur CDP pour cette méthode (tester les chemins de repli)."""
        self.error_methods.add(method)

    # -- cycle de vie ------------------------------------------------------------
    def start(self) -> MockCDP:
        self._start_ws()
        self._start_http()
        return self

    def stop(self) -> None:
        if self._http:
            self._http.shutdown()
        if self._ws_server:
            self._ws_server.shutdown()

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
        t["webSocketDebuggerUrl"] = f"ws://127.0.0.1:{self.ws_port}/devtools/page/{tid}"
        return t

    # -- serveur HTTP découverte ---------------------------------------------------
    def _start_http(self) -> None:
        mock = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _reply(self, obj, status=200):
                body = (json.dumps(obj) if not isinstance(obj, str) else obj).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                if path in ("/json", "/json/list"):
                    return self._reply([mock._public_target(t) for t in mock.targets])
                if path == "/json/version":
                    return self._reply(
                        {
                            "Browser": "MockChrome/126.0",
                            "Protocol-Version": "1.3",
                            "webSocketDebuggerUrl": f"ws://127.0.0.1:{mock.ws_port}/devtools/browser",
                        }
                    )
                if path == "/json/new":
                    url = urllib.parse.unquote(parsed.query) or "about:blank"
                    return self._reply(mock._add_target(url, "New Tab"))
                if path.startswith("/json/activate/"):
                    tid = path.rsplit("/", 1)[1]
                    if tid in mock.targets:
                        return self._reply("Target activated")
                    return self._reply("No such target id", 404)
                if path.startswith("/json/close/"):
                    tid = path.rsplit("/", 1)[1]
                    if mock.targets.pop(tid, None):
                        return self._reply("Target is closing")
                    return self._reply("No such target id", 404)
                return self._reply({"error": "not found"}, 404)

            do_GET = _route
            do_PUT = _route

        self._http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.http_port = self._http.server_address[1]
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    # -- serveur WebSocket CDP -------------------------------------------------------
    def _start_ws(self) -> None:
        mock = self

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

        self._ws_server = serve(handler, "127.0.0.1", 0)
        self.ws_port = self._ws_server.socket.getsockname()[1]
        threading.Thread(target=self._ws_server.serve_forever, daemon=True).start()

    # -- protocole scripté --------------------------------------------------------
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


def main() -> None:  # pragma: no cover - utilitaire manuel (make mock)
    import time

    mock = MockCDP().start()
    print(f"Mock CDP: découverte http://127.0.0.1:{mock.http_port}/json  ws port {mock.ws_port}")
    print(f"  essayez: cdpx --port {mock.http_port} tabs list")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        mock.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
