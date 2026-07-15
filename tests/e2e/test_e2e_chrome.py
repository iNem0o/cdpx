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
import tempfile
import time
from pathlib import Path

import pytest

from cdpx import discovery, proof
from cdpx.client import CDPClient
from cdpx.primitives import actions, advanced, audit, capture, dev, inputs, js, nav, net, state
from cdpx.session import SessionManifest, start_session, stop_session
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
def cli_page(managed_cli_session, fixtures_http):
    manifest, path = managed_cli_session
    yield manifest, path, fixtures_http.base_url


@pytest.fixture(scope="module")
def managed_cli_session(tmp_path_factory):
    runtime = tmp_path_factory.mktemp("cdpx-managed-e2e")
    manifest, path = start_session(
        run_id="e2e-cli",
        authority="privileged",
        origins="http://127.0.0.1:*",
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


def cli_json(session: tuple[SessionManifest, Path], *args: str) -> dict | list:
    manifest, path = session
    return successful_json(run_cli(manifest, path, *args))


def attach_cli_screenshot(
    evidence_case,
    session: tuple[SessionManifest, Path],
    label: str = "final",
) -> None:
    manifest, _ = session
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        attach_screenshot(evidence_case, client, label)


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["The installed CLI manages a real Chrome target lifecycle."],
)
def test_cli_browser_lifecycle_black_box(managed_cli_session, fixtures_http, evidence_case):
    manifest, path = managed_cli_session
    version_proc = run_cli(manifest, path, "version")
    version = successful_json(version_proc)
    assert version["Browser"].startswith(("Chrome/", "HeadlessChrome/", "Chromium/"))
    assert version["Protocol-Version"]

    navigated = cli_json(
        managed_cli_session,
        "goto",
        f"{fixtures_http.base_url}/index.html",
    )
    assert navigated["ok"] is True
    listed = cli_json(managed_cli_session, "tabs", "list")
    assert listed["count"] == 1
    assert listed["tabs"][0]["id"] == manifest.target_id
    attach_cli_screenshot(evidence_case, managed_cli_session, "assigned-target")


@pytest.mark.scenario(
    feature="dom-interaction",
    journey="submit-form",
    scenario_id="dom-interaction.submit-form-like-user",
    proves=["The installed CLI submits a real form through trusted keyboard input."],
)
def test_cli_dom_and_keyboard_black_box(cli_page, evidence_case, monkeypatch):
    manifest, path, base = cli_page
    session = (manifest, path)
    navigated = cli_json(session, "goto", f"{base}/form.html")
    assert navigated["ok"] is True and navigated["waited"] == "load"
    assert cli_json(session, "count", "input,button")["count"] == 3

    monkeypatch.setenv("E2E_FORM_TEXT", "Keyboard E2E")
    cli_json(session, "type", "#name", "--secret-env", "E2E_FORM_TEXT", "--clear")
    assert cli_json(session, "key", "Enter")["pressed"] == "Enter"
    html = cli_json(session, "html", "#result")
    assert 'data-state="submitted"' in html["html"]
    assert "OK:Keyboard E2E" in html["html"]
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="capture-page",
    scenario_id="browser-capture-observability.persist-screenshot-proof",
    proves=["The installed CLI writes valid JPEG and PDF artifacts."],
)
def test_cli_jpeg_and_pdf_artifacts_black_box(cli_page, tmp_path, evidence_case):
    manifest, path, base = cli_page
    session = (manifest, path)
    cli_json(session, "goto", f"{base}/long.html")
    jpeg = tmp_path / "black-box-full.jpg"
    pdf = tmp_path / "black-box.pdf"

    shot = cli_json(
        session,
        "screenshot",
        "-o",
        str(jpeg),
        "--format",
        "jpeg",
        "--full-page",
    )
    printed = cli_json(session, "pdf", "-o", str(pdf))
    jpeg_path = Path(shot["path"])
    pdf_path = Path(printed["path"])
    assert shot["format"] == "jpeg" and shot["full_page"] is True
    assert shot["bytes"] == jpeg_path.stat().st_size > 1000 and not jpeg.exists()
    assert jpeg_path.read_bytes().startswith(b"\xff\xd8\xff")
    assert printed["bytes"] == pdf_path.stat().st_size > 1000 and not pdf.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["Console follow streams bounded NDJSON from real Chrome."],
)
def test_cli_console_follow_is_bounded_ndjson(cli_page, evidence_case):
    manifest, path, base = cli_page
    session = (manifest, path)
    cli_json(session, "goto", f"{base}/console.html")
    proc = run_cli(manifest, path, "console", "--follow", "--max", "4")
    assert proc.returncode == 0 and proc.stderr == ""
    entries = [json.loads(line) for line in proc.stdout.splitlines()]
    assert len(entries) == 4
    assert {entry["kind"] for entry in entries} == {"console", "exception"}
    assert any("fixture-log" in entry["text"] for entry in entries)
    assert any("fixture-uncaught" in entry["text"] for entry in entries)
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="state-session",
    journey="prepare-session",
    scenario_id="state-session.prepare-repeatable-session",
    proves=["Cookie secrets stay masked and session storage is observable."],
)
def test_cli_cookie_masking_and_session_storage_black_box(cli_page, evidence_case, monkeypatch):
    manifest, path, base = cli_page
    session_context = (manifest, path)
    cli_json(session_context, "goto", f"{base}/storage.html")
    secret = "synthetic-e2e-cookie-value"
    monkeypatch.setenv("E2E_COOKIE_VALUE", secret)
    set_result = cli_json(
        session_context,
        "cookies",
        "set",
        "--name",
        "blackBoxCookie",
        "--value-env",
        "E2E_COOKIE_VALUE",
        "--url",
        f"{base}/",
    )
    assert set_result["success"] is True

    masked_proc = run_cli(manifest, path, "cookies", "get")
    masked = successful_json(masked_proc)
    assert masked["values_masked"] is True
    assert secret not in masked_proc.stdout
    assert all(cookie["value"] == "***" for cookie in masked["cookies"])

    shown_proc = run_cli(manifest, path, "cookies", "get", "--show-values")
    shown = successful_json(shown_proc)
    assert shown["values_masked"] is False
    assert any(
        cookie["name"] == "blackBoxCookie" and cookie["value"] == "***"
        for cookie in shown["cookies"]
    )
    assert secret not in shown_proc.stdout
    session_storage = cli_json(session_context, "storage", "--kind", "session")
    assert session_storage["entries"] == {"cdpx-session": "***"}
    assert session_storage["values_masked"] is True
    shown_session = cli_json(
        session_context,
        "storage",
        "--kind",
        "session",
        "--show-values",
    )
    assert shown_session["entries"] == {"cdpx-session": "oui"}
    assert cli_json(session_context, "cookies", "clear")["cleared"] is True
    assert cli_json(session_context, "cookies", "get")["count"] == 0
    attach_cli_screenshot(evidence_case, session_context)


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["Network and CPU presets wrap a real composed navigation."],
)
def test_cli_slow_3g_and_cpu_emulation_black_box(cli_page, evidence_case):
    manifest, path, base = cli_page
    session = (manifest, path)
    slow = cli_json(session, "emulate", "slow-3g", "--", "goto", f"{base}/index.html")
    assert slow["applied"] is True and slow["action"]["result"]["ok"] is True
    assert slow["action"]["result"]["elapsed_ms"] >= 200

    cpu = cli_json(session, "emulate", "cpu-4x", "--", "goto", f"{base}/index.html")
    assert cpu["applied"] is True and cpu["action"]["result"]["ok"] is True
    assert cpu["action"]["argv"][0] == "goto"
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["The CLI enforces stdout, stderr, and exit-code contracts end to end."],
)
def test_cli_stdout_stderr_and_exit_contract(cli_page, evidence_case):
    manifest, path, base = cli_page
    session = (manifest, path)
    success = run_cli(manifest, path, "goto", f"{base}/form.html")
    assert success.returncode == 0 and success.stderr == ""
    assert json.loads(success.stdout)["ok"] is True

    runtime_error = run_cli(manifest, path, "click", "#missing")
    assert runtime_error.returncode == 1 and runtime_error.stdout == ""
    assert "sélecteur introuvable" in runtime_error.stderr

    usage_error = run_cli(manifest, path, "goto")
    assert usage_error.returncode == 2 and usage_error.stdout == ""
    assert "the following arguments are required: url" in usage_error.stderr
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.publish-feature-proof",
    proves=["The offline Docs route renders the real session Mermaid diagrams."],
)
def test_proof_cockpit_renders_offline_docs_and_mermaid(page, tmp_path):
    client, _base = page
    summary = {
        "ok": True,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "git": {"branch": "e2e", "sha": "test"},
        "feature_inventory": {"features": [], "totals": {}},
        "documentation": proof.build_documentation_catalog(),
        "totals": {},
        "scenario_totals": {},
    }
    proof_dir = tmp_path / ".proof"
    report = proof_dir / "proof-report.html"
    proof._write_private_text(report, proof.render_html(summary))
    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        pre_redacted_paths={"proof-report.html"},
    )

    target = (
        staging / ".proof" / "proof-report.html"
    ).as_uri() + "#/docs/view/docs/SESSION-LIFECYCLE.md"
    assert nav.navigate(client, target)["ok"] is True
    nav.wait_for(client, ".panel.doc", timeout=20)
    runtime_state = js.evaluate(
        client,
        "({runtime: typeof window.mermaid, "
        "sources: document.querySelectorAll('pre.mermaid').length, title: document.title, "
        "app: document.querySelector('#app')?.textContent.slice(0, 80)})",
    )
    assert runtime_state["runtime"] == "object", runtime_state
    nav.wait_for(client, ".panel.doc .mermaid svg, .mermaid-error", timeout=20)
    mermaid_state = js.evaluate(
        client,
        "({"
        "svg: document.querySelectorAll('.panel.doc .mermaid svg').length,"
        "sources: document.querySelectorAll('.panel.doc pre.mermaid').length,"
        "errors: Array.from(document.querySelectorAll('.mermaid-error'), node => node.textContent),"
        "runtime: typeof window.mermaid"
        "})",
    )
    assert mermaid_state == {"svg": 4, "sources": 4, "errors": [], "runtime": "object"}
    assert js.evaluate(client, "document.querySelectorAll('script[src]').length") == 0
    assert js.evaluate(client, "performance.getEntriesByType('resource').length") == 0

    js.evaluate(
        client,
        "location.hash = '#/docs/view/docs/features/state-session.md'",
    )
    nav.wait_for(client, ".panel.doc #intention")
    assert "État et contrôles de session" in js.evaluate(
        client,
        "document.querySelector('#docsNav').innerText",
    )


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


def test_origin_guard_cli_real(managed_cli_session, fixtures_http, evidence_case):
    manifest, path = managed_cli_session
    cli_json(managed_cli_session, "goto", f"{fixtures_http.base_url}/index.html")
    proc = run_cli(manifest, path, "goto", "https://blocked.example/")
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        attach_screenshot(evidence_case, client, "origin-guard-final")
    assert proc.returncode == 1
    assert "origine refusée" in proc.stderr


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
            advanced.record(
                c,
                str(journal),
                ["goto", f"{base}/form.html"],
                origins="http://127.0.0.1:*",
            )
            advanced.record(
                c,
                str(journal),
                ["type", "#name", "@env:FORM_NAME", "--clear"],
                origins="http://127.0.0.1:*",
            )
            advanced.record(
                c,
                str(journal),
                ["click", "#submit-btn"],
                origins="http://127.0.0.1:*",
            )
            assert js.get_text(c, "#result")["text"] == "OK:Léo"  # record a bien AGI
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    # rejeu intégral sur un onglet vierge: le parcours se reconstruit seul
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            res = advanced.replay(c, str(journal), origins="http://127.0.0.1:*")
            assert res["ok"] is True and res["played"] == 3
            assert js.get_text(c, "#result")["text"] == "OK:Léo"
            attach_screenshot(evidence_case, c, "replay-final")
            # journal altéré (sélecteur disparu) -> divergence, arrêt net
            journal.write_text(
                journal.read_text().replace("#submit-btn", "#gone"), encoding="utf-8"
            )
            broken = advanced.replay(c, str(journal), origins="http://127.0.0.1:*")
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
    session: tuple[SessionManifest, Path],
    scenario: Path,
    *,
    timeout: float = 10.0,
) -> tuple[int, dict, str]:
    manifest, path = session
    proc = run_cli(
        manifest,
        path,
        "--timeout",
        str(timeout),
        "scenario",
        "run",
        str(scenario),
        "--settle",
        "0.5",
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
def test_declarative_scenario_static_form_real(
    managed_cli_session, fixtures_http, tmp_path, evidence_case, monkeypatch
):
    monkeypatch.setenv("E2E_FORM_NAME", "Leo")
    scenario = materialize_scenario("static_form_pass.yml", fixtures_http.base_url, tmp_path)
    code, result, err = run_scenario_cli(managed_cli_session, scenario)

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
    managed_cli_session, fixtures_http, tmp_path, evidence_case
):
    scenario = materialize_scenario(
        "static_observability_fail.yml", fixtures_http.base_url, tmp_path
    )
    code, result, _ = run_scenario_cli(managed_cli_session, scenario)

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
