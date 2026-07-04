"""Primitives de capture: screenshot, PDF, console.

Usecases agent:
- screenshot: la "vision" brute — vérifier un rendu, un état visuel, un bug CSS.
- pdf: archivage d'un état de page (audit SEO livrable, preuve de recette).
- console: LE retour d'info manquant du dev front — l'agent qui ne lit pas la
  console navigue à l'aveugle sur une app JS cassée.
"""

from __future__ import annotations

import base64
import pathlib
from collections.abc import Iterable

from cdpx.client import CDPClient

CONSOLE_EVENTS = ("Runtime.consoleAPICalled", "Runtime.exceptionThrown")


def screenshot(client: CDPClient, path: str, full_page: bool = False, fmt: str = "png") -> dict:
    params: dict = {"format": fmt}
    if full_page:
        params["captureBeyondViewport"] = True
    res = client.send("Page.captureScreenshot", params, timeout=30)
    data = base64.b64decode(res["data"])
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"path": str(out), "bytes": len(data), "format": fmt, "full_page": full_page}


def pdf(client: CDPClient, path: str) -> dict:
    res = client.send("Page.printToPDF", {"printBackground": True}, timeout=60)
    data = base64.b64decode(res["data"])
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return {"path": str(out), "bytes": len(data)}


def _summarize_arg(arg: dict) -> str:
    if "value" in arg:
        return str(arg["value"])
    if "description" in arg:
        return arg["description"]
    return arg.get("type", "?")


def console_capture(client: CDPClient, duration: float = 2.0) -> dict:
    """Active Runtime et collecte logs + exceptions pendant `duration` secondes.

    Contrat de sortie stable: liste d'entrées {kind, type, text, ts}.
    """
    client.send("Runtime.enable")
    events = client.collect_events(duration, CONSOLE_EVENTS)
    entries = list(console_entries(events))
    errors = sum(1 for e in entries if e["type"] == "error" or e["kind"] == "exception")
    return {"entries": entries, "count": len(entries), "errors": errors, "duration": duration}


def console_entries(events: Iterable[dict]) -> Iterable[dict]:
    for ev in events:
        p = ev.get("params", {})
        if ev["method"] == "Runtime.consoleAPICalled":
            yield {
                "kind": "console",
                "type": p.get("type", "log"),
                "text": " ".join(_summarize_arg(a) for a in p.get("args", [])),
                "ts": p.get("timestamp"),
            }
        else:
            details = p.get("exceptionDetails", {})
            text = details.get("exception", {}).get("description") or details.get("text", "")
            yield {"kind": "exception", "type": "error", "text": text, "ts": p.get("timestamp")}


def console_follow(client: CDPClient, max_entries: int | None = None) -> Iterable[dict]:
    """Flux d'entrées console. Le CLI le sérialise en NDJSON compact."""
    client.send("Runtime.enable")
    emitted = 0
    while max_entries is None or emitted < max_entries:
        events = client.collect_events(0.25, CONSOLE_EVENTS)
        for entry in console_entries(events):
            yield entry
            emitted += 1
            if max_entries is not None and emitted >= max_entries:
                return
