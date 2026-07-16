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

from cdpx import discovery, proof
from cdpx.action_model import ClickAction, GotoAction, TypeAction
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import (
    actions,
    audit,
    capture,
    dev,
    diagnostics,
    emulation,
    frames,
    inputs,
    interception,
    js,
    nav,
    net,
    recording,
    state,
)
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
from cdpx.testing.evidence import ARTIFACT_TYPES

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
def test_proof_cockpit_renders_offline_docs_and_mermaid(page, tmp_path, evidence_case):
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
    external_scripts = js.evaluate(client, "document.querySelectorAll('script[src]').length")
    network_resources = js.evaluate(client, "performance.getEntriesByType('resource').length")
    assert external_scripts == 0
    assert network_resources == 0
    if evidence_case is not None:
        # Rendu offline documenté: l'état Mermaid (4 sources -> 4 SVG, 0 erreur)
        # et les sondes d'herméticité (aucun script[src], aucune ressource
        # réseau) prouvent que la route Docs partageable tient en file:// seul.
        evidence_case.attach_json(
            "rendu-offline-mermaid",
            {
                "mermaid": mermaid_state,
                "hermeticity": {
                    "external_scripts": external_scripts,
                    "network_resources": network_resources,
                },
            },
        )

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


# === Cockpit de preuve: couverture comportementale de la SPA ===
# Un rapport partageable est généré UNE fois par module (rendu ~4 Mo) depuis un
# summary synthétique riche, puis chaque test sonde une vue ou un visualiseur
# de la modal via CDP. Tout passe par la façade cdpx.proof (render_html,
# build_shareable_proof): ces tests sont le filet de sécurité comportemental
# du refactor interne de proof.py à venir.

COCKPIT_FEATURE = "demo-checkout"
COCKPIT_JOURNEY = "buy-item"
COCKPIT_PASS_NODEID = "tests/e2e/demo_checkout.py::test_pay_success"
COCKPIT_FAIL_NODEID = "tests/e2e/demo_checkout.py::test_pay_declined"
COCKPIT_EVIDENCE_PREFIX = ".proof/evidence/artifacts/e2e/demo-checkout"


def _demo_cast() -> str:
    lines = [
        json.dumps({"version": 2, "width": 40, "height": 10}),
        json.dumps([0.2, "o", "$ cdpx tabs list\r\n"]),
        json.dumps([0.6, "o", '{"count": 1}\r\n']),
    ]
    return "\n".join(lines) + "\n"


def _artifact_bodies(screenshot_path: Path) -> dict[str, dict]:
    """Un corps de démonstration par type de la taxonomie fermée.

    Les types inlinables (_INLINE_TYPES de proof.py) portent inline_content —
    un attach retombé en type `file` opaque serait invisible dans la modal;
    screenshot pointe un vrai PNG, video/file assument le repli téléchargement.
    """

    console_payload = json.dumps(
        {
            "entries": [
                {"kind": "console", "type": "log", "text": "fixture-log prêt"},
                {"kind": "console", "type": "warning", "text": "API dépréciée"},
                {"kind": "exception", "type": "error", "text": "fixture-uncaught boom"},
            ]
        }
    )
    network_payload = json.dumps(
        {
            "summary": {"total": 2, "errors_4xx_5xx": 1, "failed": 0, "bytes": 640},
            "requests": [
                {
                    "method": "GET",
                    "url": "http://demo.test/api/json",
                    "status": 200,
                    "resourceType": "xhr",
                    "encodedBytes": 512,
                },
                {
                    "method": "GET",
                    "url": "http://demo.test/api/status/500",
                    "status": 500,
                    "resourceType": "xhr",
                    "encodedBytes": 128,
                },
            ],
        }
    )
    return {
        "asciinema": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/session.cast",
            "inline_content": _demo_cast(),
        },
        "command": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/goto.txt",
            "inline_content": (
                '$ cdpx goto http://demo.test/\n--- stdout ---\n{"ok": true}\n'
                "--- stderr ---\n\n--- exit_code: 0 ---\n"
            ),
            "meta": {
                "argv": ["cdpx", "goto", "http://demo.test/"],
                "exit_code": 0,
                "duration_s": 0.42,
            },
        },
        "console": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/console.json",
            "inline_content": console_payload,
        },
        "file": {"path": f"{COCKPIT_EVIDENCE_PREFIX}/dump.bin", "inline_skipped": "illisible"},
        "json": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/verdict.json",
            "inline_content": json.dumps({"verdict": "pass", "steps": [1, 2, 3]}),
        },
        "log-excerpt": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/excerpt.txt",
            "inline_content": "ligne saine\nERROR paiement refusé\nligne suivante",
            "meta": {"source": ".proof/app.log", "pattern": "ERROR", "matched_lines": [2]},
        },
        "logs": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/run.log",
            "inline_content": "ligne un\nligne deux\nligne trois",
        },
        "network": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/network.json",
            "inline_content": network_payload,
        },
        "profiler": {
            "path": f"{COCKPIT_EVIDENCE_PREFIX}/profiler.json",
            "inline_content": json.dumps(
                {"profiler_status": 200, "token_present": True, "panels": {"db": {"queries": 6}}}
            ),
        },
        "screenshot": {"path": str(screenshot_path), "bytes": screenshot_path.stat().st_size},
        "video": {"path": f"{COCKPIT_EVIDENCE_PREFIX}/replay.webm", "inline_skipped": "taille"},
    }


def _taxonomy_artifacts(screenshot_path: Path) -> list[dict]:
    bodies = _artifact_bodies(screenshot_path)
    artifacts = []
    # Itération sur la taxonomie réelle: un type ajouté à ARTIFACT_TYPES sans
    # corps de démonstration ici lève KeyError — le nouveau type doit recevoir
    # sa preuve synthétique ET son visualiseur, jamais un oubli silencieux.
    for index, artifact_type in enumerate(sorted(ARTIFACT_TYPES)):
        body = bodies[artifact_type]
        artifacts.append(
            {
                "type": artifact_type,
                "label": f"demo-{artifact_type}",
                "bytes": len(body.get("inline_content", "")) or 512,
                "created_at": f"2026-07-15T00:00:{index + 1:02d}+00:00",
                **body,
            }
        )
    return artifacts


def _cockpit_run(
    nodeid: str,
    short_id: str,
    status: str,
    artifacts: list[dict],
    *,
    intent: str,
    assertions: list[dict],
    message: str = "",
    failed_line: int = 0,
) -> dict:
    return {
        "nodeid": nodeid,
        "suite": "e2e",
        "status": status,
        "feature": COCKPIT_FEATURE,
        "journey": COCKPIT_JOURNEY,
        "scenario": short_id,
        "scenario_id": f"{COCKPIT_FEATURE}.{short_id}",
        "started_at": "2026-07-15T00:00:00+00:00",
        "duration_s": 1.2,
        "intent": intent,
        "assertions": assertions,
        "failed_line": failed_line,
        "message": message,
        "artifacts": artifacts,
    }


def _proofs_for(run: dict) -> list[dict]:
    return [
        {
            "scenario": run["nodeid"],
            "scenario_id": run["scenario_id"],
            "type": artifact["type"],
            "label": artifact["label"],
            "path": artifact["path"],
        }
        for artifact in run["artifacts"]
    ]


def _scenario_node(short_id: str, title: str, texts: dict[str, str], run: dict) -> dict:
    return {
        "id": short_id,
        "scenario_id": f"{COCKPIT_FEATURE}.{short_id}",
        "journey": COCKPIT_JOURNEY,
        "title": title,
        "tests": [run["nodeid"]],
        "expected_proofs": ["junit"],
        "matched_tests": [run["nodeid"]],
        "matched_scenarios": [run],
        "proofs": _proofs_for(run),
        "gaps": [],
        **texts,
    }


def _cockpit_summary(screenshot_path: Path) -> dict:
    help_proc = subprocess.run(
        [sys.executable, "-m", "cdpx.cli", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    help_commands = proof.parse_help_commands(help_proc.stdout)
    run_pass = _cockpit_run(
        COCKPIT_PASS_NODEID,
        "pay-success",
        "passed",
        _taxonomy_artifacts(screenshot_path),
        intent="Le client règle son panier et reçoit sa preuve d'achat.",
        assertions=[
            {
                "line": 12,
                "end_line": 12,
                "text": "le panier est facturé au bon montant",
                "code_excerpt": "assert total == 42",
                "kind": "assert",
                "status": "",
            },
            {
                "line": 15,
                "end_line": 15,
                "text": "le reçu est émis au client",
                "code_excerpt": "assert receipt.sent",
                "kind": "assert",
                "status": "",
            },
        ],
    )
    run_fail = _cockpit_run(
        COCKPIT_FAIL_NODEID,
        "pay-declined",
        "failed",
        [
            {
                "type": "screenshot",
                "label": "echec-paiement",
                "path": str(screenshot_path),
                "bytes": screenshot_path.stat().st_size,
                "created_at": "2026-07-15T00:00:02+00:00",
            }
        ],
        intent="Un paiement refusé doit rester un échec visible, jamais un faux vert.",
        assertions=[
            {
                "line": 30,
                "end_line": 30,
                "text": "la commande est créée",
                "code_excerpt": "assert order.id",
                "kind": "assert",
                "status": "",
            },
            {
                "line": 34,
                "end_line": 34,
                "text": "le paiement est accepté",
                "code_excerpt": "assert paid",
                "kind": "assert",
                "status": "failed",
            },
        ],
        message="AssertionError: paiement refusé",
        failed_line=34,
    )
    node_pass = _scenario_node(
        "pay-success",
        "Payer avec succès",
        {
            "ui_text": "Le client règle son panier.",
            "report_text": "Ce scénario prouve le paiement nominal.",
            "given": "Un panier prêt à être réglé.",
            "when": "Le client valide le paiement.",
            "then": "Le reçu est émis.",
        },
        run_pass,
    )
    node_fail = _scenario_node(
        "pay-declined",
        "Paiement refusé",
        {
            "ui_text": "Un paiement refusé est signalé au client.",
            "report_text": "Ce scénario prouve la visibilité des refus de paiement.",
            "given": "Une carte refusée par la banque.",
            "when": "Le client tente de payer.",
            "then": "Le refus est expliqué sans reçu émis.",
        },
        run_fail,
    )
    journey = {
        "id": COCKPIT_JOURNEY,
        "title": "Acheter un article",
        "entrypoint": "cdpx goto",
        "scenarios": [node_pass, node_fail],
        "matched_tests": [COCKPIT_PASS_NODEID, COCKPIT_FAIL_NODEID],
        "matched_scenarios": [run_pass, run_fail],
        "proofs": _proofs_for(run_pass) + _proofs_for(run_fail),
        "gaps": [],
    }
    feature = {
        "id": COCKPIT_FEATURE,
        "title": "Achat de démonstration",
        "status": "active",
        "summary": "Parcours d'achat synthétique construit pour éprouver le cockpit.",
        "entrypoints": ["cdpx goto"],
        "path_globs": [],
        "test_globs": [],
        "docs": [],
        "journeys": [journey],
        "scenarios": [node_pass, node_fail],
        "expected_proofs": ["junit"],
        "source": "docs/features/demo-checkout.md",
        "sections": [],
        "doc_html": (
            "<h2>Mode d'emploi</h2><p>Documentation de démonstration du parcours d'achat.</p>"
        ),
        "matched_entrypoints": [],
        "matched_paths": [],
        "matched_tests": [COCKPIT_PASS_NODEID, COCKPIT_FAIL_NODEID],
        "matched_scenarios": [run_pass, run_fail],
        "proofs": _proofs_for(run_pass) + _proofs_for(run_fail),
        "changed_paths": [],
        "gaps": [],
    }
    entrypoints = [
        {
            "id": f"cdpx {command['name']}",
            "type": "cli",
            "source": "src/cdpx/cli.py",
            "label": command.get("help", ""),
        }
        for command in help_commands
    ]
    cast_text = _demo_cast()
    return {
        "ok": False,
        "generated_at": "2026-07-15T00:00:00+00:00",
        "git": {"branch": "e2e-cockpit", "sha": "0000000", "changed_files": []},
        "environment": {"python": "3.12", "platform": "e2e-fixture", "chrome_or_chromium": True},
        "cli_help": ".proof/cdpx-help.txt",
        "totals": {"tests": 13, "passed": 12, "skipped": 0, "failed": 1, "unavailable": 0},
        "scenario_totals": {
            "scenarios": 2,
            "unit": 0,
            "integration": 0,
            "e2e": 2,
            "symfony": 0,
            "screenshots": 2,
            "missing_e2e_screenshots": [],
        },
        "scenario_evidence": {
            "suites": {"unit": [], "integration": [], "e2e": [run_pass, run_fail], "symfony": []},
            "files": [],
            "totals": {
                "scenarios": 2,
                "unit": 0,
                "integration": 0,
                "e2e": 2,
                "symfony": 0,
                "screenshots": 2,
                "missing_e2e_screenshots": [],
            },
        },
        "feature_inventory": {
            "features": [feature],
            "entrypoints": entrypoints,
            "feature_by_entrypoint": {"cdpx goto": COCKPIT_FEATURE},
            "totals": {
                "features": 1,
                "entrypoints": len(entrypoints),
                "mapped_entrypoints": 1,
                "scenarios": 2,
                "documented_scenarios": 2,
                "warnings": 1,
                "violations": 1,
            },
            "violations": ["scenario unmapped: tests/e2e/demo_checkout.py::test_flaky"],
            "warnings": ["source path unmapped: src/demo/extra.py"],
            "docs_dir": "docs/features",
        },
        "documentation": {"documents": [], "tree": {}},
        "commands": [
            {
                "id": "ruff-check",
                "label": "Ruff lint",
                "argv": ["ruff", "check", "src", "tests"],
                "log": ".proof/ruff-check.log",
                "exit_code": 0,
                "duration_s": 2.5,
                "status": "ok",
                "log_tail": "All checks passed!",
            },
            {
                "id": "unit",
                "label": "Pytest unitaires",
                "argv": ["pytest", "tests"],
                "log": ".proof/make-check-pytest.log",
                "exit_code": 0,
                "duration_s": 30.0,
                "status": "ok",
                "log_tail": "430 passed",
            },
            {
                "id": "e2e",
                "label": "Pytest E2E Chrome",
                "argv": ["pytest", "tests/e2e"],
                "log": ".proof/e2e-chrome.log",
                "exit_code": 1,
                "duration_s": 61.4,
                "status": "failed",
                "log_tail": f"FAILED {COCKPIT_FAIL_NODEID}",
            },
        ],
        "junit": {
            "unit": {
                "path": ".proof/unit-junit.xml",
                "exists": True,
                "tests": 10,
                "passed": 10,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "time_s": 3.2,
                "parse_error": None,
                "cases": [],
                "focus": [
                    {
                        "classname": "tests.test_cli",
                        "name": "test_pretty",
                        "time_s": 0.5,
                        "status": "passed",
                        "message": "",
                    }
                ],
            },
            "e2e": {
                "path": ".proof/e2e-junit.xml",
                "exists": True,
                "tests": 2,
                "passed": 1,
                "failures": 1,
                "errors": 0,
                "skipped": 0,
                "time_s": 61.0,
                "parse_error": None,
                "cases": [],
                "focus": [
                    {
                        "classname": "tests.e2e.demo_checkout",
                        "name": "test_pay_declined",
                        "time_s": 1.4,
                        "status": "failed",
                        "message": "AssertionError",
                    }
                ],
            },
            "symfony": {
                "path": ".proof/symfony-e2e-junit.xml",
                "exists": True,
                "tests": 1,
                "passed": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "time_s": 12.0,
                "parse_error": None,
                "cases": [],
                "focus": [],
            },
        },
        "casts": [
            {
                "id": "cdpx-help",
                "status": "generated",
                "path": ".proof/cdpx-help.cast",
                "bytes": len(cast_text),
            }
        ],
        "evidence_catalog": [
            {
                "type": "asciinema",
                "name": "cdpx-help",
                "path": ".proof/cdpx-help.cast",
                "status": "generated",
                "roi": "Replay terminal de la démonstration.",
                "inline_content": cast_text,
            },
            {
                "type": "screenshot",
                "name": "Capture UI",
                "path": "",
                "status": "not-needed",
                "roi": "Non générée automatiquement.",
            },
        ],
        "project": {
            "name": "cdpx",
            "version": "0.0-e2e",
            "mission": (
                "CLI de primitives Chrome DevTools Protocol pour la preuve de démonstration."
            ),
            "cli_command_count": len(help_commands),
            "cli_commands": [command["name"] for command in help_commands],
            "docs": ["README.md", "HARNESS.md"],
            "fixtures": ["tests/fixtures/index.html"],
        },
        "validation_matrix": [{"milestone": "M9", "proof": "Preuves secondaires généralisées"}],
        "coverage_groups": [
            {"suite": "e2e", "module": "demo_checkout", "tests": 2, "failed": 1, "skipped": 0}
        ],
        "risks": [
            {
                "risk": "Chrome obligatoire.",
                "mitigation": "make proof échoue sans binaire.",
                "rollback": "Installer Chrome puis relancer.",
            }
        ],
        "unknowns": [
            {
                "item": "Réseau externe",
                "why": "Fixtures loopback uniquement.",
                "how_to_verify": "Inspecter les logs réseau.",
            }
        ],
        "proof_failures": ["command failed: Pytest E2E Chrome (.proof/e2e-chrome.log)"],
    }


@pytest.fixture(scope="module")
def cockpit_report(tmp_path_factory):
    """Rapport de preuve partageable généré une seule fois pour tout le module:
    le rendu HTML (~4 Mo, bundles Mermaid + xterm inclus) est trop coûteux pour
    être reconstruit à chaque test de la SPA."""
    root = tmp_path_factory.mktemp("cdpx-cockpit")
    screenshot = root / "demo-capture.png"
    screenshot.write_bytes((Path(__file__).parents[1] / "fixtures" / "pixel.png").read_bytes())
    summary = _cockpit_summary(screenshot)
    proof_dir = root / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", proof.render_html(summary))
    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        pre_redacted_paths={"proof-report.html"},
    )
    return (staging / ".proof" / "proof-report.html").as_uri()


def _js_ready(client: CDPClient, expression: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if js.evaluate(client, expression) is True:
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"cockpit jamais prêt: {expression}")
        time.sleep(0.05)


def _click(client: CDPClient, selector: str) -> None:
    clicked = js.evaluate(
        client,
        "(() => { const el = document.querySelector(" + json.dumps(selector) + ");"
        " if (!el) return false; el.click(); return true; })()",
    )
    assert clicked is True, f"élément introuvable au clic: {selector}"


def _open_cockpit(client: CDPClient, report_url: str, route: str, ready: str) -> None:
    assert nav.navigate(client, f"{report_url}#{route}")["ok"] is True
    _js_ready(client, ready)


def _goto_route(client: CDPClient, route: str, ready: str) -> None:
    js.evaluate(client, "location.hash = " + json.dumps("#" + route))
    _js_ready(client, ready)


def _open_artifact_modal(client: CDPClient, artifact_type: str) -> None:
    selector = (
        "#app .timeline-row .shot"
        if artifact_type == "screenshot"
        else f'#app .timeline-row .chip[title="{artifact_type}"]'
    )
    _click(client, selector)
    state = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " type: document.querySelector('.modal-type').textContent})",
    )
    assert state == {"hidden": False, "type": artifact_type}, state


def _close_modal(client: CDPClient) -> None:
    inputs.press_key(client, "Escape")
    assert js.evaluate(client, "document.getElementById('artifact-modal').hidden") is True


def _expand_test_card(client: CDPClient) -> None:
    """Déplie la carte de test comme le ferait un lecteur.

    La carte d'un test passé est un <details> replié par défaut: son contenu
    répond à click() programmatique mais n'est pas focusable tant que la carte
    est fermée — l'utilisateur réel doit l'ouvrir pour atteindre les chips.
    """

    expanded = js.evaluate(
        client,
        "(() => { const card = document.querySelector('#app .test-card');"
        " if (!card) return false; card.open = true; return card.open; })()",
    )
    assert expanded is True


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["The cockpit SPA drills down from features to journeys, scenarios and test cards."],
)
def test_cockpit_features_view_drills_down_to_scenario(page, cockpit_report, evidence_case):
    """L'accueil du cockpit rend verdict et métriques, puis le drill-down
    Features -> fiche -> journey -> scénario aboutit à la carte de test avec
    fil d'Ariane, pastilles de statut, bloc BDD et déroulé annoté."""
    client, _base = page
    _open_cockpit(client, cockpit_report, "/features", "!!document.querySelector('#app .metrics')")

    home = js.evaluate(
        client,
        "({verdict: document.querySelector('.metrics .metric strong').textContent,"
        " tests: document.querySelectorAll('.metrics .metric strong')[1].textContent,"
        " cards: document.querySelectorAll('#app .grid .card').length,"
        " cardPill: document.querySelector('#app .grid .card .pill').textContent,"
        " sideEntry: document.querySelector("
        "'#featureNav a[data-feature-id=\"demo-checkout\"]').textContent})",
    )
    #: l'accueil annonce le verdict rouge et le compte de tests du run, et la
    #: feature synthétique apparaît en carte comme dans la barre latérale
    assert home["verdict"] == "ECHEC"
    assert home["tests"] == "12/13"
    assert home["cards"] == 1 and home["cardPill"] == "failed"
    assert "Achat de démonstration" in home["sideEntry"]
    assert "1 journeys" in home["sideEntry"]

    _click(client, '#app .grid .card h2 a[href="#/features/demo-checkout"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Achat de démonstration'")
    feature_view = js.evaluate(
        client,
        "({crumbs: document.querySelector('#app .crumbs').textContent,"
        " pill: document.querySelector('#app .meta .pill').textContent,"
        " doc: document.querySelector('#app .panel.doc').textContent,"
        " journeyLink: !!document.querySelector("
        "'#app a[href=\"#/features/demo-checkout/journeys/buy-item\"]')})",
    )
    #: la fiche feature porte son fil d'Ariane, sa pastille d'échec, sa doc
    #: utilisateur rendue et le lien vers son journey
    assert "Features" in feature_view["crumbs"]
    assert feature_view["pill"] == "failed"
    assert "Documentation de démonstration" in feature_view["doc"]
    assert feature_view["journeyLink"] is True

    _click(client, '#app a[href="#/features/demo-checkout/journeys/buy-item"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Acheter un article'")
    journey_view = js.evaluate(
        client,
        "({entrypoint: document.querySelector('#app p code').textContent,"
        " rows: document.querySelectorAll('#app .scenario-list .scenario-row').length,"
        " pills: Array.from("
        "document.querySelectorAll('#app .scenario-row .pill'), n => n.textContent)})",
    )
    #: le journey liste ses deux scénarios documentés avec leur verdict propre
    assert journey_view["entrypoint"] == "cdpx goto"
    assert journey_view["rows"] == 2
    assert journey_view["pills"] == ["ok", "failed"]

    _click(client, '#app a[href="#/features/demo-checkout/scenarios/pay-success"]')
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Payer avec succès'")
    scenario_view = js.evaluate(
        client,
        "({crumbLinks: document.querySelectorAll('#app .crumbs a').length,"
        " bdd: Array.from(document.querySelectorAll('#app .bdd h3'), n => n.textContent),"
        " given: document.querySelector('#app .bdd div p').textContent,"
        " cardPill: document.querySelector('#app .test-card summary .pill').textContent,"
        " nodeid: document.querySelector('#app .test-card summary code').textContent,"
        " intent: document.querySelector('#app .test-intent').textContent,"
        " okMarks: document.querySelectorAll('#app .assertion-row.assertion-ok').length,"
        " timeline: document.querySelectorAll('#app .timeline-row').length})",
    )
    #: la fiche scénario remonte jusqu'au test: fil d'Ariane complet, bloc
    #: Given/When/Then documenté, carte de test passée avec intention
    assert scenario_view["crumbLinks"] == 3
    assert scenario_view["bdd"] == ["Given", "When", "Then"]
    assert scenario_view["given"] == "Un panier prêt à être réglé."
    assert scenario_view["cardPill"] == "passed"
    assert scenario_view["nodeid"] == COCKPIT_PASS_NODEID
    assert "règle son panier" in scenario_view["intent"]
    #: le déroulé annoté peint en vert les deux assertions du test passé et la
    #: chronologie expose un artefact par type de la taxonomie
    assert scenario_view["okMarks"] == 2
    assert scenario_view["timeline"] == len(ARTIFACT_TYPES)
    if evidence_case is not None:
        evidence_case.attach_json(
            "cockpit-drilldown",
            {
                "home": home,
                "feature": feature_view,
                "journey": journey_view,
                "scenario": scenario_view,
            },
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["A red run surfaces read-first failures and a dedicated gaps view."],
)
def test_cockpit_read_first_and_gaps_surface_failures(page, cockpit_report, evidence_case):
    """Sur un run rouge, l'accueil ouvre par « À lire d'abord » (échecs de
    preuve et tests failed dont le lien mène à la fiche de leur scénario), la
    barre du haut compte les gaps, et la route #/gaps détaille violations,
    warnings et proof failures."""
    client, _base = page
    _open_cockpit(
        client, cockpit_report, "/features", "!!document.querySelector('#app .read-first')"
    )

    read_first = js.evaluate(
        client,
        "({heading: document.querySelector('.read-first h2').textContent,"
        " items: Array.from("
        "document.querySelectorAll('.read-first li'), n => n.textContent.trim()),"
        " failedPill: document.querySelector('.read-first li .pill.failed')?.textContent,"
        " failedHref: document.querySelector('.read-first li a')?.getAttribute('href'),"
        " gapsSup: document.querySelector('[data-route=\"/gaps\"] sup')?.textContent,"
        " gapsSupBad: !!document.querySelector('[data-route=\"/gaps\"] sup.sup-bad'),"
        " runSup: document.querySelector('[data-route=\"/run\"] sup.sup-bad')?.textContent})",
    )
    #: le panneau « À lire d'abord » nomme la commande échouée ET le test
    #: failed, avec sa pastille de statut
    assert read_first["heading"] == "À lire d'abord"
    assert any("command failed: Pytest E2E Chrome" in item for item in read_first["items"])
    assert any(COCKPIT_FAIL_NODEID in item for item in read_first["items"])
    assert read_first["failedPill"] == "failed"
    #: la barre du haut agrège les gaps (1 violation + 1 warning + 1 proof
    #: failure) et le nombre de tests failed sur le lien Run
    assert read_first["gapsSup"] == "3" and read_first["gapsSupBad"] is True
    assert read_first["runSup"] == "1"

    _click(client, ".read-first li a")
    _js_ready(client, "document.querySelector('#app h1')?.textContent === 'Paiement refusé'")
    failed_scenario = js.evaluate(
        client,
        "({title: document.querySelector('#app h1').textContent,"
        " crumbs: document.querySelector('#app .crumbs').textContent})",
    )
    #: le lien du test failed aboutit à la fiche de son scénario (et non à
    #: « Vue introuvable »): titre du scénario refusé et fil d'Ariane complet
    assert failed_scenario["title"] == "Paiement refusé"
    assert "Achat de démonstration" in failed_scenario["crumbs"]
    assert "Acheter un article" in failed_scenario["crumbs"]

    _goto_route(
        client, "/gaps", "document.querySelector('#app h1')?.textContent === 'Gaps et violations'"
    )
    gaps = js.evaluate(
        client,
        "({panels: Array.from(document.querySelectorAll('#app .panel h2'), n => n.textContent),"
        " violations: document.querySelectorAll('#app .panel')[0].textContent,"
        " warnings: document.querySelectorAll('#app .panel')[1].textContent,"
        " failures: document.querySelectorAll('#app .panel')[2].textContent})",
    )
    #: la vue Gaps sépare violations d'inventaire, warnings et proof failures,
    #: chacun restituant son diagnostic textuel exact
    assert gaps["panels"] == ["Violations", "Warnings", "Proof failures"]
    assert "scenario unmapped" in gaps["violations"]
    assert "source path unmapped" in gaps["warnings"]
    assert "command failed: Pytest E2E Chrome" in gaps["failures"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "read-first-et-gaps",
            {"read_first": read_first, "failed_scenario": failed_scenario, "gaps": gaps},
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["Every inlined textual artifact type opens in its dedicated modal viewer."],
)
def test_modal_renders_every_textual_viewer(page, cockpit_report, evidence_case):
    """Chaque preuve textuelle inlinée s'ouvre dans son visualiseur dédié:
    console filtrable par niveau, table réseau, arbre JSON, profiler, logs
    numérotés, extrait de log surligné et transcript de commande."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    _expand_test_card(client)

    chips = js.evaluate(
        client,
        "({total: document.querySelectorAll('#app .timeline-row a').length,"
        " openable: document.querySelectorAll('#app .timeline-row [data-modal-group]').length})",
    )
    #: ratchet: chaque type de la taxonomie fermée produit un chip capable
    #: d'ouvrir la modal — un type ajouté sans visualiseur casserait ce compte
    assert chips == {"total": len(ARTIFACT_TYPES), "openable": len(ARTIFACT_TYPES)}

    _open_artifact_modal(client, "console")
    console_view = js.evaluate(
        client,
        "({filters: Array.from("
        "document.querySelectorAll('.modal-content .console-filter'), n => n.textContent.trim()),"
        " lines: document.querySelectorAll('.modal-content .console-line').length})",
    )
    #: la console compte ses messages par niveau et rend chaque ligne
    assert console_view["filters"] == ["error (1)", "warn (1)", "log (1)"]
    assert console_view["lines"] == 3
    _click(client, '.modal-content [data-console-level="log"]')
    #: décocher un niveau masque exactement les lignes de ce niveau
    assert (
        js.evaluate(
            client, "document.querySelectorAll('.modal-content .console-line[hidden]').length"
        )
        == 1
    )
    _close_modal(client)

    _open_artifact_modal(client, "network")
    network_view = js.evaluate(
        client,
        "({summary: document.querySelector('.modal-content .viewer-summary').textContent,"
        " rows: document.querySelectorAll('.modal-content tbody tr').length,"
        " bad: document.querySelectorAll('.modal-content .net-status.net-bad').length,"
        " ok: document.querySelectorAll('.modal-content .net-status.net-ok').length})",
    )
    #: la table réseau restitue le résumé agrégé et colore les statuts HTTP
    assert "2 requêtes" in network_view["summary"]
    assert "1 erreurs 4xx/5xx" in network_view["summary"]
    assert network_view["rows"] == 2
    assert network_view["bad"] == 1 and network_view["ok"] == 1
    _close_modal(client)

    _open_artifact_modal(client, "json")
    json_view = js.evaluate(
        client,
        "({nodes: document.querySelectorAll("
        "'.modal-content .json-view details.json-node').length,"
        " keys: Array.from("
        "document.querySelectorAll('.modal-content .json-key'), n => n.textContent)})",
    )
    #: le JSON est rendu en arbre repliable, clés visibles
    assert json_view["nodes"] >= 2
    assert "verdict" in json_view["keys"] and "steps" in json_view["keys"]
    _close_modal(client)

    _open_artifact_modal(client, "profiler")
    profiler_view = js.evaluate(
        client,
        "({chips: document.querySelectorAll('.modal-content .viewer-summary .chip').length,"
        " tree: !!document.querySelector('.modal-content .json-view')})",
    )
    #: le profiler résume ses scalaires en chips et garde l'arbre JSON complet
    assert profiler_view["chips"] == 2 and profiler_view["tree"] is True
    _close_modal(client)

    _open_artifact_modal(client, "logs")
    logs_view = js.evaluate(
        client,
        "({lines: document.querySelectorAll('.modal-content .log-view .log-line').length,"
        " numbered: document.querySelectorAll('.modal-content .log-num').length})",
    )
    #: les logs pleins sont numérotés ligne à ligne
    assert logs_view == {"lines": 3, "numbered": 3}
    _close_modal(client)

    _open_artifact_modal(client, "log-excerpt")
    excerpt_view = js.evaluate(
        client,
        "({banner: document.querySelector('.modal-content .viewer-summary').textContent,"
        " hits: document.querySelectorAll('.modal-content .log-hit').length,"
        " numbered: document.querySelectorAll('.modal-content .log-num').length})",
    )
    #: l'extrait affiche source et motif, surligne la ligne correspondante et
    #: ne numérote pas (les numéros du fichier d'origine sont perdus)
    assert "source" in excerpt_view["banner"] and "motif" in excerpt_view["banner"]
    assert excerpt_view["hits"] == 1 and excerpt_view["numbered"] == 0
    _close_modal(client)

    _open_artifact_modal(client, "command")
    command_view = js.evaluate(
        client,
        "({exit: document.querySelector('.modal-content .command-head .pill').textContent,"
        " argv: document.querySelector('.modal-content .command-head code').textContent,"
        " stdout: document.querySelector('.modal-content .stream-out pre').textContent,"
        " stderr: document.querySelector('.modal-content .stream-err pre').textContent})",
    )
    #: le transcript de commande sépare stdout/stderr, rappelle l'argv exacte
    #: et le code de sortie en pastille
    assert command_view["exit"] == "exit 0"
    assert command_view["argv"] == "$ cdpx goto http://demo.test/"
    assert '{"ok": true}' in command_view["stdout"]
    assert command_view["stderr"] == "(vide)"
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json(
            "visualiseurs-textuels",
            {
                "chips": chips,
                "console": console_view,
                "network": network_view,
                "json": json_view,
                "profiler": profiler_view,
                "logs": logs_view,
                "log_excerpt": excerpt_view,
                "command": command_view,
            },
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["Media artifacts get zoom, download fallback and an embedded xterm cast player."],
)
def test_modal_renders_media_and_cast_viewers(page, cockpit_report, evidence_case):
    """Les preuves non inlinables ont leur visualiseur: screenshot zoomable
    avec horodatage relatif, vidéo en player natif local, fichier opaque en
    repli téléchargement, et cast asciinema joué dans un terminal xterm."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    _expand_test_card(client)

    _open_artifact_modal(client, "screenshot")
    shot = js.evaluate(
        client,
        "({img: !!document.querySelector("
        "'.modal-content figure.viewer-media img[data-zoomable]'),"
        " captured: document.querySelector('.modal-context-body').textContent})",
    )
    #: l'image est zoomable et le contexte date la capture relativement au
    #: début du run du test
    assert shot["img"] is True
    assert "(+" in shot["captured"]
    _click(client, ".modal-content img[data-zoomable]")
    #: un clic agrandit, le suivant restaure — bascule sans état résiduel
    assert (
        js.evaluate(
            client, "document.querySelector('.modal-content img').classList.contains('zoomed')"
        )
        is True
    )
    _click(client, ".modal-content img[data-zoomable]")
    assert (
        js.evaluate(
            client, "document.querySelector('.modal-content img').classList.contains('zoomed')"
        )
        is False
    )
    _close_modal(client)

    _open_artifact_modal(client, "video")
    video_view = js.evaluate(
        client,
        "({player: !!document.querySelector('.modal-content video[controls]'),"
        " src: document.querySelector('.modal-content video')?.getAttribute('src')})",
    )
    #: la vidéo (jamais inlinée) est servie par un player natif pointant le
    #: fichier local de la preuve privée
    assert video_view["player"] is True
    assert video_view["src"] == "evidence/artifacts/e2e/demo-checkout/replay.webm"
    _close_modal(client)

    _open_artifact_modal(client, "file")
    fallback = js.evaluate(
        client,
        "({text: document.querySelector('.modal-content .viewer-fallback').textContent,"
        " link: document.querySelector('.modal-content .viewer-fallback a')?.textContent})",
    )
    #: un fichier opaque assume son repli: raison du non-embarquement et lien
    #: de téléchargement vers l'artefact
    assert "Contenu non embarqué" in fallback["text"]
    assert fallback["link"] == "ouvrir le fichier"
    _close_modal(client)

    _open_artifact_modal(client, "asciinema")
    cast_view = js.evaluate(
        client,
        "({xterm: !!document.querySelector('.modal-content [data-cast-screen] .xterm'),"
        " time: document.querySelector('.modal-content [data-cast-time]').textContent,"
        " scrubMax: document.querySelector('.modal-content [data-cast-scrub]').max,"
        " play: document.querySelector('.modal-content [data-cast-play]').textContent})",
    )
    #: le player cast initialise un vrai terminal xterm, calé en fin de cast,
    #: avec scrubber borné à la durée réelle et bouton lecture
    assert cast_view["xterm"] is True
    assert cast_view["time"] == "0.6s / 0.6s"
    assert cast_view["scrubMax"] == "600"
    assert "lecture" in cast_view["play"]
    _click(client, ".modal-content [data-cast-rawtoggle]")
    raw_view = js.evaluate(
        client,
        "({raw: document.querySelector('.modal-content [data-cast-raw]').hidden,"
        " screen: document.querySelector('.modal-content [data-cast-screen]').hidden,"
        " text: document.querySelector('.modal-content [data-cast-raw]').textContent})",
    )
    #: la vue brute de repli remplace l'écran et restitue le texte du cast
    #: débarrassé des séquences de contrôle
    assert raw_view["raw"] is False and raw_view["screen"] is True
    assert "cdpx tabs list" in raw_view["text"]
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json(
            "visualiseurs-medias-et-cast",
            {"screenshot": shot, "video": video_view, "file": fallback, "cast": cast_view},
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.inspect-artifact-viewers",
    proves=["The artifact modal is fully keyboard drivable with a focus trap."],
)
def test_modal_keyboard_navigation_and_focus_trap(page, cockpit_report, evidence_case):
    """La modal est pilotable au clavier: focus initial sur Fermer, flèches
    précédent/suivant bornées à la liste, Tab piégé aux extrémités de la
    modal, et Échap ferme en restituant le focus à l'élément d'origine."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/features/demo-checkout/scenarios/pay-success",
        "!!document.querySelector('#app .artifact-timeline')",
    )
    #: la carte doit être dépliée pour que le chip soit focusable, condition
    #: de la restitution de focus testée à la fermeture
    _expand_test_card(client)
    chip_selector = '#app .timeline-row .chip[title="command"]'
    opened = js.evaluate(
        client,
        "(() => { const el = document.querySelector(" + json.dumps(chip_selector) + ");"
        " if (!el) return false; el.focus(); el.click(); return true; })()",
    )
    assert opened is True
    #: à l'ouverture, le focus saute sur le bouton Fermer de la modal
    assert js.evaluate(client, "document.activeElement.classList.contains('modal-close')") is True

    order = sorted(ARTIFACT_TYPES)
    total = len(order)
    position = order.index("command") + 1
    counter = "document.querySelector('.modal-counter').textContent"
    #: le compteur situe l'artefact ouvert dans la chronologie du test
    assert js.evaluate(client, counter) == f"{position}/{total}"
    inputs.press_key(client, "ArrowLeft")
    assert js.evaluate(client, counter) == f"{position - 1}/{total}"
    inputs.press_key(client, "ArrowLeft")
    #: la flèche précédente est bornée: pas de sortie de liste au premier item
    assert js.evaluate(client, counter) == f"{position - 1}/{total}"
    inputs.press_key(client, "ArrowRight")
    #: la flèche suivante revient exactement sur l'artefact de départ
    assert js.evaluate(client, counter) == f"{position}/{total}"

    focusables_expr = (
        "Array.from(document.getElementById('artifact-modal')"
        ".querySelectorAll('button, a[href], video'))"
    )
    focus_count = js.evaluate(
        client,
        "(() => { const f = " + focusables_expr + "; f[f.length - 1].focus();"
        " return f.length; })()",
    )
    assert focus_count >= 2
    inputs.press_key(client, "Tab")
    #: depuis le dernier focusable, Tab boucle sur le premier (bouton Fermer)
    assert js.evaluate(client, "document.activeElement.classList.contains('modal-close')") is True
    shift_tab = {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "modifiers": 8}
    client.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", **shift_tab})
    client.send("Input.dispatchKeyEvent", {"type": "keyUp", **shift_tab})
    #: Maj+Tab depuis le premier focusable repart sur le dernier: le clavier
    #: reste confiné à la modal dans les deux sens
    assert (
        js.evaluate(
            client,
            "(() => { const f = " + focusables_expr + ";"
            " return document.activeElement === f[f.length - 1]; })()",
        )
        is True
    )

    inputs.press_key(client, "Escape")
    closed = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " bodyOpen: document.body.classList.contains('modal-open'),"
        " content: document.querySelector('.modal-content').innerHTML,"
        " focusRestored: document.activeElement === document.querySelector("
        + json.dumps(chip_selector)
        + ")})",
    )
    #: Échap ferme, vide le contenu, libère le body et rend le focus au chip
    #: qui avait ouvert la modal
    assert closed == {"hidden": True, "bodyOpen": False, "content": "", "focusRestored": True}
    if evidence_case is not None:
        evidence_case.attach_json(
            "clavier-modal", {"total": total, "position": position, "closed": closed}
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["The run view renders command timeline, JUnit tables and playable casts."],
)
def test_cockpit_run_view_lists_commands_timeline_and_casts(page, cockpit_report, evidence_case):
    """La vue Run raconte le run de preuve: chronologie proportionnelle des
    commandes (échec en rouge), tables commandes et JUnit, fins de logs, et la
    section casts avec sa table de portail et son chip jouable dans xterm."""
    client, _base = page
    _open_cockpit(client, cockpit_report, "/run", "!!document.querySelector('#app .run-timeline')")

    run_view = js.evaluate(
        client,
        "({bars: document.querySelectorAll('.run-timeline .tl-bar').length,"
        " badBars: document.querySelectorAll('.run-timeline .tl-bad').length,"
        " badTitle: document.querySelector('.run-timeline .tl-bad').title,"
        " commandRows: document.querySelectorAll('#app .table-wrap')[0]"
        ".querySelectorAll('tbody tr').length,"
        " commandText: document.querySelectorAll('#app .table-wrap')[0].textContent,"
        " suiteRows: document.querySelectorAll('#app .table-wrap')[1]"
        ".querySelectorAll('tbody tr').length,"
        " suiteText: document.querySelectorAll('#app .table-wrap')[1].textContent,"
        " headings: Array.from(document.querySelectorAll('#app h2'), n => n.textContent),"
        " castChip: !!document.querySelector('#app .badges .chip[title=\"asciinema\"]'),"
        " castText: document.querySelectorAll('#app .table-wrap')[3].textContent,"
        " tails: Array.from("
        "document.querySelectorAll('#app details summary'), n => n.textContent).join(' ')})",
    )
    #: la chronologie trace une barre par commande et peint l'échec en rouge,
    #: avec le détail au survol
    assert run_view["bars"] == 3 and run_view["badBars"] == 1
    assert "Pytest E2E Chrome" in run_view["badTitle"]
    #: la table des commandes reprend chaque preuve de commande avec son log
    assert run_view["commandRows"] == 3
    assert "Ruff lint" in run_view["commandText"]
    assert ".proof/e2e-chrome.log" in run_view["commandText"]
    #: les trois suites JUnit (unit, e2e, symfony) sont agrégées
    assert run_view["suiteRows"] == 3
    assert "symfony" in run_view["suiteText"]
    #: la section casts du portail liste le cast généré et les fins de logs
    #: restent accessibles en repli
    assert "Casts de démonstration" in run_view["headings"]
    assert run_view["castChip"] is True
    assert "cdpx-help" in run_view["castText"]
    assert "Fins de logs" in run_view["tails"]
    #: la fin de log de la commande échouée est bien embarquée dans la vue
    assert js.evaluate(
        client,
        "document.querySelector('#app').textContent.includes("
        + json.dumps(f"FAILED {COCKPIT_FAIL_NODEID}")
        + ")",
    )

    _click(client, '#app .badges .chip[title="asciinema"]')
    cast_modal = js.evaluate(
        client,
        "({hidden: document.getElementById('artifact-modal').hidden,"
        " xterm: !!document.querySelector('.modal-content .xterm')})",
    )
    #: le cast du catalogue s'ouvre depuis la vue Run dans le player xterm
    assert cast_modal == {"hidden": False, "xterm": True}
    _close_modal(client)
    if evidence_case is not None:
        evidence_case.attach_json("vue-run", {"run": run_view, "cast_modal": cast_modal})


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["CLI surface and validation matrix render from the embedded payload."],
)
def test_cockpit_cli_and_validation_views(page, cockpit_report, evidence_case):
    """La vue CLI recense les 31 sous-commandes réelles avec leur rattachement
    feature, et la vue Validation rend matrice de milestones, couverture par
    module, risques et inconnues assumées."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/cli",
        "document.querySelector('#app h1')?.textContent === 'Surface CLI et entrypoints'",
    )
    cli_view = js.evaluate(
        client,
        "({intro: document.querySelector('#app p').textContent,"
        " rows: document.querySelectorAll('#app tbody tr').length,"
        " body: document.querySelector('#app tbody').textContent,"
        " mapped: !!document.querySelector('#app tbody a[href=\"#/features/demo-checkout\"]')})",
    )
    #: le contrat CLI (31 sous-commandes réelles, extraites de l'aide du vrai
    #: binaire) est visible tel quel dans le cockpit
    assert cli_view["rows"] == 31
    assert "31 sous-commandes" in cli_view["intro"]
    assert "cdpx goto" in cli_view["body"] and "cdpx tabs" in cli_view["body"]
    #: chaque entrypoint affiche son rattachement: lien vers la feature quand
    #: il existe, mention explicite sinon
    assert cli_view["mapped"] is True
    assert "non rattaché" in cli_view["body"]

    _goto_route(
        client,
        "/validation",
        "document.querySelector('#app h1')?.textContent === 'Matrice de validation'",
    )
    validation_view = js.evaluate(
        client,
        "({headings: Array.from(document.querySelectorAll('#app h2'), n => n.textContent),"
        " tables: document.querySelectorAll('#app .table-wrap table').length,"
        " text: document.querySelector('#app').textContent})",
    )
    #: la vue Validation aligne ses quatre volets, chacun avec sa table
    assert validation_view["headings"] == [
        "Preuve par milestone",
        "Tests par module",
        "Risques et mitigations",
        "Inconnues assumées",
    ]
    assert validation_view["tables"] == 4
    #: matrice, couverture, risques et inconnues restituent les données du run
    assert "M9" in validation_view["text"]
    assert "demo_checkout" in validation_view["text"]
    assert "make proof échoue sans binaire." in validation_view["text"]
    assert "Fixtures loopback uniquement." in validation_view["text"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "vues-cli-et-validation", {"cli": cli_view, "validation": validation_view}
        )


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="publish-proof",
    scenario_id="harness-proof-cockpit.navigate-cockpit-views",
    proves=["Project context renders and unknown routes fall back to a not-found view."],
)
def test_cockpit_project_view_and_unknown_route(page, cockpit_report, evidence_case):
    """La vue Projet rend mission, version, contexte git/environnement et les
    inventaires docs/fixtures; une route inconnue aboutit à la vue
    « Introuvable » qui cite le chemin fautif au lieu d'une page vide."""
    client, _base = page
    _open_cockpit(
        client,
        cockpit_report,
        "/project",
        "document.querySelector('#app h1')?.textContent === 'Contexte projet'",
    )
    project_view = js.evaluate(
        client,
        "({mission: document.querySelector('#app .panel').textContent,"
        " lists: Array.from("
        "document.querySelectorAll('#app .two .panel'), n => n.textContent)})",
    )
    #: le panneau mission agrège mission, version, branche git et environnement
    assert "Chrome DevTools Protocol" in project_view["mission"]
    assert "0.0-e2e" in project_view["mission"]
    assert "e2e-cockpit" in project_view["mission"]
    assert "Chrome/Chromium présent" in project_view["mission"]
    #: docs et fixtures du projet sont inventoriées en deux panneaux
    assert any("README.md" in item for item in project_view["lists"])
    assert any("tests/fixtures/index.html" in item for item in project_view["lists"])

    _goto_route(
        client,
        "/nulle-part",
        "document.querySelector('#app h1')?.textContent === 'Vue introuvable'",
    )
    not_found = js.evaluate(
        client,
        "({crumb: document.querySelector('#app .crumbs').textContent,"
        " route: document.querySelector('#app p code').textContent})",
    )
    #: la route inconnue est nommée dans la vue de repli, fil d'Ariane compris
    assert not_found["route"] == "/nulle-part"
    assert "Introuvable" in not_found["crumb"]
    if evidence_case is not None:
        evidence_case.attach_json(
            "vue-projet-et-introuvable", {"project": project_view, "not_found": not_found}
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
    res = dev.dom_diff(c, ClickAction("#submit-btn"))
    #: le diff matérialise la mutation provoquée par le clic (passage à l'état soumis)
    assert res["changed"] is True
    assert any("submitted" in line for line in res["diff"])


def test_a11y_and_frame_real(page):
    """L'arbre d'accessibilité d'une vraie page est exploitable et frame_text
    atteint le contenu à l'intérieur d'une iframe enfant."""
    c, base = page
    nav.navigate(c, f"{base}/iframe.html")
    tree = diagnostics.a11y(c)
    #: Chrome expose un arbre a11y non vide pour la page hôte
    assert tree["count"] > 0
    #: le texte lu vient bien du document enfant, pas de la page hôte
    assert frames.frame_text(c, "#child-marker")["text"] == "Contenu de l'iframe"


def test_coverage_real(page):
    """La couverture CSS mesurée sur une vraie page est cohérente: règles
    utilisées et inutilisées se répartissent exactement le total."""
    c, base = page
    res = diagnostics.coverage(c, f"{base}/coverage.html")
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
    res = interception.intercept_goto(
        c,
        f"{base}/intercept.html",
        rules=[
            "*api/status/500* => 204",
            "*api/slow* => block",
        ],
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
    res = diagnostics.vitals(c, f"{base}/vitals.html", click_selector="#inp-button", settle=1.0)
    #: les trois métriques sont toutes présentes et plausibles (jamais négatives)
    assert set(res) == {"url", "lcp", "cls", "inp"}
    assert res["lcp"] >= 0 and res["cls"] >= 0 and res["inp"] >= 0
    #: l'interaction qui alimente l'INP a réellement atteint la page
    assert js.evaluate(c, "document.body.dataset.clicked") == "1"


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="audit-seo-rendered-dom",
    scenario_id="seo-performance-accessibility.audit-rendered-seo-and-a11y",
    proves=["SEO audit surfaces edge-case findings from the rendered DOM."],
)
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


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=[
        "Record then replay reconstructs a bounded flow and halts at the first divergence.",
    ],
)
def test_record_replay_real(chrome, fixtures_http, evidence_case, tmp_path, monkeypatch):
    """Un parcours enregistré agit immédiatement puis se rejoue intégralement
    sur un onglet vierge; un journal altéré provoque une divergence détectée
    et un arrêt net au bon évènement."""
    journal = tmp_path / "session.ndjson"
    base = fixtures_http.base_url
    context = OrchestrationContext.from_origins("http://127.0.0.1:*")
    monkeypatch.setenv("FORM_NAME", "Léo")
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            recording.record(
                c,
                str(journal),
                GotoAction(f"{base}/form.html"),
                context=context,
            )
            recording.record(
                c,
                str(journal),
                TypeAction("#name", "@env:FORM_NAME", clear=True),
                context=context,
            )
            recording.record(
                c,
                str(journal),
                ClickAction("#submit-btn"),
                context=context,
            )
            #: l'enregistrement n'est pas passif: chaque étape a agi sur la page en la capturant
            assert js.get_text(c, "#result")["text"] == "OK:Léo"  # record a bien AGI
    finally:
        discovery.close_tab("127.0.0.1", chrome, tab["id"])
    # rejeu intégral sur un onglet vierge: le parcours se reconstruit seul
    tab = discovery.new_tab("127.0.0.1", chrome, "about:blank")
    try:
        with CDPClient(tab["webSocketDebuggerUrl"], timeout=15) as c:
            res = recording.replay(c, str(journal), context=context)
            #: le rejeu reconstruit seul les trois étapes et aboutit au même DOM final
            assert res["ok"] is True and res["played"] == 3
            assert js.get_text(c, "#result")["text"] == "OK:Léo"
            attach_screenshot(evidence_case, c, "replay-final")
            if evidence_case is not None:
                # Journal rejouable intact (.ndjson typé logs/internal): le
                # secret @env n'y figure jamais, seule la référence est persistée.
                evidence_case.attach_file(journal, "journal-rejouable-ndjson", "logs")
            # journal altéré (sélecteur disparu) -> divergence, arrêt net
            journal.write_text(
                journal.read_text().replace("#submit-btn", "#gone"), encoding="utf-8"
            )
            broken = recording.replay(c, str(journal), context=context)
            #: la divergence est localisée à l'évènement altéré et le rejeu
            #: s'arrête là au lieu de continuer à l'aveugle
            assert broken["ok"] is False and broken["played"] == 2
            assert broken["divergence"].startswith("event 2:")
            if evidence_case is not None:
                # Résultat de divergence: arrêt net à l'évènement altéré (played=2),
                # preuve lisible du refus de rejouer à l'aveugle après le journal cassé.
                evidence_case.attach_json("replay-divergence", broken)
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
            emulation.emulate(c, "mobile")
            result = actions.run_action(c, GotoAction(f"{fixtures_http.base_url}/index.html"))
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
            emulation.emulate(c, "mobile")
            #: le preset mobile est effectif côté page, écran et user-agent compris
            assert js.evaluate(c, "screen.width") == 390
            assert "cdpx-mobile" in js.evaluate(c, "navigator.userAgent")
            emulation.emulate(c, reset=True)
            #: --reset restaure les deux dimensions, y compris l'UA (régression historique)
            assert js.evaluate(c, "screen.width") == initial
            assert "cdpx-mobile" not in js.evaluate(c, "navigator.userAgent")
            emulation.emulate(c, "mobile")  # re-pose l'override, la connexion se ferme
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


@pytest.mark.scenario(
    feature="seo-performance-accessibility",
    journey="audit-seo-rendered-dom",
    scenario_id="seo-performance-accessibility.audit-rendered-seo-and-a11y",
    proves=["SEO audit stays clean on a healthy page and flags a broken one."],
)
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
