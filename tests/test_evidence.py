import json
import stat
from datetime import datetime

import pytest

from cdpx.artifacts import ArtifactClassification
from cdpx.security.redaction import RedactionContext
from cdpx.testing.evidence import (
    EvidenceCase,
    EvidenceSession,
    classify_nodeid,
    marker_metadata,
    proof_retention_days,
)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class FakeItem:
    def __init__(self, nodeid, marker=None):
        self.nodeid = nodeid
        self.marker = marker

    def get_closest_marker(self, name):
        return self.marker if name == "scenario" else None


class FakeMarker:
    args = ()

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_classify_nodeid_by_test_area():
    """La suite de preuve se déduit du seul nodeid, sans marqueur ni
    configuration: répertoire e2e, fichiers CLI en intégration, le reste en
    unitaire."""
    #: trois nodeids représentatifs suffisent à couvrir les trois suites
    assert classify_nodeid("tests/e2e/test_e2e_chrome.py::test_x") == "e2e"
    assert classify_nodeid("tests/test_cli.py::test_x") == "integration"
    assert classify_nodeid("tests/test_primitives.py::test_x") == "unit"


def test_evidence_case_attaches_artifacts(tmp_path):
    """Chaque pièce jointe reçoit un type, une classification et un droit
    d'upload cohérents, et tout est écrit sous le dossier privé du cas
    avec des droits POSIX stricts."""
    case = EvidenceCase(
        nodeid="tests/e2e/test_demo.py::test_records",
        root=tmp_path,
        suite="e2e",
        title="records",
    )
    screenshot = tmp_path / "shot.png"
    screenshot.write_bytes(b"\x89PNG\r\n\x1a\nxxx")

    case.attach_screenshot(screenshot, "final")
    case.attach_json("payload", {"ok": True})
    case.attach_text("stdout", "hello")

    data = case.as_dict()
    #: chaque attache est typée d'après sa nature et rangée sous la racine du cas
    assert data["artifacts"][0]["type"] == "screenshot"
    assert data["artifacts"][1]["type"] == "json"
    assert data["artifacts"][2]["type"] == "logs"
    assert all(tmp_path.as_posix() in artifact["path"] for artifact in data["artifacts"])
    #: le binaire opaque reste confiné en local tandis que le JSON redactable
    #: peut être uploadé
    assert data["artifacts"][0]["classification"] == "opaque-restricted"
    assert data["artifacts"][0]["upload_allowed"] is False
    assert data["artifacts"][1]["classification"] == "internal"
    assert data["artifacts"][1]["upload_allowed"] is True
    #: dossier privé et fichiers illisibles pour les autres comptes du système
    assert mode(case.artifact_dir) == 0o700
    assert all(mode(tmp_path / artifact["path"]) == 0o600 for artifact in data["artifacts"])


def test_evidence_redacts_reports_and_textual_attachments(tmp_path):
    """La valeur secrète injectée dans le rapport pytest et dans les pièces
    textuelles n'atteint ni le manifeste sérialisé ni le disque: seule la
    marque de redaction subsiste."""
    context = RedactionContext.from_secrets(["proof-canary-123"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_redaction",
        root=tmp_path,
        suite="unit",
        title="redaction",
        redaction_context=context,
    )

    class Report:
        duration = 0.01
        when = "call"
        outcome = "failed"
        longreprtext = "failure proof-canary-123"
        capstdout = "Bearer abc.def_ghi proof-canary-123"
        capstderr = "https://demo.test/path?token=proof-canary-123#fragment"

    case.set_report(Report())
    text_artifact = case.attach_text("secret proof-canary-123", "value=proof-canary-123")
    json_artifact = case.attach_json(
        "payload", {"url": "https://demo.test/?token=proof-canary-123", "token": "raw"}
    )

    serialized = json.dumps(case.as_dict(), ensure_ascii=False)
    #: le canari a disparu de la sérialisation et des fichiers écrits,
    #: remplacé partout par le marqueur de redaction
    assert "proof-canary-123" not in serialized
    assert "proof-canary-123" not in (tmp_path / text_artifact["path"]).read_text()
    assert "proof-canary-123" not in (tmp_path / json_artifact["path"]).read_text()
    assert "***" in serialized


def test_attach_file_redacts_text_but_keeps_binary_restricted(tmp_path):
    """Un fichier texte est copié redacté et devient uploadable; un binaire,
    impossible à redacter, est classé opaque et interdit d'upload."""
    context = RedactionContext.from_secrets(["canary-value"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_file",
        root=tmp_path,
        suite="unit",
        title="file",
        redaction_context=context,
    )
    source = tmp_path / "source.log"
    source.write_text("canary-value\n", encoding="utf-8")
    binary = tmp_path / "source.bin"
    binary.write_bytes(b"\x00canary-value")

    text_entry = case.attach_file(source, "log")
    binary_entry = case.attach_file(binary, "binary")

    #: la copie texte ne contient plus que le marqueur, donc l'upload est sûr
    assert (tmp_path / text_entry["path"]).read_text() == "***\n"
    assert text_entry["upload_allowed"] is True
    #: faute de redaction possible, le binaire est condamné à rester local
    assert binary_entry["classification"] == ArtifactClassification.OPAQUE_RESTRICTED.value
    assert binary_entry["upload_allowed"] is False


def test_attach_file_treats_ndjson_journal_as_textual_evidence(tmp_path):
    """Un journal .ndjson est une preuve textuelle: copié redacté et classé
    internal, donc inlinable par le cockpit au lieu de rester opaque."""
    context = RedactionContext.from_secrets(["canary-value"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_ndjson",
        root=tmp_path,
        suite="unit",
        title="ndjson",
        redaction_context=context,
    )
    source = tmp_path / "record.ndjson"
    source.write_text('{"typed": "canary-value"}\n', encoding="utf-8")

    entry = case.attach_file(source, "journal")

    #: typé logs et classé internal: le cockpit peut inliner le journal
    assert entry["type"] == "logs"
    assert entry["classification"] == ArtifactClassification.INTERNAL.value
    #: la copie est redactée, le journal attaché ne divulgue rien
    assert "canary-value" not in (tmp_path / entry["path"]).read_text()


def test_evidence_session_writes_private_manifest_with_ttl(tmp_path):
    """Le manifeste de session porte le schéma v2, une expiration postérieure
    à la création et la version de la politique de redaction, le tout écrit
    en fichiers privés."""
    session = EvidenceSession(tmp_path, ttl=3600)
    case = session.case_for_item(FakeItem("tests/test_cli.py::test_manifest"))
    case.attach_text("safe", "hello")
    case.status = "passed"

    session.write()

    manifest_path = tmp_path / "evidence-manifest-integration.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    #: le contrat v2 est complet: TTL dans le futur, politique de redaction
    #: versionnée et artefacts classifiés
    assert manifest["schema"] == "cdpx.evidence/v2"
    assert manifest["expires_at"] > manifest["created_at"]
    assert manifest["redaction_policy"] == "1"
    assert any(item["classification"] == "internal" for item in manifest["artifacts"])
    #: la racine de preuve et le manifeste échappent aux autres comptes
    assert mode(tmp_path) == 0o700
    assert mode(manifest_path) == 0o600


def test_two_sessions_in_same_root_write_distinct_manifests(tmp_path):
    """Deux sessions pytest écrivant dans le même dossier d'évidence (comme
    les runs unit, e2e et symfony d'une même génération de preuve) produisent
    des manifestes distincts nommés par leurs suites: la dernière session
    n'écrase plus la classification déclarée par les précédentes."""
    first = EvidenceSession(tmp_path, ttl=3600)
    first.case_for_item(FakeItem("tests/test_cli.py::test_first")).status = "passed"
    first.write()
    second = EvidenceSession(tmp_path, suite_override="symfony", ttl=3600)
    second.case_for_item(FakeItem("tests/e2e/test_e2e_symfony.py::test_second")).status = "passed"
    second.write()

    manifests = sorted(path.name for path in tmp_path.glob("evidence-manifest*.json"))
    #: chaque session possède son propre manifeste, nommé d'après sa suite
    assert manifests == [
        "evidence-manifest-integration.json",
        "evidence-manifest-symfony.json",
    ]
    #: les deux manifestes restent lisibles indépendamment et portent le schéma v2
    for name in manifests:
        payload = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert payload["schema"] == "cdpx.evidence/v2"


def test_evidence_session_uses_validated_proof_retention_environment(tmp_path, monkeypatch):
    """La rétention déclarée dans l'environnement pilote réellement la
    fenêtre created_at -> expires_at du manifeste écrit."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    session = EvidenceSession(tmp_path)
    session.case_for_item(FakeItem("tests/test_cli.py::test_manifest")).status = "passed"

    session.write()

    manifest = json.loads(
        (tmp_path / "evidence-manifest-integration.json").read_text(encoding="utf-8")
    )
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    #: la fenêtre d'expiration reflète exactement les jours demandés,
    #: preuve que la variable est lue et appliquée
    assert (expires - created).days == 30


@pytest.mark.parametrize("value", ["0", "-1", "1.5", " 14", "91", "abc"])
def test_proof_retention_environment_is_strict_and_bounded(value, monkeypatch):
    """Toute rétention hors contrat (zéro, négatif, décimal, espace parasite,
    hors borne, non numérique) est rejetée bruyamment plutôt que corrigée en
    silence."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", value)

    #: le refus nomme la variable fautive pour un diagnostic immédiat,
    #: quelle que soit la forme de la valeur invalide
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof_retention_days()


def test_proof_retention_defaults_to_fourteen_days(monkeypatch):
    """Sans configuration, la preuve expire après la rétention documentée de
    quatorze jours."""
    monkeypatch.delenv("CDPX_PROOF_RETENTION_DAYS", raising=False)

    #: l'absence de variable retombe sur le défaut documenté, sans erreur
    assert proof_retention_days() == 14


def test_evidence_session_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    """Une rétention invalide bloque la session dès sa construction, avant
    d'avoir créé le moindre artefact sur disque."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "91")

    #: la validation échoue à la construction, pas au moment d'écrire
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        EvidenceSession(tmp_path)

    #: rien n'a été écrit: le refus précède tout effet de bord
    assert list(tmp_path.iterdir()) == []


def test_attachment_filename_cannot_escape_private_case_dir(tmp_path):
    """Un nom de pièce contenant une traversée de chemin est refusé et aucun
    fichier ne s'échappe du dossier privé du cas."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_traversal",
        root=tmp_path,
        suite="unit",
        title="traversal",
    )

    #: le nom traversant est rejeté avant toute écriture
    with pytest.raises(ValueError, match="invalid proof name"):
        case.attach_text("unsafe", "value", "../escape.txt")

    #: la cible hors du dossier privé n'a jamais été créée
    assert not (tmp_path.parent / "escape.txt").exists()


def test_marker_metadata_captures_feature_and_journey():
    """Les métadonnées du marqueur scenario (feature, journey, scenario_id)
    sont recopiées telles quelles dans la preuve du cas."""
    item = FakeItem(
        "tests/test_cli.py::test_cli_contract",
        FakeMarker(
            feature="harness-proof-cockpit",
            journey="run-quality-gate",
            scenario_id="harness-proof-cockpit.run-local-quality-gate",
        ),
    )

    data = marker_metadata(item)

    #: le triplet déclaré sur le marqueur arrive intact dans la preuve,
    #: c'est lui qui reliera le cas au cockpit de scénarios
    assert data["feature"] == "harness-proof-cockpit"
    assert data["journey"] == "run-quality-gate"
    assert data["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"


def test_attach_file_enforces_closed_artifact_taxonomy(tmp_path):
    """La taxonomie des artefacts est fermée: un type inventé est refusé
    net, tandis qu'un suffixe inconnu retombe sur le type générique."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_taxonomy",
        root=tmp_path,
        suite="unit",
        title="taxonomy",
    )
    source = tmp_path / "trace.bin"
    source.write_bytes(b"\x00\x01")

    #: un type hors taxonomie est une erreur explicite, pas un fourre-tout
    with pytest.raises(ValueError, match="unknown proof artifact type"):
        case.attach_file(source, "libre", "banane")

    #: un suffixe inconnu retombe sur le type générique "file"
    assert case.attach_file(source, "brut")["type"] == "file"


def test_attach_file_maps_known_suffixes_to_artifact_types(tmp_path):
    """Chaque suffixe connu (png, cast, webm, log, ndjson, json) détermine seul
    le type d'artefact, sans indication de l'appelant."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_suffixes",
        root=tmp_path,
        suite="unit",
        title="suffixes",
    )
    expectations = {
        "shot.png": "screenshot",
        "record.cast": "asciinema",
        "clip.webm": "video",
        "trace.log": "logs",
        "journal.ndjson": "logs",
        "payload.json": "json",
    }
    for name, expected in expectations.items():
        source = tmp_path / name
        if expected in {"screenshot", "video"}:
            source.write_bytes(b"\x89BIN\x00")
        else:
            source.write_text("{}\n" if expected == "json" else "line\n", encoding="utf-8")
        #: le suffixe suffit à typer l'artefact, quel que soit le contenu
        assert case.attach_file(source, name)["type"] == expected, name

    #: le .cast est textuel (ndjson) donc redactable, mais jamais uploadable tel quel
    cast = next(artifact for artifact in case.artifacts if artifact.type == "asciinema")
    assert cast.classification == ArtifactClassification.INTERNAL.value


def test_attach_file_carries_redacted_excerpt_and_meta(tmp_path):
    """L'extrait et les métadonnées fournis à l'attache sont redactés avant
    sérialisation: ni argv ni extrait ne portent encore la valeur secrète."""
    context = RedactionContext.from_secrets(["canary-value"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_meta",
        root=tmp_path,
        suite="unit",
        title="meta",
    )
    case.redaction_context = context
    source = tmp_path / "out.log"
    source.write_text("full output\n", encoding="utf-8")

    entry = case.attach_file(
        source,
        "commande",
        "logs",
        excerpt="tail canary-value tail",
        meta={"argv": ["cdpx", "--token", "canary-value"], "exit_code": 0},
    )

    #: extrait et argv sont nettoyés du canari, mais les métadonnées
    #: neutres (exit code) survivent intactes
    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)
    assert entry["excerpt"].startswith("tail")
    assert entry["meta"]["exit_code"] == 0


def test_attach_command_output_builds_redacted_transcript_with_excerpt(tmp_path):
    """Une exécution CLI devient une preuve autonome: transcript complet
    redacté sur disque, extrait tête+queue honnête sur l'omission, et
    métadonnées d'exécution exploitables."""
    context = RedactionContext.from_secrets(["canary-value"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_command",
        root=tmp_path,
        suite="unit",
        title="command",
        redaction_context=context,
    )
    stdout = "\n".join([f"line-{index}" for index in range(120)] + ["token canary-value"])

    entry = case.attach_command_output(
        "cdpx version",
        ["cdpx", "--token", "canary-value", "version"],
        stdout,
        "warning canary-value",
        3,
        duration_s=1.2345,
        excerpt_lines=20,
    )

    #: la commande est un artefact interne uploadable, avec code de sortie
    #: fidèle et durée arrondie au millième
    assert entry["type"] == "command"
    assert entry["classification"] == "internal"
    assert entry["upload_allowed"] is True
    assert entry["meta"]["exit_code"] == 3
    assert entry["meta"]["duration_s"] == 1.234

    transcript = (tmp_path / entry["path"]).read_text(encoding="utf-8")
    #: le transcript porte argv, stdout, stderr et exit code, redactés
    assert transcript.startswith("$ cdpx --token *** version")
    assert "--- stdout ---" in transcript and "--- stderr ---" in transcript
    assert "--- exit_code: 3 ---" in transcript
    assert "canary-value" not in transcript

    #: l'extrait tête+queue annonce honnêtement l'omission
    assert "lines omitted" in entry["excerpt"]
    assert entry["excerpt"].startswith("line-0")
    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)


def test_attach_log_excerpt_selects_pattern_range_and_absence(tmp_path):
    """L'extrait de log sait cibler par motif avec contexte, par plage de
    lignes, dire l'absence de correspondance et annoncer les troncatures —
    mais refuse motif et plage à la fois."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_log_excerpt",
        root=tmp_path,
        suite="unit",
        title="log excerpt",
    )
    log = tmp_path / "app.log"
    log.write_text(
        "\n".join(f"entry {index}" + (" ERROR boom" if index == 30 else "") for index in range(60)),
        encoding="utf-8",
    )

    by_pattern = case.attach_log_excerpt(log, "erreurs", pattern="ERROR", context=1)
    assert by_pattern["type"] == "log-excerpt"
    #: chaque ligne est préfixée source:numéro pour rester traçable
    assert "app.log:31: entry 30 ERROR boom" in by_pattern["excerpt"]
    assert by_pattern["meta"]["matched_lines"] == [31]
    assert len(by_pattern["excerpt"].splitlines()) == 3

    by_range = case.attach_log_excerpt(log, "plage", line_range=(1, 2))
    #: la plage demandée est restituée exactement, bornes incluses
    assert by_range["excerpt"].splitlines() == ["app.log:1: entry 0", "app.log:2: entry 1"]

    #: l'absence de correspondance est une preuve, pas une erreur
    absent = case.attach_log_excerpt(log, "absent", pattern="FATAL")
    assert "no match for" in absent["excerpt"]

    truncated = case.attach_log_excerpt(log, "tronque", max_lines=5)
    #: la troncature dit combien de lignes manquent, la preuve reste honnête
    assert "(55 lines omitted)" in truncated["excerpt"]

    #: motif et plage sont deux modes exclusifs: l'ambiguïté est refusée
    with pytest.raises(ValueError, match="mutually exclusive"):
        case.attach_log_excerpt(log, "conflit", pattern="x", line_range=(1, 2))


def test_attach_cast_keeps_cast_local_and_redacted(tmp_path):
    """Un enregistrement asciicast attaché est redacté sur disque mais reste
    interdit d'upload malgré sa nature textuelle."""
    context = RedactionContext.from_secrets(["canary-value"])
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_cast",
        root=tmp_path,
        suite="unit",
        title="cast",
        redaction_context=context,
    )
    cast = tmp_path / "session.cast"
    cast.write_text(
        '{"version": 2, "width": 80}\n[0.1, "o", "hello canary-value"]\n',
        encoding="utf-8",
    )

    entry = case.attach_cast(cast, "make proof")

    assert entry["type"] == "asciinema"
    #: textuel donc redacté, mais jamais uploadable (secret fragmentable en ndjson)
    assert entry["classification"] == "internal"
    assert entry["upload_allowed"] is False
    assert "canary-value" not in (tmp_path / entry["path"]).read_text(encoding="utf-8")


def test_evidence_session_writes_grouped_scenarios(tmp_path):
    """La session regroupe les cas par suite dans des fichiers scenarios v2
    distincts, et un cas marqué conserve son identité feature/scenario."""
    session = EvidenceSession(tmp_path)
    case = session.case_for_item(
        FakeItem(
            "tests/test_cli.py::test_cli_contract",
            FakeMarker(
                feature="harness-proof-cockpit",
                journey="run-quality-gate",
                scenario_id="harness-proof-cockpit.run-local-quality-gate",
            ),
        )
    )
    case.status = "passed"
    session.case_for_item(FakeItem("tests/e2e/test_e2e_chrome.py::test_page")).status = "passed"

    paths = session.write()

    #: un fichier de scénarios par suite rencontrée, rien pour les suites
    #: absentes de la session
    assert sorted(path.rsplit("/", 1)[-1] for path in paths) == [
        "e2e-scenarios.json",
        "integration-scenarios.json",
    ]
    for path in paths:
        payload = json.loads((tmp_path / path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
        #: chaque groupe publié respecte le schéma scenarios v2 du cockpit
        assert payload["schema"] == "cdpx.scenarios/v2"
    #: le marqueur scenario du test se retrouve dans la preuve du cas
    assert case.as_dict()["feature"] == "harness-proof-cockpit"
    assert case.as_dict()["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"
