"""Primitives de capture: screenshot, PDF, console.

Usecases agent:
- screenshot: la "vision" brute — vérifier un rendu, un état visuel, un bug CSS.
- pdf: archivage d'un état de page (audit SEO livrable, preuve de recette).
- console: LE retour d'info manquant du dev front — l'agent qui ne lit pas la
  console navigue à l'aveugle sur une app JS cassée.
"""

from __future__ import annotations

import base64
import os
import pathlib
import secrets
from collections.abc import Iterable

from cdpx.client import CDPClient
from cdpx.security import RedactionContext, redact_text

CONSOLE_EVENTS = ("Runtime.consoleAPICalled", "Runtime.exceptionThrown")


def _write_private(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError(f"lien symbolique interdit: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def screenshot(client: CDPClient, path: str, full_page: bool = False, fmt: str = "png") -> dict:
    params: dict = {"format": fmt}
    if full_page:
        params["captureBeyondViewport"] = True
    res = client.send("Page.captureScreenshot", params, timeout=30)
    data = base64.b64decode(res["data"])
    out = pathlib.Path(path)
    _write_private(out, data)
    return {"path": str(out), "bytes": len(data), "format": fmt, "full_page": full_page}


def pdf(client: CDPClient, path: str) -> dict:
    res = client.send("Page.printToPDF", {"printBackground": True}, timeout=60)
    data = base64.b64decode(res["data"])
    out = pathlib.Path(path)
    _write_private(out, data)
    return {"path": str(out), "bytes": len(data)}


def _summarize_arg(arg: dict) -> str:
    if "value" in arg:
        return str(arg["value"])
    if "description" in arg:
        return arg["description"]
    return arg.get("type", "?")


def console_capture(
    client: CDPClient,
    duration: float = 2.0,
    context: RedactionContext | None = None,
) -> dict:
    """Active Runtime et collecte logs + exceptions pendant `duration` secondes.

    Contrat de sortie stable: liste d'entrées {kind, type, text, ts}.
    """
    client.send("Runtime.enable")
    events = client.collect_events(duration, CONSOLE_EVENTS)
    entries = list(console_entries(events, context=context))
    errors = sum(1 for e in entries if e["type"] == "error" or e["kind"] == "exception")
    return {"entries": entries, "count": len(entries), "errors": errors, "duration": duration}


def console_entries(
    events: Iterable[dict], context: RedactionContext | None = None
) -> Iterable[dict]:
    redaction = context or RedactionContext()
    for index, ev in enumerate(events):
        p = ev.get("params", {})
        if ev["method"] == "Runtime.consoleAPICalled":
            text = " ".join(_summarize_arg(a) for a in p.get("args", []))
            yield {
                "kind": "console",
                "type": p.get("type", "log"),
                "text": redact_text(
                    text,
                    context=redaction,
                    path=f"$.entries[{index}].text",
                ),
                "ts": p.get("timestamp"),
            }
        else:
            details = p.get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get("text", "")
            yield {
                "kind": "exception",
                "type": "error",
                "text": redact_text(
                    str(text),
                    context=redaction,
                    path=f"$.entries[{index}].text",
                ),
                "ts": p.get("timestamp"),
            }


def console_follow(
    client: CDPClient,
    max_entries: int | None = None,
    context: RedactionContext | None = None,
) -> Iterable[dict]:
    """Flux d'entrées console. Le CLI le sérialise en NDJSON compact."""
    client.send("Runtime.enable")
    emitted = 0
    while max_entries is None or emitted < max_entries:
        events = client.collect_events(0.25, CONSOLE_EVENTS)
        for entry in console_entries(events, context=context):
            yield entry
            emitted += 1
            if max_entries is not None and emitted >= max_entries:
                return
