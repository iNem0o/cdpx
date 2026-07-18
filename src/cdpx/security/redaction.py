"""Deterministic redaction of cdpx outputs before serialization or persistence.

The module does not try to guess every piece of personal data. It handles
explicitly registered secrets and a few high-confidence forms (Authorization
Bearer, JWT, URL, and structured headers). Free-form or binary content must
stay classified as untrusted artifacts even after this cleanup.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

MASK = "***"
_DATA_REDACTED_MARKER = ";cdpx-redacted,"

_MEDIA_TYPE_RE = re.compile(r"[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", flags=re.IGNORECASE)
# A high-confidence JWT: encoded JSON header (`eyJ`), payload, and signature.
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_HTTP_URL_RE = re.compile(r"\bhttps?://[^\s<>'\"]+", flags=re.IGNORECASE)
_DATA_URL_RE = re.compile(
    r"\bdata:(?:[a-zA-Z0-9!#$&^_.+-]+/[a-zA-Z0-9!#$&^_.+-]+)?"
    r"(?:;[a-zA-Z0-9!#$&^_.+%={}:@/?-]+)*,"
    r"[^\s<>'\"]*",
    flags=re.IGNORECASE,
)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"
_INVALID_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_URL_CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-access-token",
    "x-auth-token",
}
_API_KEY_HEADER_RE = re.compile(r"(?:^|[-_])api[-_]?key(?:$|[-_])", flags=re.IGNORECASE)
_SENSITIVE_HEADER_PART_RE = re.compile(
    r"(?:^|[-_])(?:csrf|xsrf|token|secret)(?:$|[-_])",
    flags=re.IGNORECASE,
)

_HEADER_TREE_KEYS = {"headers", "request_headers", "response_headers"}
_ACTION_TREE_KEYS = {"action", "argv"}
_URL_TREE_KEYS = {
    "canonical",
    "href",
    "location",
    "profiler_url",
    "redirect_url",
    "src",
    "url",
}
_SENSITIVE_TREE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "client_secret",
    "cookie_value",
    "csrf_token",
    "id_token",
    "passwd",
    "password",
    "refresh_token",
    "secret",
    "token",
    "web_socket_debugger_url",
    "websocketdebuggerurl",
    "xsrf_token",
}
_EXPRESSION_TREE_KEYS = {"expression", "javascript", "js"}
_SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:AUTH|COOKIE|CREDENTIAL|KEY|PASS(?:WORD)?|SECRET|TOKEN)(?:_|$)",
    flags=re.IGNORECASE,
)


class SecretRegistry:
    """In-memory registry of exact values that must never be serialized.

    The registry deliberately offers no serialization method and its
    ``repr`` never includes the values. Replacements are ordered from the
    longest secret to the shortest to avoid prefix leaks.
    """

    __slots__ = ("_values",)

    def __init__(self, values: Iterable[str] = ()) -> None:
        self._values: set[str] = set()
        for value in values:
            self.register(value)

    def register(self, value: str) -> None:
        if not isinstance(value, str):
            raise TypeError("a secret must be a string")
        if value and value != MASK:
            self._values.add(value)

    def _values_longest_first(self) -> tuple[str, ...]:
        return tuple(sorted(self._values, key=lambda value: (-len(value), value)))

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"SecretRegistry(count={len(self)})"


@dataclass(frozen=True, slots=True)
class RedactionReport:
    """Immutable report of the paths actually modified."""

    fields: tuple[str, ...] = ()

    @property
    def redacted(self) -> bool:
        return bool(self.fields)

    @property
    def count(self) -> int:
        return len(self.fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "redacted": self.redacted,
            "count": self.count,
            "fields": list(self.fields),
        }


@dataclass(slots=True)
class RedactionContext:
    """State of a redaction run: known secrets and cumulative report."""

    secrets: SecretRegistry = field(default_factory=SecretRegistry)
    _redacted_fields: set[str] = field(default_factory=set, init=False, repr=False)

    @classmethod
    def from_secrets(cls, values: Iterable[str]) -> RedactionContext:
        return cls(secrets=SecretRegistry(values))

    def register_secret(self, value: str) -> None:
        self.secrets.register(value)

    def mark(self, path: str) -> None:
        self._redacted_fields.add(path)

    @property
    def report(self) -> RedactionReport:
        return RedactionReport(tuple(sorted(self._redacted_fields)))


def secret_values_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    minimum_length: int = 4,
) -> list[str]:
    """Return the values of variables named as secrets, never their names.

    A minimum length avoids a test variable like ``KEY=x`` arbitrarily
    stripping every ``x`` letter from an observation.
    """
    values = os.environ if environ is None else environ
    return [
        value
        for name, value in values.items()
        if len(value) >= minimum_length and _SECRET_ENV_RE.search(name)
    ]


def redact_url(
    value: str,
    *,
    context: RedactionContext | None = None,
    path: str = "$",
) -> str:
    """Clean a URL without exposing userinfo, fragment, or query values.

    Parameter names and order, including repetitions, are kept to preserve
    their diagnostic value. A ``data:`` URL only keeps its media type and a
    stable marker.
    """

    ctx = context or RedactionContext()
    if not isinstance(value, str):
        raise TypeError("redact_url expects a string")
    if value.lower().startswith("data:"):
        return _redact_data_url(value, ctx, path)

    if _URL_CONTROL_RE.search(value) or _INVALID_PERCENT_RE.search(value):
        return _mask_malformed_url(ctx, path)

    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return _mask_malformed_url(ctx, path)
    if parsed.netloc and hostname is None:
        return _mask_malformed_url(ctx, path)
    if parsed.scheme.lower() in {"http", "https"} and not parsed.netloc:
        return _mask_malformed_url(ctx, path)

    netloc = parsed.netloc
    try:
        has_userinfo = parsed.username is not None or parsed.password is not None
    except ValueError:
        has_userinfo = "@" in netloc
    if has_userinfo:
        hostname = hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = f"{hostname}:{port}" if port is not None else hostname
        ctx.mark(f"{path}.userinfo")
    netloc = _redact_url_component(
        netloc,
        context=ctx,
        path=f"{path}.netloc",
        safe="[]:.-_*",
    )
    safe_path = _redact_url_component(
        parsed.path,
        context=ctx,
        path=f"{path}.path",
        safe="/:@!$&'()*+,;=-._~",
    )

    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_pairs: list[tuple[str, str]] = []
    for name, item in query_pairs:
        name = _redact_known_text(name, ctx, f"{path}.query_name")
        if item != MASK:
            item = MASK
            ctx.mark(f"{path}.query.{name}")
        redacted_pairs.append((name, item))
    query = urllib.parse.urlencode(redacted_pairs, doseq=True, safe="*")

    fragment = parsed.fragment
    if fragment:
        fragment = ""
        ctx.mark(f"{path}.fragment")
    return urllib.parse.urlunsplit((parsed.scheme, netloc, safe_path, query, fragment))


def redact_headers(
    headers: Mapping[str, Any],
    *,
    context: RedactionContext | None = None,
    path: str = "$",
) -> dict[str, Any]:
    """Mask HTTP credentials and clean redirect URLs."""

    ctx = context or RedactionContext()
    redacted: dict[str, Any] = {}
    for name, value in headers.items():
        field_path = f"{path}.{name}"
        lowered = str(name).lower()
        if _is_sensitive_header(str(name)):
            redacted[name] = MASK
            if value != MASK:
                ctx.mark(field_path)
            continue
        if lowered == "location" and isinstance(value, str):
            cleaned = redact_url(value, context=ctx, path=field_path)
            redacted[name] = redact_text(cleaned, context=ctx, path=field_path)
            continue
        if isinstance(value, str):
            redacted[name] = redact_text(value, context=ctx, path=field_path)
        else:
            redacted[name] = value
    return redacted


def redact_text(
    value: str,
    *,
    context: RedactionContext | None = None,
    path: str = "$",
) -> str:
    """Clean free text with deliberately conservative detectors."""

    ctx = context or RedactionContext()
    if not isinstance(value, str):
        raise TypeError("redact_text expects a string")
    redacted = value
    for secret in ctx.secrets._values_longest_first():
        redacted = redacted.replace(secret, MASK)
    redacted = _BEARER_RE.sub(f"Bearer {MASK}", redacted)
    redacted = _JWT_RE.sub(MASK, redacted)
    redacted = _DATA_URL_RE.sub(_embedded_url_replacer, redacted)
    redacted = _HTTP_URL_RE.sub(_embedded_url_replacer, redacted)
    if redacted != value:
        ctx.mark(path)
    return redacted


def redact_action(
    action: Sequence[Any] | Mapping[str, Any],
    *,
    context: RedactionContext | None = None,
    path: str = "$",
) -> list[Any] | dict[str, Any]:
    """Mask an argv or structured action without modifying the input."""

    ctx = context or RedactionContext()
    if isinstance(action, Mapping):
        return _redact_structured_action(action, ctx, path)
    if isinstance(action, str | bytes):
        raise TypeError("an action must be a sequence or an object")

    items = list(action)
    if not items:
        return items
    verb = str(items[0]).lower()
    protected: set[int] = set()

    if verb == "type" and len(items) > 2:
        _mask_action_item(items, 2, ctx, path)
        protected.add(2)
    elif verb == "eval":
        for index in range(1, len(items)):
            _mask_action_item(items, index, ctx, path)
            protected.add(index)
    elif verb in {"cookie", "cookies"}:
        protected.update(_mask_cookie_action(items, ctx, path))

    for index, item in enumerate(items[1:], start=1):
        if index in protected or not isinstance(item, str):
            continue
        item_path = f"{path}[{index}]"
        if _looks_like_url(item):
            items[index] = redact_url(item, context=ctx, path=item_path)
        else:
            items[index] = redact_text(item, context=ctx, path=item_path)
    return items


def redact_tree(
    value: Any,
    *,
    context: RedactionContext | None = None,
    path: str = "$",
) -> Any:
    """Recursive redaction guided by cdpx JSON contract keys."""

    ctx = context or RedactionContext()
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            child_path = f"{path}.{key}"
            normalized = _normalize_key(str(key))
            if normalized in _HEADER_TREE_KEYS and isinstance(item, Mapping):
                out[key] = redact_headers(item, context=ctx, path=child_path)
            elif (
                normalized in _ACTION_TREE_KEYS
                and isinstance(item, Mapping | Sequence)
                and not isinstance(item, str | bytes)
            ):
                out[key] = redact_action(item, context=ctx, path=child_path)
            elif _is_sensitive_tree_key(normalized):
                out[key] = _mask_tree_value(item, ctx, child_path)
            elif normalized == "typed" and isinstance(item, str):
                out[key] = _mask_tree_value(item, ctx, child_path)
            elif normalized in _EXPRESSION_TREE_KEYS and isinstance(item, str):
                out[key] = _mask_tree_value(item, ctx, child_path)
            elif normalized in _URL_TREE_KEYS and isinstance(item, str):
                out[key] = redact_url(item, context=ctx, path=child_path)
            else:
                out[key] = redact_tree(item, context=ctx, path=child_path)
        return out
    if isinstance(value, list | tuple):
        return [
            redact_tree(item, context=ctx, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return redact_text(value, context=ctx, path=path)
    return value


def _redact_data_url(value: str, context: RedactionContext, path: str) -> str:
    if _DATA_REDACTED_MARKER in value.lower() and value.endswith(MASK):
        return value
    header = value[5:].split(",", 1)[0]
    media_type = header.split(";", 1)[0]
    if not _MEDIA_TYPE_RE.fullmatch(media_type):
        media_type = "text/plain"
    context.mark(f"{path}.data")
    return f"data:{media_type.lower()}{_DATA_REDACTED_MARKER}{MASK}"


def _is_sensitive_header(name: str) -> bool:
    compact = _normalize_key(name).replace("_", "-")
    return (
        compact in _SENSITIVE_HEADER_NAMES
        or bool(_API_KEY_HEADER_RE.search(compact))
        or bool(_SENSITIVE_HEADER_PART_RE.search(compact))
    )


def _is_sensitive_tree_key(normalized: str) -> bool:
    return normalized in _SENSITIVE_TREE_KEYS or normalized.replace("_", "") in _SENSITIVE_TREE_KEYS


def _embedded_url_replacer(match: re.Match[str]) -> str:
    candidate = match.group(0)
    suffix = ""
    while candidate and candidate[-1] in _TRAILING_URL_PUNCTUATION:
        suffix = candidate[-1] + suffix
        candidate = candidate[:-1]
    return redact_url(candidate) + suffix


def _mask_action_item(items: list[Any], index: int, context: RedactionContext, path: str) -> None:
    if items[index] != MASK:
        items[index] = MASK
        context.mark(f"{path}[{index}]")


def _mask_cookie_action(items: list[Any], context: RedactionContext, path: str) -> set[int]:
    protected: set[int] = set()
    has_value_flag = False
    for index, item in enumerate(items[:-1]):
        if isinstance(item, str) and item.lower() in {"--value", "value"}:
            has_value_flag = True
            _mask_action_item(items, index + 1, context, path)
            protected.add(index + 1)
    for index, item in enumerate(items):
        if not isinstance(item, str):
            continue
        lowered = item.lower()
        if lowered.startswith("--value=") or lowered.startswith("value="):
            has_value_flag = True
            replacement = item.split("=", 1)[0] + f"={MASK}"
            if item != replacement:
                items[index] = replacement
                context.mark(f"{path}[{index}]")
            protected.add(index)
    if not has_value_flag and len(items) > 3 and str(items[1]).lower() == "set":
        _mask_action_item(items, 3, context, path)
        protected.add(3)
    return protected


def _redact_structured_action(
    action: Mapping[str, Any], context: RedactionContext, path: str
) -> dict[str, Any]:
    out = dict(action)
    verb = str(out.get("verb") or out.get("action") or out.get("name") or "").lower()
    masked_keys: set[str] = set()
    if verb == "type":
        masked_keys = {key for key in ("text", "value", "input") if key in out}
    elif verb == "eval":
        masked_keys = {key for key in ("expression", "javascript", "js", "source") if key in out}
    elif verb in {"cookie", "cookies"}:
        masked_keys = {key for key in ("value", "cookie_value") if key in out}

    for key, item in list(out.items()):
        child_path = f"{path}.{key}"
        if key in masked_keys:
            out[key] = _mask_tree_value(item, context, child_path)
        elif _normalize_key(key) in _URL_TREE_KEYS and isinstance(item, str):
            out[key] = redact_url(item, context=context, path=child_path)
        else:
            out[key] = redact_tree(item, context=context, path=child_path)
    return out


def _mask_tree_value(value: Any, context: RedactionContext, path: str) -> str:
    if value != MASK:
        context.mark(path)
    return MASK


def _normalize_key(value: str) -> str:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value.strip())
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    return re.sub(r"[-\s]+", "_", separated).lower()


def _mask_malformed_url(context: RedactionContext, path: str) -> str:
    context.mark(f"{path}.malformed")
    return MASK


def _redact_known_text(value: str, context: RedactionContext, path: str) -> str:
    redacted = value
    for secret in context.secrets._values_longest_first():
        redacted = redacted.replace(secret, MASK)
    if redacted != value:
        context.mark(path)
    return redacted


def _redact_url_component(
    value: str,
    *,
    context: RedactionContext,
    path: str,
    safe: str,
) -> str:
    """Mask a raw or percent-encoded secret without keeping the decoded component."""
    redacted = _redact_known_text(value, context, path)
    decoded = urllib.parse.unquote(redacted)
    decoded_redacted = _redact_known_text(decoded, context, path)
    if decoded_redacted == decoded:
        return redacted
    return urllib.parse.quote(decoded_redacted, safe=safe)


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http://", "https://", "data:"))
