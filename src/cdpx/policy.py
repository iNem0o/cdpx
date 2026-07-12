"""Politique d'exécution centralisée pour les usages agentiques de cdpx.

Chaque commande navigateur s'exécute dans une session supervisée fail-closed:
run, target, autorité et origines sont fixés avant toute connexion.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicyError(ValueError):
    """Une commande ou une connexion viole la politique du run."""


class Authority(StrEnum):
    OBSERVATION = "observation"
    INTERACTION = "interaction"
    PRIVILEGED = "privileged"


_AUTHORITY_RANK = {
    Authority.OBSERVATION: 0,
    Authority.INTERACTION: 1,
    Authority.PRIVILEGED: 2,
}

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DNS_PATTERN_RE = re.compile(r"(?:\*\.)?[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")


@dataclass(frozen=True)
class ExecutionContext:
    authority: Authority
    origins: tuple[str, ...]
    run_id: str
    target_id: str
    session_id: str
    content_trust: str = "untrusted"

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        target_id: str,
        authority: str | Authority,
        origins: str,
        session_id: str | None = None,
    ) -> ExecutionContext:
        if not _RUN_ID_RE.fullmatch(run_id or ""):
            raise PolicyError("session: --run-id explicite et sûr requis")
        if not target_id:
            raise PolicyError("session: --target explicite requis")
        if not session_id:
            raise PolicyError("session: identifiant de session explicite requis")
        try:
            grant = Authority(authority)
        except ValueError as e:
            raise PolicyError(f"niveau d'autorité inconnu: {authority}") from e
        return cls(
            authority=grant,
            origins=parse_origins(origins, required=True),
            run_id=run_id,
            target_id=target_id,
            session_id=session_id,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "target_id": self.target_id,
            "authority": self.authority.value,
            "content_trust": self.content_trust,
        }


_OBSERVATION_COMMANDS = {
    "version",
    "goto",
    "wait",
    "text",
    "html",
    "count",
    "screenshot",
    "pdf",
    "console",
    "network",
    "seo",
    "metrics",
    "a11y",
    "coverage",
    "frame",
}
_INTERACTION_COMMANDS = {"click", "type", "key"}
_PRIVILEGED_COMMANDS = {"eval", "cookies", "storage", "profiler", "intercept", "emulate", "session"}
_COMPOSED_COMMANDS = {"dom-diff", "record"}


def authority_for(command: str, action: list[str] | None = None) -> Authority:
    """Retourne l'autorité minimale, et refuse toute commande non classée."""
    if command in _OBSERVATION_COMMANDS:
        return Authority.OBSERVATION
    if command in _INTERACTION_COMMANDS:
        return Authority.INTERACTION
    if command in _PRIVILEGED_COMMANDS:
        return Authority.PRIVILEGED
    if command == "tabs":
        if action and action[0] == "list":
            return Authority.OBSERVATION
        raise PolicyError("action tabs non classée par la politique")
    if command == "vitals":
        return Authority.INTERACTION if action and action[0] == "click" else Authority.OBSERVATION
    if command in _COMPOSED_COMMANDS:
        if not action:
            return Authority.OBSERVATION
        return action_authority(action)
    if command in {"replay", "scenario"}:
        # Ces commandes doivent être préflightées intégralement. Sans la liste
        # d'actions validée, le défaut sûr est le niveau maximal.
        return max_authority(action or []) if action else Authority.PRIVILEGED
    raise PolicyError(f"commande non classée par la politique: {command}")


def action_authority(action: list[str]) -> Authority:
    if not action:
        raise PolicyError("action composée manquante")
    verb = action[0]
    if verb in {"goto", "wait"}:
        return Authority.OBSERVATION
    if verb in _INTERACTION_COMMANDS:
        return Authority.INTERACTION
    if verb == "eval":
        return Authority.PRIVILEGED
    raise PolicyError(f"action non classée par la politique: {verb}")


def max_authority(actions: list[Any]) -> Authority:
    required = Authority.OBSERVATION
    for item in actions:
        action = item if isinstance(item, list) else getattr(item, "action", None)
        if not isinstance(action, list):
            raise PolicyError("liste d'actions préflightée requise")
        candidate = action_authority(action)
        if _AUTHORITY_RANK[candidate] > _AUTHORITY_RANK[required]:
            required = candidate
    return required


def assert_authorized(
    context: ExecutionContext,
    command: str,
    action: list[str] | None = None,
) -> None:
    required = authority_for(command, action)
    assert_grant(context, required, command)


def assert_grant(context: ExecutionContext, required: Authority, label: str) -> None:
    if _AUTHORITY_RANK[context.authority] < _AUTHORITY_RANK[required]:
        raise PolicyError(
            f"session: {label} requiert {required.value}, authority={context.authority.value}"
        )


def parse_origins(raw: str | None, *, required: bool = True) -> tuple[str, ...]:
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if required and not items:
        raise PolicyError("session: CDPX_ORIGINS obligatoire et non vide")
    return tuple(dict.fromkeys(_canonical_origin_pattern(item) for item in items))


def _canonical_origin_pattern(value: str) -> str:
    if "://" not in value:
        raise PolicyError(f"origine invalide: {value}")
    scheme, authority = value.split("://", 1)
    scheme = scheme.lower()
    if scheme not in {"http", "https"}:
        raise PolicyError(f"origine HTTP(S) requise: {value}")
    if not authority or any(marker in authority for marker in ("/", "?", "#", "@")):
        raise PolicyError(f"origine sans chemin/credentials requise: {value}")
    host, port = _split_host_port(authority)
    host = host.lower()
    if host == "*" or not _valid_origin_host(host):
        raise PolicyError(f"hôte d'origine invalide: {value}")
    if port is not None:
        if port != "*" and (not port.isdigit() or not 1 <= int(port) <= 65535):
            raise PolicyError(f"port d'origine invalide: {value}")
        if (scheme, port) in {("http", "80"), ("https", "443")}:
            port = None
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{rendered_host}" + (f":{port}" if port is not None else "")


def _split_host_port(authority: str) -> tuple[str, str | None]:
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise PolicyError(f"adresse IPv6 invalide: {authority}")
        host = authority[1:end]
        suffix = authority[end + 1 :]
        if not suffix:
            return host, None
        if not suffix.startswith(":"):
            raise PolicyError(f"origine invalide: {authority}")
        return host, suffix[1:]
    if authority.count(":") > 1:
        raise PolicyError(f"IPv6 entre crochets requise: {authority}")
    if ":" in authority:
        return tuple(authority.rsplit(":", 1))  # type: ignore[return-value]
    return authority, None


def _valid_origin_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(_DNS_PATTERN_RE.fullmatch(host)) and ".." not in host


def origin_from_url(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as e:
        raise PolicyError(f"URL invalide: {url}") from e
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise PolicyError(f"origine HTTP(S) indéterminable: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise PolicyError("credentials interdits dans une URL de politique")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{rendered_host}" + (f":{port}" if port is not None else "")


def assert_url_allowed(url: str, origins: tuple[str, ...]) -> None:
    origin = origin_from_url(url)
    scheme, host, port = _origin_parts(origin)
    if not origins or not any(
        _origin_pattern_matches(pattern, scheme=scheme, host=host, port=port) for pattern in origins
    ):
        raise PolicyError(f"origine refusée par la politique du run: {origin}")


def _origin_parts(origin: str) -> tuple[str, str, int | None]:
    """Décompose une origine canonique sans appliquer de glob textuel.

    ``fnmatch`` ne convient pas ici: les crochets d'une IPv6 sont interprétés
    comme une classe de caractères et ``*`` peut traverser les séparateurs de
    l'origine. Le matching est donc effectué champ par champ.
    """
    try:
        parsed = urllib.parse.urlsplit(origin)
        port = parsed.port
    except ValueError as e:
        raise PolicyError(f"origine invalide: {origin}") from e
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PolicyError(f"origine HTTP(S) indéterminable: {origin}")
    return parsed.scheme, parsed.hostname.lower(), port


def _origin_pattern_matches(
    pattern: str,
    *,
    scheme: str,
    host: str,
    port: int | None,
) -> bool:
    pattern_scheme, authority = pattern.split("://", 1)
    pattern_host, raw_port = _split_host_port(authority)
    if pattern_scheme != scheme:
        return False
    pattern_host = pattern_host.lower()
    if pattern_host.startswith("*."):
        suffix = pattern_host[1:]
        if not host.endswith(suffix) or host == pattern_host[2:]:
            return False
    elif host != pattern_host:
        return False
    if raw_port == "*":
        return True
    expected_port = int(raw_port) if raw_port is not None else None
    return port == expected_port


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def assert_loopback_endpoint(discovery_host: str, websocket_url: str | None = None) -> None:
    if not is_loopback_host(discovery_host):
        raise PolicyError(f"session: endpoint de découverte non loopback: {discovery_host}")
    if websocket_url is None:
        return
    try:
        parsed = urllib.parse.urlsplit(websocket_url)
    except ValueError as e:
        raise PolicyError("WebSocket CDP invalide") from e
    if parsed.scheme not in {"ws", "wss"} or not is_loopback_host(parsed.hostname):
        raise PolicyError(f"session: WebSocket CDP non loopback: {websocket_url}")


def validate_target(target: dict[str, Any], expected_id: str) -> dict[str, Any]:
    if target.get("id") != expected_id:
        raise PolicyError(
            f"target non attribué au run: attendu {expected_id}, reçu {target.get('id')}"
        )
    if target.get("type") != "page":
        raise PolicyError("le target attribué doit être de type page")
    ws_url = target.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise PolicyError("le target attribué n'expose aucun WebSocket CDP")
    return target
