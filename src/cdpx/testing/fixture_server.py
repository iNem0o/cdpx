"""Serveur HTTP de fixtures: le "site témoin" déterministe.

Rôle: fournir des pages HTML statiques et des endpoints API au comportement
figé, pour que chaque primitive cdpx ait un terrain de jeu reproductible —
d'abord pour le e2e Chrome réel (M1), et dès maintenant testé unitairement
(le serveur lui-même est sous test: tests/test_fixture_server.py).

Règles de déterminisme:
- aucun contenu dépendant de l'heure, de l'aléa ou du réseau externe,
- Cache-Control: no-store partout (jamais d'état de cache entre deux runs),
- les seuls délais sont EXPLICITES et pilotés par l'appelant (/api/slow?ms=N).

Endpoints API (en plus des fichiers statiques de tests/fixtures/):
  GET /api/json          -> payload JSON fixe
  GET /api/slow?ms=N     -> payload JSON après N millisecondes (défaut 200)
  GET /api/status/CODE   -> répond avec le code HTTP demandé
  ANY /api/echo          -> renvoie méthode, chemin, body reçus
  GET /api/set-cookie    -> pose Set-Cookie: fixture=on
  GET /api/profiler-sim  -> pose X-Debug-Token-Link vers le faux Web Profiler
  GET /_profiler/TOKEN?panel=X -> HTML de panel figé (tests/fixtures/profiler/X.html,
                            markup WebProfilerBundle réel élagué; défaut: request)
"""

from __future__ import annotations

import http.server
import json
import pathlib
import re
import threading
import time
import urllib.parse

DEFAULT_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "fixtures"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".png": "image/png",
}


def _make_handler(root: pathlib.Path):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, body: bytes, status=200, ctype="application/json", extra=None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Fixture-Server", "cdpx")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _api(self, parsed) -> bool:
            path = parsed.path
            if path == "/api/json":
                self._send(
                    json.dumps({"ok": True, "items": [1, 2, 3], "source": "fixture"}).encode()
                )
                return True
            if path == "/api/slow":
                qs = urllib.parse.parse_qs(parsed.query)
                ms = int(qs.get("ms", ["200"])[0])
                time.sleep(ms / 1000)
                self._send(json.dumps({"ok": True, "slept_ms": ms}).encode())
                return True
            if path.startswith("/api/status/"):
                code = int(path.rsplit("/", 1)[1])
                self._send(json.dumps({"status": code}).encode(), status=code)
                return True
            if path == "/api/echo":
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                self._send(
                    json.dumps({"method": self.command, "path": self.path, "body": body}).encode()
                )
                return True
            if path == "/api/set-cookie":
                self._send(
                    json.dumps({"cookie": "fixture=on"}).encode(),
                    extra={"Set-Cookie": "fixture=on; Path=/"},
                )
                return True
            if path == "/api/profiler-sim":
                host = self.headers.get("Host", "127.0.0.1")
                self._send(
                    json.dumps({"ok": True, "profiler": "sim"}).encode(),
                    extra={"X-Debug-Token-Link": f"http://{host}/_profiler/fixed-token"},
                )
                return True
            return False

        def _serve(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path.startswith("/api/"):
                if not self._api(parsed):
                    self._send(b'{"error": "unknown api"}', status=404)
                return
            if parsed.path.startswith("/_profiler/"):
                qs = urllib.parse.parse_qs(parsed.query)
                panel = qs.get("panel", ["request"])[0]
                target = root / "profiler" / f"{panel}.html"
                if not re.fullmatch(r"[a-z_]+", panel) or not target.is_file():
                    self._send(b'{"error": "panel inconnu"}', status=404)
                    return
                self._send(target.read_bytes(), ctype="text/html; charset=utf-8")
                return
            rel = parsed.path.lstrip("/") or "index.html"
            target = (root / rel).resolve()
            if root.resolve() not in target.parents and target != root.resolve():
                self._send(b'{"error": "forbidden"}', status=403)
                return
            if not target.is_file():
                self._send(b'{"error": "not found"}', status=404)
                return
            ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
            self._send(target.read_bytes(), ctype=ctype)

        do_GET = _serve
        do_POST = _serve

    return Handler


class FixtureServer:
    def __init__(self, root: pathlib.Path | str | None = None, port: int = 0):
        self.root = pathlib.Path(root) if root else DEFAULT_FIXTURES
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), _make_handler(self.root)
        )
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    def start(self) -> FixtureServer:
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        self._server.shutdown()

    def __enter__(self) -> FixtureServer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def main() -> None:  # pragma: no cover - utilitaire manuel (make fixtures)
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--root", default=None)
    args = p.parse_args()
    srv = FixtureServer(root=args.root, port=args.port).start()
    print(f"Fixtures: {srv.base_url}  (racine: {srv.root})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
