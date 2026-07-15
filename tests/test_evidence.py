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
    assert classify_nodeid("tests/e2e/test_e2e_chrome.py::test_x") == "e2e"
    assert classify_nodeid("tests/test_cli.py::test_x") == "integration"
    assert classify_nodeid("tests/test_primitives.py::test_x") == "unit"


def test_evidence_case_attaches_artifacts(tmp_path):
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
    assert data["artifacts"][0]["type"] == "screenshot"
    assert data["artifacts"][1]["type"] == "json"
    assert data["artifacts"][2]["type"] == "logs"
    assert all(tmp_path.as_posix() in artifact["path"] for artifact in data["artifacts"])
    assert data["artifacts"][0]["classification"] == "opaque-restricted"
    assert data["artifacts"][0]["upload_allowed"] is False
    assert data["artifacts"][1]["classification"] == "internal"
    assert data["artifacts"][1]["upload_allowed"] is True
    assert mode(case.artifact_dir) == 0o700
    assert all(mode(tmp_path / artifact["path"]) == 0o600 for artifact in data["artifacts"])


def test_evidence_redacts_reports_and_textual_attachments(tmp_path):
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
    assert "proof-canary-123" not in serialized
    assert "proof-canary-123" not in (tmp_path / text_artifact["path"]).read_text()
    assert "proof-canary-123" not in (tmp_path / json_artifact["path"]).read_text()
    assert "***" in serialized


def test_attach_file_redacts_text_but_keeps_binary_restricted(tmp_path):
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

    assert (tmp_path / text_entry["path"]).read_text() == "***\n"
    assert text_entry["upload_allowed"] is True
    assert binary_entry["classification"] == ArtifactClassification.OPAQUE_RESTRICTED.value
    assert binary_entry["upload_allowed"] is False


def test_evidence_session_writes_private_manifest_with_ttl(tmp_path):
    session = EvidenceSession(tmp_path, ttl=3600)
    case = session.case_for_item(FakeItem("tests/test_cli.py::test_manifest"))
    case.attach_text("safe", "hello")
    case.status = "passed"

    session.write()

    manifest_path = tmp_path / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "cdpx.evidence/v2"
    assert manifest["expires_at"] > manifest["created_at"]
    assert manifest["redaction_policy"] == "1"
    assert any(item["classification"] == "internal" for item in manifest["artifacts"])
    assert mode(tmp_path) == 0o700
    assert mode(manifest_path) == 0o600


def test_evidence_session_uses_validated_proof_retention_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    session = EvidenceSession(tmp_path)
    session.case_for_item(FakeItem("tests/test_cli.py::test_manifest")).status = "passed"

    session.write()

    manifest = json.loads((tmp_path / "evidence-manifest.json").read_text(encoding="utf-8"))
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    assert (expires - created).days == 30


@pytest.mark.parametrize("value", ["0", "-1", "1.5", " 14", "91", "abc"])
def test_proof_retention_environment_is_strict_and_bounded(value, monkeypatch):
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", value)

    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof_retention_days()


def test_proof_retention_defaults_to_fourteen_days(monkeypatch):
    monkeypatch.delenv("CDPX_PROOF_RETENTION_DAYS", raising=False)

    assert proof_retention_days() == 14


def test_evidence_session_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "91")

    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        EvidenceSession(tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_attachment_filename_cannot_escape_private_case_dir(tmp_path):
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_traversal",
        root=tmp_path,
        suite="unit",
        title="traversal",
    )

    with pytest.raises(ValueError, match="nom de preuve invalide"):
        case.attach_text("unsafe", "value", "../escape.txt")

    assert not (tmp_path.parent / "escape.txt").exists()


def test_marker_metadata_captures_feature_and_journey():
    item = FakeItem(
        "tests/test_cli.py::test_cli_contract",
        FakeMarker(
            feature="harness-proof-cockpit",
            journey="run-quality-gate",
            scenario_id="harness-proof-cockpit.run-local-quality-gate",
        ),
    )

    data = marker_metadata(item)

    assert data["feature"] == "harness-proof-cockpit"
    assert data["journey"] == "run-quality-gate"
    assert data["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"


def test_attach_file_enforces_closed_artifact_taxonomy(tmp_path):
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_taxonomy",
        root=tmp_path,
        suite="unit",
        title="taxonomy",
    )
    source = tmp_path / "trace.bin"
    source.write_bytes(b"\x00\x01")

    with pytest.raises(ValueError, match="type d'artefact de preuve inconnu"):
        case.attach_file(source, "libre", "banane")

    #: un suffixe inconnu retombe sur le type générique "file"
    assert case.attach_file(source, "brut")["type"] == "file"


def test_attach_file_maps_known_suffixes_to_artifact_types(tmp_path):
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_suffixes",
        root=tmp_path,
        suite="unit",
        title="suffixes",
    )
    expectations = {
        "shot.png": "screenshot",
        "record.cast": "asciinema",
        "demo.gif": "gif",
        "clip.webm": "video",
        "trace.log": "logs",
        "payload.json": "json",
    }
    for name, expected in expectations.items():
        source = tmp_path / name
        if expected in {"screenshot", "gif", "video"}:
            source.write_bytes(b"\x89BIN\x00")
        else:
            source.write_text("{}\n" if expected == "json" else "line\n", encoding="utf-8")
        assert case.attach_file(source, name)["type"] == expected, name

    #: le .cast est textuel (ndjson) donc redactable, mais jamais uploadable tel quel
    cast = next(artifact for artifact in case.artifacts if artifact.type == "asciinema")
    assert cast.classification == ArtifactClassification.INTERNAL.value


def test_attach_file_carries_redacted_excerpt_and_meta(tmp_path):
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

    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)
    assert entry["excerpt"].startswith("tail")
    assert entry["meta"]["exit_code"] == 0


def test_attach_command_output_builds_redacted_transcript_with_excerpt(tmp_path):
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
    assert "lignes omises" in entry["excerpt"]
    assert entry["excerpt"].startswith("line-0")
    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)


def test_attach_log_excerpt_selects_pattern_range_and_absence(tmp_path):
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
    assert by_range["excerpt"].splitlines() == ["app.log:1: entry 0", "app.log:2: entry 1"]

    #: l'absence de correspondance est une preuve, pas une erreur
    absent = case.attach_log_excerpt(log, "absent", pattern="FATAL")
    assert "aucune correspondance" in absent["excerpt"]

    truncated = case.attach_log_excerpt(log, "tronque", max_lines=5)
    assert "(55 lignes omises)" in truncated["excerpt"]

    with pytest.raises(ValueError, match="mutuellement exclusifs"):
        case.attach_log_excerpt(log, "conflit", pattern="x", line_range=(1, 2))


def test_attach_cast_keeps_cast_local_and_attaches_companion_gif(tmp_path):
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
    gif = tmp_path / "session.gif"
    gif.write_bytes(b"GIF89a\x00")

    entry = case.attach_cast(cast, "make proof", gif=gif)

    assert entry["type"] == "asciinema"
    #: textuel donc redacté, mais jamais uploadable (secret fragmentable en ndjson)
    assert entry["classification"] == "internal"
    assert entry["upload_allowed"] is False
    assert "canary-value" not in (tmp_path / entry["path"]).read_text(encoding="utf-8")

    gif_artifact = next(artifact for artifact in case.artifacts if artifact.type == "gif")
    assert gif_artifact.classification == ArtifactClassification.OPAQUE_RESTRICTED.value
    assert gif_artifact.upload_allowed is False

    #: le gif compagnon est optionnel: absent => dégradation silencieuse
    solo = case.attach_cast(cast, "sans gif", gif=tmp_path / "missing.gif")
    assert solo["type"] == "asciinema"


def test_evidence_session_writes_grouped_scenarios(tmp_path):
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

    assert sorted(path.rsplit("/", 1)[-1] for path in paths) == [
        "e2e-scenarios.json",
        "integration-scenarios.json",
    ]
    for path in paths:
        payload = json.loads((tmp_path / path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
        assert payload["schema"] == "cdpx.scenarios/v2"
    assert case.as_dict()["feature"] == "harness-proof-cockpit"
    assert case.as_dict()["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"
