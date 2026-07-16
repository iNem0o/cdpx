"""Lecture bornée du contenu de frames same-origin."""

from __future__ import annotations

import json
from typing import Any

from cdpx.client import CDPClient
from cdpx.primitives import js


def frame_text(client: CDPClient, selector: str) -> dict[str, Any]:
    expression = (
        "(() => Array.from(document.querySelectorAll('iframe')).map(f => "
        "f.contentDocument && f.contentDocument.querySelector("
        f"{json.dumps(selector)})?.innerText).find(Boolean) || null)()"
    )
    return {"selector": selector, "text": js.evaluate(client, expression)}
