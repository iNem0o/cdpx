from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from cdpx import journal
from cdpx.artifacts import (
    ArtifactClassification,
    SecureArtifactWriter,
    scan_canaries,
)
from cdpx.client import CDPClient
from cdpx.primitives import advanced, capture, dev, net, state
from cdpx.security import MASK, RedactionContext, redact_text, redact_tree

CANARY = "CDPX-CANARY-7d3df679f62b"
ORDINARY_TEXT = "contact=alice@example.test order=123456 status=ready"


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as cdp:
        yield cdp


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _assert_private_tree(root: Path) -> None:
    assert _mode(root) == 0o700
    for path in root.rglob("*"):
        if path.is_dir():
            assert _mode(path) == 0o700, path
        elif path.is_file():
            assert _mode(path) == 0o600, path


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["Aggregated console, storage, network and error outputs ship canary-free."],
)
def test_observation_outputs_redact_url_console_storage_and_errors(
    mock,
    client,
    capsys,
    evidence_case,
):
    """Toutes les sorties d'observation (console, storage, réseau, erreurs)
    sont purgées du canari avant sérialisation et jusqu'aux flux
    stdout/stderr réels, sans appauvrir les diagnostics anodins."""
    context = RedactionContext.from_secrets([CANARY])
    mock.on_eval(
        "Object.entries(localStorage)",
        json.dumps({"session": CANARY, "diagnostic": ORDINARY_TEXT}),
    )
    mock.script_console(
        [
            {
                "type": "error",
                "args": [
                    {"value": f"failed Bearer {CANARY}"},
                    {"value": ORDINARY_TEXT},
                    {"value": f"https://user:{CANARY}@app.test/cb?code={CANARY}#trace"},
                ],
                "timestamp": 10.0,
            }
        ]
    )
    console_result = capture.console_capture(client, duration=0.01, context=context)
    storage_result = state.get_storage(client)

    navigation_url = f"http://user:{CANARY}@app.test/report?token={CANARY}#fragment"
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {
                        "url": f"http://app.test/api/orders?access_token={CANARY}",
                        "method": "GET",
                    },
                },
            },
            {
                "method": "Network.loadingFailed",
                "params": {
                    "requestId": "R1",
                    "errorText": f"upstream rejected {CANARY}; {ORDINARY_TEXT}",
                },
            },
        ]
    )
    network_result = net.capture(client, navigation_url, settle=0.01, context=context)
    safe_error = redact_tree(
        {"error": f"request failed for {CANARY}; {ORDINARY_TEXT}"},
        context=context,
    )

    output = {
        "console": console_result,
        "storage": storage_result,
        "network": network_result,
        "error": safe_error,
    }
    serialized = json.dumps(output, ensure_ascii=False)

    #: le canari n'apparaît nulle part dans la sortie agrégée sérialisée
    assert CANARY not in serialized
    #: les diagnostics anodins survivent partout: la redaction n'appauvrit
    #: pas la valeur d'observation des sorties
    assert ORDINARY_TEXT in console_result["entries"][0]["text"]
    assert ORDINARY_TEXT in network_result["requests"][0]["failed"]
    assert ORDINARY_TEXT in safe_error["error"]
    #: le storage est masqué clé par clé: c'est un réservoir de sessions
    assert storage_result["entries"] == {"session": MASK, "diagnostic": MASK}
    #: les URLs réseau perdent credentials et tokens mais restent corrélables
    assert network_result["url"] == "http://app.test/report?token=***"
    assert network_result["requests"][0]["url"] == ("http://app.test/api/orders?access_token=***")
    #: le navigateur a reçu l'URL intacte: la redaction n'altère que la
    #: sortie, jamais l'action demandée
    assert mock.commands_for("Page.navigate")[-1] == {"url": navigation_url}
    assert redact_text(ORDINARY_TEXT, context=context) == ORDINARY_TEXT

    print(serialized)
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    #: à la frontière réelle stdout/stderr, le canari est absent et le
    #: diagnostic anodin toujours présent
    assert CANARY not in emitted.out
    assert CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.out and ORDINARY_TEXT in emitted.err

    if evidence_case is not None:
        evidence_case.attach_json(
            "Sortie d'observation agrégée redactée",
            output,
        )


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["The redact_tree boundary keeps the shareable profiler artifact canary-free."],
)
def test_profiler_redacts_token_headers_and_urls_before_artifact(
    mock,
    client,
    tmp_path,
    evidence_case,
):
    """Le token du profiler Symfony, découvert en cours de route, rejoint le
    contexte partagé: URLs, headers et panneaux SQL sortent nettoyés, et
    l'artefact écrit reste privé et exempt de canari."""
    # Le token n'est pas pré-enregistré: la primitive profiler doit l'ajouter
    # au contexte partagé avant le nettoyage transversal de la sortie.
    context = RedactionContext()
    navigation_url = f"http://app.test/report?session={CANARY}"
    profiler_link = f"http://app.test/_profiler/{CANARY}?transport={CANARY}#trace"
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": navigation_url,
                        "status": 200,
                        "headers": {
                            "X-Debug-Token-Link": profiler_link,
                            "Authorization": f"Bearer {CANARY}",
                            "Set-Cookie": f"sid={CANARY}; HttpOnly",
                            "X-Diagnostic": f"{ORDINARY_TEXT}; secret={CANARY}",
                        },
                    },
                },
            }
        ]
    )
    mock.on_eval(
        "__cdpx_profiler_panels",
        json.dumps(
            [
                {
                    "panel": "db",
                    "status": 200,
                    "html": (
                        '<div class="metric"><span class="value">1</span>'
                        '<span class="label">Database queries</span></div>'
                        "<table><tr><th>Info</th><th>Time</th></tr>"
                        f"<tr><td>SELECT '{CANARY}' /* {ORDINARY_TEXT} */</td>"
                        "<td>1 ms</td></tr></table>"
                    ),
                }
            ]
        ),
    )
    mock.on_eval("window.location.href", navigation_url)

    primitive_result = dev.profiler(
        client,
        navigation_url,
        panels=["db"],
        settle=0.01,
        context=context,
    )
    # Même frontière que le CLI avant stdout ou persistance d'un artefact.
    result = redact_tree(primitive_result, context=context)
    serialized = json.dumps(result, ensure_ascii=False)

    #: la sortie brute de la primitive contient encore le token: c'est bien
    #: la frontière redact_tree qui protège, pas un hasard amont
    assert CANARY in json.dumps(primitive_result["panels"], ensure_ascii=False)
    #: après la frontière, plus aucun canari dans la sortie sérialisée
    assert CANARY not in serialized
    #: URLs de navigation et de profiler perdent le token découvert en route
    assert result["url"] == "http://app.test/report?session=***"
    assert result["profiler_url"] == "http://app.test/_profiler/***?transport=***"
    #: les headers d'auth sont masqués, le diagnostic anodin survit dans le
    #: header libre comme dans le SQL du panneau
    assert result["response_headers"]["authorization"] == MASK
    assert result["response_headers"]["set-cookie"] == MASK
    assert ORDINARY_TEXT in result["response_headers"]["x-diagnostic"]
    assert ORDINARY_TEXT in result["panels"]["db"]["list"][0]["sql"]
    #: le token n'est jamais retourné, seule sa présence est attestée
    assert "token" not in result and result["token_present"] is True
    #: côté protocole le vrai token a circulé: la redaction n'a pas dégradé
    #: l'interrogation effective du profiler
    assert CANARY in mock.commands_for("Runtime.evaluate")[-1]["expression"]
    assert mock.commands_for("Page.navigate")[-1] == {"url": navigation_url}

    writer = SecureArtifactWriter(tmp_path / "private", "profiler-run")
    writer.write_json(
        "profiler.json",
        result,
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    shareable = writer.build_shareable(tmp_path / "shareable")
    #: ni l'artefact ni sa copie partageable ne contiennent le canari, et
    #: leurs arborescences restent privées (0700/0600)
    run_dir_scan = scan_canaries(writer.run_dir, [CANARY])
    shareable_scan = scan_canaries(shareable, [CANARY])
    assert run_dir_scan == []
    assert shareable_scan == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)

    if evidence_case is not None:
        evidence_case.attach_file(
            shareable / "profiler.json",
            "Artefact profiler partageable redacté",
            "profiler",
        )
        evidence_case.attach_json(
            "Scan canari des artefacts profiler",
            {"run_dir": run_dir_scan, "shareable": shareable_scan},
        )


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["The replayable journal stores only the secret_ref, never the secret value."],
)
def test_secret_ref_record_stdout_journal_and_artifacts_are_canary_free(
    mock,
    client,
    tmp_path,
    monkeypatch,
    capsys,
    evidence_case,
):
    """Un secret injecté via @env: atteint le navigateur mais n'apparaît
    jamais ailleurs: le journal ne stocke que la référence, et artefacts,
    stdout et stderr restent exempts de la valeur secrète."""
    monkeypatch.setenv("CHECKOUT_PASSWORD", CANARY)
    mock.on_eval(
        "__cdpx_actionability focus",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 2, "width": 30, "height": 12},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    context = RedactionContext()
    record_path = tmp_path / "journal" / "record.ndjson"

    result = advanced.record(
        client,
        str(record_path),
        ["type", "#checkout-password", "@env:CHECKOUT_PASSWORD"],
        run_id="security-run",
        redaction_context=context,
        origins="http://*.test",
    )
    safe_error = redact_tree(
        {"error": f"submission failed for {CANARY}; {ORDINARY_TEXT}"},
        context=context,
    )

    #: la valeur secrète a bien été tapée dans la page: la protection ne
    #: sacrifie pas la fonctionnalité de saisie
    assert mock.commands_for("Input.insertText") == [{"text": CANARY}]
    journal_text = record_path.read_text(encoding="utf-8")
    #: le journal rejouable ne contient que la référence d'environnement,
    #: jamais la valeur secrète elle-même
    assert CANARY not in journal_text
    event = json.loads(journal_text)
    assert event["action"]["input"] == {
        "secret_ref": "CHECKOUT_PASSWORD",
        "source": "env",
    }
    #: le journal est créé privé dès l'écriture, sans resserrage a posteriori
    assert _mode(record_path.parent) == 0o700
    assert _mode(record_path) == 0o600

    writer = SecureArtifactWriter(tmp_path / "artifacts", "security-run")
    writer.write_json(
        "outputs/result.json",
        {"stdout": result, "stderr": safe_error, "ordinary": ORDINARY_TEXT},
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    writer.register_file(
        record_path,
        name="journal/record.ndjson",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    shareable = writer.build_shareable(tmp_path / "staging")

    #: aucun canari dans les artefacts ni la copie partageable, arborescences
    #: privées de bout en bout
    assert scan_canaries(writer.run_dir, [CANARY]) == []
    assert scan_canaries(shareable, [CANARY]) == []
    _assert_private_tree(writer.root)
    _assert_private_tree(shareable)
    #: le journal interne non uploadable est exclu de la copie partageable
    assert not (shareable / "journal" / "record.ndjson").exists()

    if evidence_case is not None:
        evidence_case.attach_file(
            record_path,
            "Journal record NDJSON (secret_ref uniquement)",
        )
        evidence_case.attach_json(
            "Arborescence partageable (journal interne exclu)",
            {
                "files": sorted(
                    path.relative_to(shareable).as_posix()
                    for path in shareable.rglob("*")
                    if path.is_file()
                )
            },
        )

    print(json.dumps(result, ensure_ascii=False))
    print(safe_error["error"], file=sys.stderr)
    emitted = capsys.readouterr()
    #: aux frontières stdout/stderr, le canari est absent mais le diagnostic
    #: anodin passe encore
    assert CANARY not in emitted.out and CANARY not in emitted.err
    assert ORDINARY_TEXT in emitted.err


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="replay-flow",
    scenario_id="orchestration-control.orchestrate-replay-and-emulation",
    proves=["A missing secret_ref fails the replay closed before any CDP command is emitted."],
)
def test_missing_secret_ref_is_rejected_before_any_cdp_effect(
    mock,
    client,
    tmp_path,
    monkeypatch,
    evidence_case,
):
    """Un rejeu dont la référence de secret n'existe plus dans
    l'environnement s'arrête net avant la première action: divergence
    explicite et zéro effet CDP."""
    monkeypatch.delenv("MISSING_CHECKOUT_PASSWORD", raising=False)
    record_path = tmp_path / "journal" / "record.ndjson"
    journal.append_event(
        record_path,
        {
            "schema": journal.SCHEMA,
            "action": {
                "verb": "type",
                "selector": "#checkout-password",
                "input": {
                    "source": "env",
                    "secret_ref": "MISSING_CHECKOUT_PASSWORD",
                },
                "clear": False,
            },
            "replayable": True,
            "ok": True,
        },
    )
    journal.append_event(
        record_path,
        {
            "schema": journal.SCHEMA,
            "action": ["click", "#submit"],
            "replayable": True,
            "ok": True,
        },
    )

    result = advanced.replay(client, str(record_path), origins="http://*.test")

    #: le rejeu échoue sans jouer aucune action et la divergence nomme la
    #: référence manquante pour un correctif immédiat
    assert result["ok"] is False
    assert result["played"] == 0
    assert "MISSING_CHECKOUT_PASSWORD" in result["divergence"]
    #: aucune commande n'a atteint le navigateur: le refus précède tout effet
    assert mock.commands == []

    if evidence_case is not None:
        evidence_case.attach_json(
            "Divergence du rejeu fail-closed",
            {"replay": result, "cdp_commands": mock.commands},
        )
