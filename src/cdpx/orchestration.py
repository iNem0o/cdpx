"""Shared security context for multi-step browser orchestration APIs."""

from __future__ import annotations

from dataclasses import dataclass

from cdpx.policy import origin_from_url, parse_origins
from cdpx.security import RedactionContext


@dataclass(frozen=True)
class OrchestrationContext:
    """Parsed origin policy and run-scoped redaction state."""

    origins: tuple[str, ...]
    redaction: RedactionContext

    @classmethod
    def from_origins(
        cls,
        origins: str | tuple[str, ...],
        *,
        redaction: RedactionContext | None = None,
    ) -> OrchestrationContext:
        parsed = parse_origins(origins, required=True) if isinstance(origins, str) else origins
        if not parsed:
            raise ValueError("origine d'orchestration obligatoire")
        return cls(tuple(parsed), redaction or RedactionContext())

    @classmethod
    def for_url(
        cls, url: str, *, redaction: RedactionContext | None = None
    ) -> OrchestrationContext:
        return cls((origin_from_url(url),), redaction or RedactionContext())
