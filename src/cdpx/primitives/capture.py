"""Capture primitives: screenshot, PDF, console.

Agent usecases:
- screenshot: raw "vision" — check a render, a visual state, a CSS bug.
- pdf: archiving a page state (deliverable SEO audit, acceptance proof).
- console: THE missing feedback loop for front-end dev — an agent that
  doesn't read the console navigates blind on a broken JS app.
"""

from __future__ import annotations

import base64
import pathlib
from collections.abc import Iterable

from cdpx.cdp_types import CDPEvent
from cdpx.client import CDPClient, validate_time_budget
from cdpx.private_files import atomic_write_bytes
from cdpx.security import RedactionContext, redact_text

CONSOLE_EVENTS = ("Runtime.consoleAPICalled", "Runtime.exceptionThrown")


def _write_private(path: pathlib.Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


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
    """Enables Runtime and collects logs + exceptions for `duration` seconds.

    Stable output contract: list of entries {kind, type, text, ts}.
    """
    duration = validate_time_budget(duration, "console capture duration")
    client.send("Runtime.enable")
    events = client.collect_events(duration, CONSOLE_EVENTS)
    entries = list(console_entries(events, context=context))
    errors = sum(1 for e in entries if e["type"] == "error" or e["kind"] == "exception")
    return {"entries": entries, "count": len(entries), "errors": errors, "duration": duration}


def console_entries(
    events: Iterable[CDPEvent], context: RedactionContext | None = None
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
    """Stream of console entries. The CLI serializes it as compact NDJSON."""
    client.send("Runtime.enable")
    emitted = 0
    while max_entries is None or emitted < max_entries:
        events = client.collect_events(0.25, CONSOLE_EVENTS)
        for entry in console_entries(events, context=context):
            yield entry
            emitted += 1
            if max_entries is not None and emitted >= max_entries:
                return
