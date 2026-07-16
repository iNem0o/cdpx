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
import math
import time
from typing import Any, TypeAlias, TypeGuard

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
InboundMessage: TypeAlias = CDPEvent | CDPResponse


def validate_time_budget(value: float, label: str) -> float:
    """Return a finite, non-negative CDP time budget."""

    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} numérique requis")
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} fini et positif ou nul requis")
    return rendered


class CDPError(RuntimeError):
    """Erreur retournée par Chrome pour une commande."""

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
            raise CDPTransportError(f"connexion CDP impossible vers {ws_url}: {error}") from error

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
    def send(
        self,
        method: str,
        params: CDPParams | None = None,
        timeout: float | None = None,
    ) -> CDPResult:
        """Envoie une commande, bufferise les évènements, retourne `result`."""
        timeout = self._timeout_seconds(timeout)
        cmd_id = self.send_nowait(method, params)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"réponse à {method}")
            if _is_response(msg):
                if msg["id"] == cmd_id:
                    return self._response_result(msg)
                self._responses[msg["id"]] = msg
            elif _is_event(msg):
                self.events.append(msg)

    def send_nowait(self, method: str, params: CDPParams | None = None) -> int:
        """Envoie une commande sans attendre sa réponse.

        Utile quand la commande déclenche immédiatement des évènements bloquants
        auxquels il faut répondre avant que Chrome renvoie la réponse de commande
        elle-même, typiquement Fetch.requestPaused sur la requête principale.
        """
        self._id += 1
        cmd_id = self._id
        command: CDPCommand = {"id": cmd_id, "method": method, "params": params or {}}
        payload = json.dumps(command)
        try:
            self._ws.send(payload)
        except Exception as error:
            raise CDPTransportError(
                f"transport interrompu pendant envoi {method}: {error}"
            ) from error
        return cmd_id

    def wait_response(self, cmd_id: int, timeout: float | None = None) -> CDPResult:
        """Attend la réponse d'une commande envoyée avec :meth:`send_nowait`.

        Les évènements bloquants peuvent être traités avec ``next_event`` entre
        l'envoi et cet appel; les réponses croisées sont conservées au lieu
        d'être perdues.
        """
        timeout = self._timeout_seconds(timeout)
        buffered = self._responses.pop(cmd_id, None)
        if buffered is not None:
            return self._response_result(buffered)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"réponse à la commande {cmd_id}")
            if _is_response(msg):
                if msg["id"] == cmd_id:
                    return self._response_result(msg)
                self._responses[msg["id"]] = msg
            elif _is_event(msg):
                self.events.append(msg)

    def wait_event(self, name: str, timeout: float | None = None) -> CDPEvent:
        """Attend (ou retrouve dans le buffer) le prochain évènement `name`."""
        timeout = self._timeout_seconds(timeout)
        for i, ev in enumerate(self.events):
            if ev["method"] == name:
                return self.events.pop(i)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, f"évènement {name}")
            if _is_event(msg):
                if msg["method"] == name:
                    return msg
                self.events.append(msg)
            elif _is_response(msg):
                self._responses[msg["id"]] = msg

    def next_event(self, timeout: float | None = None) -> CDPEvent:
        """Retourne le prochain évènement CDP, quel que soit son nom."""
        timeout = self._timeout_seconds(timeout)
        if self.events:
            return self.events.pop(0)
        deadline = time.monotonic() + timeout
        while True:
            msg = self._recv(deadline, "prochain évènement")
            if _is_event(msg):
                return msg
            if _is_response(msg):
                self._responses[msg["id"]] = msg

    def collect_events(
        self, duration: float, names: tuple[str, ...] | None = None
    ) -> list[CDPEvent]:
        """Collecte passivement les évènements pendant `duration` secondes.

        Un timeout court de polling est attendu et relance la collecte. Toute
        rupture de transport ou trame invalide lève ``CDPTransportError`` au
        lieu de transformer une collecte incomplète en succès partiel.
        """
        duration = validate_time_budget(duration, "durée de collecte")
        deadline = time.monotonic() + duration
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self._receive_message(
                    min(remaining, 0.25),
                    "collecte passive d'évènements",
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
        """Vide le buffer d'évènements (sans lecture réseau)."""
        return self.collect_events(0, names)

    # -- interne -------------------------------------------------------------
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
            raise CDPTimeout(f"timeout en attendant {waiting_for}")
        try:
            return self._receive_message(remaining, waiting_for)
        except TimeoutError as e:
            raise CDPTimeout(f"timeout en attendant {waiting_for}") from e

    def _receive_message(self, timeout: float, waiting_for: str) -> InboundMessage:
        try:
            raw = self._ws.recv(timeout=timeout)
        except TimeoutError:
            raise
        except Exception as error:
            raise CDPTransportError(
                f"transport interrompu pendant {waiting_for}: {error}"
            ) from error
        try:
            message = json.loads(raw)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as error:
            raise CDPTransportError(f"message CDP invalide pendant {waiting_for}") from error
        if not isinstance(message, dict):
            raise CDPTransportError(f"objet CDP requis pendant {waiting_for}")
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
            raise CDPTransportError(f"évènement CDP invalide pendant {waiting_for}")
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            raise CDPTransportError(f"params CDP invalides pendant {waiting_for}")
        event: CDPEvent = {"method": message["method"]}
        if params is not None:
            event["params"] = params
        return event

    command_id = message.get("id")
    if isinstance(command_id, bool) or not isinstance(command_id, int):
        raise CDPTransportError(f"réponse CDP sans id valide pendant {waiting_for}")
    has_result = "result" in message
    has_error = "error" in message
    if has_result == has_error:
        raise CDPTransportError(f"réponse CDP ambiguë pendant {waiting_for}")
    response: CDPResponse = {"id": command_id}
    if has_result:
        result = message["result"]
        if not isinstance(result, dict):
            raise CDPTransportError(f"result CDP invalide pendant {waiting_for}")
        response["result"] = result
        return response
    error = message["error"]
    if not isinstance(error, dict):
        raise CDPTransportError(f"error CDP invalide pendant {waiting_for}")
    code = error.get("code")
    error_message = error.get("message")
    if isinstance(code, bool) or not isinstance(code, int) or not isinstance(error_message, str):
        raise CDPTransportError(f"error CDP mal formée pendant {waiting_for}")
    payload: CDPErrorPayload = {"code": code, "message": error_message}
    if "data" in error:
        payload["data"] = error["data"]
    response["error"] = payload
    return response
