"""Shared helpers for Chrome e2e evidence."""

from __future__ import annotations

from pathlib import Path

from cdpx.client import CDPClient
from cdpx.primitives import capture
from cdpx.testing.evidence import EvidenceCase, slugify


def attach_screenshot(
    evidence_case: EvidenceCase | None,
    client: CDPClient,
    label: str = "final",
    *,
    full_page: bool = False,
) -> dict | None:
    if evidence_case is None:
        return None
    filename = f"{slugify(label)}.png"
    path = Path(evidence_case.artifact_dir) / filename
    result = capture.screenshot(client, str(path), full_page=full_page)
    artifact = evidence_case.attach_screenshot(result["path"], label)
    artifact["screenshot"] = result
    return artifact
