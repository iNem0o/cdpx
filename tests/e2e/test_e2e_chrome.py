"""E2E Chrome réel — SQUELETTE MILESTONE M1.

Statut: NON VALIDÉ en runtime (aucun Chrome disponible dans l'environnement
de génération — voir docs/CONTEXT.md). Ce fichier est le point d'entrée de la
reprise M1: il se skippe proprement sans Chrome (comportement, lui, validé),
et déroule les mêmes scénarios que les tests mock, mais contre un vrai
navigateur + le serveur de fixtures.

Lancement visé:
  chromium --headless=new --remote-debugging-port=0 ... (géré ici)
  make e2e
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import advanced, audit, capture, dev, inputs, js, nav, net, state

CHROME_BIN = next(
    (b for b in ("chromium", "chromium-browser", "google-chrome", "chrome") if shutil.which(b)),
    None,
)

pytestmark = pytest.mark.skipif(CHROME_BIN is None, reason="Chrome/Chromium absent (voir M1)")

E2E_PORT = 9777


@pytest.fixture(scope="module")
def chrome():
    profile = tempfile.mkdtemp(prefix="cdpx-e2e-")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            f"--remote-debugging-port={E2E_PORT}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # attendre la découverte
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{E2E_PORT}/json/version", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    yield E2E_PORT
    proc.terminate()


@pytest.fixture()
def page(chrome, fixtures_http):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    with CDPClient(target["webSocketDebuggerUrl"], timeout=15) as c:
        yield c, fixtures_http.base_url
    discovery.close_tab("127.0.0.1", chrome, target["id"])


def test_navigate_and_read_title(page):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    assert js.evaluate(c, "document.title") == "cdpx fixtures — accueil"


def test_wait_for_late_spa_content(page):
    c, base = page
    nav.navigate(c, f"{base}/spa.html")
    res = nav.wait_for(c, "#late-content", timeout=5)
    assert res["found"] and res["elapsed_ms"] >= 250


def test_form_click_and_type(page):
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    inputs.click(c, "#submit-btn")
    assert js.get_text(c, "#result")["text"] == "OK:Léo"


def test_console_capture_real(page):
    c, base = page
    c.send("Runtime.enable")
    nav.navigate(c, f"{base}/console.html")
    res = capture.console_capture(c, duration=1.0)
    texts = [e["text"] for e in res["entries"]]
    assert any("fixture-log" in t for t in texts)
    assert res["errors"] >= 1


def test_network_capture_real(page):
    c, base = page
    res = net.capture(c, f"{base}/network.html", settle=1.0)
    assert res["summary"]["errors_4xx_5xx"] >= 1  # /api/status/500
    urls = [r.get("url", "") for r in res["requests"]]
    assert any("/api/json" in u for u in urls)


def test_profiler_fixture_real(page):
    c, base = page
    res = dev.profiler(c, f"{base}/api/profiler-sim")
    assert res["token"] == "fixed-token"
    assert res["panels"]["db"]["queries"] == 2


def test_dom_diff_real(page):
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    res = dev.dom_diff(c, ["click", "#submit-btn"])
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])


def test_a11y_and_frame_real(page):
    c, base = page
    nav.navigate(c, f"{base}/iframe.html")
    tree = advanced.a11y(c)
    assert tree["count"] > 0
    assert advanced.frame_text(c, "#child-marker")["text"] == "Contenu de l'iframe"


def test_coverage_real(page):
    c, base = page
    res = advanced.coverage(c, f"{base}/coverage.html")
    assert res["count"] >= 1
    assert res["css"]["rules"] >= 1
    assert res["css"]["used"] >= 1
    assert res["css"]["used"] + res["css"]["unused"] == res["css"]["rules"]


def test_intercept_real_fulfill_block_continue(page):
    c, base = page
    res = advanced.intercept_goto(
        c,
        [
            "*api/status/500* => 204",
            "*api/slow* => block",
        ],
        f"{base}/intercept.html",
        settle=1.0,
    )
    actions = {hit["action"] for hit in res["hits"]}
    assert {"204", "block", "continue"}.issubset(actions)
    deadline = time.monotonic() + 3
    text = ""
    while time.monotonic() < deadline:
        text = js.get_text(c, "#intercept-result")["text"] or ""
        if "pending" not in text:
            break
        time.sleep(0.1)
    assert "/api/json:200" in text
    assert "/api/status/500:204" in text
    assert "/api/slow?ms=120:ERR" in text


def test_vitals_real_with_interaction(page):
    c, base = page
    res = advanced.vitals(c, f"{base}/vitals.html", click_selector="#inp-button", settle=1.0)
    assert set(res) == {"url", "lcp", "cls", "inp"}
    assert res["lcp"] >= 0 and res["cls"] >= 0 and res["inp"] >= 0
    assert js.evaluate(c, "document.body.dataset.clicked") == "1"


def test_seo_edge_real(page):
    c, base = page
    nav.navigate(c, f"{base}/seo-edge.html")
    res = audit.seo(c)
    assert res["title_px_estimate"] > 0
    assert "h1 dupliqué: produit dupliqué" in res["findings"]
    assert "JSON-LD invalide" in res["findings"]
    assert "Product JSON-LD incomplet (sku ou name requis)" in res["findings"]


def test_origin_guard_cli_real(chrome, fixtures_http):
    tab = discovery.new_tab("127.0.0.1", chrome, f"{fixtures_http.base_url}/index.html")
    env = {**os.environ, "CDPX_ORIGINS": "https://blocked.example"}
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "cdpx.cli",
                "--port",
                str(chrome),
                "--target",
                tab["id"],
                "click",
                "#main-title",
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    assert proc.returncode == 1
    assert "mutation refusée" in proc.stderr


def test_seo_audit_real(page):
    c, base = page
    nav.navigate(c, f"{base}/seo.html")
    res = audit.seo(c)
    assert res["findings"] == []
    assert res["jsonld"][0]["sku"] == "FIX-001"
    nav.navigate(c, f"{base}/seo-broken.html")
    broken = audit.seo(c)
    assert "2 h1 (attendu: 1)" in broken["findings"]


def test_cookies_and_storage_real(page):
    c, base = page
    nav.navigate(c, f"{base}/storage.html")
    cookies = state.get_cookies(c, show_values=True)["cookies"]
    assert any(ck["name"] == "jsCookie" for ck in cookies)
    storage = state.get_storage(c, "local")
    assert storage["entries"].get("cdpx-key") == "cdpx-value"


def test_screenshot_real(page, tmp_path):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    out = tmp_path / "e2e.png"
    res = capture.screenshot(c, str(out))
    assert res["bytes"] > 1000
    assert out.read_bytes().startswith(b"\x89PNG")


def test_full_page_screenshot_captures_long_page(page, tmp_path):
    c, base = page
    nav.navigate(c, f"{base}/long.html")
    normal = tmp_path / "normal.png"
    full = tmp_path / "full.png"
    normal_res = capture.screenshot(c, str(normal))
    full_res = capture.screenshot(c, str(full), full_page=True)
    assert full_res["full_page"] is True
    assert full_res["bytes"] > normal_res["bytes"]
    assert full.read_bytes().startswith(b"\x89PNG")


def test_json_endpoint_reachable_from_page(page):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    raw = js.evaluate(c, f"fetch('{base}/api/json').then(r => r.text())", await_promise=True)
    assert json.loads(raw)["ok"] is True
