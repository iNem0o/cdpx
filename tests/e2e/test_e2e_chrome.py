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
    attach_cli_run,
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
    """Le CLI installé pilote un vrai cycle de vie Chrome de bout en bout:
    identité du navigateur, navigation, puis inventaire des onglets sur la
    cible supervisée — sans jamais toucher un endpoint brut."""
    manifest, path = managed_cli_session
    version_proc = run_cli(manifest, path, "version")
    attach_cli_run(evidence_case, "cdpx version", version_proc)
    version = successful_json(version_proc)
    #: le navigateur répond avec une identité Chrome/Chromium réelle
    assert version["Browser"].startswith(("Chrome/", "HeadlessChrome/", "Chromium/"))
    assert version["Protocol-Version"]

    navigated = cli_json(
        managed_cli_session,
        "goto",
        f"{fixtures_http.base_url}/index.html",
    )
    #: la navigation aboutit sur le site témoin loopback
    assert navigated["ok"] is True
    listed = cli_json(managed_cli_session, "tabs", "list")
    #: la session supervisée ne voit que sa propre cible
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
    """Un formulaire réel est soumis par une frappe clavier de confiance
    (trusted input), la valeur secrète passant par l'environnement sans
    jamais apparaître dans argv."""
    manifest, path, base = cli_page
    session = (manifest, path)
    navigated = cli_json(session, "goto", f"{base}/form.html")
    #: la page formulaire est chargée jusqu'à l'évènement load
    assert navigated["ok"] is True and navigated["waited"] == "load"
    assert cli_json(session, "count", "input,button")["count"] == 3

    monkeypatch.setenv("E2E_FORM_TEXT", "Keyboard E2E")
    cli_json(session, "type", "#name", "--secret-env", "E2E_FORM_TEXT", "--clear")
    #: la touche Entrée déclenche la soumission comme le ferait un humain
    assert cli_json(session, "key", "Enter")["pressed"] == "Enter"
    html = cli_json(session, "html", "#result")
    #: le DOM final prouve la soumission avec la valeur tapée au clavier
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
    """Les captures JPEG pleine page et PDF du CLI produisent de vrais fichiers
    du format annoncé, confinés dans le dossier d'artefacts de la session
    supervisée plutôt qu'au chemin brut demandé."""
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
    #: la taille annoncée correspond au fichier réel signé JPEG, et le chemin
    #: brut passé en -o n'est jamais écrit: l'artefact est relogé sous la session
    assert shot["format"] == "jpeg" and shot["full_page"] is True
    assert shot["bytes"] == jpeg_path.stat().st_size > 1000 and not jpeg.exists()
    assert jpeg_path.read_bytes().startswith(b"\xff\xd8\xff")
    #: même contrat pour le PDF: fichier non trivial, signature %PDF-, confinement session
    assert printed["bytes"] == pdf_path.stat().st_size > 1000 and not pdf.exists()
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    if evidence_case is not None:
        # Binaires: JPEG relogé en type screenshot, PDF en type file — tous deux
        # opaque-restricted (non inlinés). Le JSON dérivé rend lisibles taille,
        # format et signatures observées sans exposer le contenu binaire.
        evidence_case.attach_file(jpeg_path, "jpeg-full-page", "screenshot")
        evidence_case.attach_file(pdf_path, "pdf-print")
        evidence_case.attach_json(
            "artefacts-binaires-observes",
            {
                "jpeg": {
                    "format": shot["format"],
                    "full_page": shot["full_page"],
                    "bytes": jpeg_path.stat().st_size,
                    "signature": jpeg_path.read_bytes()[:3].hex(),
                },
                "pdf": {
                    "bytes": pdf_path.stat().st_size,
                    "signature": pdf_path.read_bytes()[:5].decode("ascii"),
                },
            },
        )
    attach_cli_screenshot(evidence_case, session)


@pytest.mark.scenario(
    feature="browser-capture-observability",
    journey="inspect-runtime",
    scenario_id="browser-capture-observability.inspect-runtime-failures",
    proves=["Console follow streams bounded NDJSON from real Chrome."],
)
def test_cli_console_follow_is_bounded_ndjson(cli_page, evidence_case):
    """`console --follow --max N` s'arrête seul après exactement N évènements
    NDJSON issus d'un vrai Chrome, en mêlant logs console et exceptions non
    rattrapées."""
    manifest, path, base = cli_page
    session = (manifest, path)
    cli_json(session, "goto", f"{base}/console.html")
    proc = run_cli(manifest, path, "console", "--follow", "--max", "4")
    attach_cli_run(evidence_case, "console-follow-max-4", proc)
    #: le suivi borné se termine proprement, sans diagnostic parasite
    assert proc.returncode == 0 and proc.stderr == ""
    entries = [json.loads(line) for line in proc.stdout.splitlines()]
    #: chaque ligne du flux est un objet JSON autonome et la borne --max est exacte
    assert len(entries) == 4
    #: le flux mêle les deux familles d'évènements, avec les messages plantés par la fixture
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
    """Le masquage des valeurs sensibles est le défaut de bout en bout: cookies
    et sessionStorage sortent masqués, et un cookie posé via l'environnement
    reste masqué même sous --show-values."""
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
    #: Chrome accepte le cookie dont la valeur secrète n'a jamais transité par argv
    assert set_result["success"] is True

    masked_proc = run_cli(manifest, path, "cookies", "get")
    masked = successful_json(masked_proc)
    #: par défaut aucune valeur ne sort: masquage annoncé, secret absent du flux brut
    assert masked["values_masked"] is True
    assert secret not in masked_proc.stdout
    assert all(cookie["value"] == "***" for cookie in masked["cookies"])

    shown_proc = run_cli(manifest, path, "cookies", "get", "--show-values")
    shown = successful_json(shown_proc)
    #: même le démasquage explicite ne révèle pas un secret venu de l'environnement
    assert shown["values_masked"] is False
    assert any(
        cookie["name"] == "blackBoxCookie" and cookie["value"] == "***"
        for cookie in shown["cookies"]
    )
    assert secret not in shown_proc.stdout
    if evidence_case is not None:
        # Le run masqué est sûr (toutes valeurs "***"): transcript joint intégral.
        # Le run --show-values, lui, révèle des valeurs de cookies en clair
        # (valeurs de fixture, non des secrets d'environnement) — on n'attache
        # donc PAS son transcript brut. Le JSON dérivé prouve le contraste
        # masquage-par-défaut vs --show-values sans exposer aucune valeur.
        attach_cli_run(evidence_case, "cookies-get-masque", masked_proc)
        evidence_case.attach_json(
            "cookies-masquage-contraste",
            {
                "masked_run": {
                    "values_masked": masked["values_masked"],
                    "distinct_values": sorted({cookie["value"] for cookie in masked["cookies"]}),
                },
                "shown_run": {
                    "values_masked": shown["values_masked"],
                    "env_cookie_stays_masked": any(
                        cookie["name"] == "blackBoxCookie" and cookie["value"] == "***"
                        for cookie in shown["cookies"]
                    ),
                    "secret_absent_from_stdout": secret not in shown_proc.stdout,
                },
            },
        )
    session_storage = cli_json(session_context, "storage", "--kind", "session")
    #: le sessionStorage applique la même politique de masquage par défaut
    assert session_storage["entries"] == {"cdpx-session": "***"}
    assert session_storage["values_masked"] is True
    shown_session = cli_json(
        session_context,
        "storage",
        "--kind",
        "session",
        "--show-values",
    )
    #: --show-values restitue la valeur anodine réellement posée par la page
    assert shown_session["entries"] == {"cdpx-session": "oui"}
    #: le nettoyage ramène le contexte cookies à un état vierge vérifiable
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
    """La forme composée `emulate <preset> -- goto` applique réellement les
    presets réseau et CPU autour d'une navigation, et restitue fidèlement le
    résultat de l'action déléguée."""
    manifest, path, base = cli_page
    session = (manifest, path)
    slow = cli_json(session, "emulate", "slow-3g", "--", "goto", f"{base}/index.html")
    #: le preset slow-3g est posé et la latence mesurée trahit un vrai ralentissement réseau
    assert slow["applied"] is True and slow["action"]["result"]["ok"] is True
    assert slow["action"]["result"]["elapsed_ms"] >= 200

    cpu = cli_json(session, "emulate", "cpu-4x", "--", "goto", f"{base}/index.html")
    #: le preset CPU s'applique aussi et le rapport identifie l'action composée exécutée
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
    """Le contrat CLI (stdout = un objet JSON, stderr = diagnostics, exit
    0/1/2) tient contre un vrai Chrome pour le succès, l'erreur d'exécution
    et l'erreur d'usage."""
    manifest, path, base = cli_page
    session = (manifest, path)
    success = run_cli(manifest, path, "goto", f"{base}/form.html")
    attach_cli_run(evidence_case, "exit-0-success", success)
    #: la réussite n'emprunte que stdout, avec un unique objet JSON parsable
    assert success.returncode == 0 and success.stderr == ""
    assert json.loads(success.stdout)["ok"] is True

    runtime_error = run_cli(manifest, path, "click", "#missing")
    attach_cli_run(evidence_case, "exit-1-runtime-error", runtime_error)
    #: un sélecteur introuvable est une erreur d'exécution: code 1, diagnostic hors de stdout
    assert runtime_error.returncode == 1 and runtime_error.stdout == ""
    assert "sélecteur introuvable" in runtime_error.stderr

    usage_error = run_cli(manifest, path, "goto")
    attach_cli_run(evidence_case, "exit-2-usage-error", usage_error)
    #: une invocation malformée se distingue par le code 2 réservé aux erreurs d'usage
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
    """La preuve partageable rend sa route Docs entièrement hors ligne dans un
    vrai Chrome: les diagrammes Mermaid du cycle de session deviennent des SVG
    sans script externe ni requête réseau."""
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
    #: la version partageable s'ouvre en file:// sans aucun serveur
    assert nav.navigate(client, target)["ok"] is True
    nav.wait_for(client, ".panel.doc", timeout=20)
    runtime_state = js.evaluate(
        client,
        "({runtime: typeof window.mermaid, "
        "sources: document.querySelectorAll('pre.mermaid').length, title: document.title, "
        "app: document.querySelector('#app')?.textContent.slice(0, 80)})",
    )
    #: le runtime Mermaid est embarqué dans la page elle-même, pas chargé d'un CDN
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
    #: les quatre diagrammes source sont tous rendus en SVG sans erreur de parsing
    assert mermaid_state == {"svg": 4, "sources": 4, "errors": [], "runtime": "object"}
    #: hermétisme prouvé: aucun script externe déclaré, aucune ressource réseau chargée
    assert js.evaluate(client, "document.querySelectorAll('script[src]').length") == 0
    assert js.evaluate(client, "performance.getEntriesByType('resource').length") == 0

    js.evaluate(
        client,
        "location.hash = '#/docs/view/docs/features/state-session.md'",
    )
    nav.wait_for(client, ".panel.doc #intention")
    #: la navigation hash vers une fiche feature fonctionne et le menu offline la référence
    assert "État et contrôles de session" in js.evaluate(
        client,
        "document.querySelector('#docsNav').innerText",
    )


def test_navigate_and_read_title(page):
    """Une navigation CDP directe charge le site témoin et le contexte JS de
    la page reflète le document réellement chargé."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    #: le titre lu via Runtime prouve que la bonne page est chargée et exécutable
    assert js.evaluate(c, "document.title") == "cdpx fixtures — accueil"


def test_wait_for_late_spa_content(page):
    """wait_for attend réellement un contenu injecté tardivement par une SPA
    au lieu de conclure au premier passage."""
    c, base = page
    nav.navigate(c, f"{base}/spa.html")
    res = nav.wait_for(c, "#late-content", timeout=5)
    #: l'élément est trouvé et le délai mesuré prouve une vraie attente
    #: (la fixture n'injecte le contenu qu'après ~250 ms)
    assert res["found"] and res["elapsed_ms"] >= 250


def test_form_click_and_type(page):
    """La saisie puis le clic synthétiques déclenchent la vraie logique du
    formulaire: le DOM final contient la valeur soumise."""
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    inputs.click(c, "#submit-btn")
    #: le handler de soumission a vu la valeur tapée — toute la chaîne
    #: frappe/clic/JS a réellement fonctionné
    assert js.get_text(c, "#result")["text"] == "OK:Léo"


def test_rich_interactions_enforce_hit_test_and_clear_with_input_events(page):
    """Le hit-test refuse clics et saisies sur tout élément non actionnable
    (caché, désactivé, inerte, recouvert...), et --clear vide le champ via de
    vrais évènements input que les frameworks contrôlés peuvent observer."""
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
        #: chaque cause de non-actionnabilité est refusée avec sa raison précise, avant le clic
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.click(c, selector)

    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: la page confirme qu'aucun des clics refusés n'a fui jusqu'aux handlers
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
    #: un point de clic qui tombe sur un descendant du sélecteur reste un clic légitime
    assert js.evaluate(c, "window.interactionFixture.snapshot().clicks.descendant") == 1

    for selector, reason in (
        ("#hidden-button", "non visible"),
        ("#disabled-button", "désactivé"),
        ("#descendant-button", "non éditable"),
    ):
        #: la saisie applique les mêmes gardes, plus le refus des éléments non éditables
        with pytest.raises(inputs.ElementNotInteractable, match=reason):
            inputs.type_text(c, selector, "must-not-be-typed")

    type_result = inputs.type_text(c, "#controlled-input", "fresh", clear=True)
    #: la frappe réussit et la valeur tapée ne fuit pas dans la sortie JSON
    assert type_result["typed"] is True
    assert type_result["value_masked"] is True
    assert "fresh" not in json.dumps(type_result, ensure_ascii=False)
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: le champ et son miroir contrôlé voient la nouvelle valeur, et --clear a
    #: émis les beforeinput/input attendus au lieu d'écraser silencieusement
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
        #: chaque touche spéciale est transmise et acquittée par le protocole
        assert inputs.press_key(c, key) == {"pressed": key}
    snapshot = js.evaluate(c, "window.interactionFixture.snapshot()")
    #: le contenu final prouve que les touches ont déplacé un vrai curseur et édité le champ
    assert snapshot["input"] == "resh "
    assert snapshot["mirror"] == "resh "


def test_console_capture_real(page):
    """La capture console fenêtrée observe les logs ET les exceptions émis
    par une vraie page, avec un comptage d'erreurs agrégé."""
    c, base = page
    c.send("Runtime.enable")
    nav.navigate(c, f"{base}/console.html")
    res = capture.console_capture(c, duration=1.0)
    texts = [e["text"] for e in res["entries"]]
    #: le log planté par la fixture est capté et l'exception non rattrapée compte comme erreur
    assert any("fixture-log" in t for t in texts)
    assert res["errors"] >= 1


def test_network_capture_real(page):
    """La capture réseau observe le trafic réel d'une page: les échecs HTTP
    sont agrégés et chaque requête est restituée individuellement."""
    c, base = page
    res = net.capture(c, f"{base}/network.html", settle=1.0)
    #: l'appel volontairement en 500 de la fixture est compté comme échec
    assert res["summary"]["errors_4xx_5xx"] >= 1  # /api/status/500
    urls = [r.get("url", "") for r in res["requests"]]
    #: le détail par requête permet de retrouver les appels API individuels
    assert any("/api/json" in u for u in urls)


def test_profiler_fixture_real(page):
    """Le lecteur de profiler Symfony extrait les métriques des panels via un
    fetch page-context réel, sans jamais laisser fuiter le token du profiler
    dans la sortie."""
    # fetch page-context réel: Chrome va chercher les panels HTML du fixture
    # server et les parseurs en extraient les valeurs figées.
    c, base = page
    res = dev.profiler(c, f"{base}/api/profiler-sim")
    #: la présence du token est signalée mais sa valeur n'apparaît nulle part
    assert res["token_present"] is True
    assert "token" not in res and "fixed-token" not in json.dumps(res)
    #: chaque panel (db, cache, router, exception, logger) est parsé avec
    #: les valeurs figées servies par la fixture
    assert res["profiler_status"] == 200
    assert res["panels"]["db"]["queries"] == 6
    assert res["panels"]["db"]["duplicates"] == 4
    assert res["panels"]["cache"]["hits"] == 3
    assert res["panels"]["router"]["route"] == "scenario_profiler"
    assert res["panels"]["exception"]["raised"] is False
    assert res["panels"]["logger"]["deprecations"] == 2


def test_dom_diff_real(page):
    """dom-diff exécute l'action encadrée et rend lisible le changement de
    DOM qu'elle provoque."""
    c, base = page
    nav.navigate(c, f"{base}/form.html")
    inputs.type_text(c, "#name", "Léo")
    res = dev.dom_diff(c, ["click", "#submit-btn"])
    #: le diff matérialise la mutation provoquée par le clic (passage à l'état soumis)
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])


def test_a11y_and_frame_real(page):
    """L'arbre d'accessibilité d'une vraie page est exploitable et frame_text
    atteint le contenu à l'intérieur d'une iframe enfant."""
    c, base = page
    nav.navigate(c, f"{base}/iframe.html")
    tree = advanced.a11y(c)
    #: Chrome expose un arbre a11y non vide pour la page hôte
    assert tree["count"] > 0
    #: le texte lu vient bien du document enfant, pas de la page hôte
    assert advanced.frame_text(c, "#child-marker")["text"] == "Contenu de l'iframe"


def test_coverage_real(page):
    """La couverture CSS mesurée sur une vraie page est cohérente: règles
    utilisées et inutilisées se répartissent exactement le total."""
    c, base = page
    res = advanced.coverage(c, f"{base}/coverage.html")
    #: la fixture expose au moins une feuille dont des règles sont réellement exercées
    assert res["count"] >= 1
    assert res["css"]["rules"] >= 1
    assert res["css"]["used"] >= 1
    #: la partition utilisé/inutilisé est exacte — aucune règle perdue ni comptée deux fois
    assert res["css"]["used"] + res["css"]["unused"] == res["css"]["rules"]


def test_intercept_real_fulfill_block_continue(page):
    """L'interception réseau applique les trois verdicts (réécriture en 204,
    blocage, laisser-passer) sur un trafic réel, et la page observe exactement
    les réponses altérées."""
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
    #: les trois verdicts d'interception ont tous été exercés pendant la navigation
    assert {"204", "block", "continue"}.issubset(actions)
    deadline = time.monotonic() + 3
    text = ""
    while time.monotonic() < deadline:
        text = js.get_text(c, "#intercept-result")["text"] or ""
        if "pending" not in text:
            break
        time.sleep(0.1)
    #: la page elle-même a vu la réponse laissée passer intacte, la réponse
    #: réécrite en 204 et l'appel bloqué terminé en erreur
    assert "/api/json:200" in text
    assert "/api/status/500:204" in text
    assert "/api/slow?ms=120:ERR" in text


def test_vitals_real_with_interaction(page):
    """Les Web Vitals (LCP, CLS, INP) sont mesurés sur une vraie page, l'INP
    étant provoqué par un clic synthétique dont la page garde la trace."""
    c, base = page
    res = advanced.vitals(c, f"{base}/vitals.html", click_selector="#inp-button", settle=1.0)
    #: les trois métriques sont toutes présentes et plausibles (jamais négatives)
    assert set(res) == {"url", "lcp", "cls", "inp"}
    assert res["lcp"] >= 0 and res["cls"] >= 0 and res["inp"] >= 0
    #: l'interaction qui alimente l'INP a réellement atteint la page
    assert js.evaluate(c, "document.body.dataset.clicked") == "1"


def test_seo_edge_real(page):
    """L'audit SEO détecte les cas limites: estimation en pixels du titre,
    h1 dupliqués, JSON-LD invalide et Product incomplet."""
    c, base = page
    nav.navigate(c, f"{base}/seo-edge.html")
    res = audit.seo(c)
    #: la largeur du titre est estimée en pixels, au-delà du simple comptage de caractères
    assert res["title_px_estimate"] > 0
    #: chaque piège posé par la fixture ressort comme finding explicite et actionnable
    assert "h1 dupliqué: produit dupliqué" in res["findings"]
    assert "JSON-LD invalide" in res["findings"]
    assert "Product JSON-LD incomplet (sku ou name requis)" in res["findings"]


def test_origin_guard_cli_real(managed_cli_session, fixtures_http, evidence_case):
    """La garde d'origines de la session supervisée bloque toute navigation
    hors des origines autorisées, via le canal d'erreur du contrat CLI."""
    manifest, path = managed_cli_session
    cli_json(managed_cli_session, "goto", f"{fixtures_http.base_url}/index.html")
    proc = run_cli(manifest, path, "goto", "https://blocked.example/")
    attach_cli_run(evidence_case, "goto-origine-refusee", proc)
    with CDPClient(manifest.websocket_url, timeout=10) as client:
        attach_screenshot(evidence_case, client, "origin-guard-final")
    #: la sortie vers une origine interdite échoue en erreur runtime avec un refus explicite
    assert proc.returncode == 1
    assert "origine refusée" in proc.stderr


def test_metrics_real(page):
    """Les métriques de performance de Chrome sont collectées sur une vraie
    page avec des valeurs vivantes, pas des zéros de complaisance."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    res = audit.metrics(c)
    #: nœuds DOM, documents et tas JS non nuls prouvent une collecte réelle, pas un stub
    assert res["Nodes"] > 0 and res["Documents"] > 0
    assert res["JSHeapUsedSize"] > 0


def test_pdf_real(page, tmp_path):
    """L'impression via CDP produit un vrai document PDF non trivial sur
    disque."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    dest = tmp_path / "page.pdf"
    res = capture.pdf(c, str(dest))
    #: taille plausible et signature %PDF- attestent d'un document réellement imprimé
    assert res["bytes"] > 1000
    assert dest.read_bytes().startswith(b"%PDF-")


def test_record_replay_real(chrome, fixtures_http, evidence_case, tmp_path, monkeypatch):
    """Un parcours enregistré agit immédiatement puis se rejoue intégralement
    sur un onglet vierge; un journal altéré provoque une divergence détectée
    et un arrêt net au bon évènement."""
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
            #: l'enregistrement n'est pas passif: chaque étape a agi sur la page en la capturant
            assert js.get_text(c, "#result")["text"] == "OK:Léo"  # record a bien AGI
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    # rejeu intégral sur un onglet vierge: le parcours se reconstruit seul
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            res = advanced.replay(c, str(journal), origins="http://127.0.0.1:*")
            #: le rejeu reconstruit seul les trois étapes et aboutit au même DOM final
            assert res["ok"] is True and res["played"] == 3
            assert js.get_text(c, "#result")["text"] == "OK:Léo"
            attach_screenshot(evidence_case, c, "replay-final")
            # journal altéré (sélecteur disparu) -> divergence, arrêt net
            journal.write_text(
                journal.read_text().replace("#submit-btn", "#gone"), encoding="utf-8"
            )
            broken = advanced.replay(c, str(journal), origins="http://127.0.0.1:*")
            #: la divergence est localisée à l'évènement altéré et le rejeu
            #: s'arrête là au lieu de continuer à l'aveugle
            assert broken["ok"] is False and broken["played"] == 2
            assert broken["divergence"].startswith("event 2:")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_composed_action_real(chrome, fixtures_http, evidence_case):
    """L'émulation mobile appliquée dans la même connexion CDP que l'action
    composée est visible par la page pendant le goto (device et user-agent)."""
    # Agir sous émulation = action dans la MÊME connexion (les overrides
    # meurent avec elle): la page voit le device mobile pendant le goto.
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            advanced.emulate(c, "mobile")
            result = actions.run_action(c, ["goto", f"{fixtures_http.base_url}/index.html"])
            #: la page chargée sous émulation voit l'écran et l'user-agent du preset mobile
            assert result["ok"] is True
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            attach_screenshot(evidence_case, c, "mobile-final")
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])


def test_emulate_mobile_and_reset_real(chrome, fixtures_http, evidence_case):
    """Deux propriétés d'emulate prouvées contre Chrome réel: --reset restaure
    device ET user-agent dans la connexion, et les overrides meurent avec la
    connexion CDP — une invocation isolée ne pollue pas la page."""
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
            #: le preset mobile est effectif côté page, écran et user-agent compris
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            advanced.emulate(c, reset=True)
            #: --reset restaure les deux dimensions, y compris l'UA (régression historique)
            assert js.evaluate(c, "screen.width") == initial
            assert "cdpx-mobile" not in js.evaluate(c, "navigator.userAgent")
            advanced.emulate(c, "mobile")  # re-pose l'override, la connexion se ferme
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            #: après fermeture de la connexion, l'override reposé a disparu:
            #: aucune émulation fantôme ne survit à une invocation isolée
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
    """Un scénario YAML déclaratif pilote un vrai navigateur (navigation +
    formulaire) jusqu'au verdict pass, en collectant les artefacts de
    checkpoint et de fin de parcours comme preuve."""
    monkeypatch.setenv("E2E_FORM_NAME", "Leo")
    scenario = materialize_scenario("static_form_pass.yml", fixtures_http.base_url, tmp_path)
    code, result, err = run_scenario_cli(managed_cli_session, scenario)

    attach_scenario_run(evidence_case, result, "static-form-scenario")
    #: le scénario métier aboutit avec le verdict attendu, diagnostics joints en cas d'échec
    assert code == 0, f"stderr={err}\nresult={json.dumps(result, ensure_ascii=False, indent=2)}"
    assert result["verdict"] == "pass"
    #: les preuves visuelles du checkpoint et de la fin de parcours sont bien collectées
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
    """Quand les assertions d'observabilité (console, réseau) échouent, le
    scénario rend un verdict fail unique avec des findings identifiables,
    sans casser la collecte de preuve."""
    scenario = materialize_scenario(
        "static_observability_fail.yml", fixtures_http.base_url, tmp_path
    )
    code, result, _ = run_scenario_cli(managed_cli_session, scenario)

    attach_scenario_run(evidence_case, result, "static-observability-scenario")
    #: l'échec métier emprunte le canal d'erreur runtime avec un verdict explicite
    assert code == 1
    assert result["verdict"] == "fail"
    #: chaque assertion violée produit son finding codé, exploitable par une machine
    assert {finding["code"] for finding in result["findings"]} >= {
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    }


def test_seo_audit_real(page):
    """L'audit SEO rend un rapport vierge sur une page saine (JSON-LD parsé
    compris) et signale les défauts d'une page cassée."""
    c, base = page
    nav.navigate(c, f"{base}/seo.html")
    res = audit.seo(c)
    #: la page témoin saine ne déclenche aucun faux positif et son JSON-LD est bien parsé
    assert res["findings"] == []
    assert res["jsonld"][0]["sku"] == "FIX-001"
    nav.navigate(c, f"{base}/seo-broken.html")
    broken = audit.seo(c)
    #: le même audit sur la page cassée détecte le doublon de h1
    assert "2 h1 (attendu: 1)" in broken["findings"]


def test_cookies_and_storage_real(page):
    """Les cookies posés par la page sont lisibles via CDP et le localStorage
    sort masqué par défaut, le démasquage restant un choix explicite."""
    c, base = page
    nav.navigate(c, f"{base}/storage.html")
    cookies = state.get_cookies(c, show_values=True)["cookies"]
    #: le cookie créé en JavaScript par la page est visible via CDP
    assert any(ck["name"] == "jsCookie" for ck in cookies)
    storage = state.get_storage(c, "local")
    #: par défaut la valeur du storage est masquée, avec le drapeau qui l'annonce
    assert storage["entries"].get("cdpx-key") == "***"
    assert storage["values_masked"] is True
    shown = state.get_storage(c, "local", show_values=True)
    #: le démasquage explicite restitue la valeur réelle posée par la fixture
    assert shown["entries"].get("cdpx-key") == "cdpx-value"


def test_screenshot_real(page, tmp_path, evidence_case):
    """La capture d'écran écrit un vrai PNG non trivial depuis une page
    chargée dans Chrome."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    out = tmp_path / "e2e.png"
    res = capture.screenshot(c, str(out))
    if evidence_case is not None:
        evidence_case.attach_screenshot(out, "screenshot-command")
    #: la taille et la signature PNG attestent d'une image réellement capturée
    assert res["bytes"] > 1000
    assert out.read_bytes().startswith(b"\x89PNG")


def test_full_page_screenshot_captures_long_page(page, tmp_path, evidence_case):
    """La capture pleine page embarque le contenu au-delà du viewport: elle
    est strictement plus lourde que la capture standard de la même page
    longue."""
    c, base = page
    nav.navigate(c, f"{base}/long.html")
    normal = tmp_path / "normal.png"
    full = tmp_path / "full.png"
    normal_res = capture.screenshot(c, str(normal))
    full_res = capture.screenshot(c, str(full), full_page=True)
    if evidence_case is not None:
        evidence_case.attach_screenshot(normal, "normal-screenshot")
        evidence_case.attach_screenshot(full, "full-page-screenshot")
    #: le surpoids de la version pleine page prouve que le contenu hors
    #: écran est bien dedans, et le fichier reste un PNG valide
    assert full_res["full_page"] is True
    assert full_res["bytes"] > normal_res["bytes"]
    assert full.read_bytes().startswith(b"\x89PNG")


def test_json_endpoint_reachable_from_page(page):
    """Le serveur de fixtures est joignable depuis le contexte de la page:
    un fetch same-origin réel aboutit et rend le JSON attendu."""
    c, base = page
    nav.navigate(c, f"{base}/index.html")
    raw = js.evaluate(c, f"fetch('{base}/api/json').then(r => r.text())", await_promise=True)
    #: la réponse parsée depuis la page prouve la chaîne complète fetch → serveur de fixtures
    assert json.loads(raw)["ok"] is True
