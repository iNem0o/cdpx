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
    """The proof suite is deduced from the nodeid alone, without any marker
    or configuration: e2e directory, CLI files in integration, the rest in
    unit."""
    #: three representative nodeids are enough to cover the three suites
    assert classify_nodeid("tests/e2e/test_e2e_chrome.py::test_x") == "e2e"
    assert classify_nodeid("tests/test_cli.py::test_x") == "integration"
    assert classify_nodeid("tests/test_primitives.py::test_x") == "unit"


def test_evidence_case_attaches_artifacts(tmp_path):
    """Each attachment receives a consistent type, classification, and
    upload right, and everything is written under the case's private
    directory with strict POSIX permissions."""
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
    #: each attachment is typed according to its nature and filed under the case root
    assert data["artifacts"][0]["type"] == "screenshot"
    assert data["artifacts"][1]["type"] == "json"
    assert data["artifacts"][2]["type"] == "logs"
    assert all(tmp_path.as_posix() in artifact["path"] for artifact in data["artifacts"])
    #: the opaque binary stays confined locally while the redactable JSON
    #: can be uploaded
    assert data["artifacts"][0]["classification"] == "opaque-restricted"
    assert data["artifacts"][0]["upload_allowed"] is False
    assert data["artifacts"][1]["classification"] == "internal"
    assert data["artifacts"][1]["upload_allowed"] is True
    #: private directory and files unreadable by other system accounts
    assert mode(case.artifact_dir) == 0o700
    assert all(mode(tmp_path / artifact["path"]) == 0o600 for artifact in data["artifacts"])


def test_evidence_redacts_reports_and_textual_attachments(tmp_path):
    """The secret value injected into the pytest report and into the
    textual attachments reaches neither the serialized manifest nor
    disk: only the redaction mark remains."""
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
    #: the canary has vanished from the serialization and the written files,
    #: replaced everywhere by the redaction marker
    assert "proof-canary-123" not in serialized
    assert "proof-canary-123" not in (tmp_path / text_artifact["path"]).read_text()
    assert "proof-canary-123" not in (tmp_path / json_artifact["path"]).read_text()
    assert "***" in serialized


def test_attach_file_redacts_text_but_keeps_binary_restricted(tmp_path):
    """A text file is copied redacted and becomes uploadable; a binary,
    impossible to redact, is classified opaque and forbidden from upload."""
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

    #: the text copy now contains only the marker, so the upload is safe
    assert (tmp_path / text_entry["path"]).read_text() == "***\n"
    assert text_entry["upload_allowed"] is True
    #: with no redaction possible, the binary is doomed to stay local
    assert binary_entry["classification"] == ArtifactClassification.OPAQUE_RESTRICTED.value
    assert binary_entry["upload_allowed"] is False


def test_attach_file_treats_ndjson_journal_as_textual_evidence(tmp_path):
    """An .ndjson journal is textual evidence: copied redacted and classified
    internal, hence inlinable by the cockpit instead of staying opaque."""
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

    #: typed logs and classified internal: the cockpit can inline the journal
    assert entry["type"] == "logs"
    assert entry["classification"] == ArtifactClassification.INTERNAL.value
    #: the copy is redacted, the attached journal discloses nothing
    assert "canary-value" not in (tmp_path / entry["path"]).read_text()


def test_evidence_session_writes_private_manifest_with_ttl(tmp_path):
    """The session manifest carries the v2 schema, an expiration later
    than the creation, and the redaction policy version, all written
    to private files."""
    session = EvidenceSession(tmp_path, ttl=3600)
    case = session.case_for_item(FakeItem("tests/test_cli.py::test_manifest"))
    case.attach_text("safe", "hello")
    case.status = "passed"

    session.write()

    manifest_path = tmp_path / "evidence-manifest-integration.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    #: the v2 contract is complete: TTL in the future, versioned redaction
    #: policy, and classified artifacts
    assert manifest["schema"] == "cdpx.evidence/v2"
    assert manifest["expires_at"] > manifest["created_at"]
    assert manifest["redaction_policy"] == "1"
    assert any(item["classification"] == "internal" for item in manifest["artifacts"])
    #: the proof root and the manifest are out of reach of other accounts
    assert mode(tmp_path) == 0o700
    assert mode(manifest_path) == 0o600


def test_two_sessions_in_same_root_write_distinct_manifests(tmp_path):
    """Two pytest sessions writing into the same evidence directory (like
    the unit, e2e, and symfony runs of the same proof generation) produce
    distinct manifests named after their suites: the last session no longer
    overwrites the classification declared by the previous ones."""
    first = EvidenceSession(tmp_path, ttl=3600)
    first.case_for_item(FakeItem("tests/test_cli.py::test_first")).status = "passed"
    first.write()
    second = EvidenceSession(tmp_path, suite_override="symfony", ttl=3600)
    second.case_for_item(FakeItem("tests/e2e/test_e2e_symfony.py::test_second")).status = "passed"
    second.write()

    manifests = sorted(path.name for path in tmp_path.glob("evidence-manifest*.json"))
    #: each session has its own manifest, named after its suite
    assert manifests == [
        "evidence-manifest-integration.json",
        "evidence-manifest-symfony.json",
    ]
    #: both manifests remain independently readable and carry the v2 schema
    for name in manifests:
        payload = json.loads((tmp_path / name).read_text(encoding="utf-8"))
        assert payload["schema"] == "cdpx.evidence/v2"


def test_evidence_session_uses_validated_proof_retention_environment(tmp_path, monkeypatch):
    """The retention declared in the environment actually drives the
    created_at -> expires_at window of the written manifest."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    session = EvidenceSession(tmp_path)
    session.case_for_item(FakeItem("tests/test_cli.py::test_manifest")).status = "passed"

    session.write()

    manifest = json.loads(
        (tmp_path / "evidence-manifest-integration.json").read_text(encoding="utf-8")
    )
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    #: the expiration window exactly reflects the requested days,
    #: proof that the variable is read and applied
    assert (expires - created).days == 30


@pytest.mark.parametrize("value", ["0", "-1", "1.5", " 14", "91", "abc"])
def test_proof_retention_environment_is_strict_and_bounded(value, monkeypatch):
    """Any retention outside the contract (zero, negative, decimal, stray
    whitespace, out of bounds, non-numeric) is rejected loudly rather
    than silently corrected."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", value)

    #: the refusal names the faulty variable for an immediate diagnosis,
    #: whatever the shape of the invalid value
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof_retention_days()


def test_proof_retention_defaults_to_fourteen_days(monkeypatch):
    """Without configuration, the proof expires after the documented
    retention of fourteen days."""
    monkeypatch.delenv("CDPX_PROOF_RETENTION_DAYS", raising=False)

    #: the absence of the variable falls back to the documented default, without error
    assert proof_retention_days() == 14


def test_evidence_session_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    """An invalid retention blocks the session right at construction,
    before the slightest artifact has been created on disk."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "91")

    #: validation fails at construction, not at write time
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        EvidenceSession(tmp_path)

    #: nothing was written: the refusal precedes any side effect
    assert list(tmp_path.iterdir()) == []


def test_attachment_filename_cannot_escape_private_case_dir(tmp_path):
    """A piece name containing a path traversal is refused and no file
    escapes the case's private directory."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_traversal",
        root=tmp_path,
        suite="unit",
        title="traversal",
    )

    #: the traversing name is rejected before any write
    with pytest.raises(ValueError, match="invalid proof name"):
        case.attach_text("unsafe", "value", "../escape.txt")

    #: the target outside the private directory was never created
    assert not (tmp_path.parent / "escape.txt").exists()


def test_marker_metadata_captures_feature_and_journey():
    """The scenario marker's metadata (feature, journey, scenario_id)
    is copied as-is into the case's proof."""
    item = FakeItem(
        "tests/test_cli.py::test_cli_contract",
        FakeMarker(
            feature="harness-proof-cockpit",
            journey="run-quality-gate",
            scenario_id="harness-proof-cockpit.run-local-quality-gate",
        ),
    )

    data = marker_metadata(item)

    #: the triple declared on the marker arrives intact in the proof,
    #: it is what will link the case to the scenario cockpit
    assert data["feature"] == "harness-proof-cockpit"
    assert data["journey"] == "run-quality-gate"
    assert data["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"


def test_attach_file_enforces_closed_artifact_taxonomy(tmp_path):
    """The artifact taxonomy is closed: a made-up type is flatly refused,
    while an unknown suffix falls back to the generic type."""
    case = EvidenceCase(
        nodeid="tests/test_demo.py::test_taxonomy",
        root=tmp_path,
        suite="unit",
        title="taxonomy",
    )
    source = tmp_path / "trace.bin"
    source.write_bytes(b"\x00\x01")

    #: a type outside the taxonomy is an explicit error, not a catch-all
    with pytest.raises(ValueError, match="unknown proof artifact type"):
        case.attach_file(source, "open", "banana")

    #: an unknown suffix falls back to the generic "file" type
    assert case.attach_file(source, "raw")["type"] == "file"


def test_attach_file_maps_known_suffixes_to_artifact_types(tmp_path):
    """Each known suffix (png, cast, webm, log, ndjson, json) alone
    determines the artifact type, without any hint from the caller."""
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
        #: the suffix is enough to type the artifact, whatever the content
        assert case.attach_file(source, name)["type"] == expected, name

    #: the .cast is textual (ndjson) hence redactable, but never uploadable as-is
    cast = next(artifact for artifact in case.artifacts if artifact.type == "asciinema")
    assert cast.classification == ArtifactClassification.INTERNAL.value


def test_attach_file_carries_redacted_excerpt_and_meta(tmp_path):
    """The excerpt and metadata provided to the attachment are redacted
    before serialization: neither argv nor excerpt still carry the secret
    value."""
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
        "command",
        "logs",
        excerpt="tail canary-value tail",
        meta={"argv": ["cdpx", "--token", "canary-value"], "exit_code": 0},
    )

    #: excerpt and argv are cleaned of the canary, but the neutral
    #: metadata (exit code) survives intact
    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)
    assert entry["excerpt"].startswith("tail")
    assert entry["meta"]["exit_code"] == 0


def test_attach_command_output_builds_redacted_transcript_with_excerpt(tmp_path):
    """A CLI execution becomes a self-contained proof: full redacted
    transcript on disk, honest head+tail excerpt about the omission, and
    usable execution metadata."""
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

    #: the command is an uploadable internal artifact, with a faithful
    #: exit code and duration rounded to the millisecond
    assert entry["type"] == "command"
    assert entry["classification"] == "internal"
    assert entry["upload_allowed"] is True
    assert entry["meta"]["exit_code"] == 3
    assert entry["meta"]["duration_s"] == 1.234

    transcript = (tmp_path / entry["path"]).read_text(encoding="utf-8")
    #: the transcript carries argv, stdout, stderr, and exit code, redacted
    assert transcript.startswith("$ cdpx --token *** version")
    assert "--- stdout ---" in transcript and "--- stderr ---" in transcript
    assert "--- exit_code: 3 ---" in transcript
    assert "canary-value" not in transcript

    #: the head+tail excerpt honestly announces the omission
    assert "lines omitted" in entry["excerpt"]
    assert entry["excerpt"].startswith("line-0")
    assert "canary-value" not in json.dumps(entry, ensure_ascii=False)


def test_attach_log_excerpt_selects_pattern_range_and_absence(tmp_path):
    """The log excerpt can target by pattern with context, by line range,
    state the absence of a match, and announce truncations — but refuses
    pattern and range together."""
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

    by_pattern = case.attach_log_excerpt(log, "errors", pattern="ERROR", context=1)
    assert by_pattern["type"] == "log-excerpt"
    #: each line is prefixed source:number to stay traceable
    assert "app.log:31: entry 30 ERROR boom" in by_pattern["excerpt"]
    assert by_pattern["meta"]["matched_lines"] == [31]
    assert len(by_pattern["excerpt"].splitlines()) == 3

    by_range = case.attach_log_excerpt(log, "range", line_range=(1, 2))
    #: the requested range is returned exactly, bounds included
    assert by_range["excerpt"].splitlines() == ["app.log:1: entry 0", "app.log:2: entry 1"]

    #: the absence of a match is a proof, not an error
    absent = case.attach_log_excerpt(log, "absent", pattern="FATAL")
    assert "no match for" in absent["excerpt"]

    truncated = case.attach_log_excerpt(log, "truncated", max_lines=5)
    #: the truncation states how many lines are missing, the proof stays honest
    assert "(55 lines omitted)" in truncated["excerpt"]

    #: pattern and range are two exclusive modes: the ambiguity is refused
    with pytest.raises(ValueError, match="mutually exclusive"):
        case.attach_log_excerpt(log, "conflict", pattern="x", line_range=(1, 2))


def test_attach_cast_keeps_cast_local_and_redacted(tmp_path):
    """An attached asciicast recording is redacted on disk but stays
    forbidden from upload despite its textual nature."""
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
    #: textual hence redacted, but never uploadable (secret fragmentable in ndjson)
    assert entry["classification"] == "internal"
    assert entry["upload_allowed"] is False
    assert "canary-value" not in (tmp_path / entry["path"]).read_text(encoding="utf-8")


def test_evidence_session_writes_grouped_scenarios(tmp_path):
    """The session groups cases by suite into distinct scenarios v2 files,
    and a marked case keeps its feature/scenario identity."""
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

    #: one scenarios file per suite encountered, nothing for suites
    #: absent from the session
    assert sorted(path.rsplit("/", 1)[-1] for path in paths) == [
        "e2e-scenarios.json",
        "integration-scenarios.json",
    ]
    for path in paths:
        payload = json.loads((tmp_path / path.rsplit("/", 1)[-1]).read_text(encoding="utf-8"))
        #: each published group follows the cockpit's scenarios v2 schema
        assert payload["schema"] == "cdpx.scenarios/v2"
    #: the test's scenario marker is found again in the case's proof
    assert case.as_dict()["feature"] == "harness-proof-cockpit"
    assert case.as_dict()["scenario_id"] == "harness-proof-cockpit.run-local-quality-gate"
