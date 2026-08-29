"""Blocking Shopware 6.7 profiler runtime proof."""

import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

from cdpx.client import CDPClient
from cdpx.primitives import dev, js
from cdpx.session import SessionManifest, start_session, stop_session
from cdpx.testing.e2e import attach_screenshot

PINNED_CHROMIUM = Path("/usr/bin/chromium")
SHOPWARE_URL = os.environ.get("SHOPWARE_E2E_URL")

pytestmark = pytest.mark.skipif(
    not SHOPWARE_URL,
    reason="SHOPWARE_E2E_URL missing (run ./dev check)",
)


def wait_for_shopware(url: str, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/cdpx-profiler", timeout=3) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    pytest.fail(f"Shopware app unavailable after {timeout:.0f}s: {last_error}", pytrace=False)


@pytest.fixture(scope="module")
def chrome(tmp_path_factory) -> SessionManifest:
    assert SHOPWARE_URL is not None
    if not PINNED_CHROMIUM.is_file() or not os.access(PINNED_CHROMIUM, os.X_OK):
        pytest.fail(
            f"Pinned CI Chromium required for Shopware e2e: {PINNED_CHROMIUM}",
            pytrace=False,
        )
    runtime = tmp_path_factory.mktemp("cdpx-shopware-session")
    manifest, manifest_path = start_session(
        run_id="shopware-e2e",
        authority="privileged",
        origins=SHOPWARE_URL,
        ttl=900,
        owner_pid=os.getpid(),
        chrome_bin=str(PINNED_CHROMIUM),
        root=runtime,
    )
    try:
        yield manifest
    finally:
        if manifest_path.exists():
            stop_session(
                manifest_path,
                run_id=manifest.run_id,
                target_id=manifest.target_id,
            )


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-shopware-profiler",
    target="shopware",
    proof_level="runtime",
    proves=[
        "Shopware 6.7 emits a real profiler token.",
        "The db panel falls back to app.connection_collector with grouped queries.",
    ],
)
def test_profiler_reads_real_shopware_connection_collector(chrome, evidence_case):
    assert SHOPWARE_URL is not None
    wait_for_shopware(SHOPWARE_URL)
    with CDPClient(chrome.websocket_url, timeout=30) as client:
        result = dev.profiler(
            client,
            f"{SHOPWARE_URL}/cdpx-profiler",
            panels=["db"],
            timeout=30,
            settle=0.8,
        )
        requested_collectors = json.loads(
            js.evaluate(
                client,
                "JSON.stringify(performance.getEntriesByType('resource')"
                ".map(entry => new URL(entry.name).searchParams.get('panel'))"
                ".filter(Boolean))",
            )
        )
        attach_screenshot(evidence_case, client, "Real Shopware profiler target")

    panel = result["panels"]["db"]
    assert result["token_present"] is True and "token" not in result
    assert panel["available"] is True
    assert panel["queries"] >= 5
    assert panel["max_repetitions"] >= 5
    assert requested_collectors[:2] == ["db", "app.connection_collector"]

    if evidence_case is not None:
        evidence_case.attach_json("Shopware profiler result", result, "shopware-profiler.json")
        evidence_case.attach_json(
            "Shopware collector fallback",
            {"requested_collectors": requested_collectors},
            "shopware-collector-fallback.json",
        )
