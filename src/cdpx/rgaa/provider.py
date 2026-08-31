"""Optional advisory providers for RGAA scans.

Provider observations are never RGAA verdicts.  axe-core runs from an
integrity-pinned local bundle in a fresh isolated world and returns a bounded,
content-minimized result (no HTML snippets).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from cdpx.client import CDPClient
from cdpx.primitives.js import JSException

AXE_VERSION = "4.10.3"
AXE_HASH = "880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb"
AXE_PATH = Path(__file__).with_name("vendor") / f"axe-core-{AXE_VERSION}.min.js"

AXE_TO_RGAA: dict[str, tuple[str, ...]] = {
    "image-alt": ("1.1.1",),
    "frame-title": ("2.1.1",),
    "color-contrast": ("3.2.1", "3.2.2", "3.2.3", "3.2.4"),
    "link-name": ("6.1.1",),
    "html-has-lang": ("8.3.1",),
    "html-lang-valid": ("8.4.1",),
    "document-title": ("8.5.1",),
    "label": ("11.1.1",),
    "button-name": ("11.9.1",),
    "bypass": ("12.7.1",),
    "meta-refresh": ("13.1.1",),
}


@lru_cache(maxsize=1)
def _bundle() -> str:
    try:
        payload = AXE_PATH.read_bytes()
    except OSError as error:
        raise ValueError("axe-core advisory bundle unavailable") from error
    if hashlib.sha256(payload).hexdigest() != AXE_HASH:
        raise ValueError("axe-core advisory bundle integrity mismatch")
    return payload.decode("utf-8")


def _main_frame_id(client: CDPClient, remaining: Callable[[], float]) -> str:
    tree = client.send("Page.getFrameTree", timeout=remaining()).get("frameTree", {})
    frame_id = tree.get("frame", {}).get("id") if isinstance(tree, dict) else None
    if not isinstance(frame_id, str) or not frame_id:
        raise ValueError("axe-core advisory provider: main frame unavailable")
    return frame_id


def run_axe(client: CDPClient, *, remaining: Callable[[], float]) -> dict[str, Any]:
    frame_id = _main_frame_id(client, remaining)
    world = client.send(
        "Page.createIsolatedWorld",
        {
            "frameId": frame_id,
            "worldName": "__cdpx_rgaa_axe",
            "grantUniveralAccess": False,
        },
        timeout=remaining(),
    )
    context_id = world.get("executionContextId")
    if not isinstance(context_id, int):
        raise ValueError("axe-core advisory provider: isolated world unavailable")
    projection = r"""
// __cdpx_rgaa_axe_provider
globalThis.axe.run(document, {
  runOnly: {type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]},
  resultTypes: ["violations", "incomplete", "passes", "inapplicable"]
}).then((result) => {
  const project = (items) => items.slice(0, 200).map((rule) => ({
    id: rule.id,
    impact: rule.impact || null,
    tags: (rule.tags || []).slice(0, 20),
    nodes: (rule.nodes || []).slice(0, 20).map((node) => ({
      target: (node.target || []).slice(0, 4).map((item) => String(item).slice(0, 240)),
      impact: node.impact || null,
      failure_summary: String(node.failureSummary || "").slice(0, 500)
    })),
    nodes_total: (rule.nodes || []).length
  }));
  return JSON.stringify({
    violations: project(result.violations || []),
    incomplete: project(result.incomplete || []),
    passes: project(result.passes || []),
    inapplicable: project(result.inapplicable || [])
  });
})
"""
    response = client.send(
        "Runtime.evaluate",
        {
            "expression": _bundle() + "\n;" + projection,
            "contextId": context_id,
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout=remaining(),
    )
    if "exceptionDetails" in response:
        details = response["exceptionDetails"]
        message = details.get("exception", {}).get("description") or details.get(
            "text", "axe-core provider error"
        )
        raise JSException(message)
    raw = response.get("result", {}).get("value")
    if not isinstance(raw, str):
        raise ValueError("axe-core advisory provider returned no value")
    try:
        projected = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("axe-core advisory provider returned invalid JSON") from error
    return {
        "name": "axe-core",
        "version": AXE_VERSION,
        "sha256": AXE_HASH,
        "authority": "advisory",
        "isolated_world": True,
        "result": projected,
    }


def mapped_observations(provider: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    mapped: dict[str, list[dict[str, Any]]] = {}
    result = provider.get("result", {})
    for outcome in ("violations", "incomplete", "passes", "inapplicable"):
        rules = result.get(outcome, []) if isinstance(result, dict) else []
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict):
                continue
            for test_id in AXE_TO_RGAA.get(str(rule.get("id")), ()):
                mapped.setdefault(test_id, []).append(
                    {
                        "provider": "axe-core",
                        "provider_rule_id": rule.get("id"),
                        "provider_outcome": outcome,
                        "impact": rule.get("impact"),
                        "nodes": rule.get("nodes", []),
                        "nodes_total": rule.get("nodes_total", 0),
                        "verdict_authority": "advisory_only",
                    }
                )
    return mapped
