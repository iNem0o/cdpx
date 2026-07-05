"""E2E Symfony réel pour M2.

Ce test est lancé par docker-compose.symfony-e2e.yml. Il prouve que `cdpx
profiler` lit un vrai header X-Debug-Token-Link émis par WebProfilerBundle.
"""

import json
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
from cdpx.primitives import advanced, audit, capture, dev, js, nav

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


def open_tab(chrome: int):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    return target, CDPClient(target["webSocketDebuggerUrl"], timeout=20)


def close_tab(chrome: int, target: dict) -> None:
    discovery.close_tab("127.0.0.1", chrome, target["id"])


def screenshot(c: CDPClient, tmp_path: Path, filename: str, evidence_case, label: str) -> None:
    path = tmp_path / filename
    capture.screenshot(c, str(path))
    if evidence_case is not None:
        evidence_case.attach_screenshot(path, label)


def expected_from_page(c: CDPClient) -> dict:
    raw = js.evaluate(c, "JSON.stringify(window.__scenarioExpected || {})")
    return json.loads(raw)


def rgaa_checks(c: CDPClient) -> dict:
    raw = js.evaluate(
        c,
        """
JSON.stringify((() => {
  const labels = new Set(Array.from(document.querySelectorAll('label[for]')).map(l => l.htmlFor));
  const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
  return {
    h1_count: document.querySelectorAll('h1').length,
    has_main_landmark: Boolean(document.querySelector('main')),
    all_inputs_labelled: inputs.every(el =>
      labels.has(el.id) || Boolean(el.getAttribute('aria-label'))
    ),
    focus_visible: document.body.dataset.focusVisible === 'true',
    contrast_token: document.body.dataset.contrastToken || null
  };
})())
""",
    )
    return json.loads(raw)


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
    target, client = open_tab(chrome)
    try:
        with client as c:
            res = dev.profiler(c, f"{SYMFONY_URL}/profiler-target", timeout=20, settle=0.5)
            shot = Path(tmp_path) / "symfony-profiler-target.png"
            capture.screenshot(c, str(shot))
    finally:
        close_tab(chrome, target)

    if evidence_case is not None:
        evidence_case.attach_json("Symfony profiler result", res, "symfony-profiler-result.json")
        evidence_case.attach_text(
            "Symfony profiler URL",
            f"target={res['url']}\nprofiler={res['profiler_url']}\ntoken={res['token']}\n",
            "symfony-profiler-url.log",
        )
        evidence_case.attach_screenshot(shot, "Symfony profiler target")

    assert res["url"].endswith("/profiler-target")
    assert res["status"] == 200
    assert res["token"]
    assert res["profiler_url"].startswith(f"{SYMFONY_URL}/_profiler/")
    assert res["profiler_bytes"] > 1000
    assert res["panels"]["raw"]["bytes"] == res["profiler_bytes"]


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="compare-profiler-variants",
    scenario_id="dev-profiler-diff.compare-symfony-profiler-variants",
    proves=[
        "Symfony exposes deterministic baseline/degraded profiler scenarios.",
        "Doctrine-like N+1 and cache hit/miss signals are compared as structured JSON.",
    ],
)
def test_profiler_compares_deterministic_symfony_variants(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    cases = [
        "baseline",
        "degraded",
        "doctrine-normal",
        "doctrine-n-plus-one",
        "cache-miss",
        "cache-hit",
    ]
    try:
        with client as c:
            results = {
                case: dev.profiler(
                    c,
                    f"{SYMFONY_URL}/scenario/profiler/{case}",
                    timeout=20,
                    settle=0.5,
                )
                for case in cases
            }
            screenshot(
                c,
                tmp_path,
                "symfony-profiler-variants.png",
                evidence_case,
                "Profiler variants",
            )
    finally:
        close_tab(chrome, target)

    comparison = {case: result["signals"] for case, result in results.items()}
    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony profiler variant comparison",
            {
                "cases": comparison,
                "tokens": {case: result["token"] for case, result in results.items()},
            },
            "symfony-profiler-variant-comparison.json",
        )

    assert comparison["baseline"]["time_ms"] < comparison["degraded"]["time_ms"]
    assert comparison["baseline"]["payload_bytes"] < comparison["degraded"]["payload_bytes"]
    assert (
        comparison["doctrine-normal"]["db_queries"]
        < comparison["doctrine-n-plus-one"]["db_queries"]
    )
    assert comparison["doctrine-n-plus-one"]["db_duplicate_queries"] > 0
    assert comparison["cache-miss"]["cache_hit"] is False
    assert comparison["cache-hit"]["cache_hit"] is True


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="measure-vitals",
    scenario_id="seo-performance-accessibility.compare-symfony-vitals",
    proves=[
        "Symfony renders deterministic baseline and degraded vitals pages.",
        "cdpx vitals, metrics and screenshots are orchestrated into comparable evidence.",
    ],
)
def test_symfony_vitals_compare_baseline_degraded(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            baseline = advanced.vitals(
                c,
                f"{SYMFONY_URL}/scenario/vitals/baseline",
                click_selector="#inp-button",
                settle=1.0,
            )
            baseline_metrics = audit.metrics(c)
            baseline_expected = expected_from_page(c)
            degraded = advanced.vitals(
                c,
                f"{SYMFONY_URL}/scenario/vitals/degraded",
                click_selector="#inp-button",
                settle=1.0,
            )
            degraded_metrics = audit.metrics(c)
            degraded_expected = expected_from_page(c)
            screenshot(
                c,
                tmp_path,
                "symfony-vitals-degraded.png",
                evidence_case,
                "Symfony vitals degraded",
            )
    finally:
        close_tab(chrome, target)

    evidence = {
        "baseline": {
            "vitals": baseline,
            "metrics": baseline_metrics,
            "expected": baseline_expected,
        },
        "degraded": {
            "vitals": degraded,
            "metrics": degraded_metrics,
            "expected": degraded_expected,
        },
    }
    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony vitals comparison",
            evidence,
            "symfony-vitals-comparison.json",
        )

    assert baseline["url"].endswith("/scenario/vitals/baseline")
    assert degraded["url"].endswith("/scenario/vitals/degraded")
    assert degraded_expected["layout_shift"] is True
    assert baseline_expected["layout_shift"] is False
    assert degraded_expected["interaction_work_ms"] > baseline_expected["interaction_work_ms"]
    assert degraded_expected["payload_blocks"] > baseline_expected["payload_blocks"]


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="audit-front-accessibility",
    scenario_id="seo-performance-accessibility.audit-symfony-rgaa-subset",
    proves=[
        "Symfony exposes deterministic accessible and regressed front states.",
        "Automated RGAA-like checks are reported as a limited subset, not full RGAA coverage.",
    ],
)
def test_symfony_rgaa_subset_checks_are_deterministic(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            nav.navigate(c, f"{SYMFONY_URL}/scenario/rgaa/baseline", timeout=20)
            baseline_tree = advanced.a11y(c)
            baseline = rgaa_checks(c)
            baseline_expected = expected_from_page(c)
            nav.navigate(c, f"{SYMFONY_URL}/scenario/rgaa/regression", timeout=20)
            regression_tree = advanced.a11y(c)
            regression = rgaa_checks(c)
            regression_expected = expected_from_page(c)
            screenshot(
                c,
                tmp_path,
                "symfony-rgaa-regression.png",
                evidence_case,
                "Symfony RGAA regression",
            )
    finally:
        close_tab(chrome, target)

    evidence = {
        "baseline": {
            "checks": baseline,
            "expected": baseline_expected,
            "a11y_nodes": baseline_tree["count"],
        },
        "regression": {
            "checks": regression,
            "expected": regression_expected,
            "a11y_nodes": regression_tree["count"],
        },
        "scope": (
            "Automated subset: headings, landmark, labels, focus visibility and contrast token."
        ),
    }
    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony RGAA subset checks",
            evidence,
            "symfony-rgaa-subset.json",
        )

    assert baseline["h1_count"] == 1
    assert baseline["has_main_landmark"] is True
    assert baseline["all_inputs_labelled"] is True
    assert baseline["focus_visible"] is True
    assert regression["h1_count"] == 2
    assert regression["has_main_landmark"] is False
    assert regression["all_inputs_labelled"] is False
    assert regression["focus_visible"] is False


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="diff-dom-action",
    scenario_id="dev-profiler-diff.symfony-front-state-regression",
    proves=[
        "A Symfony route exposes deterministic front before/after state.",
        "cdpx DOM diff captures the state transition as structured evidence.",
    ],
)
def test_symfony_front_state_dom_diff(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            nav.navigate(c, f"{SYMFONY_URL}/scenario/front/states", timeout=20)
            expected = expected_from_page(c)
            diff = dev.dom_diff(c, ["click", "#submit-btn"])
            screenshot(c, tmp_path, "symfony-front-state.png", evidence_case, "Symfony front state")
    finally:
        close_tab(chrome, target)

    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony front state DOM diff",
            {"expected": expected, "diff": diff},
            "symfony-front-state-dom-diff.json",
        )

    assert expected["before"] == "idle"
    assert expected["after"] == "submitted"
    assert diff["changed"] is True
    assert any("submitted" in line for line in diff["diff"])
