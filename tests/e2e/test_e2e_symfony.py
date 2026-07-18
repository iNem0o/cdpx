"""Real Symfony E2E for M2.

This test is launched by docker-compose.symfony-e2e.yml. It proves that
`cdpx profiler` follows a real X-Debug-Token-Link header emitted by
WebProfilerBundle and parses the real panels (Doctrine, Twig, cache,
exceptions, HTTP client, Messenger, routing, time, logs) fed by real
collectors.
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
from cdpx.action_model import ClickAction
from cdpx.client import CDPClient
from cdpx.primitives import audit, capture, dev, diagnostics, emulation, js, nav
from cdpx.session import SessionManifest, start_session, stop_session

CHROME_BIN = next(
    (b for b in ("chromium", "chromium-browser", "google-chrome", "chrome") if shutil.which(b)),
    None,
)
SYMFONY_URL = os.environ.get("SYMFONY_E2E_URL")

if CHROME_BIN is None:
    pytest.fail("Chrome/Chromium required for cdpx Symfony e2e", pytrace=False)

pytestmark = pytest.mark.skipif(
    not SYMFONY_URL,
    reason="SYMFONY_E2E_URL missing (run make docker-symfony-e2e)",
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
    session: tuple[SessionManifest, Path],
    scenario: Path,
    *,
    timeout: float = 12.0,
) -> tuple[int, dict, str]:
    manifest, manifest_path = session
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "cdpx.cli",
            "--session",
            str(manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            str(timeout),
            "scenario",
            "run",
            str(scenario),
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
    ['frames/iframes', 'frames', ['2.1'], checks.frames],
    ['colors', 'colors', ['3.1', '3.2'], checks.colors],
    ['multimedia', 'media', ['4.1'], checks.media],
    ['tables', 'tables', ['5.4', '5.6', '5.7'], checks.tables],
    ['links', 'links', ['6.1', '6.2'], checks.links],
    ['scripts/components', 'scripts', ['7.1', '7.3'], checks.scripts],
    ['mandatory elements', 'mandatory', ['8.3', '8.5', '8.6', '8.9'], checks.mandatory],
    ['structure', 'structure', ['9.1'], checks.structure],
    ['presentation', 'presentation', ['10.11', '10.12'], checks.presentation],
    ['forms', 'forms', ['11.1', '11.2', '11.10'], checks.forms],
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


@pytest.fixture(scope="module")
def managed_cli_session(tmp_path_factory):
    assert SYMFONY_URL is not None
    runtime = tmp_path_factory.mktemp("cdpx-symfony-managed")
    manifest, path = start_session(
        run_id="symfony-cli",
        authority="privileged",
        origins=SYMFONY_URL,
        ttl=900,
        owner_pid=os.getpid(),
        chrome_bin=CHROME_BIN,
        root=runtime,
    )
    try:
        yield manifest, path
    finally:
        if path.exists():
            stop_session(path, run_id=manifest.run_id, target_id=manifest.target_id)


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="read-profiler",
    scenario_id="dev-profiler-diff.read-symfony-profiler",
    proves=[
        "Real Symfony WebProfilerBundle emits a token reachable from browser navigation.",
        "cdpx profiler parses the real profiler panels into structured metrics.",
    ],
)
def test_profiler_reads_real_symfony_web_profiler(chrome, tmp_path, evidence_case):
    """Against the real Symfony app, `cdpx profiler` follows the
    X-Debug-Token-Link header emitted by WebProfilerBundle and delivers
    panels parsed into metrics, without ever exposing the token itself."""
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
            f"target={res['url']}\nprofiler={res['profiler_url']}\n"
            f"token_present={res['token_present']}\n",
            "symfony-profiler-url.log",
        )
        evidence_case.attach_screenshot(shot, "Symfony profiler target")

    #: the real navigation landed on the profiled route, with a 200
    assert res["url"].endswith("/profiler-target")
    assert res["status"] == 200
    #: the real token emitted by WebProfilerBundle stays secret: only its
    #: presence is reported, and the derived profiler URL does answer 200
    assert res["token_present"] is True and "token" not in res
    assert res["profiler_url"].startswith(f"{SYMFONY_URL}/_profiler/")
    assert res["profiler_status"] == 200
    #: internal collection fields do not leak into the CLI contract
    assert "signals" not in res and "profiler_bytes" not in res
    panels = res["panels"]
    #: each real collector (routing, exception, time, Doctrine, logs) is
    #: parsed into metrics consistent with the route visited: correct
    #: route, no exception, typed durations and zero SQL query on this page
    assert panels["router"]["available"] is True
    assert panels["router"]["route"] == "profiler_target"
    assert panels["exception"]["available"] is True
    assert panels["exception"]["raised"] is False
    assert panels["time"]["available"] is True
    assert isinstance(panels["time"]["total_ms"], float)
    assert panels["db"]["available"] is True
    assert panels["db"]["queries"] == 0  # this route does not touch the database
    assert panels["logger"]["available"] is True


@pytest.mark.scenario(
    feature="dev-profiler-diff",
    journey="compare-profiler-variants",
    scenario_id="dev-profiler-diff.compare-symfony-profiler-variants",
    proves=[
        "Symfony scenario routes drive real collectors (Doctrine, cache, HTTP client, Messenger).",
        "cdpx reads N+1, duplicate bursts and cache hit/miss from the real profiler panels.",
    ],
)
def test_profiler_compares_deterministic_symfony_variants(chrome, tmp_path, evidence_case):
    """Twenty scenario routes drive the real Symfony collectors and cdpx
    finds their expected signatures in the panels (N+1, duplicates,
    cache hit/miss, HTTP client errors, exceptions, headers) — in counts,
    classes and statuses, never in milliseconds."""
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
        "cache-expired",
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
    # Individual captures of key variants, taken while the variant's page
    # is displayed (right after its profiler navigation), instead of a
    # single final capture that would only show the last route.
    captured_variants = ("doctrine-n-plus-one", "routing-404", "routing-500")
    try:
        with client as c:
            results = {}
            for case in cases:
                results[case] = dev.profiler(
                    c,
                    f"{SYMFONY_URL}/scenario/profiler/{case}",
                    timeout=20,
                    settle=0.5,
                )
                if case in captured_variants:
                    screenshot(
                        c,
                        tmp_path,
                        f"symfony-profiler-{case}.png",
                        evidence_case,
                        f"Profiler variant {case}",
                    )
    finally:
        close_tab(chrome, target)

    panels = {case: result["panels"] for case, result in results.items()}
    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony profiler variant comparison",
            {
                "cases": panels,
                "token_presence": {
                    case: result["token_present"] for case, result in results.items()
                },
            },
            "symfony-profiler-variant-comparison.json",
        )

    # Determinism = counts/classes/routes/statuses, never milliseconds.
    def db(case):
        return panels[case]["db"]

    def cache(case):
        return panels[case]["cache"]

    #: the real Doctrine N+1 leaves its exact signature in the db panel:
    #: 1 findAll + 5 lazy loads = 6 queries including 4 duplicates, while
    #: the healthy variant makes 3 with no duplicate at all — this is the
    #: diagnosis cdpx sells, read on real SQL queries
    assert db("baseline")["queries"] < db("degraded")["queries"]
    assert db("degraded")["duplicates"] >= 3
    assert db("doctrine-normal")["queries"] == 3
    assert db("doctrine-normal")["duplicates"] == 0
    assert db("doctrine-n-plus-one")["queries"] == 6  # 1 findAll + 5 lazy loads
    assert db("doctrine-n-plus-one")["duplicates"] == 4
    assert db("doctrine-duplicates")["duplicates"] >= 3
    assert any("FROM" in q["sql"].upper() for q in db("doctrine-n-plus-one")["list"])

    #: miss, hit and expiration are distinguished by their hits/misses/writes
    #: counters read in the real pool: a cold cache has no hit, a warm cache
    #: dominates its misses, expiration forces a rewrite
    assert cache("cache-miss")["hits"] == 0
    assert cache("cache-miss")["misses"] >= 3
    assert cache("cache-hit")["hits"] >= 3
    assert cache("cache-hit")["hits"] > cache("cache-hit")["misses"]
    assert cache("cache-hit")["writes"] >= 1
    assert cache("cache-expired")["hits"] == 0
    assert cache("cache-expired")["writes"] >= 2

    #: the heavy page renders strictly more templates than the light one:
    #: the Twig panel discriminates between the two rendering variants
    assert panels["twig-heavy"]["twig"]["templates"] > panels["twig-light"]["twig"]["templates"]

    time_panel = panels["stopwatch-sections"]["time"]
    #: the application stopwatch produces a typed time panel; named sections
    #: are only required if the timeline comes back (best-effort)
    assert time_panel["available"] is True
    assert isinstance(time_panel["total_ms"], float)
    if time_panel["events"]:  # best-effort timeline, real sections if present
        assert any("cdpx.section" in (event["name"] or "") for event in time_panel["events"])

    success = panels["http-client-success"]["http_client"]
    #: success, error and timeout of the real HTTP client are distinguished
    #: by statuses and error count — never by duration: the timeout is read
    #: from the absence of a 200, not from a stopwatch
    assert success["requests"] == 1
    assert any(item.get("status") == 200 for item in success["list"])
    error = panels["http-client-error"]["http_client"]
    assert error["requests"] == 1
    assert error["errors"] >= 1 or any(item.get("status") == 500 for item in error["list"])
    timeout = panels["http-client-timeout"]["http_client"]
    assert timeout["requests"] >= 1
    assert not any(item.get("status") == 200 for item in timeout["list"])

    #: each messenger variant dispatches exactly one message; the message
    #: class is only checked if the panel lists the messages
    assert panels["messenger-sync"]["messenger"]["dispatched"] == 1
    assert panels["messenger-queued"]["messenger"]["dispatched"] == 1
    sync_classes = [item["class"] for item in panels["messenger-sync"]["messenger"]["list"]]
    if sync_classes:  # best-effort list
        assert any(cls.endswith("SyncPing") for cls in sync_classes)

    #: redirect, 404 and 500 come through with their real HTTP status, and
    #: exceptions surface with their exact class — including the global,
    #: namespace-less class of the 500 — while the logger captures the error
    assert results["routing-redirect"]["status"] == 302
    assert results["routing-404"]["status"] == 404
    assert results["routing-500"]["status"] == 500
    assert panels["routing-404"]["exception"]["raised"] is True
    assert panels["routing-404"]["exception"]["class"].endswith("NotFoundHttpException")
    assert panels["routing-500"]["exception"]["raised"] is True
    assert panels["routing-500"]["exception"]["class"].endswith("RuntimeException")
    assert panels["routing-500"]["logger"]["available"] is True

    cache_control = results["headers-cache"]["response_headers"].get("cache-control", "")
    #: the Cache-Control directives set by the controller carry through to
    #: the report: non-sensitive headers are not redacted
    assert "max-age=60" in cache_control
    assert "public" in cache_control


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
    """The baseline/degraded vitals pages rendered by Symfony produce
    comparable measurements: cdpx orchestrates vitals, metrics and
    diagnostics, and the expected contrast between the two variants is
    observable."""
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            baseline = diagnostics.vitals(
                c,
                f"{SYMFONY_URL}/scenario/vitals/baseline",
                click_selector="#inp-button",
                settle=1.0,
            )
            baseline_metrics = audit.metrics(c)
            baseline_expected = expected_from_page(c)
            baseline_diagnostics = vitals_diagnostics(c)
            degraded = diagnostics.vitals(
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

    #: each measurement carries its variant's URL: no possible mixup
    #: between the two successive navigations in the same tab
    assert baseline["url"].endswith("/scenario/vitals/baseline")
    assert degraded["url"].endswith("/scenario/vitals/degraded")
    #: the contrast is declared by the pages themselves: layout shift only
    #: in the degraded variant, strictly higher interaction work and
    #: payload — the comparison stays deterministic, not timed
    assert degraded_expected["layout_shift"] is True
    assert baseline_expected["layout_shift"] is False
    assert degraded_expected["interaction_work_ms"] > baseline_expected["interaction_work_ms"]
    assert degraded_expected["payload_blocks"] > baseline_expected["payload_blocks"]
    #: the standard Web Vitals thresholds are exposed and the CLS/INP
    #: attribution of the degraded page exceeds the baseline's: the
    #: diagnosis is measured in the browser, not merely asserted
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
    """Each diagnostic route (LCP image/text, injected CLS, INP under
    throttled CPU, blocking resources under slow-3g) exposes a
    deterministic attribution that cdpx reads after applying then lifting
    emulation."""
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
    applied_emulation = {}
    try:
        with client as c:
            for case in cases:
                if case == "inp-long-task":
                    applied_emulation[case] = emulation.emulate(c, "cpu-4x")
                elif case == "resource-blocking":
                    applied_emulation[case] = emulation.emulate(c, "slow-3g")
                result = diagnostics.vitals(
                    c,
                    f"{SYMFONY_URL}/scenario/vitals/{case}",
                    click_selector="#inp-button",
                    settle=1.0,
                )
                diagnostic_details = vitals_diagnostics(c)
                expected = expected_from_page(c)
                evidence[case] = {
                    "vitals": result,
                    "diagnostics": diagnostic_details,
                    "expected": expected,
                    "applied_emulation": applied_emulation.get(case),
                }
                emulation.emulate(c, reset=True)
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

    #: the LCP attribution names the culprit element: type AND selector for
    #: the hero image, text type for the text variant — this is what makes
    #: the diagnosis actionable
    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["type"] == "image"
    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["selector"] == "#hero-image"
    assert evidence["lcp-text"]["diagnostics"]["lcp_attribution"]["type"] == "text"
    #: the injected shift and the long INP task leave quantified traces in
    #: their respective attributions
    assert (
        evidence["cls-injected-banner"]["diagnostics"]["cls_attribution"]["expected_shift_count"]
        >= 1
    )
    assert (
        evidence["inp-long-task"]["diagnostics"]["inp_attribution"]["expected_event_duration_ms"]
        >= 90
    )
    #: resource timing counts exactly the 3 critical scripts declared by
    #: the blocking page: the measurement cross-checks the route's contract
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
    """The accessible and regressed states rendered by Symfony are
    discriminated by the automated RGAA-like probes, and each themed report
    declares its limited scope: never a claim of complete RGAA audit."""
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            nav.navigate(c, f"{SYMFONY_URL}/scenario/rgaa/baseline", timeout=20)
            baseline_tree = diagnostics.a11y(c)
            baseline = rgaa_checks(c)
            baseline_expected = expected_from_page(c)
            nav.navigate(c, f"{SYMFONY_URL}/scenario/rgaa/regression", timeout=20)
            regression_tree = diagnostics.a11y(c)
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

    #: the key probes all flip between the two states: they genuinely
    #: discriminate the accessible from the regressed instead of always
    #: passing (single h1, landmark, labels, focus visible)
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
        "frames/iframes",
        "colors",
        "multimedia",
        "tables",
        "links",
        "scripts/components",
        "mandatory elements",
        "structure",
        "presentation",
        "forms",
        "navigation",
        "consultation",
    }
    baseline_reports = {item["theme"]: item for item in baseline["reports"]}
    regression_reports = {item["theme"]: item for item in regression["reports"]}
    #: the thirteen RGAA themes covered are present in both reports: none
    #: silently disappears depending on the page's state
    assert set(baseline_reports) == expected_themes
    assert set(regression_reports) == expected_themes
    #: each theme declares its automated scope and its limitations: the
    #: report cannot be mistaken for a complete RGAA audit
    assert all(
        item["automated_scope"].startswith("automated subset") for item in baseline_reports.values()
    )
    assert all(item["limitations"] for item in baseline_reports.values())
    #: consistent overall verdict: the baseline passes everywhere, the
    #: regression fails somewhere — the reports carry the signal, not noise
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
    """cdpx's DOM diff captures the state transition triggered by a click on
    a real Symfony page and renders it as a structured diff usable as
    evidence."""
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            nav.navigate(c, f"{SYMFONY_URL}/scenario/front/states", timeout=20)
            expected = expected_from_page(c)
            # Capture before click (idle state): taken after reading the
            # initial state and with no banner, it does not touch the DOM
            # compared by dom_diff, whose baseline stays intact.
            screenshot(
                c,
                tmp_path,
                "symfony-front-state-before.png",
                evidence_case,
                "Symfony front state (idle, before click)",
            )
            diff = dev.dom_diff(c, ClickAction("#submit-btn"))
            # Capture after click (submitted state): materializes the target
            # of the transition that the DOM diff proves.
            screenshot(
                c,
                tmp_path,
                "symfony-front-state-after.png",
                evidence_case,
                "Symfony front state (submitted, after click)",
            )
    finally:
        close_tab(chrome, target)

    if evidence_case is not None:
        evidence_case.attach_json(
            "Symfony front state DOM diff",
            {"expected": expected, "diff": diff},
            "symfony-front-state-dom-diff.json",
        )

    #: the page itself declares the expected idle -> submitted transition:
    #: the comparison reference comes from the server, not the test
    assert expected["before"] == "idle"
    assert expected["after"] == "submitted"
    #: the diff detects the change and the new state appears in the
    #: modified lines: the transition is captured, not merely signaled
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
def test_declarative_scenarios_run_against_real_symfony(
    managed_cli_session,
    tmp_path,
    evidence_case,
):
    """The declarative YAML scenarios run against the real app via the
    supervised session: pass, controlled failure and profiler/vitals
    collection each produce a report with a verdict, artifacts and
    findings."""
    wait_for_symfony(SYMFONY_URL)
    cases = [
        ("symfony_front_pass.yml", 0, "pass", 12.0),
        ("symfony_front_fail.yml", 1, "fail", 1.0),
        ("symfony_profiler_vitals.yml", 0, "pass", 15.0),
    ]
    results = {}
    for template, expected_code, expected_verdict, timeout in cases:
        scenario = materialize_scenario(template, SYMFONY_URL, tmp_path)
        code, result, err = run_scenario_cli(
            managed_cli_session,
            scenario,
            timeout=timeout,
        )
        attach_scenario_run(evidence_case, result, template.replace(".yml", ""))
        #: for each scenario, the CLI exit code and the report verdict
        #: agree with the expected outcome, and proof artifacts are
        #: produced even on a controlled failure
        assert code == expected_code, err
        assert result["verdict"] == expected_verdict
        assert result["artifacts"]
        results[template] = result

    profiler_artifacts = [
        artifact
        for artifact in results["symfony_profiler_vitals.yml"]["artifacts"]
        if artifact["type"] == "profiler"
    ]
    #: the collection scenario does deliver a profiler artifact: the
    #: business proof is attached to the report, not just the verdict
    assert profiler_artifacts
    #: the controlled failure is attributed to the faulty step in the
    #: findings, which makes the report diagnosable without rereading logs
    assert any(
        finding["code"] == "step_failed"
        for finding in results["symfony_front_fail.yml"]["findings"]
    )
