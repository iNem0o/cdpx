import json
import stat
from pathlib import Path

import pytest

from cdpx import discovery, scenarios
from cdpx.artifacts import scan_canaries
from cdpx.cli import main
from cdpx.client import CDPClient
from cdpx.primitives import profiler_panels


def client_for(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://shop.test/"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


def test_parse_scenario_with_step_capture():
    """Le parseur transforme un scénario déclaratif complet en objet typé qui
    préserve le contexte d'émulation et les captures attachées à chaque étape."""
    scenario = scenarios.parse(
        {
            "name": "checkout_guest_add_to_cart",
            "context": {"base_url": "http://shop.localhost", "emulation": "mobile"},
            "steps": [
                {
                    "label": "product",
                    "goto": "/produit/42",
                    "capture": ["screenshot", "console"],
                },
                {"wait_text": ["#count", "1"]},
            ],
            "assertions": [{"text_contains": ["#count", "1"]}],
            "artifacts": ["network"],
        }
    )

    #: le nom, l'émulation du contexte et les captures par étape survivent
    #: au parsing sans perte ni valeur par défaut écrasante
    assert scenario.name == "checkout_guest_add_to_cart"
    assert scenario.emulation == "mobile"
    assert scenario.steps[0].capture == ["screenshot", "console"]


def test_parse_rejects_unknown_field():
    """Une clé inconnue dans le YAML est une erreur d'usage dès le parsing:
    une faute de frappe ne peut pas désactiver silencieusement une étape ou
    une assertion."""
    #: le rejet nomme le champ fautif avant tout contact avec un navigateur
    with pytest.raises(scenarios.ScenarioUsageError, match="champ"):
        scenarios.parse(
            {
                "name": "bad",
                "context": {"base_url": "http://x.test"},
                "steps": [{"goto": "/"}],
                "unexpected": True,
            }
        )


def test_run_scenario_happy_path_with_checkpoint_artifacts(mock, tmp_path):
    """Un scénario nominal (goto, click, wait_text) passe sur le mock et
    matérialise sur disque les captures de checkpoint puis les artefacts
    finaux, dans l'ordre déclaré."""
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    mock.on_eval("innerText", "0", "1", "1")
    mock.on_eval("querySelector", True)
    scenario = scenarios.parse(
        {
            "name": "cart",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {"label": "product", "goto": "/product", "capture": ["screenshot", "network"]},
                {"label": "add", "click": "#add", "capture": ["console"]},
                {"label": "cart_count", "wait_text": ["#cart-count", "1"]},
            ],
            "assertions": [
                {"no_console_errors": True},
                {"network_errors_max": 0},
                {"text_contains": ["#cart-count", "1"]},
            ],
            "artifacts": ["screenshot", "console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    #: verdict pass sans aucun finding: les trois étapes et les trois
    #: assertions d'observabilité ont toutes abouti
    assert result["verdict"] == "pass"
    assert result["findings"] == []
    assert len(result["steps"]) == 3
    artifact_types = [artifact["type"] for artifact in result["artifacts"]]
    #: les captures de checkpoint précèdent les artefacts finaux, dans
    #: l'ordre où le scénario les a demandés
    assert artifact_types == [
        "screenshot",
        "network",
        "console",
        "screenshot",
        "console",
        "network",
    ]
    #: chaque artefact annoncé dans le résultat existe réellement sur disque
    assert all(Path(artifact["path"]).exists() for artifact in result["artifacts"])


def test_scenario_wait_visible_requires_visibility_not_only_dom_attachment(mock, tmp_path):
    """wait_visible ne se satisfait pas d'un élément attaché au DOM: il
    re-sonde la page jusqu'à ce que la visibilité soit effectivement acquise."""
    mock.on_eval("__cdpx_visible", False, True)
    mock.on_eval("querySelector", True)
    scenario = scenarios.parse(
        {
            "name": "visible",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"wait_visible": "#revealed"}],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            timeout=0.5,
            settle=0,
            origins="http://*.test",
        )

    #: l'étape n'aboutit qu'une fois la visibilité réellement constatée
    assert result["verdict"] == "pass"
    assert result["steps"][0]["result"]["visible"] is True
    visibility_checks = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_visible" in item["expression"]
    ]
    #: deux sondes de visibilité ont été émises: la première réponse
    #: (invisible) a bien forcé une nouvelle tentative au lieu de conclure
    assert len(visibility_checks) == 2


def test_run_scenario_profiler_artifact_parses_real_panels(mock, tmp_path):
    """L'artefact profiler suit le lien X-Debug-Token du réseau, lit les
    panneaux Symfony réels (fixtures HTML) et n'en persiste que des métriques
    structurées — jamais le token lui-même."""
    fixtures = Path(__file__).parent / "fixtures" / "profiler"
    mock.on_eval("window.location.href", "http://shop.test/")
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://shop.test/",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": "http://shop.test/_profiler/fixed-token"},
                    },
                },
            }
        ]
    )
    payload = json.dumps(
        [
            {
                "panel": key,
                "status": 200,
                "html": (fixtures / f"{profiler_panels.PANEL_SOURCES[key]}.html").read_text(
                    encoding="utf-8"
                ),
            }
            for key in profiler_panels.ALL_PANELS
        ]
    )
    mock.on_eval("__cdpx_profiler_panels", payload)
    scenario = scenarios.parse(
        {
            "name": "profiler_capture",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["profiler"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    assert result["verdict"] == "pass"
    (artifact,) = [a for a in result["artifacts"] if a["type"] == "profiler"]
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    #: la preuve atteste qu'un token existait sans jamais en écrire la valeur
    assert data["token_present"] is True
    assert "token" not in data
    #: les panneaux HTML bruts sont réduits à des métriques exploitables
    #: (nombre de requêtes SQL, route résolue)
    assert data["panels"]["db"]["queries"] == 6
    assert data["panels"]["router"]["route"] == "scenario_profiler"
    #: aucun champ hors contrat ne fuit dans l'artefact persisté
    assert "signals" not in data


def test_run_scenario_failure_still_captures_checkpoint_and_final(mock, tmp_path):
    """L'échec d'une étape ne sacrifie pas la preuve: les captures du
    checkpoint raté et les artefacts finaux sont quand même produits, et le
    finding désigne l'étape fautive."""
    mock.on_eval("getBoundingClientRect", None)
    scenario = scenarios.parse(
        {
            "name": "missing_button",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"label": "broken_click", "click": "#missing", "capture": ["console"]}],
            "artifacts": ["screenshot"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    #: le clic sur un élément introuvable rend un verdict fail sans lever
    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    #: la capture du checkpoint et le screenshot final existent malgré
    #: l'interruption, et le finding incrimine bien l'étape
    assert [artifact["type"] for artifact in result["artifacts"]] == ["console", "screenshot"]
    assert result["findings"][0]["code"] == "step_failed"


def test_run_scenario_console_and_network_assertions_fail(mock, tmp_path):
    """Les assertions d'observabilité voient les évènements passifs: une
    erreur console et une réponse 5xx suffisent chacune à produire son
    finding dédié."""
    mock.script_console(
        [{"type": "error", "args": [{"type": "string", "value": "boom"}], "timestamp": 1.0}]
    )
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {"url": "http://shop.test/api", "method": "GET"},
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {"url": "http://shop.test/api", "status": 500},
                },
            },
        ]
    )
    scenario = scenarios.parse(
        {
            "name": "bad_observability",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "assertions": [{"no_console_errors": True}, {"network_errors_max": 0}],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    #: chaque assertion violée produit son propre finding identifiable,
    #: au lieu d'un échec global indifférencié
    assert result["verdict"] == "fail"
    assert [finding["code"] for finding in result["findings"]] == [
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    ]


def test_final_drain_precedes_console_and_network_assertions(mock, tmp_path, monkeypatch):
    """Les évènements qui n'arrivent qu'au tout dernier drain comptent encore
    dans les assertions ET dans les artefacts: pas de fenêtre aveugle entre
    la dernière étape et le jugement."""

    class LateCollector(scenarios.PassiveCollector):
        def __init__(self, context=None):
            super().__init__(context)
            self.drain_count = 0

        def drain(self, client, settle):
            self.drain_count += 1
            if self.drain_count != 3:
                return
            self.console_entries.append(
                {
                    "kind": "console",
                    "type": "error",
                    "text": "late console error",
                    "ts": 2.0,
                }
            )
            self.requests["LATE"] = {
                "requestId": "LATE",
                "url": "http://shop.test/api/late",
                "method": "GET",
                "status": 500,
            }

    monkeypatch.setattr(scenarios, "PassiveCollector", LateCollector)
    scenario = scenarios.parse(
        {
            "name": "late_observability",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "assertions": [
                {"no_console_errors": True},
                {"network_errors_max": 0},
            ],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    #: l'erreur console et le 500 injectés après la dernière étape sont
    #: quand même comptés par les deux assertions
    assert result["verdict"] == "fail"
    assert [record["actual"] for record in result["assertions"]] == [1, 1]
    artifacts = {
        artifact["type"]: json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        for artifact in result["artifacts"]
    }
    #: les artefacts écrits racontent la même histoire que le verdict:
    #: aucune divergence possible entre la preuve et le jugement
    assert artifacts["console"]["errors"] == result["assertions"][0]["actual"]
    assert artifacts["network"]["summary"]["errors_4xx_5xx"] == result["assertions"][1]["actual"]


def test_scenario_network_evidence_redacts_sensitive_headers(mock, tmp_path):
    """L'artefact réseau écrit sur disque masque les en-têtes porteurs de
    secrets (Authorization, Set-Cookie) tout en gardant les en-têtes
    inoffensifs lisibles pour le diagnostic."""
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://shop.test/",
                        "status": 200,
                        "headers": {
                            "Authorization": "Bearer secret",
                            "Set-Cookie": "session=secret",
                            "Content-Type": "text/html",
                        },
                    },
                },
            }
        ]
    )
    scenario = scenarios.parse(
        {
            "name": "redacted",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["network"],
        }
    )
    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )
    artifact = next(a for a in result["artifacts"] if a["type"] == "network")
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    headers = data["requests"][0]["headers"]
    #: seuls les en-têtes sensibles sont remplacés par le marqueur de
    #: redaction; le Content-Type reste intact pour l'analyse
    assert headers == {
        "Authorization": "***",
        "Set-Cookie": "***",
        "Content-Type": "text/html",
    }


def test_strict_scenario_stops_after_redirect_before_next_mutation_or_capture(mock, tmp_path):
    """Une redirection vers une origine non autorisée arrête le scénario
    avant toute capture ou mutation suivante: garde-fou contre l'envoi
    d'actions ou de preuves vers un domaine imprévu."""
    mock.on_eval("window.location.href", "https://forbidden.example/redirected")
    scenario = scenarios.parse(
        {
            "name": "redirect_guard",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {"goto": "/start", "capture": ["screenshot"]},
                {"click": "#danger"},
            ],
            "artifacts": ["network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            origins="http://shop.test",
            settle=0,
        )

    #: le refus d'origine est un finding explicite, pas un échec générique
    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    assert [finding["code"] for finding in result["findings"]] == ["origin_refused"]
    #: après la redirection interdite, ni capture ni clic n'ont été émis:
    #: l'arrêt précède toute interaction avec la page compromise
    assert result["artifacts"] == []
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_scenario_secret_ref_never_reaches_outputs_or_evidence(mock, tmp_path, monkeypatch):
    """Une frappe via secret_ref transmet la valeur secrète au navigateur
    tout en la tenant hors du résultat JSON et de chaque fichier de preuve,
    même quand la page la répète en console."""
    secret = "checkout-password-canary-9347"
    monkeypatch.setenv("CHECKOUT_PASSWORD", secret)
    mock.script_console(
        [{"type": "log", "args": [{"type": "string", "value": secret}], "timestamp": 1.0}]
    )
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    scenario = scenarios.parse(
        {
            "name": "secret_ref",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "type": {
                        "selector": "#password",
                        "secret_ref": "CHECKOUT_PASSWORD",
                        "clear": True,
                    }
                }
            ],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    serialized = json.dumps(result, ensure_ascii=False)
    #: le résultat annonce la frappe comme masquée et le canari est absent
    #: de toute sa sérialisation
    assert secret not in serialized
    assert result["steps"][0]["result"]["typed"] is True
    assert result["steps"][0]["result"]["value_masked"] is True
    #: aucun fichier du répertoire d'évidence ne contient le canari
    assert scan_canaries(result["evidence_dir"], [secret]) == []
    chars = [item["text"] for item in mock.commands_for("Input.insertText")]
    #: la valeur secrète a pourtant bien été tapée intégralement côté CDP:
    #: le masquage n'a pas amputé la saisie
    assert "".join(chars) == secret


def test_scenario_literal_type_is_rejected_before_cdp(mock):
    """Un texte littéral dans une étape type est interdit dès le parsing:
    seule la voie secret_ref existe, et le rejet n'émet rien vers Chrome."""
    #: le refus survient à l'analyse du scénario, avant toute session
    with pytest.raises(scenarios.ScenarioUsageError, match="text|secret_ref"):
        scenarios.parse(
            {
                "name": "literal_type",
                "context": {"base_url": "http://shop.test"},
                "steps": [{"type": {"selector": "#field", "text": "literal"}}],
            }
        )

    #: aucune commande CDP n'a été émise pendant le rejet
    assert mock.commands == []


@pytest.mark.parametrize("fails", [False, True])
def test_scenario_eval_never_persists_result_or_error(mock, tmp_path, fails):
    """Le retour d'une étape eval — valeur ou message d'exception — est masqué
    partout: sortie JSON et fichiers de preuve, quel que soit le dénouement
    de l'évaluation."""
    canary = "scenario-eval-canary-5571"
    if fails:
        mock.on_eval(
            "window.readSensitive",
            {"raw": {"exceptionDetails": {"text": f"failure contained {canary}"}}},
        )
    else:
        mock.on_eval("window.readSensitive", canary)
    scenario = scenarios.parse(
        {
            "name": "eval_result",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"eval": "window.readSensitive()"}],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    #: le canari renvoyé par la page ne fuit ni dans le résultat sérialisé
    #: ni dans les fichiers d'évidence
    assert canary not in json.dumps(result, ensure_ascii=False)
    assert scan_canaries(result["evidence_dir"], [canary]) == []
    step = result["steps"][0]
    #: quel que soit le chemin (succès ou exception), le champ exposé est le
    #: marqueur de masquage accompagné de son drapeau explicite
    if fails:
        assert step["error"] == "***" and step["error_masked"] is True
    else:
        assert step["result"] == {"value": "***", "value_masked": True}


def test_scenario_artifacts_are_private_classified_and_manifested(mock, tmp_path):
    """Les artefacts d'un run sont privés au propriétaire, classifiés selon
    leur sensibilité, interdits d'upload et tous inventoriés dans le
    manifeste du répertoire d'évidence."""
    scenario = scenarios.parse(
        {
            "name": "private_evidence",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["screenshot", "console"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    run_dir = Path(result["evidence_dir"])
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    #: le répertoire d'évidence et chacun de ses fichiers sont illisibles
    #: pour tout autre utilisateur de la machine
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run_dir.iterdir() if path.is_file()
    )
    classes = {artifact["type"]: artifact["classification"] for artifact in result["artifacts"]}
    #: la capture d'écran (contenu opaque) et la console reçoivent chacune
    #: la classification adaptée, et rien n'est déclaré uploadable
    assert classes == {"screenshot": "opaque-restricted", "console": "internal"}
    assert all(not artifact["upload_allowed"] for artifact in result["artifacts"])
    #: le manifeste référence chaque artefact produit, résultat inclus
    assert {entry["path"] for entry in manifest["artifacts"]} >= {
        "final-screenshot.png",
        "final-console.json",
        "scenario-result.json",
    }


def run_cli(mock, capsys, *argv):
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


def test_scenario_cli_run_passes_with_json(mock, cli_manifest, capsys, tmp_path):
    """La sous-commande scenario run lit un fichier YAML, exécute le scénario
    sur la session supervisée et respecte le contrat CLI: exit 0 et un objet
    JSON unique portant le verdict sur stdout."""
    scenario = tmp_path / "scenario.yml"
    scenario.write_text(
        """
name: cli_pass
context:
  base_url: http://shop.test
steps:
  - goto: /
artifacts:
  - network
""",
        encoding="utf-8",
    )

    code, out, err = run_cli(
        mock,
        capsys,
        "scenario",
        "run",
        str(scenario),
        "--settle",
        "0.01",
    )

    #: exit 0 et un stdout parsable en JSON: le pipe agent peut consommer
    #: le verdict sans nettoyage
    assert code == 0, err
    data = json.loads(out)
    assert data["name"] == "cli_pass"
    assert data["verdict"] == "pass"


def test_scenario_cli_invalid_file_exits_2(mock, cli_manifest, capsys, tmp_path):
    """Un fichier de scénario invalide est traité en erreur d'usage: exit 2
    et diagnostic en français sur stderr, jamais sur stdout."""
    scenario = tmp_path / "bad.yml"
    scenario.write_text("[]\n", encoding="utf-8")

    code, _, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    #: le code 2 réserve la sortie aux erreurs d'usage, et l'explication
    #: part sur le canal diagnostic
    assert code == 2
    assert "scénario doit être un objet" in err
