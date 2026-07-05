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

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import dev

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


@pytest.fixture(scope="module")
def chrome():
    profile = tempfile.mkdtemp(prefix="cdpx-symfony-e2e-")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            f"--remote-debugging-port={E2E_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-features=HttpsFirstBalancedModeAutoEnable,HttpsUpgrades",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{E2E_PORT}/json/version", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield E2E_PORT
    proc.terminate()


def test_profiler_reads_real_symfony_web_profiler(chrome):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    with CDPClient(target["webSocketDebuggerUrl"], timeout=20) as c:
        res = dev.profiler(c, f"{SYMFONY_URL}/profiler-target", timeout=20, settle=0.5)
    discovery.close_tab("127.0.0.1", chrome, target["id"])

    assert res["url"].endswith("/profiler-target")
    assert res["status"] == 200
    assert res["token"]
    assert res["profiler_url"].startswith(f"{SYMFONY_URL}/_profiler/")
    assert res["profiler_bytes"] > 1000
    assert res["panels"]["raw"]["bytes"] == res["profiler_bytes"]
