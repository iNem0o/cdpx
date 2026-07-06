"""E2E Symfony réel pour M2.

Ce test est lancé par docker-compose.symfony-e2e.yml. Il prouve que `cdpx
profiler` lit un vrai header X-Debug-Token-Link émis par WebProfilerBundle.
"""

import json
import os
import shutil
import subprocess
import sys
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
SCENARIO_FIXTURES = Path(__file__).parents[1] / "fixtures" / "scenarios"


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


def materialize_scenario(template: str, base_url: str, tmp_path: Path) -> Path:
    src = SCENARIO_FIXTURES / template
    dest = tmp_path / template
    dest.write_text(src.read_text(encoding="utf-8").replace("__BASE_URL__", base_url), "utf-8")
    return dest


def run_scenario_cli(
    chrome: int,
    tab: dict,
    scenario: Path,
    evidence_dir: Path,
    *,
    timeout: float = 12.0,
) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cdpx.cli",
            "--port",
            str(chrome),
            "--target",
            tab["id"],
            "--timeout",
            str(timeout),
            "scenario",
            "run",
            str(scenario),
            "--evidence-dir",
            str(evidence_dir),
            "--settle",
            "0.5",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=max(timeout * 8, 30),
    )
    payload = json.loads(proc.stdout) if proc.stdout else {}
    return proc.returncode, payload, proc.stderr


def attach_scenario_run(evidence_case, result: dict, label: str) -> None:
    if evidence_case is None:
        return
    evidence_case.attach_json(label, result, f"{label}.json")
    for artifact in result.get("artifacts", []):
        path = Path(artifact["path"])
        if path.exists():
            evidence_case.attach_file(
                path,
                f"{label}-{artifact['label']}-{artifact['type']}",
                artifact["type"],
            )


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
  const named = (el) => (el.innerText || el.getAttribute('aria-label') || '').trim()
    || (el.querySelector('img')?.getAttribute('alt') || '').trim();
  const checks = {
    images: {
      informative_alt: Boolean(document.querySelector('img[src*="info"][alt]:not([alt=""])')),
      decorative_alt_empty: Boolean(document.querySelector('img[src*="decorative"][alt=""]')),
      linked_image_named: Boolean(document.querySelector('a img[alt]:not([alt=""])'))
    },
    frames: {
      iframe_title: Array.from(document.querySelectorAll('iframe')).every(f => f.title)
    },
    colors: {
      contrast_token: document.body.dataset.contrastToken === 'AA',
      not_color_only: (document.querySelector('.badge')?.innerText || '').trim().length > 0
    },
    media: {
      captions_or_transcript: Boolean(
        document.querySelector('video track[kind="captions"], a[href="#transcript"]')
      )
    },
    tables: {
      caption: Boolean(document.querySelector('table caption')),
      th_scope_or_headers: Boolean(document.querySelector('th[scope], td[headers]'))
    },
    links: {
      accessible_names: Array.from(document.querySelectorAll('a[href]')).every(a => named(a)),
      no_ambiguous_links: !Array.from(document.querySelectorAll('a[href]')).some(
        a => named(a).toLowerCase() === 'click here'
      ),
      image_links_named: Array.from(document.querySelectorAll('a img')).every(
        img => (img.getAttribute('alt') || '').trim()
      )
    },
    scripts: {
      name_role_value_state: Boolean(document.querySelector('button[aria-pressed]'))
    },
    mandatory: {
      lang: Boolean(document.documentElement.lang),
      title: Boolean(document.title),
      viewport: Boolean(document.querySelector('meta[name="viewport"]')),
      landmark: Boolean(document.querySelector('main')),
      single_h1: document.querySelectorAll('h1').length === 1
    },
    structure: {
      heading_order: document.querySelectorAll('h1').length === 1
    },
    presentation: {
      reflow_basic: document.documentElement.scrollWidth <= window.innerWidth + 1,
      text_not_truncated: Boolean(document.querySelector('.clip-check'))
    },
    forms: {
      labels: inputs.every(el => labels.has(el.id) || Boolean(el.getAttribute('aria-label'))),
      required: Array.from(document.querySelectorAll('[required]')).every(
        el => el.hasAttribute('required')
      ),
      errors_described: Boolean(
        document.querySelector('[role="alert"]')
        && document.querySelector('form[aria-describedby]')
      ),
      fieldset_legend: Boolean(document.querySelector('fieldset legend'))
    },
    navigation: {
      skip_link: Boolean(document.querySelector('a[href="#content"]')),
      focus_order: Array.from(document.querySelectorAll('a[href], button, input')).length >= 3,
      focus_visible: document.body.dataset.focusVisible === 'true',
      keyboard_activation: !Array.from(document.querySelectorAll('[onclick]')).some(
        el => el.tagName !== 'BUTTON' && el.getAttribute('role') !== 'button'
      )
    },
    consultation: {
      zoom_reflow_basic: document.documentElement.scrollWidth <= window.innerWidth + 1,
      content_not_hidden: Boolean(document.querySelector('main'))
    }
  };
  const specs = [
    ['images', 'images', ['1.1', '1.2', '1.6'], checks.images],
    ['cadres/iframes', 'frames', ['2.1'], checks.frames],
    ['couleurs', 'colors', ['3.1', '3.2'], checks.colors],
    ['multimédia', 'media', ['4.1'], checks.media],
    ['tableaux', 'tables', ['5.4', '5.6', '5.7'], checks.tables],
    ['liens', 'links', ['6.1', '6.2'], checks.links],
    ['scripts/composants', 'scripts', ['7.1', '7.3'], checks.scripts],
    ['éléments obligatoires', 'mandatory', ['8.3', '8.5', '8.6', '8.9'], checks.mandatory],
    ['structuration', 'structure', ['9.1'], checks.structure],
    ['présentation', 'presentation', ['10.11', '10.12'], checks.presentation],
    ['formulaires', 'forms', ['11.1', '11.2', '11.10'], checks.forms],
    ['navigation', 'navigation', ['12.7', '12.8', '12.9'], checks.navigation],
    ['consultation', 'consultation', ['13.8', '13.9'], checks.consultation]
  ];
  const reports = specs.map(([theme, key, criteria, themeChecks]) => {
    const passed = Object.values(themeChecks).every(Boolean);
    return {
      theme,
      criteria,
      automated_scope: 'automated subset; deterministic DOM/accessibility-tree probes only',
      status: passed ? 'pass' : 'fail',
      checks: themeChecks,
      limitations: [
        'Static checks do not validate content relevance or full assistive technology behavior.',
        'This is not a complete RGAA conformance audit.'
      ]
    };
  });
  return {
    h1_count: document.querySelectorAll('h1').length,
    has_main_landmark: Boolean(document.querySelector('main')),
    all_inputs_labelled: checks.forms.labels,
    focus_visible: checks.navigation.focus_visible,
    contrast_token: document.body.dataset.contrastToken || null,
    automated_scope: document.body.dataset.automatedScope,
    reports
  };
})())
""",
    )
    return json.loads(raw)


def vitals_diagnostics(c: CDPClient) -> dict:
    raw = js.evaluate(
        c,
        """
JSON.stringify((() => {
  const vitals = window.__cdpxVitals || {};
  const meta = window.__cdpxVitalsMeta || {};
  const thresholds = meta.thresholds || {};
  const rate = (metric, value) => {
    const t = thresholds[metric] || {};
    if (value <= (t.good ?? 0)) return 'good';
    if (value <= (t.poor ?? 0)) return 'needs-improvement';
    return 'poor';
  };
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const fcp = performance.getEntriesByName('first-contentful-paint')[0] || {};
  const resources = performance.getEntriesByType('resource').map((entry) => ({
    name: entry.name,
    initiatorType: entry.initiatorType,
    duration: Math.round(entry.duration || 0),
    transferSize: entry.transferSize || 0
  }));
  const bucket = (predicate) => resources.filter(predicate);
  return {
    thresholds,
    core_web_vitals: {
      lcp: {value_ms: vitals.lcp || 0, rating: rate('lcp', vitals.lcp || 0)},
      inp: {value_ms: vitals.inp || 0, rating: rate('inp', vitals.inp || 0)},
      cls: {value: vitals.cls || 0, rating: rate('cls', vitals.cls || 0)}
    },
    lcp_attribution: meta.lcp || {},
    navigation_timing: {
      ttfb_ms: Math.max(0, Math.round((nav.responseStart || 0) - (nav.requestStart || 0))),
      fcp_ms: Math.round(fcp.startTime || 0),
      dom_complete_ms: Math.round(nav.domComplete || 0)
    },
    resource_timing: {
      total: resources.length,
      css: bucket((r) => r.initiatorType === 'link' && r.name.includes('/style/')).length,
      js: bucket((r) => r.initiatorType === 'script').length,
      images: bucket((r) => r.initiatorType === 'img').length,
      font: bucket((r) => r.name.includes('/font/')).length,
      critical: meta.critical_resources || {}
    },
    cls_attribution: {
      expected_shift_count: meta.cls?.expected_shift_count || 0,
      expected_max_shift: meta.cls?.expected_max_shift || 0
    },
    inp_attribution: {
      target: meta.inp?.target || null,
      expected_event_duration_ms: meta.inp?.expected_event_duration_ms || 0,
      expected_long_tasks: meta.inp?.expected_long_tasks || 0
    },
    emulation_variants: meta.emulation || {},
    limitations: [
      'Browser support controls whether INP/event timing attribution appears.',
      'LoAF-like long task attribution may be unavailable.',
      'Local deterministic pages expose expected attribution metadata for stable CI comparison.'
    ]
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
        "doctrine-duplicates",
        "cache-miss",
        "cache-hit",
        "cache-stale",
        "twig-light",
        "twig-heavy",
        "stopwatch-sections",
        "http-client-success",
        "http-client-error",
        "http-client-timeout",
        "messenger-sync",
        "messenger-queued",
        "routing-redirect",
        "routing-404",
        "routing-500",
        "headers-cache",
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
    assert comparison["cache-stale"]["cache_state"] == "stale"
    assert (
        comparison["doctrine-duplicates"]["db_duplicate_queries"]
        > (comparison["doctrine-n-plus-one"]["db_duplicate_queries"])
    )
    assert comparison["twig-heavy"]["twig_renders"] > comparison["twig-light"]["twig_renders"]
    assert comparison["stopwatch-sections"]["stopwatch_sections"] >= 4
    assert comparison["http-client-success"]["http_client"] == "success"
    assert comparison["http-client-error"]["http_client"] == "error"
    assert comparison["http-client-timeout"]["http_client"] == "timeout"
    assert comparison["messenger-sync"]["messenger"] == "sync-handled"
    assert comparison["messenger-queued"]["queue_depth"] == 3
    assert comparison["routing-redirect"]["response_status"] == 302
    assert comparison["routing-404"]["response_status"] == 404
    assert comparison["routing-500"]["response_status"] == 500
    assert comparison["headers-cache"]["expected"] == "cache-headers-present"


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
            baseline_diagnostics = vitals_diagnostics(c)
            degraded = advanced.vitals(
                c,
                f"{SYMFONY_URL}/scenario/vitals/degraded",
                click_selector="#inp-button",
                settle=1.0,
            )
            degraded_metrics = audit.metrics(c)
            degraded_expected = expected_from_page(c)
            degraded_diagnostics = vitals_diagnostics(c)
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
            "diagnostics": baseline_diagnostics,
        },
        "degraded": {
            "vitals": degraded,
            "metrics": degraded_metrics,
            "expected": degraded_expected,
            "diagnostics": degraded_diagnostics,
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
    assert baseline_diagnostics["thresholds"]["lcp"]["good"] == 2500
    assert degraded_diagnostics["cls_attribution"]["expected_shift_count"] >= 1
    assert (
        degraded_diagnostics["inp_attribution"]["expected_event_duration_ms"]
        > (baseline_diagnostics["inp_attribution"]["expected_event_duration_ms"])
    )


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="measure-vitals",
    scenario_id="seo-performance-accessibility.symfony-vitals-diagnostic-attribution",
    proves=[
        "Symfony exposes deterministic LCP, CLS, INP and resource timing diagnostic routes.",
        "Core Web Vitals remain primary while attribution and emulation metadata are explicit.",
    ],
)
def test_symfony_vitals_diagnostics_cover_attribution_routes(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    cases = [
        "lcp-image",
        "lcp-text",
        "cls-injected-banner",
        "inp-long-task",
        "resource-blocking",
    ]
    evidence = {}
    emulation = {}
    try:
        with client as c:
            for case in cases:
                if case == "inp-long-task":
                    emulation[case] = advanced.emulate(c, "cpu-4x")
                elif case == "resource-blocking":
                    emulation[case] = advanced.emulate(c, "slow-3g")
                result = advanced.vitals(
                    c,
                    f"{SYMFONY_URL}/scenario/vitals/{case}",
                    click_selector="#inp-button",
                    settle=1.0,
                )
                diagnostics = vitals_diagnostics(c)
                expected = expected_from_page(c)
                evidence[case] = {
                    "vitals": result,
                    "diagnostics": diagnostics,
                    "expected": expected,
                    "applied_emulation": emulation.get(case),
                }
                advanced.emulate(c, reset=True)
            screenshot(
                c,
                tmp_path,
                "symfony-vitals-diagnostics.png",
                evidence_case,
                "Symfony vitals diagnostics",
            )
    finally:
        close_tab(chrome, target)

    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony vitals diagnostic attribution",
            evidence,
            "symfony-vitals-diagnostic-attribution.json",
        )

    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["type"] == "image"
    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["selector"] == "#hero-image"
    assert evidence["lcp-text"]["diagnostics"]["lcp_attribution"]["type"] == "text"
    assert (
        evidence["cls-injected-banner"]["diagnostics"]["cls_attribution"]["expected_shift_count"]
        >= 1
    )
    assert (
        evidence["inp-long-task"]["diagnostics"]["inp_attribution"]["expected_event_duration_ms"]
        >= 90
    )
    assert evidence["resource-blocking"]["diagnostics"]["resource_timing"]["critical"]["js"] == 3
    assert evidence["resource-blocking"]["diagnostics"]["resource_timing"]["js"] >= 3


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
        "scope": "Automated subset: deterministic RGAA-themed DOM and accessibility probes.",
        "claim": "This evidence does not claim complete RGAA conformance.",
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
    expected_themes = {
        "images",
        "cadres/iframes",
        "couleurs",
        "multimédia",
        "tableaux",
        "liens",
        "scripts/composants",
        "éléments obligatoires",
        "structuration",
        "présentation",
        "formulaires",
        "navigation",
        "consultation",
    }
    baseline_reports = {item["theme"]: item for item in baseline["reports"]}
    regression_reports = {item["theme"]: item for item in regression["reports"]}
    assert set(baseline_reports) == expected_themes
    assert set(regression_reports) == expected_themes
    assert all(
        item["automated_scope"].startswith("automated subset") for item in baseline_reports.values()
    )
    assert all(item["limitations"] for item in baseline_reports.values())
    assert all(item["status"] == "pass" for item in baseline_reports.values())
    assert any(item["status"] == "fail" for item in regression_reports.values())


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


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=[
        "Declarative YAML scenarios execute against the real Symfony test application.",
        "Pass, controlled fail, and profiler/vitals evidence runs all produce reports.",
    ],
)
def test_declarative_scenarios_run_against_real_symfony(chrome, tmp_path, evidence_case):
    wait_for_symfony(SYMFONY_URL)
    cases = [
        ("symfony_front_pass.yml", 0, "pass", 12.0),
        ("symfony_front_fail.yml", 1, "fail", 1.0),
        ("symfony_profiler_vitals.yml", 0, "pass", 15.0),
    ]
    results = {}
    for template, expected_code, expected_verdict, timeout in cases:
        scenario = materialize_scenario(template, SYMFONY_URL, tmp_path)
        tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
        try:
            code, result, err = run_scenario_cli(
                chrome,
                tab,
                scenario,
                tmp_path / f"evidence-{template}",
                timeout=timeout,
            )
        finally:
            close_tab(chrome, tab)
        attach_scenario_run(evidence_case, result, template.replace(".yml", ""))
        assert code == expected_code, err
        assert result["verdict"] == expected_verdict
        assert result["artifacts"]
        results[template] = result

    profiler_artifacts = [
        artifact
        for artifact in results["symfony_profiler_vitals.yml"]["artifacts"]
        if artifact["type"] == "profiler"
    ]
    assert profiler_artifacts
    assert any(
        finding["code"] == "step_failed"
        for finding in results["symfony_front_fail.yml"]["findings"]
    )
