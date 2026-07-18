"""Le CLI de bout en bout (in-process): parsing args -> découverte -> WS ->
primitive -> JSON sur stdout + exit code. C'est le contrat vu par l'agent."""

import json
import pathlib
from pathlib import Path

import pytest

from cdpx import __version__
from cdpx.cli import _build_redaction_context, _prepare_args, build_parser, main
from cdpx.cli_context import CommandInvocation, CommandOptions, SessionArtifactPolicy
from cdpx.client import CDPTransportError
from cdpx.policy import Authority
from cdpx.primitives import nav


@pytest.fixture(autouse=True)
def managed_cli_session(cli_manifest):
    return cli_manifest


def run(mock, capsys, *argv):
    manifest = mock.cli_manifest
    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "5",
            *argv,
        ]
    )
    out = capsys.readouterr()
    return code, out.out, out.err


def test_prepare_builds_immutable_typed_invocation(cli_manifest, mock):
    """La préparation normalise les options dans un contexte explicite sans
    enrichir le Namespace argparse avec des attributs privés cachés."""
    manifest = cli_manifest
    namespace = build_parser().parse_args(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "version",
        ]
    )
    parsed_values = vars(namespace).copy()
    options = CommandOptions.from_namespace(namespace)
    invocation = CommandInvocation(options, _build_redaction_context(options))

    prepared = _prepare_args(invocation)

    assert vars(namespace) == parsed_values
    assert prepared.execution == manifest.execution_context()
    assert prepared.manifest == manifest
    assert isinstance(prepared.artifacts, SessionArtifactPolicy)


def test_command_options_convert_cli_domain_values():
    goto = CommandOptions.from_namespace(
        build_parser().parse_args(["goto", "http://demo.test/", "--wait", "none"])
    )
    storage = CommandOptions.from_namespace(
        build_parser().parse_args(["storage", "--kind", "session"])
    )
    lifecycle = CommandOptions.from_namespace(
        build_parser().parse_args(
            [
                "session",
                "start",
                "--run-id",
                "R1",
                "--authority",
                "privileged",
                "--origins",
                "http://demo.test",
            ]
        )
    )

    assert goto.wait == "none"
    assert storage.kind == "session"
    assert lifecycle.authority is Authority.PRIVILEGED


@pytest.mark.parametrize(("field", "value"), [("wait", "later"), ("kind", "memory")])
def test_command_options_reject_invalid_domain_values(field, value):
    namespace = build_parser().parse_args(["version"])
    setattr(namespace, field, value)

    with pytest.raises(RuntimeError, match="invalid CLI"):
        CommandOptions.from_namespace(namespace)


def test_tabs_list(mock, capsys):
    """L'inventaire d'onglets restitue en JSON la cible supervisée unique,
    identifiable par l'agent (type page + id), avec un exit succès."""
    code, out, _ = run(mock, capsys, "tabs", "list")
    #: le contrat stdout est tenu: exit 0 et un objet JSON parseable
    assert code == 0
    payload = json.loads(out)
    #: la session supervisée n'expose qu'une page, avec de quoi la cibler
    assert payload["count"] == 1
    assert payload["tabs"][0]["type"] == "page" and "id" in payload["tabs"][0]


@pytest.mark.parametrize("action", ["new", "activate", "close"])
def test_tabs_lifecycle_actions_are_absent(action):
    """Les actions de cycle de vie d'onglet (new/activate/close) ont été
    retirées du CLI: argparse les rejette avant toute connexion."""
    #: la sous-commande retirée échoue au parsing, sans toucher CDP
    with pytest.raises(SystemExit) as exc:
        main(["tabs", action])
    #: exit 2 = erreur d'usage, pas une erreur runtime déguisée
    assert exc.value.code == 2


def test_goto(mock, capsys):
    """Une navigation réussie renvoie ok + l'évènement attendu en JSON avec
    exit 0: le signal minimal dont l'agent a besoin pour enchaîner."""
    code, out, _ = run(mock, capsys, "goto", "http://site.test/")
    data = json.loads(out)
    #: la sortie dit explicitement que load a été atteint, pas juste "ok"
    assert code == 0 and data["ok"] is True and data["waited"] == "load"


@pytest.mark.scenario(
    feature="browser-navigation",
    journey="open-page",
    scenario_id="browser-navigation.open-page-success",
    proves=["A CDP navigation error is surfaced as runtime exit 1."],
)
def test_goto_error_result_exits_1(mock, capsys, monkeypatch):
    """Un échec de navigation CDP (errorText) devient exit 1 avec le motif
    sur stderr, au lieu d'un JSON trompeusement vert sur stdout."""

    def fail_navigation(*_args, **_kwargs):
        raise nav.NavigationError(
            {
                "url": "http://bad.test",
                "ok": False,
                "errorText": "ERR_NAME_NOT_RESOLVED",
            }
        )

    monkeypatch.setattr("cdpx.commands.navigation.nav.navigate", fail_navigation)
    code, _, err = run(mock, capsys, "goto", "http://bad.test")
    #: l'erreur réseau remonte comme échec runtime diagnostiqué sur stderr
    assert code == 1 and "ERR_NAME_NOT_RESOLVED" in err


def test_transport_failure_exits_1_instead_of_returning_partial_success(mock, capsys, monkeypatch):
    def fail_transport(*_args, **_kwargs):
        raise CDPTransportError("transport interrompu pendant collecte")

    monkeypatch.setattr("cdpx.commands.navigation.nav.navigate", fail_transport)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "transport interrompu" in err


def test_connection_failure_exits_1_with_transport_diagnostic(mock, capsys, monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise CDPTransportError("connexion CDP impossible vers le target")

    monkeypatch.setattr("cdpx.commands.shared.CDPClient", fail_connect)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "connexion CDP impossible" in err


def test_send_failure_exits_1_with_transport_diagnostic(mock, capsys, monkeypatch):
    def fail_send(*_args, **_kwargs):
        raise CDPTransportError("transport interrompu pendant envoi Page.enable")

    monkeypatch.setattr("cdpx.client.CDPClient.send", fail_send)

    code, out, err = run(mock, capsys, "goto", "http://site.test/")

    assert code == 1
    assert out == ""
    assert "envoi Page.enable" in err


def test_eval(mock, capsys):
    """eval restitue la valeur JS calculée dans la page et l'étiquette comme
    contenu non fiable, distinct des données produites par le harnais."""
    mock.on_eval("6 * 7", 42)
    code, out, _ = run(mock, capsys, "eval", "6 * 7")
    payload = json.loads(out)
    #: la valeur évaluée côté page revient telle quelle à l'agent
    assert code == 0 and payload["value"] == 42
    #: tout contenu issu de la page porte le marqueur untrusted
    assert payload["_cdpx"]["content_trust"] == "untrusted"


def test_pretty_output_is_explicit(mock, capsys):
    """L'indentation du JSON est opt-in: le CLI reste compact par défaut
    pour les agents, et n'indente que sur demande explicite --pretty."""
    manifest = mock.cli_manifest
    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--pretty",
            "eval",
            "1",
        ]
    )
    out = capsys.readouterr().out
    #: le flag produit un JSON multi-lignes, preuve qu'il a bien agi
    assert code == 0
    assert out.startswith("{\n")


def test_agent_output_bounds_large_lists(mock, capsys):
    """--limit borne les listes volumineuses sans perte silencieuse: la
    troncature et le total réel sont annoncés, l'agent sait ce qui manque."""
    events = []
    for i in range(3):
        events.append(
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": f"R{i}",
                    "type": "Fetch",
                    "request": {"url": f"http://s.test/{i}", "method": "GET"},
                },
            }
        )
    mock.script_network(events)
    code, out, _ = run(mock, capsys, "--limit", "2", "network", "http://s.test/")
    data = json.loads(out)
    #: la liste est coupée à la limite demandée, et la sortie avoue la
    #: coupe en donnant le décompte réel des requêtes observées
    assert code == 0
    assert len(data["requests"]) == 2
    assert data["requests_truncated"] is True
    assert data["requests_total"] == 3


def test_console_follow_outputs_compact_ndjson(mock, capsys):
    """console --follow émet un objet NDJSON compact par message, dans
    l'ordre d'arrivée, chaque ligne étant marquée contenu non fiable."""
    mock.script_console(
        [
            {
                "type": "log",
                "args": [{"type": "string", "value": "one"}],
                "timestamp": 1.0,
            },
            {
                "type": "error",
                "args": [{"type": "string", "value": "two"}],
                "timestamp": 2.0,
            },
        ]
    )
    code, out, _ = run(mock, capsys, "console", "--follow", "--max", "2")
    lines = [json.loads(line) for line in out.splitlines()]
    #: chaque ligne stdout est un objet autonome, restitué dans l'ordre
    #: d'émission des messages console
    assert code == 0
    assert [(item["type"], item["text"]) for item in lines] == [
        ("log", "one"),
        ("error", "two"),
    ]
    #: les messages viennent de la page: tous étiquetés untrusted
    assert all(item["_cdpx"]["content_trust"] == "untrusted" for item in lines)


def test_seo_with_navigation(mock, capsys):
    """seo <url> navigue d'abord vers la page à auditer puis l'analyse: une
    page SEO complète ne produit aucun finding parasite."""
    payload = {
        "url": "u",
        "lang": "fr",
        "title": "T",
        "metas": {"description": "d"},
        "canonical": "c",
        "robots": None,
        "h1": ["H"],
        "hreflang": [],
        "jsonld": [],
        "images_without_alt": 0,
        "links": {"internal": 0, "external": 0, "nofollow": 0},
    }
    mock.on_eval("__cdpx_seo", json.dumps(payload))
    code, out, _ = run(mock, capsys, "seo", "http://site.test/seo.html")
    data = json.loads(out)
    #: l'audit d'une page saine reste muet: pas de faux positifs
    assert code == 0 and data["findings"] == []
    #: la navigation vers l'URL d'audit a réellement été émise au protocole
    assert mock.commands_for("Page.navigate") == [{"url": "http://site.test/seo.html"}]


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["La valeur de cookie est masquée par défaut: aucun secret de session ne fuit."],
)
def test_cookies_masked_output(mock, capsys, evidence_case):
    """Les valeurs de cookies sont masquées par défaut dans la sortie:
    aucun secret de session ne fuit vers le transcript de l'agent."""
    code, out, _ = run(mock, capsys, "cookies", "get")
    data = json.loads(out)
    #: la valeur secrète n'apparaît que sous sa forme masquée
    assert code == 0 and data["cookies"][0]["value"] == "***"
    # preuve secondaire: la sortie déjà masquée alimente le cockpit sans exposer le canari
    if evidence_case is not None:
        evidence_case.attach_command_output(
            "cookies get (valeurs masquées)",
            ["cdpx", "cookies", "get"],
            out,
            "",
            code,
        )


@pytest.mark.parametrize(
    "argv",
    [
        ("cookies", "set", "--name", "flag"),
        ("cookies", "get", "--url", "http://x.test/"),
    ],
)
@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Invalid conditional arguments fail with usage exit 2 before CDP."],
)
def test_conditional_cli_arguments_exit_2_before_discovery(mock, capsys, argv):
    """Une combinaison d'arguments invalide est tranchée en erreur d'usage
    (exit 2) avec un motif explicite, avant toute découverte ou commande CDP."""
    code, _, err = run(mock, capsys, *argv)
    #: l'usage invalide sort en 2 avec un diagnostic actionnable
    assert code == 2 and ("required" in err or "not supported" in err)
    #: le refus précède le protocole: rien n'a été émis vers Chrome
    assert mock.commands == []


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Mutating command variants cannot bypass the configured origin guard."],
)
def test_cookie_mutations_and_vitals_click_use_origin_guard(mock, capsys, monkeypatch):
    """Les variantes mutantes détournées (cookies set --url, vitals --click)
    passent par le garde d'origine: hors liste, elles sont refusées."""
    monkeypatch.setenv("COOKIE_FLAG", "1")
    for argv in (
        (
            "cookies",
            "set",
            "--name",
            "flag",
            "--value-env",
            "COOKIE_FLAG",
            "--url",
            "https://prod.example/",
        ),
        ("vitals", "https://prod.example/", "--click", "#go"),
    ):
        code, _, err = run(mock, capsys, *argv)
        #: chaque variante mutante est refusée avec le motif d'origine,
        #: aucune ne contourne le garde configuré
        assert code == 1 and "origin rejected" in err


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="intercept-network",
    scenario_id="orchestration-control.intercept-network-request",
    proves=["Le garde d'origine juge la destination du goto composé, pas l'onglet initial."],
)
def test_intercept_checks_destination_origin_not_initial_tab(mock, capsys, monkeypatch):
    """Le garde d'origine d'intercept juge l'URL de destination du goto
    composé, pas l'onglet initial: un onglet permis ne blanchit pas une
    navigation vers une origine interdite."""
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    tid = next(iter(mock.targets))
    mock.targets[tid]["url"] = "http://allowed.test/"
    code, _, err = run(
        mock,
        capsys,
        "intercept",
        "--rule",
        "* => block",
        "--",
        "goto",
        "https://prod.example/",
    )
    #: la destination interdite est refusée malgré l'onglet permis, et
    #: le refus tombe avant la moindre commande CDP
    assert code == 1 and "origin rejected" in err
    assert mock.commands == []


@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["A mutating click on a disallowed origin is refused before the input protocol."],
)
def test_origin_guard_blocks_cli_mutation(mock, capsys, monkeypatch):
    """Une mutation (click) visant une origine non autorisée est refusée
    avant d'atteindre le protocole d'entrée: la page reste intouchée."""
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    code, _, err = run(mock, capsys, "click", "#submit")
    #: le refus est une erreur runtime motivée sur stderr
    assert code == 1
    assert "origin rejected" in err
    #: aucun évènement souris n'a été dispatché vers la page
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_origin_guard_blocks_dom_diff(mock, capsys, monkeypatch):
    """dom-diff exécute une vraie action mutante: l'enveloppe subit le même
    garde d'origine que la mutation qu'elle transporte."""
    # dom-diff exécute de vraies mutations (click/type/key/eval): même garde que click.
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    code, _, err = run(mock, capsys, "dom-diff", "--", "click", "#x")
    #: envelopper la mutation dans dom-diff n'offre aucun contournement
    assert code == 1
    assert "origin rejected" in err
    #: le click enveloppé n'a jamais atteint la page
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_origin_guard_allows_dom_diff_on_allowed_origin(mock, capsys, monkeypatch):
    """Sur une origine explicitement permise, dom-diff exécute l'action
    enveloppée et rapporte la mutation du DOM observée."""
    monkeypatch.setenv("CDPX_ORIGINS", "http://*.test")
    tid = next(iter(mock.targets))
    mock.targets[tid]["url"] = "http://demo.test/page"
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]), json.dumps(["<body>", "  <p>"]))
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    code, out, _ = run(mock, capsys, "dom-diff", "--", "click", "#x")
    #: le garde laisse passer l'origine listée et le diff voit le changement
    assert code == 0 and json.loads(out)["changed"] is True


def test_screenshot(mock, capsys, tmp_path):
    """screenshot capture en png via le protocole et range le fichier dans
    les artefacts supervisés de la session, pas au chemin brut demandé."""
    dest = tmp_path / "s.png"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest))
    data = json.loads(out)
    #: le fichier vit dans les artefacts supervisés; le chemin brut -o
    #: n'est jamais écrit directement
    assert code == 0 and Path(data["path"]).exists() and not dest.exists()
    #: le protocole a bien reçu une demande de capture png
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "png"


def test_screenshot_format_jpeg(mock, capsys, tmp_path):
    """--format jpeg se propage jusqu'à la commande CDP de capture et
    jusqu'au champ format de la sortie JSON."""
    dest = tmp_path / "s.jpg"
    code, out, _ = run(mock, capsys, "screenshot", "-o", str(dest), "--format", "jpeg")
    data = json.loads(out)
    #: le format demandé est reflété dans la sortie et le fichier écrit
    assert code == 0 and Path(data["path"]).exists() and data["format"] == "jpeg"
    #: la capture CDP a été émise en jpeg, pas avec le png par défaut
    assert mock.commands_for("Page.captureScreenshot")[0]["format"] == "jpeg"


RECT = json.dumps({"x": 0, "y": 0, "width": 10, "height": 10})

# Filet de dispatch: chaque sous-commande traverse argparse -> _client -> primitive
# -> JSON stdout, et émet au moins sa commande CDP signature (contrat protocole).
# Format: (id, argv, règles on_eval, méthode CDP attendue, prédicat sur la sortie)
DISPATCH_CASES = [
    ("wait", ["wait", "#late"], {"querySelector": True}, "Runtime.evaluate", lambda d: d["found"]),
    (
        "text",
        ["text"],
        {"innerText": "Bonjour"},
        "Runtime.evaluate",
        lambda d: d["text"] == "Bonjour",
    ),
    (
        "html",
        ["html", "#x"],
        {"outerHTML": "<b>x</b>"},
        "Runtime.evaluate",
        lambda d: d["html"] == "<b>x</b>",
    ),
    (
        "count",
        ["count", ".item"],
        {"querySelectorAll": 3},
        "Runtime.evaluate",
        lambda d: d["count"] == 3,
    ),
    (
        "click",
        ["click", "#go"],
        {"getBoundingClientRect": RECT},
        "Input.dispatchMouseEvent",
        lambda d: d["clicked"] == "#go",
    ),
    (
        "type",
        ["type", "#name", "--secret-env", "CLI_TEXT"],
        {"focus": True},
        "Input.insertText",
        lambda d: d["typed"] is True and d["value_masked"] is True,
    ),
    ("key", ["key", "Enter"], {}, "Input.dispatchKeyEvent", lambda d: d["pressed"] == "Enter"),
    (
        "network",
        ["network", "http://s.test/", "--settle", "0.1"],
        {},
        "Page.navigate",
        lambda d: "summary" in d,
    ),
    ("storage", ["storage"], {"localStorage": "{}"}, "Runtime.evaluate", lambda d: d["count"] == 0),
    ("metrics", ["metrics"], {}, "Performance.getMetrics", lambda d: d["Nodes"] == 42),
    ("a11y", ["a11y"], {}, "Accessibility.getFullAXTree", lambda d: d["count"] == 2),
    (
        "coverage",
        ["coverage", "http://s.test/"],
        {},
        "Profiler.startPreciseCoverage",
        lambda d: d["css"]["rules"] == 2,
    ),
    (
        "frame",
        ["frame", "#m"],
        {"contentDocument": "texte iframe"},
        "Runtime.evaluate",
        lambda d: d["text"] == "texte iframe",
    ),
    (
        "vitals",
        ["vitals", "http://s.test/", "--settle", "0.1"],
        {"__cdpxVitals": json.dumps({"lcp": 1, "cls": 0, "inp": 0})},
        "Page.addScriptToEvaluateOnNewDocument",
        lambda d: d["lcp"] == 1,
    ),
    (
        "emulate",
        ["emulate", "slow-3g"],
        {},
        "Network.emulateNetworkConditions",
        lambda d: d["applied"] is True,
    ),
    (
        "dom-diff",
        ["dom-diff", "--", "eval", "1 + 1"],
        {"__cdpx_dom_snapshot": json.dumps(["<body>"])},
        "Runtime.evaluate",
        lambda d: d["changed"] is False,
    ),
]


@pytest.mark.parametrize(
    "case_id,argv,rules,method,check", DISPATCH_CASES, ids=[c[0] for c in DISPATCH_CASES]
)
@pytest.mark.scenario(
    feature="harness-proof-cockpit",
    journey="run-quality-gate",
    scenario_id="harness-proof-cockpit.run-local-quality-gate",
    proves=["Each catalog subcommand reaches its primitive and emits its signature CDP command."],
)
def test_cli_dispatch_emits_protocol_and_json(
    mock, capsys, monkeypatch, case_id, argv, rules, method, check
):
    """Filet de dispatch: chaque sous-commande du catalogue traverse
    argparse -> client -> primitive -> JSON stdout, et émet au moins sa
    commande CDP signature (le protocole attendu EST la spec)."""
    monkeypatch.setenv("CLI_TEXT", "Léo")
    for substring, value in rules.items():
        mock.on_eval(substring, value)
    code, out, err = run(mock, capsys, *argv)
    #: la sous-commande aboutit; stderr est joint au diagnostic sinon
    assert code == 0, f"{case_id}: exit {code}, stderr={err}"
    data = json.loads(out)
    #: la sortie JSON porte la donnée signature attendue pour ce cas
    assert check(data), f"{case_id}: sortie inattendue {data}"
    if method:
        #: la commande CDP signature du cas a réellement été émise
        assert mock.commands_for(method), f"{case_id}: {method} jamais émis"


def test_pdf_cli_writes_valid_signature(mock, capsys, tmp_path, evidence_case):
    """pdf produit un vrai document (signature %PDF) via la commande CDP
    d'impression, rangé dans les artefacts supervisés de la session."""
    dest = tmp_path / "page.pdf"
    code, out, _ = run(mock, capsys, "pdf", "-o", str(dest))
    data = json.loads(out)
    #: la sortie annonce un contenu non vide, pas un fichier fantôme
    assert code == 0 and data["bytes"] > 0
    #: le fichier écrit est un PDF réel et vit dans les artefacts
    #: supervisés, jamais au chemin brut -o
    assert Path(data["path"]).read_bytes().startswith(b"%PDF") and not dest.exists()
    #: l'impression est passée par le protocole, pas par un raccourci
    assert mock.commands_for("Page.printToPDF")
    # preuve secondaire: le PDF binaire (opaque, non inliné) + un résumé lisible dans le modal
    if evidence_case is not None:
        evidence_case.attach_file(data["path"], "PDF imprimé (signature %PDF)")
        evidence_case.attach_json(
            "Signature PDF observée",
            {
                "signature": "%PDF",
                "bytes": data["bytes"],
                "artifact_basename": Path(data["path"]).name,
            },
        )


def test_dom_diff_accepts_action_with_or_without_separator(mock, capsys):
    """dom-diff accepte l'action composée avec ou sans `--`, et masque les
    arguments de l'action dans la sortie (l'expression peut être secrète)."""
    mock.on_eval("__cdpx_dom_snapshot", json.dumps(["<body>"]))
    mock.on_eval("2 + 2", 4)
    code, out, _ = run(mock, capsys, "dom-diff", "eval", "2 + 2")
    #: sans séparateur, l'action passe et ses arguments sont masqués
    assert code == 0 and json.loads(out)["action"] == ["eval", "***"]
    code, out, _ = run(mock, capsys, "dom-diff", "--", "eval", "2 + 2")
    #: avec `--`, même contrat: le séparateur ne change rien au résultat
    assert code == 0 and json.loads(out)["action"] == ["eval", "***"]


def test_profiler_cli_panels_flag(mock, capsys):
    """--panels db récupère et résume le panel Doctrine du profiler Symfony
    sans jamais exposer le token de debug ni les champs du mode global."""
    db_html = (pathlib.Path(__file__).parent / "fixtures" / "profiler" / "db.html").read_text(
        encoding="utf-8"
    )
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://s.test/",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": "http://s.test/_profiler/tok"},
                    },
                },
            }
        ]
    )
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps([{"panel": "db", "status": 200, "html": db_html}]),
    )
    mock.on_eval("window.location.href", "http://s.test/")
    code, out, _ = run(
        mock, capsys, "profiler", "http://s.test/", "--settle", "0.05", "--panels", "db"
    )
    data = json.loads(out)
    assert code == 0
    #: le token est signalé présent mais sa valeur secrète ne fuit jamais
    assert data["token_present"] is True and "token" not in data
    #: le panel demandé est parsé jusqu'au décompte des requêtes SQL
    assert data["panels"]["db"]["queries"] == 6
    #: le mode panels reste ciblé: pas d'analyse globale embarquée
    assert "signals" not in data and "profiler_bytes" not in data


def test_profiler_cli_unknown_panel_is_usage_error(mock, capsys):
    """Un panel de profiler inexistant est rejeté en erreur d'usage avec un
    message nommant le problème, avant toute navigation."""
    #: le panel inconnu échoue au parsing en exit 2
    with pytest.raises(SystemExit) as exc:
        run(mock, capsys, "profiler", "http://s.test/", "--panels", "doctrine")
    assert exc.value.code == 2
    #: le diagnostic nomme la cause pour corriger l'invocation
    assert "unknown panel(s)" in capsys.readouterr().err


def test_intercept_multiple_rules_and_invalid_action(mock, capsys):
    """intercept applique plusieurs règles simultanées (la règle qui matche
    répond) et refuse toute action composée autre que goto avant d'armer
    la moindre interception."""
    mock.script_network(
        [
            {
                "method": "Fetch.requestPaused",
                "params": {
                    "requestId": "P1",
                    "request": {"url": "http://s.test/api/x"},
                },
            }
        ]
    )
    code, out, _ = run(
        mock,
        capsys,
        "intercept",
        "--rule",
        "*api* => 503",
        "--rule",
        "*img* => block",
        "--settle",
        "0.1",
        "--",
        "goto",
        "http://s.test/",
    )
    data = json.loads(out)
    #: les deux règles sont armées et la requête api interceptée a
    #: réellement reçu le 503 promis via le protocole Fetch
    assert code == 0 and len(data["rules"]) == 2
    assert mock.commands_for("Fetch.fulfillRequest")[0]["responseCode"] == 503
    # action non-goto: erreur d'usage AVANT toute commande Fetch
    mock.commands.clear()
    code, _, err = run(mock, capsys, "intercept", "--rule", "*x* => block", "--", "click", "#x")
    #: l'action non supportée est refusée sans émettre de commande
    assert code == 1 and "intercept supports" in err and mock.commands == []


def test_emulate_requires_preset_or_reset(mock, capsys):
    """emulate sans preset ni --reset échoue avec un motif nommé: pas
    d'émulation implicite silencieuse."""
    code, _, err = run(mock, capsys, "emulate")
    #: l'absence de preset est un échec runtime explicite, pas un no-op
    assert code == 1 and "unknown preset" in err


def test_record_cli_executes_and_journals(mock, capsys, tmp_path):
    """record exécute réellement l'action composée et journalise chaque
    évènement en NDJSON rejouable, sans laisser fuir le séparateur `--`."""
    journal = tmp_path / "j.ndjson"
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    data = json.loads(out)
    #: l'action a tourné et exactement un évènement a été journalisé
    assert code == 0 and data["ok"] is True and data["recorded"] == 1
    #: la navigation enregistrée a réellement été émise au protocole
    assert mock.commands_for("Page.navigate") == [{"url": "http://a.test/"}]
    event = json.loads(Path(data["path"]).read_text().splitlines()[0])
    #: le journal capture l'action nettoyée, rejouable telle quelle
    assert event["action"] == ["goto", "http://a.test/"]  # le `--` ne fuit pas dans le journal


def test_replay_cli_divergence_exits_1_with_json(mock, capsys, tmp_path, evidence_case):
    """Un replay qui diverge (sélecteur disparu) sort en 1 tout en gardant
    un JSON structuré qui localise l'évènement fautif."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["click","#gone"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    mock.on_eval("getBoundingClientRect", None)
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    #: la divergence est une erreur d'exécution, pas une erreur d'usage
    assert code == 1  # divergence = erreur d'exécution, JSON structuré conservé
    #: le JSON survit à l'échec et pointe l'évènement divergent
    assert data["ok"] is False and data["divergence"].startswith("event 0:")
    # preuve secondaire: le JSON de divergence structuré (event 0:) illustre le contrat replay
    if evidence_case is not None:
        evidence_case.attach_command_output(
            "replay divergent (exit 1, event 0:)",
            ["cdpx", "replay", journal.name],
            out,
            "",
            code,
        )


def test_replay_cli_green_journal_exits_0(mock, capsys, tmp_path):
    """Un journal rejoué sans divergence sort en 0 avec le décompte complet
    des évènements joués: la preuve du rejeu est chiffrée."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    code, out, _ = run(mock, capsys, "replay", str(journal))
    data = json.loads(out)
    #: le rejeu vert identifie le journal source dans sa sortie
    assert code == 0 and data["path"] == str(journal)
    #: tous les évènements ont été joués, aucun sauté silencieusement
    assert data["events"] == 1 and data["played"] == 1 and data["ok"] is True


def test_emulate_composed_action_runs_in_same_connection(mock, capsys):
    """emulate <preset> -- <action> pose les overrides puis joue l'action
    dans la même connexion WS: l'action voit l'émulation active (les
    overrides meurent à la déconnexion)."""
    code, out, _ = run(mock, capsys, "emulate", "mobile", "--", "goto", "http://a.test/")
    data = json.loads(out)
    #: l'émulation et l'action composée réussissent toutes les deux
    assert code == 0 and data["applied"] is True
    assert data["action"]["result"]["ok"] is True
    # le preset est posé AVANT l'action, dans la même connexion
    methods = [m for (_t, m, _p) in mock.commands]
    #: l'ordre protocole prouve que le preset précède la navigation,
    #: donc que la page chargée subit bien l'émulation
    assert methods.index("Emulation.setDeviceMetricsOverride") < methods.index("Page.navigate")


def test_origin_guard_composed_commands_follow_action_verb(mock, capsys, monkeypatch, tmp_path):
    """Le garde d'origine des commandes composées (record/replay) juge le
    verbe de l'action enveloppée: mutation refusée, lecture permise, et le
    rejeu est gardé séquentiellement plutôt que sur l'onglet initial."""
    journal = Path(mock.cli_manifest.artifacts_dir) / "journals" / "j.ndjson"
    target = next(iter(mock.targets))
    mock.targets[target]["url"] = "https://prod.example/"
    # record avec verbe mutant: refusé (aucune commande CDP émise)
    code, _, err = run(mock, capsys, "record", "-o", str(journal), "--", "click", "#x")
    #: le verbe mutant enveloppé est refusé avant d'atteindre la page
    assert code == 1 and "origin rejected" in err
    assert mock.commands_for("Input.dispatchMouseEvent") == []
    # replay est gardé séquentiellement: une navigation de lecture vers une
    # origine permise n'est plus refusée à cause de l'onglet initial about:blank.
    journal.parent.mkdir(parents=True, mode=0o700)
    journal.write_text('{"action":["goto","http://a.test/"],"ok":true}\n', encoding="utf-8")
    journal.chmod(0o600)
    mock.on_eval("window.location.href", "http://a.test/")
    code, out, err = run(mock, capsys, "replay", str(journal))
    #: la navigation de lecture rejouée passe malgré l'onglet initial
    #: about:blank: le garde suit la séquence, pas l'état de départ
    assert code == 0 and json.loads(out)["ok"] is True and not err
    # record avec verbe de lecture: permis même hors liste
    code, out, _ = run(mock, capsys, "record", "-o", str(journal), "--", "goto", "http://a.test/")
    #: un verbe de lecture n'exige pas d'origine listée pour record
    assert code == 0 and json.loads(out)["ok"] is True


def test_error_path_exit_code_and_stderr(mock, capsys):
    """Une exception JS levée dans la page devient exit 1 avec le message
    d'erreur sur stderr, stdout restant réservé au JSON."""
    mock.on_eval("kaboom", {"raw": {"exceptionDetails": {"text": "TypeError: kaboom"}}})
    code, _, err = run(mock, capsys, "eval", "kaboom()")
    #: l'exception de la page remonte en échec runtime diagnostiqué
    assert code == 1 and "kaboom" in err


def test_missing_session_fails_before_discovery(capsys, monkeypatch):
    """Sans session supervisée (variables CDPX_* absentes), le CLI échoue en
    erreur d'usage nommant la variable manquante, avant toute découverte."""
    for name in ("CDPX_SESSION", "CDPX_RUN_ID", "CDPX_TARGET"):
        monkeypatch.delenv(name, raising=False)
    code = main(["tabs", "list"])
    err = capsys.readouterr().err
    #: l'absence de session est un exit 2 qui dit quoi exporter
    assert code == 2 and "CDPX_SESSION" in err


def test_invalid_action_argv_without_session_stays_usage_error(capsys, monkeypatch):
    """Un argv d'action invalide ne court-circuite pas le diagnostic de
    session: la redaction se construit sans parser l'action, et l'absence
    d'identité reste une erreur d'usage propre, jamais un traceback."""
    for name in ("CDPX_SESSION", "CDPX_RUN_ID", "CDPX_TARGET"):
        monkeypatch.delenv(name, raising=False)
    code = main(["dom-diff", "--", "bogus", "x"])
    captured = capsys.readouterr()
    #: l'identité manquante prime sur l'action illisible: exit 2 documenté
    assert code == 2 and "CDPX_SESSION" in captured.err
    #: le diagnostic reste un message cdpx, pas un ValueError brut
    assert "Traceback" not in captured.err and captured.out == ""


def test_invalid_action_argv_with_session_is_diagnosed(mock, capsys):
    """Avec une session valide, un argv d'action inconnu échoue au préflight
    en erreur diagnostiquée sur stderr, stdout restant vide."""
    code, out, err = run(mock, capsys, "dom-diff", "--", "bogus", "x")
    #: le préflight rejette l'action inconnue avec son usage, exit 1
    assert code == 1 and "cdpx:" in err and "action" in err
    #: pas de traceback brut ni de JSON trompeur sur stdout
    assert "Traceback" not in err and out == ""


@pytest.mark.parametrize("option", ["--host", "--port"])
def test_direct_connection_options_are_removed(option):
    """Les options de connexion directe (--host/--port) ont disparu du CLI:
    seule la session supervisée peut désigner le Chrome cible."""
    #: l'option retirée est rejetée au parsing comme argument inconnu
    with pytest.raises(SystemExit) as exc:
        main([option, "1", "tabs", "list"])
    #: exit 2 confirme que la connexion directe n'existe plus
    assert exc.value.code == 2


def test_usage_error_exit_2():
    """Un argument positionnel manquant est tranché par argparse en exit 2,
    distinct des erreurs runtime (exit 1) du contrat CLI."""
    #: goto sans url échoue au parsing, avant toute connexion
    with pytest.raises(SystemExit) as exc:
        main(["goto"])  # url manquante
    assert exc.value.code == 2


def test_cdpx_version(capsys):
    """--version imprime exactement `cdpx <version>` et sort en 0: le numéro
    vient de la source unique __version__ du paquet."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    #: demander la version est un succès, pas une erreur d'usage
    assert exc.value.code == 0
    #: la sortie reflète la version unique du paquet, rien d'autre
    assert capsys.readouterr().out == f"cdpx {__version__}\n"
