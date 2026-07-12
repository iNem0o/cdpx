"""E2E Chrome réel.

Chrome/Chromium est une dépendance obligatoire du portail e2e: si aucun binaire
n'est disponible, la suite échoue au lieu de produire un faux succès par skip.
Les scénarios déroulent les mêmes fixtures que les tests mock, mais contre un
vrai navigateur + le serveur de fixtures.

Lancement visé:
  chromium --headless=new --remote-debugging-port=0 ... (géré ici)
  make test-e2e
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cdpx import discovery
from cdpx.client import CDPClient
from cdpx.primitives import actions, advanced, audit, capture, dev, inputs, js, nav, net, state
from cdpx.testing.e2e import (
    attach_screenshot,
    free_loopback_port,
    run_cli,
    stop_process,
    successful_json,
    wait_for_chrome,
)

SCENARIO_FIXTURES = Path(__file__).parents[1] / "fixtures" / "scenarios"

CHROME_BIN = next(
    (
        b
        for b in (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
            "chrome",
        )
        if shutil.which(b)
    ),
    None,
)

if CHROME_BIN is None:
    pytest.fail("Chrome/Chromium obligatoire pour les e2e cdpx", pytrace=False)


@pytest.fixture(scope="module")
def chrome():
    profile = tempfile.mkdtemp(prefix="cdpx-e2e-")
    port = free_loopback_port()
    log_path = Path(profile) / "chrome-stderr.log"
    stderr = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            CHROME_BIN,
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-sandbox",
            "--disable-gpu",
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr,
    )
    try:
        wait_for_chrome(proc, port, log_path)
        yield port
    finally:
        stop_process(proc)
        stderr.close()
        shutil.rmtree(profile, ignore_errors=True)


@pytest.fixture()
def page(chrome, fixtures_http, evidence_case):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    with CDPClient(target["webSocketDebuggerUrl"], timeout=15) as c:
        yield c, fixtures_http.base_url
        attach_screenshot(evidence_case, c, "final")
    discovery.close_tab("127.0.0.1", chrome, target["id"])


@pytest.fixture()
def cli_page(chrome, fixtures_http):
    target = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        yield target, fixtures_http.base_url
    finally:
        discovery.close_tab("127.0.0.1", chrome, target["id"])


def cli_json(chrome: int, target: dict, *args: str) -> dict | list:
    return successful_json(run_cli(chrome, *args, target=target["id"]))


def attach_cli_screenshot(evidence_case, chrome: int, target: dict, label: str = "final") -> None:
    assigned = discovery.pick_page("127.0.0.1", chrome, target["id"])
    with CDPClient(assigned["webSocketDebuggerUrl"], timeout=10) as client:
        attach_screenshot(evidence_case, client, label)


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["The installed CLI manages a real Chrome target lifecycle."],
)
def test_cli_browser_lifecycle_black_box(chrome, fixtures_http, evidence_case):
    version_proc = run_cli(chrome, "version")
    version = successful_json(version_proc)
    assert version["Browser"].startswith(("Chrome/", "HeadlessChrome/", "Chromium/"))
    assert version["Protocol-Version"]

    created = successful_json(
        run_cli(chrome, "tabs", "new", "--url", f"{fixtures_http.base_url}/index.html")
    )
    try:
        activated = successful_json(run_cli(chrome, "tabs", "activate", "--id", created["id"]))
        assert activated == {"activated": created["id"]}
        listed = successful_json(run_cli(chrome, "tabs", "list"))
        assert listed["count"] >= 1
        assert any(
            item["id"] == created["id"] and item["type"] == "page" for item in listed["tabs"]
        )
        attach_cli_screenshot(evidence_case, chrome, created, "active-tab")
    finally:
        closed = successful_json(run_cli(chrome, "tabs", "close", "--id", created["id"]))
    assert closed == {"closed": created["id"]}
    remaining = successful_json(run_cli(chrome, "tabs", "list"))
    assert all(item["id"] != created["id"] for item in remaining["tabs"])


@pytest.mark.scenario(
    feature="dom-interaction",
    journey="submit-form",
    scenario_id="dom-interaction.submit-form-like-user",
    proves=["The installed CLI submits a real form through trusted keyboard input."],
)
def test_cli_dom_and_keyboard_black_box(chrome, cli_page, evidence_case):
    target, base = cli_page
    navigated = cli_json(chrome, target, "goto", f"{base}/form.html")
    assert navigated["ok"] is True and navigated["waited"] == "load"
    assert cli_json(chrome, target, "count", "input,button")["count"] == 3

    cli_json(chrome, target, "type", "#name", "Keyboard E2E", "--clear")
    assert cli_json(chrome, target, "key", "Enter") == {"pressed": "Enter"}
    html = cli_json(chrome, target, "html", "#result")
    assert 'data-state="submitted"' in html["html"]
    assert "OK:Keyboard E2E" in html["html"]
    attach_cli_screenshot(evidence_case, chrome, target)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="capture-page",
    scenario_id="browser-capture-observability.persist-screenshot-proof",
    proves=["The installed CLI writes valid JPEG and PDF artifacts."],
)
def test_cli_jpeg_and_pdf_artifacts_black_box(chrome, cli_page, tmp_path, evidence_case):
    target, base = cli_page
    cli_json(chrome, target, "goto", f"{base}/long.html")
    jpeg = tmp_path / "black-box-full.jpg"
    pdf = tmp_path / "black-box.pdf"

    shot = cli_json(
        chrome,
        target,
        "screenshot",
        "-o",
        str(jpeg),
        "--format",
        "jpeg",
        "--full-page",
    )
    printed = cli_json(chrome, target, "pdf", "-o", str(pdf))
    assert shot["format"] == "jpeg" and shot["full_page"] is True
    assert shot["bytes"] == jpeg.stat().st_size > 1000
    assert jpeg.read_bytes().startswith(b"\xff\xd8\xff")
    assert printed["bytes"] == pdf.stat().st_size > 1000
    assert pdf.read_bytes().startswith(b"%PDF-")
    attach_cli_screenshot(evidence_case, chrome, target)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["Console follow streams bounded NDJSON from real Chrome."],
)
def test_cli_console_follow_is_bounded_ndjson(chrome, cli_page, evidence_case):
    target, base = cli_page
    cli_json(chrome, target, "goto", f"{base}/console.html")
    proc = run_cli(chrome, "console", "--follow", "--max", "4", target=target["id"])
    assert proc.returncode == 0 and proc.stderr == ""
    entries = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(entries) == 4
    assert {entry["kind"] for entry in entries} == {"console", "exception"}
    assert any("fixture-log" in entry["text"] for entry in entries)
    assert any("fixture-uncaught" in entry["text"] for entry in entries)
    attach_cli_screenshot(evidence_case, chrome, target)


@pytest.mark.scenario(
    feature="state-session",
    journey="prepare-session",
    scenario_id="state-session.prepare-repeatable-session",
    proves=["Cookie secrets stay masked and session storage is observable."],
)
def test_cli_cookie_masking_and_session_storage_black_box(chrome, cli_page, evidence_case):
    target, base = cli_page
    cli_json(chrome, target, "goto", f"{base}/storage.html")
    secret = "synthetic-e2e-cookie-value"
    set_result = cli_json(
        chrome,
        target,
        "cookies",
        "set",
        "--name",
        "blackBoxCookie",
        "--value",
        secret,
        "--url",
        f"{base}/",
    )
    assert set_result["success"] is True

    masked_proc = run_cli(chrome, "cookies", "get", target=target["id"])
    masked = successful_json(masked_proc)
    assert masked["values_masked"] is True
    assert secret not in masked_proc.stdout
    assert all(cookie["value"] == "***" for cookie in masked["cookies"])

    shown = cli_json(chrome, target, "cookies", "get", "--show-values")
    assert any(
        cookie["name"] == "blackBoxCookie" and cookie["value"] == secret
        for cookie in shown["cookies"]
    )
    session = cli_json(chrome, target, "storage", "--kind", "session")
    assert session["entries"] == {"cdpx-session": "***"}
    assert session["values_masked"] is True
    shown_session = cli_json(
        chrome,
        target,
        "storage",
        "--kind",
        "session",
        "--show-values",
    )
    assert shown_session["entries"] == {"cdpx-session": "oui"}
    assert cli_json(chrome, target, "cookies", "clear")["cleared"] is True
    assert cli_json(chrome, target, "cookies", "get")["count"] == 0
    attach_cli_screenshot(evidence_case, chrome, target)


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["Network and CPU presets wrap a real composed navigation."],
)
def test_cli_slow_3g_and_cpu_emulation_black_box(chrome, cli_page, evidence_case):
    target, base = cli_page
    slow = cli_json(chrome, target, "emulate", "slow-3g", "--", "goto", f"{base}/index.html")
    assert slow["applied"] is True and slow["action"]["result"]["ok"] is True
    assert slow["action"]["result"]["elapsed_ms"] >= 200

    cpu = cli_json(chrome, target, "emulate", "cpu-4x", "--", "goto", f"{base}/index.html")
    assert cpu["applied"] is True and cpu["action"]["result"]["ok"] is True
    assert cpu["action"]["argv"][0] == "goto"
    attach_cli_screenshot(evidence_case, chrome, target)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["The CLI enforces stdout, stderr, and exit-code contracts end to end."],
)
def test_cli_stdout_stderr_and_exit_contract(chrome, cli_page, evidence_case):
    target, base = cli_page
    success = run_cli(chrome, "goto", f"{base}/form.html", target=target["id"])
    assert success.returncode == 0 and success.stderr == ""
    assert json.loads(success.stdout)["ok"] is True

    runtime_error = run_cli(chrome, "click", "#missing", target=target["id"])
    assert runtime_error.returncode == 1 and runtime_error.stdout == ""
    assert "sélecteur introuvable" in runtime_error.stderr

    usage_error = run_cli(chrome, "goto")
    assert usage_error.returncode == 2 and usage_error.stdout == ""
    assert "the following arguments are required: url" in usage_error.stderr
    attach_cli_screenshot(evidence_case, chrome, target)


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


def test_rich_interactions_enforce_hit_test_and_clear_with_input_events(page):
    c, base = page
    nav.navigate(c, f"{base}/interactions-rich.html")

    for selector, reason in (
        ("#hidden-button", "non visible"),
        ("#disabled-button", "désactivé"),
        ("#aria-disabled-button", "désactivé"),
        ("#inert-button", "désactivé"),
        ("#pointer-events-button", "désactivé"),
        ("#covered-button", "recouvert"),
    ):
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.click(c, selector)

    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    assert snapshot["clicks"] == {
        "hidden": 0,
        "disabled": 0,
        "ariaDisabled": 0,
        "inert": 0,
        "pointerEvents": 0,
        "covered": 0,
        "descendant": 0,
    }

    inputs.click(c, "#descendant-button")
    assert js.evaluate(c, "window.interactionFixture.snapshot().clicks.descendant") == 1

    for selector, reason in (
        ("#hidden-button", "non visible"),
        ("#disabled-button", "désactivé"),
        ("#descendant-button", "non éditable"),
    ):
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.type_text(c, selector, "must-not-be-typed")

    type_result = inputs.type_text(c, "#controlled-input", "fresh", clear=True)
    assert type_result["typed"] is True
    assert type_result["value_masked"] is True
    assert "fresh" not in json.dumps(type_result, ensure_ascii=False)
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    assert snapshot["input"] == "fresh"
    assert snapshot["mirror"] == "fresh"
    assert any(
        event["type"] == "beforeinput" and event["value"] == "legacy"
        for event in snapshot["inputEvents"]
    )
    assert any(
        event["type"] == "input" and event["value"] == "" for event in snapshot["inputEvents"]
    )

    for key in ("Home", "Delete", "End", "Space"):
        assert inputs.press_key(c, key) == {"pressed": key}
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    assert snapshot["input"] == "resh "
    assert snapshot["mirror"] == "resh "


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
    # fetch page-context réel: Chrome va chercher les panels HTML du fixture
    # server et les parseurs en extraient les valeurs figées.
    c, base = page
    res = dev.profiler(c, f"{base}/api/profiler-sim")
    assert res["token_present"] is True
    assert "token" not in res and "fixed-token" not in json.dumps(res)
    assert res["profiler_status"] == 200
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    assert res["panels"]["cache"]["hits"] == 3
    assert res["panels"]["router"]["route"] == "scenario_profiler"
    assert res["panels"]["exception"]["raised"] is False
    assert res["panels"]["logger"]["deprecations"] == 2


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


def test_origin_guard_cli_real(chrome, fixtures_http, evidence_case):
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
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=10) as c:
            attach_screenshot(evidence_case, c, "origin-guard-final")
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    assert proc.returncode == 1
    assert "mutation refusée" in proc.stderr


def test_metrics_real(page):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    res = audit.metrics(c)
    assert res["Nodes"] > 0 and res["Documents"] > 0
    assert res["JSHeapUsedSize"] > 0


def test_pdf_real(page, tmp_path):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    dest = tmp_path / "page.pdf"
    res = capture.pdf(c, str(dest))
    assert res["bytes"] > 1000
    assert dest.read_bytes().startswith(b"%PDF-")


def test_record_replay_real(chrome, fixtures_http, evidence_case, tmp_path, monkeypatch):
    journal = tmp_path / "session.ndjson"
    base = fixtures_http.base_url
    monkeypatch.setenv("FORM_NAME", "Léo")
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            advanced.record(c, str(journal), ["goto", f"{base}/form.html"])
            advanced.record(c, str(journal), ["type", "#name", "@env:FORM_NAME", "--clear"])
            advanced.record(c, str(journal), ["click", "#submit-btn"])
            assert js.get_text(c, "#result")["text"] == "OK:Léo"  # record a bien AGI
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    # rejeu intégral sur un onglet vierge: le parcours se reconstruit seul
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            res = advanced.replay(c, str(journal))
            assert res["ok"] is True and res["played"] == 3
            assert js.get_text(c, "#result")["text"] == "OK:Léo"
            attach_screenshot(evidence_case, c, "replay-final")
            # journal altéré (sélecteur disparu) -> divergence, arrêt net
            journal.write_text(
                journal.read_text().replace("#submit-btn", "#gone"), encoding="utf-8"
            )
            broken = advanced.replay(c, str(journal))
            assert broken["ok"] is False and broken["played"] == 2
            assert broken["divergence"].startswith("event 2:")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_composed_action_real(chrome, fixtures_http, evidence_case):
    # Agir sous émulation = action dans la MÊME connexion (les overrides
    # meurent avec elle): la page voit le device mobile pendant le goto.
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            advanced.emulate(c, "mobile")
            result = actions.run_action(c, ["goto", f"{fixtures_http.base_url}/index.html"])
            assert result["ok"] is True
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            attach_screenshot(evidence_case, c, "mobile-final")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_mobile_and_reset_real(chrome, fixtures_http, evidence_case):
    # Sémantique prouvée contre Chrome réel:
    # 1. intra-connexion, `--reset` restaure device ET user-agent (bug
    #    historique: l'UA du preset mobile survivait au reset);
    # 2. les overrides d'émulation meurent avec la connexion CDP — une
    #    invocation cdpx isolée ne laisse donc PAS la page émulée derrière
    #    elle (d'où la forme composée `emulate <preset> -- <action>`).
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            nav.navigate(c, f"{fixtures_http.base_url}/index.html")
            initial = js.evaluate(c, "screen.width")
            advanced.emulate(c, "mobile")
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            advanced.emulate(c, reset=True)
            assert js.evaluate(c, "screen.width") == initial
            assert "cdpx-mobile" not in js.evaluate(c, "navigator.userAgent")
            advanced.emulate(c, "mobile")  # re-pose l'override, la connexion se ferme
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            assert js.evaluate(c, "screen.width") == initial  # mort avec la connexion
            attach_screenshot(evidence_case, c, "final")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


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
    timeout: float = 10.0,
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
        timeout=max(timeout * 8, 20),
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


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=[
        "A YAML scenario drives a real browser through form navigation and interaction.",
        "Checkpoint and final artifacts are collected as proof files.",
    ],
)
def test_declarative_scenario_static_form_real(chrome, fixtures_http, tmp_path, evidence_case):
    scenario = materialize_scenario("static_form_pass.yml", fixtures_http.base_url, tmp_path)
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        code, result, err = run_scenario_cli(chrome, tab, scenario, tmp_path / "evidence")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])

    attach_scenario_run(evidence_case, result, "static-form-scenario")
    assert code == 0, f"stderr={err}\nresult={json.dumps(result, ensure_ascii=False, indent=2)}"
    assert result["verdict"] == "pass"
    assert any(artifact["label"] == "form_page" for artifact in result["artifacts"])
    assert any(artifact["label"] == "final" for artifact in result["artifacts"])


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=[
        "A YAML scenario returns one fail verdict when console and network assertions fail.",
        "Failure evidence still includes checkpoint and final artifacts.",
    ],
)
def test_declarative_scenario_static_observability_fail_real(
    chrome, fixtures_http, tmp_path, evidence_case
):
    scenario = materialize_scenario(
        "static_observability_fail.yml", fixtures_http.base_url, tmp_path
    )
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        code, result, _ = run_scenario_cli(chrome, tab, scenario, tmp_path / "evidence")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])

    attach_scenario_run(evidence_case, result, "static-observability-scenario")
    assert code == 1
    assert result["verdict"] == "fail"
    assert {finding["code"] for finding in result["findings"]} >= {
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    }


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
    assert storage["entries"].get("cdpx-key") == "***"
    assert storage["values_masked"] is True
    shown = state.get_storage(c, "local", show_values=True)
    assert shown["entries"].get("cdpx-key") == "cdpx-value"


def test_screenshot_real(page, tmp_path, evidence_case):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    out = tmp_path / "e2e.png"
    res = capture.screenshot(c, str(out))
    if evidence_case is not None:
        evidence_case.attach_screenshot(out, "screenshot-command")
    assert res["bytes"] > 1000
    assert out.read_bytes().startswith(b"\x89PNG")


def test_full_page_screenshot_captures_long_page(page, tmp_path, evidence_case):
    c, base = page
    nav.navigate(c, f"{base}/long.html")
    normal = tmp_path / "normal.png"
    full = tmp_path / "full.png"
    normal_res = capture.screenshot(c, str(normal))
    full_res = capture.screenshot(c, str(full), full_page=True)
    if evidence_case is not None:
        evidence_case.attach_screenshot(normal, "normal-screenshot")
        evidence_case.attach_screenshot(full, "full-page-screenshot")
    assert full_res["full_page"] is True
    assert full_res["bytes"] > normal_res["bytes"]
    assert full.read_bytes().startswith(b"\x89PNG")


def test_json_endpoint_reachable_from_page(page):
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    raw = js.evaluate(c, f"fetch('{base}/api/json').then(r => r.text())", await_promise=True)
    assert json.loads(raw)["ok"] is True
