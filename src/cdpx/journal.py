"""Format sûr des actions persistées par record/replay."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cdpx.primitives import actions
from cdpx.security import MASK, RedactionContext, redact_action, redact_text

SCHEMA = "cdpx.record/v2"
_SECRET_REF = re.compile(r"@env:([A-Za-z_][A-Za-z0-9_]*)\Z")


class JournalError(ValueError):
    pass


def serialize_action(
    action: list[str],
    *,
    context: RedactionContext | None = None,
) -> tuple[list[str] | dict[str, Any], bool]:
    actions.validate_action(action)
    ctx = context or RedactionContext()
    verb = action[0]
    if verb == "type":
        value = action[2]
        clear = "--clear" in action[3:]
        match = _SECRET_REF.fullmatch(value)
        if not match:
            ctx.register_secret(value)
        selector = redact_text(action[1], context=ctx, path="$.action.selector")
        selector_changed = selector != action[1]
        if match:
            stored_input: dict[str, Any] = {
                "secret_ref": match.group(1),
                "source": "env",
            }
            replayable = not selector_changed
        else:
            ctx.mark("$.action.input")
            stored_input = {"redacted": True}
            replayable = False
        return {
            "verb": "type",
            "selector": selector,
            "input": stored_input,
            "clear": clear,
        }, replayable
    if verb == "eval":
        expression = " ".join(action[1:])
        ctx.register_secret(expression)
        ctx.mark("$.action.expression")
        return {
            "verb": "eval",
            "expression": MASK,
            "sha256": hashlib.sha256(expression.encode("utf-8")).hexdigest(),
        }, False
    cleaned = redact_action(action, context=ctx, path="$.action")
    if not isinstance(cleaned, list):  # pragma: no cover - entrée validée argv
        raise JournalError("action nettoyée invalide")
    stored = [str(item) for item in cleaned]
    return stored, stored == action


def materialize_action(
    stored: list[str] | Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    if isinstance(stored, list):
        if not all(isinstance(item, str) for item in stored):
            raise JournalError("action v1 invalide")
        actions.validate_action(stored)
        if stored[0] in {"type", "eval"}:
            raise JournalError("action v1 sensible refusée")
        return list(stored)
    if not isinstance(stored, Mapping):
        raise JournalError("action de journal invalide")
    verb = stored.get("verb")
    if verb == "type":
        selector = stored.get("selector")
        input_spec = stored.get("input")
        if not isinstance(selector, str) or not isinstance(input_spec, Mapping):
            raise JournalError("action type v2 invalide")
        secret_ref = input_spec.get("secret_ref")
        if input_spec.get("source") != "env" or not isinstance(secret_ref, str):
            raise JournalError("action type redacted non rejouable sans secret_ref")
        source = os.environ if environ is None else environ
        value = source.get(secret_ref)
        if value is None:
            raise JournalError(f"secret_ref introuvable dans l'environnement: {secret_ref}")
        action = ["type", selector, value]
        if stored.get("clear") is True:
            action.append("--clear")
        actions.validate_action(action)
        return action
    if verb == "eval":
        raise JournalError("action eval redacted non rejouable")
    raise JournalError(f"verbe de journal v2 inconnu: {verb}")


def append_event(path: str | Path, event: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.parent.chmod(0o700)
    if destination.is_symlink():
        raise JournalError(f"journal symbolique interdit: {destination}")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(destination, flags, 0o600)
    except OSError as e:
        raise JournalError(f"journal non ouvrable: {destination}: {e}") from e
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise JournalError(f"journal régulier requis: {destination}")
        os.fchmod(fd, 0o600)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
