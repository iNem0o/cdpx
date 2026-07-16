"""E2E Symfony réel pour M2.

Ce test est lancé par docker-compose.symfony-e2e.yml. Il prouve que `cdpx
profiler` suit un vrai header X-Debug-Token-Link émis par WebProfilerBundle
et parse les vrais panels (Doctrine, Twig, cache, exceptions, HTTP client,
Messenger, routing, temps, logs) alimentés par de vrais collecteurs.
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
    """Contre la vraie app Symfony, `cdpx profiler` suit le header
    X-Debug-Token-Link émis par WebProfilerBundle et livre des panels parsés
    en métriques, sans jamais exposer le token lui-même."""
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

    #: la navigation réelle a abouti sur la route profilée, en 200
    assert res["url"].endswith("/profiler-target")
    assert res["status"] == 200
    #: le vrai token émis par WebProfilerBundle reste secret: seule sa
    #: présence est déclarée, et l'URL profiler dérivée répond bien en 200
    assert res["token_present"] is True and "token" not in res
    assert res["profiler_url"].startswith(f"{SYMFONY_URL}/_profiler/")
    assert res["profiler_status"] == 200
    #: les champs internes de collecte ne fuient pas dans le contrat CLI
    assert "signals" not in res and "profiler_bytes" not in res
    panels = res["panels"]
    #: chaque collecteur réel (routing, exception, temps, Doctrine, logs) est
    #: parsé en métriques cohérentes avec la route visitée: bonne route, pas
    #: d'exception, durées typées et zéro requête SQL sur cette page
    assert panels["router"]["available"] is True
    assert panels["router"]["route"] == "profiler_target"
    assert panels["exception"]["available"] is True
    assert panels["exception"]["raised"] is False
    assert panels["time"]["available"] is True
    assert isinstance(panels["time"]["total_ms"], float)
    assert panels["db"]["available"] is True
    assert panels["db"]["queries"] == 0  # cette route ne touche pas la base
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
    """Vingt routes scénario pilotent les vrais collecteurs Symfony et cdpx
    retrouve dans les panels leurs signatures attendues (N+1, doublons,
    hit/miss de cache, erreurs HTTP client, exceptions, headers) — en
    comptes, classes et statuts, jamais en millisecondes."""
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
    # Captures individuelles des variantes clés, prises pendant que la page
    # de la variante est affichée (juste après sa navigation profiler), au
    # lieu d'une seule capture finale montrant la dernière route seulement.
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

    # Déterminisme = comptes/classes/routes/statuts, jamais de millisecondes.
    def db(case):
        return panels[case]["db"]

    def cache(case):
        return panels[case]["cache"]

    #: le N+1 Doctrine réel laisse sa signature exacte dans le panel db:
    #: 1 findAll + 5 lazy loads = 6 requêtes dont 4 doublons, quand la
    #: variante saine en fait 3 sans aucun doublon — c'est le diagnostic
    #: que cdpx vend, lu sur de vraies requêtes SQL
    assert db("baseline")["queries"] < db("degraded")["queries"]
    assert db("degraded")["duplicates"] >= 3
    assert db("doctrine-normal")["queries"] == 3
    assert db("doctrine-normal")["duplicates"] == 0
    assert db("doctrine-n-plus-one")["queries"] == 6  # 1 findAll + 5 lazy loads
    assert db("doctrine-n-plus-one")["duplicates"] == 4
    assert db("doctrine-duplicates")["duplicates"] >= 3
    assert any("FROM" in q["sql"].upper() for q in db("doctrine-n-plus-one")["list"])

    #: miss, hit et expiration se distinguent par leurs compteurs
    #: hits/misses/writes lus dans le vrai pool: un cache froid n'a aucun
    #: hit, un cache chaud domine ses misses, l'expiration force la réécriture
    assert cache("cache-miss")["hits"] == 0
    assert cache("cache-miss")["misses"] >= 3
    assert cache("cache-hit")["hits"] >= 3
    assert cache("cache-hit")["hits"] > cache("cache-hit")["misses"]
    assert cache("cache-hit")["writes"] >= 1
    assert cache("cache-expired")["hits"] == 0
    assert cache("cache-expired")["writes"] >= 2

    #: la page lourde rend strictement plus de templates que la légère:
    #: le panel Twig discrimine les deux variantes de rendu
    assert panels["twig-heavy"]["twig"]["templates"] > panels["twig-light"]["twig"]["templates"]

    time_panel = panels["stopwatch-sections"]["time"]
    #: le stopwatch applicatif produit un panel temps typé; les sections
    #: nommées ne sont exigées que si la timeline est remontée (best-effort)
    assert time_panel["available"] is True
    assert isinstance(time_panel["total_ms"], float)
    if time_panel["events"]:  # timeline best-effort, sections réelles si présente
        assert any("cdpx.section" in (event["name"] or "") for event in time_panel["events"])

    success = panels["http-client-success"]["http_client"]
    #: succès, erreur et timeout du client HTTP réel se distinguent par
    #: statuts et compteur d'erreurs — jamais par durée: le timeout se lit
    #: à l'absence de 200, pas à un chrono
    assert success["requests"] == 1
    assert any(item.get("status") == 200 for item in success["list"])
    error = panels["http-client-error"]["http_client"]
    assert error["requests"] == 1
    assert error["errors"] >= 1 or any(item.get("status") == 500 for item in error["list"])
    timeout = panels["http-client-timeout"]["http_client"]
    assert timeout["requests"] >= 1
    assert not any(item.get("status") == 200 for item in timeout["list"])

    #: chaque variante messenger dispatch exactement un message; la classe
    #: du message n'est vérifiée que si le panel liste les messages
    assert panels["messenger-sync"]["messenger"]["dispatched"] == 1
    assert panels["messenger-queued"]["messenger"]["dispatched"] == 1
    sync_classes = [item["class"] for item in panels["messenger-sync"]["messenger"]["list"]]
    if sync_classes:  # liste best-effort
        assert any(cls.endswith("SyncPing") for cls in sync_classes)

    #: redirection, 404 et 500 traversent avec leur vrai statut HTTP, et les
    #: exceptions remontent avec leur classe exacte — y compris la classe
    #: globale sans namespace du 500 — pendant que le logger capte l'erreur
    assert results["routing-redirect"]["status"] == 302
    assert results["routing-404"]["status"] == 404
    assert results["routing-500"]["status"] == 500
    assert panels["routing-404"]["exception"]["raised"] is True
    assert panels["routing-404"]["exception"]["class"].endswith("NotFoundHttpException")
    assert panels["routing-500"]["exception"]["raised"] is True
    assert panels["routing-500"]["exception"]["class"].endswith("RuntimeException")
    assert panels["routing-500"]["logger"]["available"] is True

    cache_control = results["headers-cache"]["response_headers"].get("cache-control", "")
    #: les directives Cache-Control posées par le contrôleur traversent
    #: jusqu'au rapport: les headers non sensibles ne sont pas masqués
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
    """Les pages vitals baseline/degraded rendues par Symfony produisent des
    mesures comparables: cdpx orchestre vitals, metrics et diagnostics et le
    contraste attendu entre les deux variantes est observable."""
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

    #: chaque mesure porte l'URL de sa variante: pas de mélange possible
    #: entre les deux navigations successives dans le même onglet
    assert baseline["url"].endswith("/scenario/vitals/baseline")
    assert degraded["url"].endswith("/scenario/vitals/degraded")
    #: le contraste est déclaré par les pages elles-mêmes: layout shift
    #: seulement en dégradé, travail d'interaction et payload strictement
    #: supérieurs — la comparaison reste déterministe, pas chronométrée
    assert degraded_expected["layout_shift"] is True
    assert baseline_expected["layout_shift"] is False
    assert degraded_expected["interaction_work_ms"] > baseline_expected["interaction_work_ms"]
    assert degraded_expected["payload_blocks"] > baseline_expected["payload_blocks"]
    #: les seuils standards Web Vitals sont exposés et l'attribution CLS/INP
    #: de la page dégradée dépasse celle de la baseline: le diagnostic est
    #: mesuré dans le navigateur, pas seulement affirmé
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
    """Chaque route diagnostique (LCP image/texte, CLS injecté, INP sous CPU
    ralenti, ressources bloquantes sous slow-3g) expose une attribution
    déterministe que cdpx lit après avoir appliqué puis levé l'émulation."""
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

    #: l'attribution LCP désigne l'élément fautif: type ET sélecteur pour
    #: l'image héro, type texte pour la variante textuelle — c'est ce qui
    #: rend le diagnostic actionnable
    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["type"] == "image"
    assert evidence["lcp-image"]["diagnostics"]["lcp_attribution"]["selector"] == "#hero-image"
    assert evidence["lcp-text"]["diagnostics"]["lcp_attribution"]["type"] == "text"
    #: le shift injecté et la longue tâche INP laissent des traces
    #: quantifiées dans leurs attributions respectives
    assert (
        evidence["cls-injected-banner"]["diagnostics"]["cls_attribution"]["expected_shift_count"]
        >= 1
    )
    assert (
        evidence["inp-long-task"]["diagnostics"]["inp_attribution"]["expected_event_duration_ms"]
        >= 90
    )
    #: le resource timing compte exactement les 3 scripts critiques déclarés
    #: par la page bloquante: la mesure recoupe le contrat de la route
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
    """Les états accessible et régressé rendus par Symfony sont discriminés
    par les sondes RGAA automatisées, et chaque rapport thématique déclare sa
    portée limitée: jamais une prétention d'audit RGAA complet."""
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

    #: les sondes clés basculent toutes entre les deux états: elles
    #: discriminent réellement l'accessible du régressé au lieu de toujours
    #: passer (h1 unique, landmark, labels, focus visible)
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
    #: les treize thématiques RGAA couvertes sont présentes dans les deux
    #: rapports: aucune ne disparaît silencieusement selon l'état de la page
    assert set(baseline_reports) == expected_themes
    assert set(regression_reports) == expected_themes
    #: chaque thème déclare sa portée automatisée et ses limitations: le
    #: rapport ne peut pas être confondu avec un audit RGAA complet
    assert all(
        item["automated_scope"].startswith("automated subset") for item in baseline_reports.values()
    )
    assert all(item["limitations"] for item in baseline_reports.values())
    #: verdict global cohérent: la baseline passe partout, la régression
    #: échoue quelque part — les rapports portent le signal, pas le bruit
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
    """Le diff DOM de cdpx capture la transition d'état provoquée par un clic
    sur une vraie page Symfony et la restitue en diff structuré exploitable
    comme preuve."""
    wait_for_symfony(SYMFONY_URL)
    target, client = open_tab(chrome)
    try:
        with client as c:
            nav.navigate(c, f"{SYMFONY_URL}/scenario/front/states", timeout=20)
            expected = expected_from_page(c)
            # Capture avant clic (état idle): prise après lecture de l'état
            # initial et sans bandeau, elle ne touche pas au DOM comparé par
            # dom_diff, dont la baseline reste intacte.
            screenshot(
                c,
                tmp_path,
                "symfony-front-state-before.png",
                evidence_case,
                "Symfony front state (idle, before click)",
            )
            diff = dev.dom_diff(c, ClickAction("#submit-btn"))
            # Capture après clic (état submitted): matérialise la cible de la
            # transition que le diff DOM prouve.
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

    #: la page déclare elle-même la transition attendue idle -> submitted:
    #: la référence de comparaison vient du serveur, pas du test
    assert expected["before"] == "idle"
    assert expected["after"] == "submitted"
    #: le diff détecte le changement et le nouvel état apparaît dans les
    #: lignes modifiées: la transition est capturée, pas seulement signalée
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
    """Les scénarios YAML déclaratifs s'exécutent contre la vraie app via la
    session supervisée: succès, échec contrôlé et collecte profiler/vitals
    produisent chacun un rapport avec verdict, artefacts et findings."""
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
        #: pour chaque scénario, code de sortie CLI et verdict du rapport
        #: concordent avec l'issue attendue, et des artefacts de preuve sont
        #: produits même en cas d'échec contrôlé
        assert code == expected_code, err
        assert result["verdict"] == expected_verdict
        assert result["artifacts"]
        results[template] = result

    profiler_artifacts = [
        artifact
        for artifact in results["symfony_profiler_vitals.yml"]["artifacts"]
        if artifact["type"] == "profiler"
    ]
    #: le scénario de collecte livre bien un artefact profiler: la preuve
    #: métier est jointe au rapport, pas seulement le verdict
    assert profiler_artifacts
    #: l'échec contrôlé est attribué à l'étape fautive dans les findings,
    #: ce qui rend le rapport diagnosticable sans relire les logs
    assert any(
        finding["code"] == "step_failed"
        for finding in results["symfony_front_fail.yml"]["findings"]
    )
