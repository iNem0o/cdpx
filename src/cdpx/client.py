"""Client CDP synchrone.

Un client = une connexion WebSocket vers UN target (page) Chrome.
Modèle: JSON-RPC-like. Commandes {id, method, params} -> réponse {id, result|error}.
Les évènements ({method, params} sans id) arrivant entre-temps sont bufferisés
dans `self.events` et consommables via wait_event()/collect_events().

Choix délibérés (voir docs/CONTEXT.md):
- sync (websockets.sync): un CLI est séquentiel par nature, pas d'asyncio à traîner.
- pas de sessionId/flatten: on se connecte directement au webSocketDebuggerUrl
  du target page fourni par la découverte HTTP (/json), comme un humain avec
  chrome --remote-debugging-port.
"""

from __future__ import annotations

import json
import time
from typing import Any

from websockets.sync.client import connect

DEFAULT_TIMEOUT = 15.0


class CDPError(RuntimeError):
    """Erreur retournée par Chrome pour une commande."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"CDP error {code}: {message}" + (f" ({data})" if data else ""))
        self.code = code
        self.data = data


class CDPTimeout(TimeoutError):
    pass


class CDPClient:
    def __init__(self, ws_url: str, timeout: float = DEFAULT_TIMEOUT):
        self.ws_url = ws_url
        self.timeout = timeout
        self._id = 0
        self.events: list[dict] = []
        self._ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=timeout)

    # -- contexte ------------------------------------------------------------
    def __enter__(self) -> CDPClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass

    # -- coeur ---------------------------------------------------------------
    def send(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        """Envoie une commande, bufferise les évènements, retourne `result`."""
        timeout = timeout or self.timeout
        cmd_id = self.send_nowait(method, params)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"réponse à {method}")
            if msg.get("id") == cmd_id:
                if "error" in msg:
                    err = msg["error"]
                    raise CDPError(err.get("code", -1), err.get("message", "?"), err.get("data"))
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def send_nowait(self, method: str, params: dict | None = None) -> int:
        """Envoie une commande sans attendre sa réponse.

        Utile quand la commande déclenche immédiatement des évènements bloquants
        auxquels il faut répondre avant que Chrome renvoie la réponse de commande
        elle-même, typiquement Fetch.requestPaused sur la requête principale.
        """
        self._id += 1
        cmd_id = self._id
        self._ws.send(json.dumps({"id": cmd_id, "method": method, "params": params or {}}))
        return cmd_id

    def wait_event(self, name: str, timeout: float | None = None) -> dict:
        """Attend (ou retrouve dans le buffer) le prochain évènement `name`."""
        for i, ev in enumerate(self.events):
            if ev["method"] == name:
                return self.events.pop(i)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            msg = self._recv(deadline, f"évènement {name}")
            if msg.get("method") == name:
                return msg
            if "method" in msg:
                self.events.append(msg)

    def next_event(self, timeout: float | None = None) -> dict:
        """Retourne le prochain évènement CDP, quel que soit son nom."""
        if self.events:
            return self.events.pop(0)
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            msg = self._recv(deadline, "prochain évènement")
            if "method" in msg:
                return msg

    def collect_events(self, duration: float, names: tuple[str, ...] | None = None) -> list[dict]:
        """Collecte passivement les évènements pendant `duration` secondes."""
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = self._ws.recv(timeout=min(remaining, 0.25))
            except TimeoutError:
                continue
            except Exception:
                break
            msg = json.loads(raw)
            if "method" in msg:
                self.events.append(msg)
        out = [ev for ev in self.events if names is None or ev["method"] in names]
        if names is not None:
            self.events = [ev for ev in self.events if ev["method"] not in names]
        else:
            self.events = []
        return out

    def drain_events(self, names: tuple[str, ...] | None = None) -> list[dict]:
        """Vide le buffer d'évènements (sans lecture réseau)."""
        return self.collect_events(0, names)

    # -- interne -------------------------------------------------------------
    def _recv(self, deadline: float, waiting_for: str) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CDPTimeout(f"timeout en attendant {waiting_for}")
        try:
            raw = self._ws.recv(timeout=remaining)
        except TimeoutError as e:
            raise CDPTimeout(f"timeout en attendant {waiting_for}") from e
        return json.loads(raw)
