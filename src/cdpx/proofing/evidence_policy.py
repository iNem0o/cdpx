"""Shared proof schemas, retention policy, and environment redaction setup."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from cdpx.security.redaction import RedactionContext, secret_values_from_environment

EVIDENCE_SCHEMA = "cdpx.evidence/v2"
SCENARIOS_SCHEMA = "cdpx.scenarios/v2"
PROOF_RETENTION_ENV = "CDPX_PROOF_RETENTION_DAYS"
DEFAULT_PROOF_RETENTION_DAYS = 14
MIN_PROOF_RETENTION_DAYS = 1
MAX_PROOF_RETENTION_DAYS = 90
DEFAULT_EVIDENCE_TTL = DEFAULT_PROOF_RETENTION_DAYS * 24 * 60 * 60
# Closed taxonomy: every known type has a classification policy and a
# dedicated viewer in the cockpit; a free-form string would let both drift.
ARTIFACT_TYPES = frozenset(
    {
        "screenshot",
        "video",
        "asciinema",
        "console",
        "network",
        "profiler",
        "json",
        "logs",
        "command",
        "log-excerpt",
        "file",
    }
)


def redaction_context_from_environment(
    environ: Mapping[str, str] | None = None,
) -> RedactionContext:
    """Build a process-local registry without serialising environment values."""

    return RedactionContext.from_secrets(environment_secret_values(environ))


def environment_secret_values(environ: Mapping[str, str] | None = None) -> list[str]:
    values = environ if environ is not None else os.environ
    detected = secret_values_from_environment(values)
    canaries = [
        value for name, value in values.items() if value and name.startswith("CDPX_PROOF_CANARY")
    ]
    return list(dict.fromkeys([*detected, *canaries]))


def proof_retention_days(environ: Mapping[str, str] | None = None) -> int:
    """Return the proof retention, with strict fail-closed validation."""

    values = os.environ if environ is None else environ
    raw = values.get(PROOF_RETENTION_ENV)
    if raw is None:
        return DEFAULT_PROOF_RETENTION_DAYS
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ValueError(f"{PROOF_RETENTION_ENV} must be a positive integer")
    days = int(raw)
    if not MIN_PROOF_RETENTION_DAYS <= days <= MAX_PROOF_RETENTION_DAYS:
        raise ValueError(
            f"{PROOF_RETENTION_ENV} must be between "
            f"{MIN_PROOF_RETENTION_DAYS} and {MAX_PROOF_RETENTION_DAYS}"
        )
    return days


def proof_retention_seconds(environ: Mapping[str, str] | None = None) -> int:
    return proof_retention_days(environ) * 24 * 60 * 60
