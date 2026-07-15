from __future__ import annotations

import json
import os
import stat

import pytest

from cdpx.client import CDPClient
from cdpx.journal import JournalError, append_event, materialize_action, serialize_action
from cdpx.primitives import advanced
from cdpx.security import RedactionContext


def test_type_literal_is_redacted_and_not_replayable():
    """Une valeur secrète tapée en littéral est stockée sous forme redactée
    et l'action perd son droit de rejeu: la valeur n'existe plus, rejouer
    serait mentir."""
    context = RedactionContext.from_secrets(["super-secret"])
    stored, replayable = serialize_action(
        ["type", "#password", "super-secret", "--clear"],
        context=context,
    )
    #: la forme stockée ne garde que la structure de l'action,
    #: jamais la valeur secrète elle-même
    assert replayable is False
    assert stored == {
        "verb": "type",
        "selector": "#password",
        "input": {"redacted": True},
        "clear": True,
    }
    assert "super-secret" not in json.dumps(stored)
    #: rejouer une action amputée de sa valeur serait un mensonge: refus net
    with pytest.raises(JournalError, match="non rejouable"):
        materialize_action(stored)


def test_secret_env_reference_is_replayable_without_serializing_value(monkeypatch):
    """Une référence @env: rend l'action rejouable: le journal ne stocke que
    le nom de la variable et la valeur secrète est résolue au moment du
    rejeu."""
    monkeypatch.setenv("CHECKOUT_PASSWORD", "env-secret")
    stored, replayable = serialize_action(["type", "#password", "@env:CHECKOUT_PASSWORD"])
    #: seule la référence est persistée, la valeur reste dans l'environnement
    assert replayable is True
    assert stored["input"] == {"secret_ref": "CHECKOUT_PASSWORD", "source": "env"}
    assert "env-secret" not in json.dumps(stored)
    #: la matérialisation résout la référence au dernier moment et
    #: reconstruit l'action complète pour l'exécution
    assert materialize_action(stored) == ["type", "#password", "env-secret"]


def test_missing_secret_ref_fails_before_action(monkeypatch):
    """Une référence de secret absente de l'environnement fait échouer la
    matérialisation avant toute exécution, en nommant la variable."""
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    stored, _ = serialize_action(["type", "#password", "@env:MISSING_SECRET"])
    #: l'erreur cite la référence manquante, diagnostic immédiat sans avoir
    #: lancé la moindre action navigateur
    with pytest.raises(JournalError, match="MISSING_SECRET"):
        materialize_action(stored)


def test_eval_is_redacted_and_non_replayable():
    """Une expression eval est masquée dans le journal mais reste corrélable
    par son empreinte SHA-256; l'action n'est jamais rejouable."""
    stored, replayable = serialize_action(["eval", "document.cookie"])
    #: l'expression disparaît du journal, seule son empreinte permet de la
    #: corréler à une exécution connue
    assert replayable is False
    assert stored["verb"] == "eval"
    assert stored["expression"] == "***"
    assert len(stored["sha256"]) == 64
    assert "document.cookie" not in json.dumps(stored)


def test_any_redacted_action_is_stored_safely_and_marked_non_replayable():
    """Toute action dont un argument a dû être redacté (credentials d'URL,
    chemin secret, token) perd son droit de rejeu; une action intacte le
    conserve."""
    stored, replayable = serialize_action(
        ["goto", "https://user:pass@example.test/reset/private-path?token=value#trace"],
        context=RedactionContext.from_secrets(["private-path"]),
    )

    #: l'URL stockée a perdu credentials, segment secret et fragment: la
    #: rejouer produirait une autre navigation, donc rejeu interdit
    assert stored == ["goto", "https://example.test/reset/***?token=***"]
    assert replayable is False

    unchanged, replayable = serialize_action(["click", "#submit"])
    #: une action sans rien à masquer traverse intacte et reste rejouable
    assert unchanged == ["click", "#submit"]
    assert replayable is True


def test_v1_sensitive_actions_are_always_rejected():
    """Le format liste v1 ne sait pas distinguer une valeur secrète d'une
    référence: ses actions sensibles sont rejetées au rejeu, les actions
    anodines passent."""
    #: type et eval au format v1 sont irrécupérables sans risque de rejouer
    #: une valeur qui aurait dû rester secrète
    with pytest.raises(JournalError, match="v1 sensible"):
        materialize_action(["type", "#password", "raw"])
    with pytest.raises(JournalError, match="v1 sensible"):
        materialize_action(["eval", "1 + 1"])
    #: une action v1 sans donnée sensible reste rejouable telle quelle
    assert materialize_action(["click", "#go"]) == ["click", "#go"]


def test_secure_append_permissions_are_enforced(tmp_path):
    """L'append crée l'arborescence du journal avec des droits privés et
    écrit un évènement ndjson tenant sur une seule ligne."""
    path = tmp_path / "private" / "record.ndjson"
    append_event(path, {"schema": "cdpx.record/v2", "ok": True})
    #: dossier et journal naissent illisibles pour les autres comptes
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    #: l'évènement tient sur une seule ligne, contrat ndjson respecté
    assert os.linesep not in path.read_text(encoding="utf-8").rstrip("\n")


def test_secure_append_refuses_a_symbolic_journal(tmp_path):
    """Un journal qui est en réalité un lien symbolique est refusé: l'append
    ne peut pas servir à écraser un fichier arbitraire du système."""
    sensitive = tmp_path / "sensitive.txt"
    sensitive.write_text("preserve", encoding="utf-8")
    link = tmp_path / "record.ndjson"
    link.symlink_to(sensitive)

    #: le lien est détecté et refusé avant toute écriture
    with pytest.raises(JournalError, match="symbolique"):
        append_event(link, {"ok": True})

    #: le fichier visé par le lien n'a pas été touché
    assert sensitive.read_text(encoding="utf-8") == "preserve"


def test_record_v2_executes_secret_but_never_persists_it(mock, tmp_path, monkeypatch):
    """L'enregistrement v2 exécute l'action avec la vraie valeur secrète
    résolue depuis l'environnement, mais ne persiste que la référence: le
    journal reste rejouable sans jamais contenir la valeur."""
    path = tmp_path / "record.ndjson"
    seen = []

    def run_action(_client, action, timeout=30):
        seen.append(action)
        return {"typed": action[2], "selector": action[1]}

    monkeypatch.setenv("CHECKOUT_PASSWORD", "runtime-canary-secret")
    monkeypatch.setattr(advanced.actions, "run_action", run_action)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = advanced.record(
            client,
            str(path),
            ["type", "#password", "@env:CHECKOUT_PASSWORD"],
            run_id="R1",
            origins="http://*.test",
        )
    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    #: l'action réellement exécutée a bien reçu la valeur résolue: la
    #: redaction ne dégrade pas l'exécution
    assert seen == [["type", "#password", "runtime-canary-secret"]]
    #: sur disque, seule la référence subsiste et le résultat est masqué
    assert "runtime-canary-secret" not in raw
    assert event["schema"] == "cdpx.record/v2"
    assert event["action"]["input"]["secret_ref"] == "CHECKOUT_PASSWORD"
    assert event["result"]["typed"] == "***"
    #: grâce à la référence, l'enregistrement reste rejouable malgré tout
    assert result["replayable"] is True


@pytest.mark.parametrize("fails", [False, True])
def test_record_eval_never_persists_result_or_error(mock, tmp_path, monkeypatch, fails):
    """Qu'un eval réussisse ou échoue, ni sa valeur de retour ni son message
    d'erreur n'atteignent le journal: seuls des marqueurs masqués et un
    drapeau explicite sont persistés."""
    path = tmp_path / "eval.ndjson"
    canary = "unknown-eval-result-canary-7734"

    def run_action(_client, _action, timeout=30):
        if fails:
            raise ValueError(f"eval failed with {canary}")
        return {"value": canary}

    monkeypatch.setattr(advanced.actions, "run_action", run_action)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        if fails:
            #: l'échec de l'eval remonte à l'appelant, le journal ne
            #: l'avale pas
            with pytest.raises(ValueError, match="eval failed"):
                advanced.record(
                    client,
                    str(path),
                    ["eval", "window.readSecret()"],
                    origins="http://*.test",
                )
        else:
            advanced.record(
                client,
                str(path),
                ["eval", "window.readSecret()"],
                origins="http://*.test",
            )

    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    #: le canari issu de l'eval (résultat comme erreur) n'atteint jamais
    #: le disque
    assert canary not in raw
    #: le journal ne garde qu'un marqueur masqué et un drapeau honnête,
    #: côté succès comme côté échec
    if fails:
        assert event["result"] == {"error": "***", "error_masked": True}
    else:
        assert event["result"] == {"value": "***", "value_masked": True}


def test_replay_v2_resolves_all_refs_before_first_action(mock, tmp_path, monkeypatch):
    """Le rejeu valide toutes les références de secret avant la première
    action: une référence manquante stoppe tout, sans le moindre effet de
    bord côté navigateur."""
    path = tmp_path / "record.ndjson"
    path.write_text(
        '{"schema":"cdpx.record/v2","action":{"verb":"type",'
        '"selector":"#password","input":{"source":"env",'
        '"secret_ref":"MISSING"},"clear":false},"replayable":true,"ok":true}\n'
        '{"action":["click","#go"],"ok":true}\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING", raising=False)
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://demo.test/page"
    target = mock._public_target(target_id)
    with CDPClient(target["webSocketDebuggerUrl"]) as client:
        result = advanced.replay(client, str(path), origins="http://*.test")
    #: la référence manquante interrompt le rejeu avant la première action
    #: et la divergence la nomme pour le diagnostic
    assert result["played"] == 0 and result["ok"] is False
    assert "MISSING" in result["divergence"]
    #: aucun ordre CDP n'a été émis: l'échec précède tout effet de bord
    assert mock.commands == []
