"""Centralized execution policy for cdpx's agentic usages.

Each browser command runs inside a fail-closed supervised session:
run, target, authority and origins are fixed before any connection.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from cdpx.action_model import (
    BrowserAction,
    ClickAction,
    EvalAction,
    GotoAction,
    KeyAction,
    TypeAction,
    WaitAction,
)
from cdpx.cdp_types import DiscoveryTarget


class PolicyError(ValueError):
    """A command or connection violates the run's policy."""


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
            raise PolicyError("session: explicit and safe --run-id required")
        if not target_id:
            raise PolicyError("session: explicit --target required")
        if not session_id:
            raise PolicyError("session: explicit session identifier required")
        try:
            grant = Authority(authority)
        except ValueError as e:
            raise PolicyError(f"unknown authority level: {authority}") from e
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


AuthorityMode = Literal["fixed", "preflight"]
DestinationSource = Literal["none", "url", "cookie-url", "action-goto"]
CurrentOriginPolicy = Literal["always", "never", "unless-destination", "action-non-navigation"]


@dataclass(frozen=True)
class CommandSemantics:
    authority_mode: AuthorityMode
    authority: Authority | None = None
    destination_source: DestinationSource = "none"
    current_origin: CurrentOriginPolicy = "always"


def _fixed(
    authority: Authority,
    *,
    destination_source: DestinationSource = "none",
    current_origin: CurrentOriginPolicy = "always",
) -> CommandSemantics:
    return CommandSemantics(
        "fixed",
        authority,
        destination_source,
        current_origin,
    )


COMMAND_SEMANTICS = {
    "version": _fixed(Authority.OBSERVATION),
    "goto": _fixed(Authority.OBSERVATION, destination_source="url", current_origin="never"),
    "wait": _fixed(Authority.OBSERVATION),
    "text": _fixed(Authority.OBSERVATION),
    "html": _fixed(Authority.OBSERVATION),
    "count": _fixed(Authority.OBSERVATION),
    "screenshot": _fixed(Authority.OBSERVATION),
    "pdf": _fixed(Authority.OBSERVATION),
    "console": _fixed(Authority.OBSERVATION),
    "network": _fixed(Authority.OBSERVATION, destination_source="url", current_origin="never"),
    "seo": _fixed(
        Authority.OBSERVATION,
        destination_source="url",
        current_origin="unless-destination",
    ),
    "metrics": _fixed(Authority.OBSERVATION),
    "a11y": _fixed(Authority.OBSERVATION),
    "coverage": _fixed(Authority.OBSERVATION, destination_source="url", current_origin="never"),
    "frame": _fixed(Authority.OBSERVATION),
    "click": _fixed(Authority.INTERACTION),
    "type": _fixed(Authority.INTERACTION),
    "key": _fixed(Authority.INTERACTION),
    "eval": _fixed(Authority.PRIVILEGED),
    "cookies": _fixed(
        Authority.PRIVILEGED,
        destination_source="cookie-url",
        current_origin="never",
    ),
    "storage": _fixed(Authority.PRIVILEGED),
    "profiler": _fixed(
        Authority.PRIVILEGED,
        destination_source="url",
        current_origin="never",
    ),
    "intercept": _fixed(
        Authority.PRIVILEGED,
        destination_source="action-goto",
        current_origin="never",
    ),
    "emulate": _fixed(
        Authority.PRIVILEGED,
        destination_source="action-goto",
        current_origin="action-non-navigation",
    ),
    "tabs": _fixed(Authority.OBSERVATION),
    "vitals": _fixed(
        Authority.OBSERVATION,
        destination_source="url",
        current_origin="never",
    ),
    "dom-diff": _fixed(
        Authority.OBSERVATION,
        destination_source="action-goto",
    ),
    "record": CommandSemantics(
        "preflight",
        destination_source="action-goto",
        current_origin="action-non-navigation",
    ),
    "replay": CommandSemantics("preflight", current_origin="never"),
    "scenario": CommandSemantics("preflight", current_origin="never"),
}

# Session lifecycle commands do not use a browser authority grant. ``start``
# creates that grant; ``status`` and ``stop`` authenticate possession of the
# private manifest with its exact run/target identity at the session boundary.
LIFECYCLE_COMMANDS = frozenset({"session"})


def command_semantics(command: str) -> CommandSemantics:
    if command in LIFECYCLE_COMMANDS:
        raise PolicyError(f"lifecycle command outside the browser authority matrix: {command}")
    try:
        return COMMAND_SEMANTICS[command]
    except KeyError as error:
        raise PolicyError(f"command not classified by policy: {command}") from error


def authority_for(command: str) -> Authority:
    """Return the command's baseline authority without classifying actions."""
    semantics = command_semantics(command)
    if semantics.authority_mode == "fixed":
        if semantics.authority is None:
            raise PolicyError(f"missing fixed authority: {command}")
        return semantics.authority
    if semantics.authority_mode == "preflight":
        return Authority.PRIVILEGED
    raise PolicyError(f"unhandled authority mode: {semantics.authority_mode}")


_ACTION_TYPES = (GotoAction, WaitAction, ClickAction, TypeAction, KeyAction, EvalAction)


def action_authority(action: BrowserAction) -> Authority:
    if isinstance(action, GotoAction | WaitAction):
        return Authority.OBSERVATION
    if isinstance(action, ClickAction | TypeAction | KeyAction):
        return Authority.INTERACTION
    if isinstance(action, EvalAction):
        return Authority.PRIVILEGED
    raise PolicyError(f"action not classified by policy: {action!r}")


def max_authority(actions: list[Any]) -> Authority:
    required = Authority.OBSERVATION
    for item in actions:
        action = item if isinstance(item, _ACTION_TYPES) else getattr(item, "action", None)
        if not isinstance(action, _ACTION_TYPES):
            raise PolicyError("preflighted action list required")
        candidate = action_authority(action)
        if _AUTHORITY_RANK[candidate] > _AUTHORITY_RANK[required]:
            required = candidate
    return required


def assert_authorized(
    context: ExecutionContext,
    command: str,
) -> None:
    required = authority_for(command)
    assert_grant(context, required, command)


def assert_grant(context: ExecutionContext, required: Authority, label: str) -> None:
    if _AUTHORITY_RANK[context.authority] < _AUTHORITY_RANK[required]:
        raise PolicyError(
            f"session: {label} requires {required.value}, authority={context.authority.value}"
        )


def parse_origins(raw: str | None, *, required: bool = True) -> tuple[str, ...]:
    items = [item.strip() for item in (raw or "").split(",") if item.strip()]
    if required and not items:
        raise PolicyError("session: CDPX_ORIGINS required and non-empty")
    return tuple(dict.fromkeys(_canonical_origin_pattern(item) for item in items))


def _canonical_origin_pattern(value: str) -> str:
    if "://" not in value:
        raise PolicyError(f"invalid origin: {value}")
    scheme, authority = value.split("://", 1)
    scheme = scheme.lower()
    if scheme not in {"http", "https"}:
        raise PolicyError(f"HTTP(S) origin required: {value}")
    if not authority or any(marker in authority for marker in ("/", "?", "#", "@")):
        raise PolicyError(f"origin without path/credentials required: {value}")
    host, port = _split_host_port(authority)
    host = host.lower()
    if host == "*" or not _valid_origin_host(host):
        raise PolicyError(f"invalid origin host: {value}")
    if port is not None:
        if port != "*" and (not port.isdigit() or not 1 <= int(port) <= 65535):
            raise PolicyError(f"invalid origin port: {value}")
        if (scheme, port) in {("http", "80"), ("https", "443")}:
            port = None
    rendered_host = f"[{host}]" if ":" in host else host
    return f"{scheme}://{rendered_host}" + (f":{port}" if port is not None else "")


def _split_host_port(authority: str) -> tuple[str, str | None]:
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise PolicyError(f"invalid IPv6 address: {authority}")
        host = authority[1:end]
        suffix = authority[end + 1 :]
        if not suffix:
            return host, None
        if not suffix.startswith(":"):
            raise PolicyError(f"invalid origin: {authority}")
        return host, suffix[1:]
    if authority.count(":") > 1:
        raise PolicyError(f"bracketed IPv6 required: {authority}")
    if ":" in authority:
        host, port = authority.rsplit(":", 1)
        return host, port
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
        raise PolicyError(f"invalid URL: {url}") from e
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise PolicyError(f"undeterminable HTTP(S) origin: {url}")
    if parsed.username is not None or parsed.password is not None:
        raise PolicyError("credentials forbidden in a policy URL")
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
        raise PolicyError(f"origin rejected by the run's policy: {origin}")


def _origin_parts(origin: str) -> tuple[str, str, int | None]:
    """Decompose a canonical origin without applying a textual glob.

    ``fnmatch`` doesn't fit here: an IPv6 address's brackets are interpreted
    as a character class and ``*`` can cross the origin's separators.
    Matching is therefore performed field by field.
    """
    try:
        parsed = urllib.parse.urlsplit(origin)
        port = parsed.port
    except ValueError as e:
        raise PolicyError(f"invalid origin: {origin}") from e
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PolicyError(f"undeterminable HTTP(S) origin: {origin}")
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
        raise PolicyError(f"session: non-loopback discovery endpoint: {discovery_host}")
    if websocket_url is None:
        return
    try:
        parsed = urllib.parse.urlsplit(websocket_url)
    except ValueError as e:
        raise PolicyError("invalid CDP WebSocket") from e
    if parsed.scheme not in {"ws", "wss"} or not is_loopback_host(parsed.hostname):
        raise PolicyError(f"session: non-loopback CDP WebSocket: {websocket_url}")


def validate_target(target: DiscoveryTarget, expected_id: str) -> DiscoveryTarget:
    if target.get("id") != expected_id:
        raise PolicyError(
            f"target not assigned to the run: expected {expected_id}, got {target.get('id')}"
        )
    if target.get("type") != "page":
        raise PolicyError("the assigned target must be of type page")
    ws_url = target.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url:
        raise PolicyError("the assigned target exposes no CDP WebSocket")
    return target
