"""E2E Symfony réel pour M2.

Ce test est lancé par docker-compose.symfony-e2e.yml. Il prouve que `cdpx
profiler` lit un vrai header X-Debug-Token-Link émis par WebProfilerBundle.
"""

import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import capture, dev

CHROME_BIN = next(
    (b for b in ("chromium", "chromium-browser", "google-chrome", "chrome") if shutil.which(b)),
    None,
)
SYMFONY_URL = os.environ.get("SYMFONY_E2E_URL")

if CHROME_BIN is None:
    pytest.fail("Chrome/Chromium obligatoire pour les e2e Symfony cdpx", pytrace=False)

pytestmark = pytest.mark.skipif(
    not SYMFONY_URL,
    reason="SYMFONY_E2E_URL absent (lancer make docker-symfony-e2e)",
)

E2E_PORT = 9778


def wait_for_symfony(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/profiler-target", timeout=2) as response:
                if response.status == 200:
                    return
                last_error = f"HTTP {response.status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    pytest.fail(f"Symfony app unavailable after {timeout:.0f}s: {last_error}", pytrace=False)


@pytest.fixture(scope="module")
def chrome():
    profile = tempfile.mkdtemp(prefix="cdpx-symfony-e2e-")
    chrome_log = Path(profile) / "chrome-stderr.log"
    stderr = chrome_log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={E2E_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-features=HttpsFirstBalancedModeAutoEnable,HttpsUpgrades",
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr,
    )
    ready = False
    for _ in range(150):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{E2E_PORT}/json/version", timeout=1)
            ready = True
            break
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.2)
    if not ready:
        proc.terminate()
        stderr.close()
        details = chrome_log.read_text(encoding="utf-8", errors="replace")[-2000:]
        pytest.fail(f"Chrome/Chromium did not expose CDP on {E2E_PORT}:\n{details}", pytrace=False)
    yield E2E_PORT
    proc.terminate()
    stderr.close()


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-symfony-profiler",
    proves=[
        "Real Symfony WebProfilerBundle emits a token reachable from browser navigation.",
        "cdpx profiler follows the token link and stores profiler payload evidence.",
    ],
)
def test_profiler_reads_real_symfony_web_profiler(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(target["webSocketDebuggerUrl"], timeout=20) as c:
            res = dev.profiler(c, f"{SYMFONY_URL}/profiler-target", timeout=20, settle=0.5)
            screenshot = Path(tmp_path) / "symfony-profiler-target.png"
            capture.screenshot(c, str(screenshot))
    finally:
        discovery.close_tab("127.0.0.1", chrome, target["id"])

    if evidence_case is not None:
        evidence_case.attach_json("Symfony profiler result", res, "symfony-profiler-result.json")
        evidence_case.attach_text(
            "Symfony profiler URL",
            f"target={res['url']}\nprofiler={res['profiler_url']}\ntoken={res['token']}\n",
            "symfony-profiler-url.log",
        )
        evidence_case.attach_screenshot(screenshot, "Symfony profiler target")

    assert res["url"].endswith("/profiler-target")
    assert res["status"] == 200
    assert res["token"]
    assert res["profiler_url"].startswith(f"{SYMFONY_URL}/_profiler/")
    assert res["profiler_bytes"] > 1000
    assert res["panels"]["raw"]["bytes"] == res["profiler_bytes"]
