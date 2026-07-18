import json
import os
import stat
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from cdpx import proof
from cdpx.artifacts import ArtifactClassification, ArtifactError
from cdpx.cli import build_parser
from cdpx.proofing import cast as proof_cast
from cdpx.security.redaction import RedactionContext


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_cast_private_write_creates_private_parent_and_refuses_symlink(tmp_path):
    """Writing .cast files follows the shared private-write protocol from
    private_io (parent created 0700, file 0600, symlink refused fail-closed)
    instead of a weaker local duplicate specific to the cast module."""
    cast_path = tmp_path / "nested" / "demo.cast"

    proof_cast._write_private_text(cast_path, '{"version":2}\n')

    #: the parent is created and hardened, the cast stays private
    assert mode(cast_path.parent) == 0o700
    assert mode(cast_path) == 0o600
    outside = tmp_path / "outside.cast"
    outside.write_text("preserve", encoding="utf-8")
    link = tmp_path / "linked.cast"
    link.symlink_to(outside)
    #: a symlink in place of the cast is refused without following the target
    with pytest.raises(ArtifactError, match="symlink forbidden"):
        proof_cast._write_private_text(link, "replace")
    assert outside.read_text(encoding="utf-8") == "preserve"


def _manifest_entry(path, classification, upload_allowed):
    return {
        "path": path,
        "bytes": 1,
        "sha256": "0" * 64,
        "mime": "text/plain",
        "classification": classification,
        "upload_allowed": upload_allowed,
        "redaction_policy": "1",
        "created_at": "2026-07-15T00:00:00+00:00",
    }


def _write_evidence_manifest(
    proof_dir,
    entries,
    *,
    name="evidence-manifest-unit.json",
    schema="cdpx.evidence/v2",
    redaction_policy="1",
):
    payload = {
        "schema": schema,
        "created_at": "2026-07-15T00:00:00+00:00",
        "expires_at": "2026-07-29T00:00:00+00:00",
        "redaction_policy": redaction_policy,
        "artifacts": entries,
        "redaction": {},
    }
    proof._write_private_text(
        proof_dir / "evidence" / name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def test_repo_env_is_allowlisted_and_excludes_credentials(monkeypatch):
    """The environment passed to proof commands is built via an allowlist:
    ambient shell credentials never enter it, only useful variables and the
    retention setting pass through."""
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("CDPX_TEST_SECRET", "cdpx-secret")
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")

    env = proof._repo_env()

    #: only allowlisted variables survive, PYTHONPATH is guaranteed for subprocesses
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/tmp/home"
    assert "PYTHONPATH" in env
    #: credentials present in the shell cannot leak into proof logs
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "CDPX_TEST_SECRET" not in env
    #: the retention setting, not sensitive, is explicitly allowed through
    assert env["CDPX_PROOF_RETENTION_DAYS"] == "30"


def test_run_evidence_redacts_command_and_output_and_uses_private_mode(tmp_path, evidence_case):
    """An evidence run redacts its output before writing to disk and
    protects the log in private mode: the secret value never reaches a
    file readable by others."""
    secret = "proof-secret-123"
    context = RedactionContext.from_secrets([secret])
    log = tmp_path / "logs" / "command.log"

    evidence = proof.run_evidence(
        "secret",
        "Secret",
        [sys.executable, "-c", f"print('token={secret}'); print('Bearer abcdefghijk')"],
        log,
        env={"PATH": "/usr/bin"},
        redaction_context=context,
    )

    contents = log.read_text(encoding="utf-8")
    #: the real command ran and its verdict is captured in the evidence
    assert evidence.exit_code == 0
    #: the secret value (also present in argv) is replaced by the
    #: redaction marker before reaching the final log
    assert secret not in contents
    assert "***" in contents
    #: directory 0700 and log 0600: the raw evidence stays private to the owner
    assert mode(log.parent) == 0o700
    assert mode(log) == 0o600
    #: the intermediate raw stream *.partial is removed after redaction
    assert not log.with_name(f"{log.name}.partial").exists()

    if evidence_case is not None:
        # Excerpt targeted on the *** marker of the log ALREADY redacted on
        # disk: the visual proof shows the censorship without ever carrying
        # the secret.
        excerpt = evidence_case.attach_log_excerpt(
            log,
            "Redacted evidence log — *** marker in place of the secret",
            pattern=r"\*\*\*",
        )
        #: the produced proof artifact never contains the secret value, only ***
        assert secret not in Path(excerpt["path"]).read_text(encoding="utf-8")


def test_build_shareable_proof_allowlists_sanitized_text_and_excludes_opaque(
    tmp_path, evidence_case
):
    """The shareable staging only carries allowlisted sanitized text; opaque
    binaries stay out of staging and are classified as not uploadable in the
    private manifest."""
    proof_dir = tmp_path / ".proof"
    report = '<script>const graph={data:[1,2]};const icon="data:image/png;base64,abc";</script>'
    proof._write_private_text(proof_dir / "proof-report.html", report)
    proof._write_private_text(proof_dir / "validation-summary.json", '{"ok": true}\n')
    proof._write_private_bytes(proof_dir / "evidence" / "shot.png", b"\x89PNG\r\n")
    _write_evidence_manifest(proof_dir, [_manifest_entry("shot.png", "opaque-restricted", False)])

    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        ttl=7200,
        pre_redacted_paths={"proof-report.html"},
    )

    #: only the report and summary make it into staging, the binary capture is withheld
    assert (staging / ".proof" / "proof-report.html").exists()
    assert (staging / ".proof" / "validation-summary.json").exists()
    assert not (staging / ".proof" / "evidence" / "shot.png").exists()
    public_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    #: the public manifest announces a future expiration and only allows uploadable items
    assert public_manifest["expires_at"] > public_manifest["created_at"]
    assert all(item["upload_allowed"] for item in public_manifest["artifacts"])
    #: staging keeps private permissions, nothing is widened before upload
    assert mode(staging) == 0o700
    assert mode(staging / "manifest.json") == 0o600
    assert mode(staging / ".proof" / "proof-report.html") == 0o600
    #: the pre-redacted report is copied as-is, without destructive re-sanitization
    assert (staging / ".proof" / "proof-report.html").read_text(encoding="utf-8") == report
    private_manifest = json.loads(
        (proof_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    screenshot = next(
        item for item in private_manifest["artifacts"] if item["path"].endswith("shot.png")
    )
    #: the private manifest traces the decision to exclude the binary, auditable afterwards
    assert screenshot["classification"] == "opaque-restricted"
    assert screenshot["upload_allowed"] is False

    if evidence_case is not None:
        # Both manifests materialize the allowlist decision: the public one
        # only lists the uploadable, the private one keeps the trace of the
        # exclusion.
        evidence_case.attach_json(
            "Shareable public manifest (allowlist)",
            public_manifest,
            filename="public-manifest.json",
        )
        evidence_case.attach_json(
            "Private manifest (opaque binary exclusion)",
            private_manifest,
            filename="private-manifest.json",
        )


def test_build_shareable_proof_fails_closed_on_canary(tmp_path):
    """Detecting a canary in an artifact fails the build closed: no partial
    staging survives the failure."""
    proof_dir = tmp_path / ".proof"
    # A pipeline log (allowlisted, hence uploadable) that carries the canary.
    proof._write_private_text(proof_dir / "ruff-check.log", "leaked-canary")

    #: a canary present in an artifact blocks the build and is named in the error
    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(proof_dir, canaries=["leaked-canary"])

    #: closed failure: no shareable residue was created
    assert not (proof_dir / "shareable").exists()


def test_pre_redacted_report_still_fails_closed_on_canary(tmp_path):
    """Declaring a pre-redacted file does not exempt it from the canary
    check: the anti-leak verification stays systematic."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>leaked-canary</p>")

    #: even a path declared pre-redacted is scanned and blocks the build
    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(
            proof_dir,
            canaries=["leaked-canary"],
            pre_redacted_paths={"proof-report.html"},
        )

    #: the closed failure left no residual staging
    assert not (proof_dir / "shareable").exists()


def test_secret_text_artifact_never_staged_even_if_not_canary(tmp_path):
    """A text artifact declared secret and not uploadable in the evidence
    manifest never reaches the shareable staging, even when its value is
    unknown to the canaries: the classification declared by the test takes
    precedence over the MIME policy that would have reclassified it as
    internal/uploadable."""
    proof_dir = tmp_path / ".proof"
    secret_value = "session-token-9f8e7d6c"
    secret_rel = "artifacts/unit/tests-test_demo-py-test_token/token.txt"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")
    proof._write_private_text(proof_dir / "evidence" / secret_rel, secret_value + "\n")
    _write_evidence_manifest(proof_dir, [_manifest_entry(secret_rel, "secret", False)])

    staging = proof.build_shareable_proof(proof_dir, canaries=["other-value"], ttl=3600)

    #: the secret file, even though .txt, is not copied into staging
    assert not (staging / ".proof" / "evidence" / secret_rel).exists()
    #: the secret value is absent from the whole shareable tree
    shared_files = [path for path in staging.rglob("*") if path.is_file()]
    assert shared_files
    assert all(secret_value.encode() not in path.read_bytes() for path in shared_files)
    public_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    #: the public manifest never announces the secret artifact
    assert not any(item["path"].endswith("token.txt") for item in public_manifest["artifacts"])
    private_manifest = json.loads(
        (proof_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    secret_entry = next(
        item for item in private_manifest["artifacts"] if item["path"].endswith("token.txt")
    )
    #: the private manifest traces the secret classification and the upload ban
    assert secret_entry["classification"] == "secret"
    assert secret_entry["upload_allowed"] is False


def test_unmanifested_evidence_file_fails_closed(tmp_path):
    """An evidence file outside the pipeline allowlist and absent from the
    manifests blocks staging: no unknown artifact can slip into the
    shareable tree via the MIME policy."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")
    proof._write_private_text(proof_dir / "evidence" / "artifacts" / "rogue.txt", "dump\n")

    #: the unmanifested file is named in the fail-closed error
    with pytest.raises(ArtifactError, match="unmanifested proof artifact"):
        proof.build_shareable_proof(proof_dir, ttl=3600)

    #: closed failure: no residual staging was produced
    assert not (proof_dir / "shareable").exists()


def test_evidence_policy_merges_duplicates_to_most_restrictive(tmp_path):
    """When two manifests declare the same path with diverging
    classifications, the aggregation keeps the most restrictive one and only
    allows the upload if all manifests allow it."""
    proof_dir = tmp_path / ".proof"
    shared_rel = "artifacts/shared/output.txt"
    proof._write_private_text(proof_dir / "evidence" / shared_rel, "data\n")
    _write_evidence_manifest(
        proof_dir,
        [_manifest_entry(shared_rel, "internal", True)],
        name="evidence-manifest-unit.json",
    )
    _write_evidence_manifest(
        proof_dir,
        [_manifest_entry(shared_rel, "secret", False)],
        name="evidence-manifest-e2e.json",
    )

    policy = proof._load_evidence_policy(proof_dir)

    classification, upload_allowed = policy[(proof_dir / "evidence" / shared_rel).resolve()]
    #: the most restrictive classification wins, regardless of manifest order
    assert classification is ArtifactClassification.SECRET
    #: the upload requires unanimity across manifests: a single refusal is enough to forbid it
    assert upload_allowed is False


def test_evidence_manifest_with_unexpected_schema_fails_closed(tmp_path):
    """An evidence manifest with an unknown schema invalidates the whole
    staging: rather than interpreting classifications from an unforeseen
    format, the build fails closed."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")
    _write_evidence_manifest(proof_dir, [], schema="cdpx.evidence/v1")

    #: the unexpected schema is rejected before any staging decision
    with pytest.raises(ArtifactError, match="unexpected evidence manifest schema"):
        proof.build_shareable_proof(proof_dir, ttl=3600)

    #: closed failure: no residual staging was produced
    assert not (proof_dir / "shareable").exists()


def test_evidence_manifests_with_mixed_redaction_policies_fail_closed(tmp_path):
    """Manifests coming from different redaction policies cannot be merged:
    their guarantees are not comparable, staging fails closed."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")
    _write_evidence_manifest(proof_dir, [], name="evidence-manifest-unit.json")
    _write_evidence_manifest(proof_dir, [], name="evidence-manifest-e2e.json", redaction_policy="2")

    #: heterogeneous redaction policies is a named error, not a silent merge
    with pytest.raises(ArtifactError, match="heterogeneous redaction policies"):
        proof.build_shareable_proof(proof_dir, ttl=3600)

    #: closed failure: no residual staging was produced
    assert not (proof_dir / "shareable").exists()


def test_build_shareable_proof_uses_validated_environment_retention(tmp_path, monkeypatch):
    """The retention read from the environment really drives the expiration
    written into the shareable manifest."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    staging = proof.build_shareable_proof(proof_dir)

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    #: the created/expires gap reflects exactly the retention requested via the environment
    assert (expires - created).days == 30


def test_build_shareable_proof_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    """A non-numeric retention is rejected with an error naming the faulty
    variable, rather than silently replaced by a default."""
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "unbounded")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    #: the invalid value is rejected and the error targets the variable for a direct diagnosis
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.build_shareable_proof(proof_dir)

    #: validation happens before any write: no staging despite a healthy report
    assert not (proof_dir / "shareable").exists()


def test_generate_rejects_invalid_retention_before_replacing_existing_proof(tmp_path, monkeypatch):
    """generate() validates retention before purging .proof: an invalid
    configuration never destroys the existing proof."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    marker = proof_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "0")

    #: zero retention is rejected right at the entry of generate()
    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.generate()

    #: the previous proof is intact: no purge before successful validation
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_proof_timeout_scale_is_validated_fail_closed():
    """The deadline scale factor is validated like retention: only a
    strictly positive float is accepted, otherwise the error names the
    faulty variable instead of silently falling back to the default."""
    #: without the variable, the neutral factor 1.0 applies
    assert proof.proof_timeout_scale({}) == 1.0
    #: a valid factor uniformly multiplies deadline budgets
    assert proof.proof_timeout_scale({"CDPX_PROOF_TIMEOUT_SCALE": "2.5"}) == 2.5
    #: a non-numeric, zero, or negative value is rejected, naming the variable
    for invalid in ("abc", "0", "-1", "1s"):
        with pytest.raises(ValueError, match="CDPX_PROOF_TIMEOUT_SCALE"):
            proof.proof_timeout_scale({"CDPX_PROOF_TIMEOUT_SCALE": invalid})


def test_generate_preserves_previous_proof_when_a_step_fails(tmp_path, monkeypatch):
    """An exception in the middle of generation leaves the previous proof
    intact: everything is written to a disposable staging area and the
    atomic swap never happens on a failed run."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    sentinel = proof_dir / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")

    def explode(*_args, **_kwargs):
        raise RuntimeError("proof step broken")

    monkeypatch.setattr(proof, "run_evidence", explode)

    #: the step failure propagates instead of being disguised as a partial proof
    with pytest.raises(RuntimeError, match="proof step broken"):
        proof.generate()

    #: the previous proof was neither destroyed nor altered by the failed run
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    #: no partial swap: no .proof.old, and the failed staging stays
    #: outside .proof, available for diagnosis
    assert not (tmp_path / ".proof.old").exists()
    assert (tmp_path / ".proof.new").is_dir()


def test_generate_recovers_interrupted_swap_before_purging_leftovers(tmp_path, monkeypatch):
    """A crash between the two os.replace calls of the final swap leaves the
    last good proof in .proof.old without .proof: the next run restores it
    to the canonical location BEFORE purging the leftovers, instead of
    destroying it via rmtree."""
    proof_dir = tmp_path / ".proof"
    previous = tmp_path / ".proof.old"
    previous.mkdir()
    (previous / "keep.txt").write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")

    def explode(*_args, **_kwargs):
        raise RuntimeError("proof step broken")

    monkeypatch.setattr(proof, "run_evidence", explode)

    #: the injected failure interrupts the run AFTER the swap recovery
    with pytest.raises(RuntimeError, match="proof step broken"):
        proof.generate()

    #: the last good proof is back in .proof, not destroyed in .proof.old
    assert (proof_dir / "keep.txt").read_text(encoding="utf-8") == "preserve"
    #: the leftover of the interrupted swap was indeed purged after recovery
    assert not previous.exists()


def test_generate_reports_unpurgeable_staging_with_actionable_error(tmp_path, monkeypatch):
    """A non-purgeable residual staging (root files left by an interrupted
    Docker run) produces an actionable error naming the remedy, instead of a
    raw PermissionError, and does not touch the proof in place."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    sentinel = proof_dir / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    staging = tmp_path / ".proof.new"
    staging.mkdir()
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")

    def deny(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(proof.shutil, "rmtree", deny)

    #: the impossible purge fails closed with the remedy (chown via disposable container)
    with pytest.raises(ArtifactError, match="leftover staging cannot be purged"):
        proof.generate()

    #: the previous proof stays intact despite the aborted run
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def _fake_run_evidence(
    id, label, argv, log_path, *, env, timeout=None, path_rewrites=(), **_kwargs
):
    # The fake honors the published-path rewrite contract: the log attests
    # that _generate() correctly forwards the staging -> .proof rewrites.
    proof._write_private_text(
        log_path, proof._rewrite_text_paths(f"$ {' '.join(argv)}\nok\n", path_rewrites)
    )
    return proof.CommandEvidence(
        id=id,
        label=label,
        argv=list(argv),
        log=str(log_path),
        exit_code=0,
        duration_s=0.01,
        status="ok",
    )


def _fake_symfony_evidence(*, proof_dir, **_kwargs):
    # Fake green Symfony gate: log written into the target tree (staging), ok.
    log_path = proof_dir / "symfony-e2e.log"
    proof._write_private_text(log_path, "docker compose ok\n")
    return proof.CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=["docker", "compose", "up"],
        log=str(log_path),
        exit_code=0,
        duration_s=0.01,
        status="ok",
    )


def _fake_cast_entries(root, **_kwargs):
    return [
        {"id": cast_id, "path": str(root / f"{cast_id}.cast"), "bytes": 1, "status": "generated"}
        for cast_id, _argv in proof.CAST_COMMANDS
    ]


def _fake_git_context(**_kwargs):
    return {
        "branch": "test",
        "sha": "abc1234",
        "status_code": 0,
        "diff_stat_code": 0,
        "changed_files": [],
        "generated_files": [],
        "changed_count": 0,
        "generated_count": 0,
        "status_path": ".proof/git-status.txt",
        "diff_stat_path": ".proof/git-diff-stat.txt",
    }


def _install_generate_fakes(monkeypatch, tmp_path, *, run_evidence=None, symfony=None, casts=None):
    # Wires the _generate() pipeline to deterministic fakes (no external
    # process) and returns the target .proof; each test replaces the brick
    # in which it wants to inject the failure. The runtime evidence store is
    # also confined to tmp_path: the retention purge at the start of the run
    # must never touch the repo's real store during tests.
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")
    monkeypatch.setattr(proof, "run_evidence", run_evidence or _fake_run_evidence)
    monkeypatch.setattr(proof, "run_symfony_evidence", symfony or _fake_symfony_evidence)
    monkeypatch.setattr(proof, "collect_cast_evidence", casts or _fake_cast_entries)
    monkeypatch.setattr(proof, "collect_git_context", _fake_git_context)
    return proof_dir


def test_generate_publishes_staging_atomically_on_success(tmp_path, monkeypatch):
    """A complete run writes everything to .proof.new then publishes the
    final tree into .proof via an atomic swap: paths published under
    .proof/…, no staging residue, previous proof replaced."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    (proof_dir / "stale.txt").write_text("old run", encoding="utf-8")

    summary = proof.generate()

    #: the final tree is published at the canonical location, shareable staging included
    summary_path = proof_dir / "validation-summary.json"
    assert summary_path.is_file()
    assert (proof_dir / "proof-report.html").is_file()
    assert (proof_dir / "shareable" / "manifest.json").is_file()
    #: the swap is complete: no residual staging nor previous proof
    assert not (tmp_path / ".proof.new").exists()
    assert not (tmp_path / ".proof.old").exists()
    #: the previous proof was indeed replaced by the new run
    assert not (proof_dir / "stale.txt").exists()
    #: the published-path contract holds: .proof/… everywhere, never the staging
    assert summary["report_html"] == ".proof/proof-report.html"
    summary_text = summary_path.read_text(encoding="utf-8")
    assert ".proof.new" not in summary_text
    assert ".proof.new" not in (proof_dir / "make-check-pytest.log").read_text(encoding="utf-8")


def test_generate_completes_with_red_verdict_when_a_command_fails(tmp_path, monkeypatch):
    """A proof command that fails (here killed by deadline, exit 124) turns
    the verdict red but does not interrupt generation: the complete tree is
    published with the cause named in proof_failures."""

    def failing_run_evidence(id, label, argv, log_path, **kwargs):
        evidence = _fake_run_evidence(id, label, argv, log_path, **kwargs)
        if id == "unit":
            return replace(evidence, exit_code=124, status="failed")
        return evidence

    proof_dir = _install_generate_fakes(monkeypatch, tmp_path, run_evidence=failing_run_evidence)

    summary = proof.generate()

    #: the command failure turns the verdict red instead of masking it
    assert summary["ok"] is False
    #: the cause is named: the faulty command and its log are in proof_failures
    assert any(
        failure.startswith("command failed: Pytest unit tests")
        for failure in summary["proof_failures"]
    )
    #: generation SUCCEEDS despite the red: the complete tree is published in .proof
    assert (proof_dir / "validation-summary.json").is_file()
    assert (proof_dir / "proof-report.html").is_file()
    assert not (tmp_path / ".proof.new").exists()
    #: the verdict written to disk is consistent with the returned summary
    published = json.loads((proof_dir / "validation-summary.json").read_text(encoding="utf-8"))
    assert published["ok"] is False


def test_generate_purges_orphans_when_pytest_dies_without_sessionfinish(tmp_path, monkeypatch):
    """A pytest killed without an epilogue (SIGKILL, returncode -9) leaves
    artifacts without a manifest: generation purges these orphans and
    reaches a red verdict naming the command, instead of failing with a
    misleading "unmanifested" ArtifactError."""

    def killed_run_evidence(id, label, argv, log_path, **kwargs):
        evidence = _fake_run_evidence(id, label, argv, log_path, **kwargs)
        if id == "unit":
            orphan = log_path.parent / "evidence" / "artifacts" / "unit" / "scn" / "orphan.txt"
            proof._write_private_text(orphan, "written before the SIGKILL\n")
            return replace(evidence, exit_code=-9, status="failed")
        return evidence

    proof_dir = _install_generate_fakes(monkeypatch, tmp_path, run_evidence=killed_run_evidence)

    summary = proof.generate()

    #: the abnormal death turns the verdict red instead of blocking generation
    assert summary["ok"] is False
    #: the visible cause stays the command failure, never a fake "unmanifested"
    assert any(
        failure.startswith("command failed: Pytest unit tests")
        for failure in summary["proof_failures"]
    )
    #: the manifest-less orphan was purged before the shareable staging
    assert not (proof_dir / "evidence" / "artifacts" / "unit" / "scn" / "orphan.txt").exists()
    #: the complete tree is published despite the killed suite
    assert (proof_dir / "validation-summary.json").is_file()


def test_generate_warns_without_failing_when_previous_cleanup_is_denied(
    tmp_path, monkeypatch, capsys
):
    """A .proof.old that cannot be removed after publishing (root files)
    does not turn the run red — the proof is already published — but the
    actionable warning (docker chown remedy) is immediately emitted on
    stderr instead of being silently swallowed."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    (proof_dir / "stale.txt").write_text("old run", encoding="utf-8")
    real_rmtree = proof.shutil.rmtree

    def stubborn_rmtree(path, *args, **kwargs):
        if Path(path) == tmp_path / ".proof.old":
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(proof.shutil, "rmtree", stubborn_rmtree)

    proof.generate()

    #: generation succeeds: the new proof is indeed published
    assert (proof_dir / "validation-summary.json").is_file()
    captured = capsys.readouterr()
    #: the warning names the faulty folder and the chown remedy on stderr
    assert ".proof.old" in captured.err
    assert "chown" in captured.err


def _write_retention_manifest(run_dir, expires_at):
    # Minimal retention manifest: only expires_at is read by the purge.
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(json.dumps({"expires_at": expires_at}), encoding="utf-8")


def test_generate_purges_expired_evidence_runs_at_start(tmp_path, monkeypatch, capsys):
    """The retention purge at the start of generate() removes expired runs
    from the runtime evidence store, keeps fresh runs, and attests the
    decision both in summary["retention"] and on stderr."""
    monkeypatch.delenv("CDPX_PROOF_RETENTION_DAYS", raising=False)
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    store = tmp_path / ".cdpx-evidence"
    _write_retention_manifest(store / "run-expired", "2000-01-01T00:00:00+00:00")
    _write_retention_manifest(store / "run-fresh", "2999-01-01T00:00:00+00:00")

    summary = proof.generate()

    #: only the expired run disappears; the run still covered by its TTL remains intact
    assert not (store / "run-expired").exists()
    assert (store / "run-fresh" / "manifest.json").is_file()
    #: the published summary attests the purge: run listed, .proof kept, TTL named
    assert summary["retention"]["purged"]["evidence_runs"] == ["run-expired"]
    assert summary["retention"]["purged"]["proof_dir"] is False
    assert summary["retention"]["retention_days"] == 14
    #: the field indeed lands in validation-summary.json, not just in memory
    published = json.loads((proof_dir / "validation-summary.json").read_text(encoding="utf-8"))
    assert published["retention"]["purged"]["evidence_runs"] == ["run-expired"]
    #: the purge is traced on stderr, never silent
    assert "run-expired" in capsys.readouterr().err


def test_generate_purges_expired_proof_dir_before_regeneration(tmp_path, monkeypatch, capsys):
    """A .proof whose global artifact-manifest.json manifest carries an
    expired expires_at is automatically purged at the start of the run,
    before any regeneration, and the decision is attested in the summary."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    (proof_dir / "artifact-manifest.json").write_text(
        json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"}), encoding="utf-8"
    )

    summary = proof.generate()

    #: the expired proof was purged then a fresh tree published in its place
    assert summary["retention"]["purged"]["proof_dir"] is True
    assert (proof_dir / "validation-summary.json").is_file()
    #: the expired-proof purge is traced on stderr before regeneration
    assert "retention: expired local proof purged" in capsys.readouterr().err


@pytest.mark.parametrize("corruption", ["absent", "invalid"])
def test_purge_retention_keeps_proof_when_manifest_is_unreadable(tmp_path, monkeypatch, corruption):
    """Missing or corrupted retention manifest => fail-open preservation: the
    purge at the start of the run never destroys a proof whose expiration is
    unknown, same contract as purge_expired on evidence runs."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    (proof_dir / "keep.txt").write_text("preserve", encoding="utf-8")
    if corruption == "invalid":
        (proof_dir / "artifact-manifest.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")

    result = proof._purge_expired_local_proofs()

    #: nothing is purged and the purge reports no deletion
    assert result == {"evidence_runs": [], "proof_dir": False}
    #: the proof with unknown expiration is kept as-is
    assert (proof_dir / "keep.txt").read_text(encoding="utf-8") == "preserve"


def test_purge_retention_survives_evidence_run_without_manifest(tmp_path, monkeypatch, capsys):
    """A residual evidence run without manifest.json (interruption, foreign
    residue) does not fail make proof: the purge at the start of the run
    keeps it fail-open and continues, same contract as purge_expired."""
    store = tmp_path / ".cdpx-evidence"
    orphan = store / "run-orphan"
    orphan.mkdir(parents=True)
    (orphan / "chrome.log").write_text("leftover", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path / ".proof")
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", store)

    result = proof._purge_expired_local_proofs()

    #: the manifest-less run is kept and the purge concludes without error
    assert result == {"evidence_runs": [], "proof_dir": False}
    assert (orphan / "chrome.log").read_text(encoding="utf-8") == "leftover"


def test_purge_retention_warns_and_continues_on_unreadable_evidence_manifest(
    tmp_path, monkeypatch, capsys
):
    """An unreadable evidence manifest (root files left by a Docker run)
    does not break make proof: the purge warns with the chown remedy and the
    run continues — the consumer's PermissionError catch is alive."""
    store = tmp_path / ".cdpx-evidence"
    protected = store / "run-root"
    protected.mkdir(parents=True)
    manifest = protected / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def fail_manifest_read(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("permission denied")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", fail_manifest_read)
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path / ".proof")
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", store)

    result = proof._purge_expired_local_proofs()

    #: the run is not purged and nothing exploded: best-effort purge
    assert result == {"evidence_runs": [], "proof_dir": False}
    assert protected.exists()
    #: the actionable warning names the store and the ownership remedy
    err = capsys.readouterr().err
    assert "warning: retention purge impossible" in err and "chown" in err


def test_purge_retention_never_touches_transactional_dirs(tmp_path, monkeypatch):
    """The retention purge ignores .proof.new and .proof.old even when
    carrying an expired manifest: these directories belong to _generate's
    transactional logic, never to retention."""
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path / ".proof")
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")
    expired = json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"})
    for name in (".proof.new", ".proof.old"):
        side = tmp_path / name
        side.mkdir()
        (side / "artifact-manifest.json").write_text(expired, encoding="utf-8")

    result = proof._purge_expired_local_proofs()

    #: the purge claims no deletion: neither evidence run nor .proof
    assert result == {"evidence_runs": [], "proof_dir": False}
    #: the transactional directories stay intact despite their expired manifest
    assert (tmp_path / ".proof.new" / "artifact-manifest.json").is_file()
    assert (tmp_path / ".proof.old" / "artifact-manifest.json").is_file()


def test_generate_survives_denied_retention_purge_with_actionable_warning(
    tmp_path, monkeypatch, capsys
):
    """A PermissionError during the retention purge (root files from an
    interrupted Docker run) produces an stderr warning with the chown remedy
    and lets generation succeed: retention is best-effort."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    store = tmp_path / ".cdpx-evidence"
    _write_retention_manifest(store / "run-expired", "2000-01-01T00:00:00+00:00")
    real_rmtree = proof.shutil.rmtree

    def deny(path, *args, **kwargs):
        if Path(path).name == "run-expired":
            raise PermissionError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(proof.shutil, "rmtree", deny)

    summary = proof.generate()

    #: generation SUCCEEDS despite the denied purge: the complete tree is published
    assert (proof_dir / "validation-summary.json").is_file()
    #: the non-purgeable run is not claimed as purged in the summary
    assert summary["retention"]["purged"]["evidence_runs"] == []
    captured = capsys.readouterr()
    #: the warning names the faulty store and the chown remedy on stderr
    assert ".cdpx-evidence" in captured.err
    assert "chown" in captured.err


def test_purge_retention_warns_when_expired_proof_dir_is_denied(tmp_path, monkeypatch, capsys):
    """An expired but non-removable .proof (root files) does not interrupt
    the purge: actionable warning on stderr, proof left in place and never
    claimed as purged."""
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    (proof_dir / "artifact-manifest.json").write_text(
        json.dumps({"expires_at": "2000-01-01T00:00:00+00:00"}), encoding="utf-8"
    )
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_STORE_DIR", tmp_path / ".cdpx-evidence")

    def deny(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(proof.shutil, "rmtree", deny)

    result = proof._purge_expired_local_proofs()

    #: the denied deletion is not claimed as a successful purge
    assert result == {"evidence_runs": [], "proof_dir": False}
    #: the proof stays in place, the warning names the folder and the chown remedy
    assert (proof_dir / "artifact-manifest.json").is_file()
    captured = capsys.readouterr()
    assert ".proof" in captured.err
    assert "chown" in captured.err


def test_generate_reports_missing_junit_as_red_verdict(tmp_path, monkeypatch):
    """All commands green but no JUnit produced: the verdict is red with a
    "required JUnit missing" failure per required suite — a silent
    zero-test count cannot pass as proof."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)

    summary = proof.generate()

    #: without JUnit XML, the verdict is red despite the 0 exits
    assert summary["ok"] is False
    #: each required suite (unit, e2e) is named as missing JUnit
    missing = [f for f in summary["proof_failures"] if f.startswith("required JUnit missing")]
    assert any("unit-junit.xml" in failure for failure in missing)
    assert any("e2e-junit.xml" in failure for failure in missing)
    #: the report is still published for diagnosis
    assert (proof_dir / "proof-report.html").is_file()


def test_generate_marks_symfony_unavailable_as_blocking(tmp_path, monkeypatch):
    """An unavailable Symfony gate (Docker missing) flows through
    _generate() all the way to the verdict: unavailable scenario counted,
    red verdict, causes named — never a silent skip."""

    def unavailable_symfony(*, proof_dir, redaction_context=None, **_kwargs):
        log_path = proof_dir / "symfony-e2e.log"
        proof._write_private_text(log_path, "docker unavailable\n")
        proof.write_symfony_unavailable_evidence(
            "Docker daemon unavailable",
            redaction_context=redaction_context,
            proof_dir=proof_dir,
        )
        return proof.CommandEvidence(
            id="symfony-e2e",
            label="Symfony E2E Docker",
            argv=["docker", "compose", "up"],
            log=str(log_path),
            exit_code=1,
            duration_s=0.01,
            status="unavailable",
        )

    proof_dir = _install_generate_fakes(monkeypatch, tmp_path, symfony=unavailable_symfony)

    summary = proof.generate()

    #: the Symfony unavailability turns the verdict red and shows up in the totals
    assert summary["ok"] is False
    assert summary["totals"]["unavailable"] == 1
    #: the failure names both the unavailable evidence AND the command failure
    assert any("symfony evidence unavailable" in failure for failure in summary["proof_failures"])
    assert any(
        failure.startswith("command failed: Symfony E2E Docker")
        for failure in summary["proof_failures"]
    )
    #: the explicit unavailability evidence is published with the tree
    assert (proof_dir / "evidence" / "symfony-scenarios.json").is_file()


def test_generate_flags_degraded_cast_at_the_gate(tmp_path, monkeypatch):
    """A demo cast degraded during collection fails the cast gate at
    _generate()'s final verdict, naming the faulty demo."""

    def degraded_casts(root, **_kwargs):
        entries = _fake_cast_entries(root)
        entries[0]["status"] = "unavailable"
        return entries

    _install_generate_fakes(monkeypatch, tmp_path, casts=degraded_casts)

    summary = proof.generate()

    #: the cast gate is blocking at the level of the complete pipeline, not
    #: only within build_summary
    assert summary["ok"] is False
    assert any(failure.startswith("cast unavailable:") for failure in summary["proof_failures"])


def test_generate_keeps_previous_proof_when_canary_reaches_staging(tmp_path, monkeypatch):
    """A canary that reaches the shareable staging interrupts generation
    with an ArtifactError: the swap does not happen and the previous proof
    stays intact — the transaction protects against publishing a leak."""

    def leaking_run_evidence(id, label, argv, log_path, **kwargs):
        evidence = _fake_run_evidence(id, label, argv, log_path, **kwargs)
        proof._write_private_text(log_path, "token=canary-123\n")
        return evidence

    proof_dir = _install_generate_fakes(monkeypatch, tmp_path, run_evidence=leaking_run_evidence)
    sentinel = proof_dir / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "environment_secret_values", lambda: ["canary-123"])

    #: the canary detected in staging fails generation closed
    with pytest.raises(ArtifactError, match="canary"):
        proof.generate()

    #: the previous proof was neither replaced nor altered (transaction)
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not (tmp_path / ".proof.old").exists()
    #: the failed staging stays outside .proof for diagnosis, without a shareable tree
    assert (tmp_path / ".proof.new").is_dir()
    assert not (tmp_path / ".proof.new" / "shareable").exists()


def test_generate_converts_hardening_permission_error_into_actionable_error(tmp_path, monkeypatch):
    """A PermissionError during staging hardening (root files left by a
    Symfony container killed before its chown) becomes an actionable
    ArtifactError naming the remedy, and the previous proof stays intact."""
    proof_dir = _install_generate_fakes(monkeypatch, tmp_path)
    sentinel = proof_dir / "keep.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    def deny(_root):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(proof, "_harden_tree", deny)

    #: the raw PermissionError is converted into an actionable error
    with pytest.raises(ArtifactError, match="leftover staging cannot be purged") as excinfo:
        proof.generate()
    #: the remedy (chown via disposable container) is named in the message
    assert "chown" in str(excinfo.value)

    #: the previous proof was not touched by the aborted run
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_run_evidence_times_out_with_redacted_final_log(tmp_path):
    """A command that exceeds its deadline is killed: exit 124 and status
    failed like a conventional failure, final redacted log written, no
    residual raw stream on disk."""
    secret = "deadline-secret-42"
    context = RedactionContext.from_secrets([secret])
    log = tmp_path / "slow.log"
    argv = [
        sys.executable,
        "-u",
        "-c",
        f"import time; print('token={secret}', flush=True); time.sleep(60)",
    ]

    evidence = proof.run_evidence(
        "slow",
        "Slow",
        argv,
        log,
        env={"PATH": "/usr/bin"},
        timeout=0.5,
        redaction_context=context,
    )

    #: the deadline converts the hang into a conventional exit-124 failure
    assert evidence.exit_code == 124
    assert evidence.status == "failed"
    contents = log.read_text(encoding="utf-8")
    #: the final log is written despite the kill, names the timeout, and stays redacted
    assert "timeout" in contents
    assert "exit_code: 124" in contents
    assert secret not in contents
    assert "***" in contents
    #: the raw *.partial stream does not survive the run, even killed by deadline
    assert not log.with_name(f"{log.name}.partial").exists()


def test_run_evidence_streams_progress_into_partial_file(tmp_path):
    """During execution, the raw output is observable in the private
    *.partial stream (tail -f): progress is no longer buffered in memory,
    and the final redacted log replaces the stream at the end of the run."""
    log = tmp_path / "stream.log"
    partial = log.with_name(f"{log.name}.partial")
    sync = tmp_path / "sync"
    script = (
        "import pathlib, time\n"
        "print('first-line', flush=True)\n"
        f"sync = pathlib.Path({str(sync)!r})\n"
        "for _ in range(400):\n"
        "    if sync.exists():\n"
        "        break\n"
        "    time.sleep(0.025)\n"
        "print('second-line', flush=True)\n"
    )
    result = {}

    def run():
        result["evidence"] = proof.run_evidence(
            "stream",
            "Stream",
            [sys.executable, "-u", "-c", script],
            log,
            env={"PATH": "/usr/bin"},
            timeout=30,
        )

    worker = threading.Thread(target=run)
    worker.start()
    try:
        for _ in range(400):
            if partial.exists() and "first-line" in partial.read_text(encoding="utf-8"):
                break
            time.sleep(0.025)
        #: the first line is visible in the .partial file while the command is still running
        assert "first-line" in partial.read_text(encoding="utf-8")
        #: the raw stream, not yet redacted, stays private to the owner (0600)
        assert mode(partial) == 0o600
    finally:
        sync.write_text("go", encoding="utf-8")
        worker.join(timeout=30)
    assert not worker.is_alive()
    #: after unblocking, the command completes cleanly
    assert result["evidence"].exit_code == 0
    contents = log.read_text(encoding="utf-8")
    #: the final log aggregates the entire stream and the .partial file is gone
    assert "first-line" in contents and "second-line" in contents
    assert not partial.exists()


def test_rewrite_text_paths_only_rewrites_anchored_paths():
    """Published-path rewriting is anchored: only the `root/…` prefix and
    the value exactly equal to the root change — a `.proof.new` literal
    quoted in a captured code excerpt is preserved as-is instead of being
    corrupted by a naive replacement."""
    rewrites = ((".proof.new", ".proof"),)
    excerpt = 'assert ".proof.new" not in summary_text\nlog: .proof.new/unit-junit.xml\n'

    rewritten = proof._rewrite_text_paths(excerpt, rewrites)

    #: the real path, anchored by a slash, is rewritten to the logical root
    assert "log: .proof/unit-junit.xml" in rewritten
    #: the literal quoted without a slash survives: code excerpts stay faithful
    assert 'assert ".proof.new" not in summary_text' in rewritten
    #: a value exactly equal to the physical root is rewritten in full
    assert proof._rewrite_text_paths(".proof.new", rewrites) == ".proof"


def test_stream_deadline_kills_the_entire_process_group(tmp_path):
    """The deadline kills the entire process group, not just the direct
    child: a grandchild (Chrome, fixture server launched by pytest) never
    survives the kill, keeps no port, and can no longer write into evidence
    after the purge."""
    pids_path = tmp_path / "pids.txt"
    wait_loop = "\nfor _ in range(400): time.sleep(0.025)\n"
    grandchild = "import time" + wait_loop
    child = (
        "import os, subprocess, sys, time\n"
        f"grand = subprocess.Popen([sys.executable, '-c', {grandchild!r}])\n"
        f"with open({str(pids_path)!r}, 'w') as fh:\n"
        "    fh.write(f'{os.getpid()} {grand.pid}')\n" + wait_loop
    )
    sink = tmp_path / "stream.log"

    code, timed_out = proof._stream_to_private_file(
        [sys.executable, "-u", "-c", child], sink, env={"PATH": "/usr/bin"}, timeout=2.0
    )

    #: the deadline is converted into a conventional exit 124
    assert code == 124
    assert timed_out is True
    pids = [int(value) for value in pids_path.read_text(encoding="utf-8").split()]
    assert len(pids) == 2
    #: BOUNDED wait (≤ 10 s): both the child AND the grandchild are dead
    deadline = time.monotonic() + 10
    for pid in pids:
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, f"process {pid} survives the group kill"
            time.sleep(0.05)


def test_stream_and_collect_unlinks_raw_partial_on_unexpected_failure(tmp_path, monkeypatch):
    """The raw *.partial stream (not redacted) is removed even when an
    unexpected exception interrupts collection: a partial staging kept for
    diagnosis never contains raw output."""
    log = tmp_path / "cmd.log"

    def exploding_stream(argv, sink, *, env, timeout):
        sink.write_text("raw secret stream\n", encoding="utf-8")
        raise KeyboardInterrupt

    monkeypatch.setattr(proof, "_stream_to_private_file", exploding_stream)

    #: the unexpected exception propagates to the caller, it's not swallowed
    with pytest.raises(KeyboardInterrupt):
        proof._stream_and_collect(
            ["cmd"], log, env={}, timeout=1.0, timeout_label="interrupted command"
        )

    #: the raw stream did not survive the failure: no unredacted residue
    assert not log.with_name(f"{log.name}.partial").exists()


def test_pytest_timeout_orphans_are_purged_before_shareable_staging(tmp_path):
    """Artifacts from a pytest killed by deadline (session without a
    manifest) are purged from the evidence tree: the shareable staging
    succeeds instead of failing closed with a misleading message, without
    touching manifested artifacts."""
    proof_dir = tmp_path / ".proof"
    kept = proof_dir / "evidence" / "artifacts" / "unit" / "scn" / "kept.txt"
    proof._write_private_text(kept, "manifested\n")
    orphan = proof_dir / "evidence" / "artifacts" / "e2e" / "scn" / "orphan.txt"
    proof._write_private_text(orphan, "written before the kill\n")
    _write_evidence_manifest(
        proof_dir, [_manifest_entry("artifacts/unit/scn/kept.txt", "internal", True)]
    )
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    #: without the purge, the orphan still fails staging closed (fail-closed preserved)
    with pytest.raises(ArtifactError, match="unmanifested proof artifact"):
        proof.build_shareable_proof(proof_dir)

    removed = proof._purge_unmanifested_evidence(proof_dir)

    #: only the manifest-less orphan is purged; the manifested artifact survives
    assert removed == ["evidence/artifacts/e2e/scn/orphan.txt"]
    assert kept.is_file() and not orphan.exists()
    #: emptied folders disappear too: no residue from the killed suite
    assert not orphan.parent.exists()

    staging = proof.build_shareable_proof(proof_dir)

    #: after the purge, the shareable staging succeeds and carries the manifested artifact
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert any(
        entry["path"] == ".proof/evidence/artifacts/unit/scn/kept.txt"
        for entry in manifest["artifacts"]
    )


def test_project_unknowns_describe_private_screenshot_scope():
    """The risks/unknowns packet honestly documents visual captures:
    private, excluded from sharing, without claiming they are not kept."""
    packet = proof.build_project_risks_and_unknowns()
    screenshot = next(
        item for item in packet["unknowns"] if item["item"] == "Scope of visual captures"
    )

    #: the text locates the captures, states their exclusion from sharing, and
    #: avoids the misleading wording about them not being kept
    assert ".proof/evidence/" in screenshot["why"]
    assert "excluded from shareable staging" in screenshot["why"]
    assert "without keeping it" not in screenshot["why"]


def empty_scenario_evidence():
    suites = {"unit": [], "integration": [], "e2e": []}
    return {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}


def generated_casts():
    return [
        {"id": cast_id, "path": f".proof/{cast_id}.cast", "bytes": 64, "status": "generated"}
        for cast_id, _argv in proof.CAST_COMMANDS
    ]


def _evidence_with_artifacts(artifacts):
    suites = {
        "unit": [
            {
                "nodeid": "tests/test_demo.py::test_inline",
                "suite": "unit",
                "title": "inline",
                "status": "passed",
                "artifacts": artifacts,
            }
        ],
        "integration": [],
        "e2e": [],
    }
    return {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}


def test_inline_scenario_artifacts_inlines_small_text_and_excerpts_large(tmp_path):
    """Inlining embeds small texts whole, honestly truncates large ones,
    refuses binaries, flags unreadable paths, and never mutates the input
    evidence."""
    small = tmp_path / "run.txt"
    small.write_text("$ cdpx version\nok\n", encoding="utf-8")
    large = tmp_path / "big.log"
    large.write_text("\n".join(f"line-{index}" for index in range(2000)), encoding="utf-8")
    shot = tmp_path / "final.png"
    shot.write_bytes(b"\x89PNG\r\n")

    evidence = _evidence_with_artifacts(
        [
            {"type": "command", "label": "run", "path": str(small), "excerpt": ""},
            {"type": "logs", "label": "big", "path": str(large)},
            {"type": "screenshot", "label": "final", "path": str(shot)},
            {"type": "json", "label": "gone", "path": str(tmp_path / "missing.json")},
        ]
    )

    inlined = proof.inline_scenario_artifacts(evidence)
    command, logs, screenshot, missing = inlined["suites"]["unit"][0]["artifacts"]

    #: the small text travels whole in the payload
    assert command["inline_content"].startswith("$ cdpx version")
    assert command["truncated"] is False

    #: the large log becomes an honestly truncated head+tail excerpt
    assert "inline_content" not in logs
    assert logs["inline_skipped"] == "size"
    assert logs["truncated"] is True
    assert logs["excerpt"].startswith("line-0")
    assert "lines truncated" in logs["excerpt"]

    #: the binary is never inlined (it would remain in the shareable HTML)
    assert "inline_content" not in screenshot and "inline_skipped" not in screenshot

    #: an unreadable path is flagged, not fatal
    assert missing["inline_skipped"] == "unreadable"

    #: the input artifacts are not mutated
    assert "inline_content" not in evidence["suites"]["unit"][0]["artifacts"][0]


def test_inline_scenario_artifacts_respects_global_budget(tmp_path):
    """The global inlining budget bounds the report's weight: the first
    artifacts travel whole, the following ones degrade into a marked
    excerpt."""
    files = []
    for index in range(3):
        path = tmp_path / f"part-{index}.txt"
        path.write_text("x" * 1000, encoding="utf-8")
        files.append({"type": "command", "label": f"part-{index}", "path": str(path)})

    inlined = proof.inline_scenario_artifacts(_evidence_with_artifacts(files), budget=2500)
    first, second, third = inlined["suites"]["unit"][0]["artifacts"]

    #: as long as the budget allows it, the full content travels
    assert "inline_content" in first and "inline_content" in second
    #: budget exhausted => marked excerpt, never silently missing content
    assert third["inline_skipped"] == "budget"
    assert third["truncated"] is True and third["excerpt"]


def test_strip_inline_content_keeps_excerpts_but_drops_bodies(tmp_path):
    """The lean version meant for the summary JSON strips inlined bodies
    while keeping excerpt metadata, without touching the copy used for HTML
    rendering."""
    path = tmp_path / "run.txt"
    path.write_text("payload\n", encoding="utf-8")
    inlined = proof.inline_scenario_artifacts(
        _evidence_with_artifacts([{"type": "command", "label": "run", "path": str(path)}])
    )

    lean = proof._strip_inline_content(inlined)

    artifact = lean["suites"]["unit"][0]["artifacts"][0]
    #: the body disappears from the lean payload, truncation metadata remains
    assert "inline_content" not in artifact
    assert artifact["truncated"] is False
    #: the inlined version stays intact for HTML rendering
    assert inlined["suites"]["unit"][0]["artifacts"][0]["inline_content"] == "payload\n"


def test_render_html_size_stays_bounded():
    """The complete HTML report stays under a known size ceiling: any
    shell/CSS/JS drift beyond the Mermaid margin breaks the build."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    # Mermaid vendored ~3.5 MB; the cockpit shell/CSS/JS must stay marginal.
    #: beyond the ceiling, an asset grew without justification: the gate blocks it
    assert len(proof.render_html(summary)) < 4_500_000


def test_load_scenario_evidence_accepts_legacy_v1_payloads(tmp_path):
    """A v1 *-scenarios.json (without a schema or intent key) stays readable
    as-is: reader tolerance avoids any migrator."""
    # A v1 *-scenarios.json (no schema key, no intent/assertions) must
    # stay readable: readers are tolerant, no migrator required.
    legacy = {
        "suite": "unit",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "count": 1,
        "scenarios": [
            {
                "nodeid": "tests/test_demo.py::test_legacy",
                "suite": "unit",
                "title": "legacy",
                "status": "passed",
                "artifacts": [],
            }
        ],
    }
    (tmp_path / "unit-scenarios.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8"
    )

    evidence = proof.load_scenario_evidence(tmp_path)

    #: the legacy payload is counted and returned like a modern scenario
    assert evidence["totals"]["unit"] == 1
    assert evidence["suites"]["unit"][0]["nodeid"] == "tests/test_demo.py::test_legacy"


def test_load_scenario_evidence_rejects_invalid_json(tmp_path):
    """A corrupted *-scenarios.json is rejected with an error naming the
    faulty file, instead of an anonymous JSONDecodeError in the middle of
    proof generation."""
    path = tmp_path / "unit-scenarios.json"
    path.write_text('{"suite": "unit", scenarios', encoding="utf-8")

    #: the error locates the unreadable file and qualifies the problem
    with pytest.raises(ArtifactError, match="unreadable scenarios JSON") as excinfo:
        proof.load_scenario_evidence(tmp_path)
    assert str(path) in str(excinfo.value)


def test_load_scenario_evidence_rejects_unknown_schema_version(tmp_path):
    """A schema key present but different from cdpx.scenarios/v2 is rejected,
    naming the file, the expected version, and the found version — only the
    absence of schema (legacy v1) remains tolerated."""
    path = tmp_path / "unit-scenarios.json"
    path.write_text(
        json.dumps({"schema": "cdpx.scenarios/v3", "suite": "unit", "scenarios": []}),
        encoding="utf-8",
    )

    #: the error message carries the file, the expected version, and the received version
    with pytest.raises(ArtifactError, match="unexpected scenarios schema") as excinfo:
        proof.load_scenario_evidence(tmp_path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "cdpx.scenarios/v2" in message
    assert "cdpx.scenarios/v3" in message


def test_load_scenario_evidence_rejects_structurally_invalid_payloads(tmp_path):
    """A JSON that is valid but structurally wrong (non-object root,
    non-list scenarios, scenario without nodeid, non-list artifacts) fails
    with a localized `{file}: …` error, never a deferred KeyError."""
    path = tmp_path / "unit-scenarios.json"

    #: a non-object root is named as such
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ArtifactError, match="root expected as JSON object"):
        proof.load_scenario_evidence(tmp_path)

    #: a non-list scenarios field is rejected before any traversal
    path.write_text(json.dumps({"suite": "unit", "scenarios": {"oops": 1}}), encoding="utf-8")
    with pytest.raises(ArtifactError, match="`scenarios` must be a list"):
        proof.load_scenario_evidence(tmp_path)

    #: a scenario without a textual nodeid is located by its index and the file
    path.write_text(
        json.dumps({"suite": "unit", "scenarios": [{"status": "passed"}]}), encoding="utf-8"
    )
    with pytest.raises(
        ArtifactError, match=r"scenarios\[0\] without a non-empty textual"
    ) as excinfo:
        proof.load_scenario_evidence(tmp_path)
    assert str(path) in str(excinfo.value)

    #: non-list artifacts are rejected, naming the carrying scenario
    path.write_text(
        json.dumps(
            {"suite": "unit", "scenarios": [{"nodeid": "tests/x.py::t", "artifacts": "nope"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="`artifacts` must be a list"):
        proof.load_scenario_evidence(tmp_path)

    #: nested fields are validated before casting to the strict models
    path.write_text(
        json.dumps(
            {
                "suite": "unit",
                "scenarios": [
                    {
                        "nodeid": "tests/x.py::t",
                        "proves": ["ok", 3],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="`proves` must be a list of strings"):
        proof.load_scenario_evidence(tmp_path)

    path.write_text(
        json.dumps(
            {
                "suite": "unit",
                "scenarios": [
                    {
                        "nodeid": "tests/x.py::t",
                        "artifacts": [{"path": "shot.png", "bytes": "large"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="`bytes` must be of type int"):
        proof.load_scenario_evidence(tmp_path)


def test_scenario_evidence_normalizes_to_the_declared_model(tmp_path):
    path = tmp_path / "unit-scenarios.json"
    path.write_text(
        json.dumps(
            {
                "schema": "cdpx.scenarios/v2",
                "suite": "unit",
                "unknown_root": "discarded",
                "scenarios": [
                    {
                        "nodeid": "tests/x.py::t",
                        "duration_s": 1,
                        "unknown_scenario": "discarded",
                        "artifacts": [{"path": "shot.png", "unknown": "discarded"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence = proof.load_scenario_evidence(tmp_path)
    scenario = evidence["suites"]["unit"][0]

    assert scenario["duration_s"] == 1.0
    assert "unknown_scenario" not in scenario
    assert "unknown" not in scenario["artifacts"][0]


def test_write_scenario_evidence_round_trips_versioned_suites(tmp_path):
    """The writer publishes every non-empty suite under the versioned v2
    schema, scenarios sorted by nodeid, and the reader reads it all back
    identically."""
    suites = {
        "unit": [
            {"nodeid": "tests/test_b.py::test_b", "suite": "unit", "status": "passed"},
            {"nodeid": "tests/test_a.py::test_a", "suite": "unit", "status": "passed"},
        ],
        "e2e": [],
    }
    evidence = {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}

    proof.write_scenario_evidence(tmp_path, evidence)

    payload = json.loads((tmp_path / "unit-scenarios.json").read_text(encoding="utf-8"))
    #: the written root is versioned and consistent (schema v2, suite, count)
    assert payload["schema"] == "cdpx.scenarios/v2"
    assert payload["suite"] == "unit"
    assert payload["count"] == 2
    #: scenarios are sorted by nodeid for a stable diff between runs
    assert [item["nodeid"] for item in payload["scenarios"]] == [
        "tests/test_a.py::test_a",
        "tests/test_b.py::test_b",
    ]
    #: an empty suite writes no file
    assert not (tmp_path / "e2e-scenarios.json").exists()
    #: the validating reader reads back what the writer just produced
    reloaded = proof.load_scenario_evidence(tmp_path)
    assert reloaded["totals"]["unit"] == 2


def test_parse_junit_extracts_counts_and_cases(tmp_path, evidence_case):
    """The JUnit parser restores the aggregated counts and the per-case
    detail (status, failure message) from the XML produced by pytest."""
    junit = tmp_path / "junit.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="1" errors="0" skipped="1" time="1.25">
    <testcase classname="tests.test_ok" name="test_passes" time="0.1" />
    <testcase classname="tests.test_bad" name="test_fails" time="0.2">
      <failure message="assertion failed">details</failure>
    </testcase>
    <testcase classname="tests.test_skip" name="test_skips" time="0.0">
      <skipped message="no chrome" />
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    parsed = proof.parse_junit(junit)

    #: the derived counters (including passed) are consistent with the source XML
    assert parsed["tests"] == 3
    assert parsed["passed"] == 1
    assert parsed["failures"] == 1
    assert parsed["skipped"] == 1
    #: each case keeps status and failure message for the cockpit rendering
    assert parsed["cases"][1]["status"] == "failed"
    assert parsed["cases"][1]["message"] == "assertion failed"

    if evidence_case is not None:
        # Input/output side by side: the raw source XML and the derived dict,
        # to visually verify that counts and cases match.
        evidence_case.attach_text(
            "Source JUnit XML (parser input)",
            junit.read_text(encoding="utf-8"),
            filename="junit-source.xml",
        )
        evidence_case.attach_json(
            "Dict parsed by parse_junit (output)",
            parsed,
            filename="parsed-junit.json",
        )


def test_parse_junit_reports_malformed_xml(tmp_path):
    """A truncated XML does not crash the collection: the parser reports the
    parsing error while still confirming the file's existence."""
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite>", encoding="utf-8")

    parsed = proof.parse_junit(junit)

    #: file present but unreadable: zero tests counted and an explicit error, never an exception
    assert parsed["exists"] is True
    assert parsed["tests"] == 0
    assert parsed["parse_error"]


def test_parse_help_commands_uses_captured_argparse_help():
    """The report's CLI catalog comes from the real argparse help: flagship
    subcommands appear there with their help text."""
    help_text = build_parser().format_help()

    commands = proof.parse_help_commands(help_text)

    names = {command["name"] for command in commands}
    #: finding the key primitives proves the extraction reads the real subcommands section
    assert {"goto", "seo", "vitals", "replay"}.issubset(names)
    assert any(command["help"] for command in commands if command["name"] == "seo")


def test_build_summary_preserves_historical_artifact_keys():
    """The summary's historical keys stay stable: the published paths are
    the canonical locations, not those of the input entries."""
    unit = {
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "cases": [],
    }
    e2e = {
        "tests": 1,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 1,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: the historical JSON contract is frozen: verdict and canonical artifact paths
    assert summary["ok"] is True
    assert summary["unit_log"] == ".proof/make-check-pytest.log"
    assert summary["e2e_log"] == ".proof/e2e-chrome.log"
    assert summary["report_html"] == ".proof/proof-report.html"


def test_build_summary_adds_project_evidence_sections():
    """The summary carries the project sections (identity, validation
    matrix, evidence catalog, unknowns) that feed the cockpit."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.2,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    git_context = {
        "branch": "feature",
        "sha": "abc123",
        "changed_files": [
            {"status": "M", "path": "Makefile"},
            {"status": "A", "path": "src/cdpx/proof.py"},
            {"status": "A", "path": "tests/test_proof.py"},
        ],
        "generated_files": [],
        "changed_count": 3,
        "generated_count": 0,
        "status_path": ".proof/git-status.txt",
        "diff_stat_path": ".proof/git-diff-stat.txt",
    }

    help_commands = proof.parse_help_commands(build_parser().format_help())

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        git_context=git_context,
        help_commands=help_commands,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: project identity and volumes are computed from the repo, not hardcoded
    assert summary["project"]["name"] == "cdpx"
    assert summary["project"]["cli_command_count"] >= 20
    assert summary["project"]["fixture_count"] >= 1
    #: matrix, catalog, and unknowns are populated: the SPA has what it needs to render each section
    assert summary["validation_matrix"]
    assert summary["coverage_groups"] == []
    assert any(item["type"] == "junit" for item in summary["evidence_catalog"])
    assert summary["unknowns"]


def test_build_summary_includes_symfony_suite_and_catalog():
    """When provided, the Symfony suite enters the verdict, the totals, and
    the evidence catalog on the same footing as unit and e2e."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 2,
        "passed": 2,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.2,
        "cases": [],
    }
    symfony = {
        "path": ".proof/symfony-e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.3,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=["docker", "compose", "up"],
        log=".proof/symfony-e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )

    summary = proof.build_summary(
        [command],
        unit,
        e2e,
        symfony,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: the green Symfony suite contributes to the totals and publishes log and JUnit to the catalog
    assert summary["ok"] is True
    assert summary["symfony_log"] == ".proof/symfony-e2e.log"
    assert summary["junit"]["symfony"]["tests"] == 1
    assert summary["totals"]["tests"] == 4
    assert any(item["name"] == "Symfony E2E JUnit" for item in summary["evidence_catalog"])


def test_write_symfony_unavailable_evidence_is_explicit(tmp_path, monkeypatch):
    """Symfony unavailability leaves explicit evidence on disk (suite,
    status, reason) instead of a silent absence."""
    proof_dir = tmp_path / ".proof"
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_DIR", proof_dir / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", proof_dir / "symfony-e2e.log")
    proof.SYMFONY_LOG.parent.mkdir(parents=True)
    proof.SYMFONY_LOG.write_text("docker unavailable\n", encoding="utf-8")

    proof.write_symfony_unavailable_evidence("Docker daemon unavailable")

    payload = (proof.EVIDENCE_DIR / "symfony-scenarios.json").read_text(encoding="utf-8")
    #: the written JSON names the suite, the unavailable status, and the
    #: reason, readable by the cockpit
    assert '"suite": "symfony"' in payload
    assert '"status": "unavailable"' in payload
    assert "Docker daemon unavailable" in payload


def test_run_symfony_evidence_fails_when_docker_is_missing(tmp_path, monkeypatch):
    """Without the docker binary, Symfony collection fails outright: status
    unavailable, non-zero exit code, and a log recalling the release
    requirement."""
    monkeypatch.setattr(proof, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", tmp_path / "symfony.log")
    monkeypatch.setattr(proof.shutil, "which", lambda _name: None)

    command = proof.run_symfony_evidence()

    #: the absence of Docker is a traced failure that the release gate can judge, not a skip
    assert command.exit_code == 1
    assert command.status == "unavailable"
    assert "required for release proof" in proof.SYMFONY_LOG.read_text(encoding="utf-8")


def _minimal_suite(path, tests=1, cases=None):
    cases = cases or []
    return {
        "path": path,
        "exists": True,
        "tests": tests,
        "passed": tests,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": cases,
    }


def _ok_command():
    return proof.CommandEvidence(
        id="unit",
        label="Unit",
        argv=["pytest"],
        log=".proof/unit.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )


def test_spa_renders_every_summary_key():
    """Guard rail "computed => rendered": every top-level summary key must be
    consumed by the SPA, except shell HTML keys and metadata."""
    # Guard rail "computed => rendered": every top-level summary key must be
    # read by the SPA (data.<key>), except those rendered by the HTML shell or
    # purely meta ones (artifact paths, raw duplicates).
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        help_commands=proof.parse_help_commands(build_parser().format_help()),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    shell_keys = {"ok", "generated_at", "git"}  # rendered directly by render_html
    meta_keys = {"artifact_dir", "report_html", "unit_log", "e2e_log", "symfony_log"}
    meta_keys.add("scenario_evidence")  # raw duplicate of feature_inventory/matched_scenarios
    #: a key computed but never read by the SPA fails here: dead work is a bug
    for key in summary:
        if key in shell_keys | meta_keys or f"data.{key}" in proof.cockpit_javascript():
            continue
        raise AssertionError(f"summary key computed but never rendered by the SPA: {key}")


def test_render_html_embeds_payload_verdict_and_routes(evidence_case):
    """The rendered HTML embeds the JSON payload and the verdict, wires all
    SPA routes, and locks down the report via CSP, with no external
    script."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    html = proof.render_html(summary)
    #: payload and verdict are inlined: the report is self-contained and readable offline
    assert 'id="report-data"' in html and '"ok": true'.replace(" ", "") in html.replace(" ", "")
    assert ">OK<" in html
    #: every expected navigation route is present in the shell
    for route in (
        "#/features",
        "#/docs",
        "#/cli",
        "#/validation",
        "#/gaps",
        "#/run",
        "#/project",
    ):
        assert route in html
    #: strict CSP and Mermaid in strict mode forbid any outgoing request
    assert "securityLevel: 'strict'" in html
    assert "connect-src 'none'" in html
    assert "media-src 'self'" in html
    #: the artifact modal exists and announces itself as an accessible dialog
    assert 'id="artifact-modal"' in html
    assert 'role="dialog"' in html
    #: no external script: offline self-containment is structural
    assert "<script src=" not in html

    if evidence_case is not None:
        # The full HTML embeds Mermaid (~3.5 MB): only a meaningful excerpt is
        # attached (shell/CSP head + embedded payload zone), under the
        # cockpit's 16 KiB inline cap.
        marker = html.index('id="report-data"')
        shell_excerpt = "\n".join(
            [
                "=== <head> / shell (excerpt) ===",
                html[:1200],
                "=== embedded payload zone (report-data) ===",
                html[max(0, marker - 200) : marker + 3500],
            ]
        )
        evidence_case.attach_text(
            "HTML report excerpt — shell + embedded payload",
            shell_excerpt,
            filename="report-shell.html",
        )


def test_build_summary_exposes_curated_documentation_catalog():
    """The summary's documentation catalog follows its versioned schema,
    with no violations, and references the expected curated documents."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    documentation = summary["documentation"]
    #: announced schema, zero violations, and presence of a key document: curation is verified
    assert documentation["schema"] == "cdpx.docs/v1"
    assert documentation["violations"] == []
    assert any(
        document["path"] == "docs/SESSION-LIFECYCLE.md" for document in documentation["documents"]
    )
    #: a healthy catalog triggers no gate failure
    assert not any(failure.startswith("documentation:") for failure in summary["proof_failures"])


def test_mermaid_vendor_bundle_is_integrity_checked_and_embedded(monkeypatch):
    """The vendored Mermaid bundle is loaded whole and verified via
    SHA-256: a diverging hash is rejected rather than embedding a tampered
    script."""
    bundle = proof._mermaid_bundle()
    #: the complete bundle is actually read from the package resources
    assert len(bundle) > 3_000_000
    assert "mermaid" in bundle.lower()

    proof._mermaid_bundle.cache_clear()
    monkeypatch.setattr(proof, "MERMAID_SHA256", "0" * 64)
    #: an unexpected hash fails the load: integrity before embedding
    with pytest.raises(ValueError, match="bundle vendor/mermaid"):
        proof._mermaid_bundle()
    proof._mermaid_bundle.cache_clear()


def test_xterm_vendor_bundle_is_integrity_checked_and_embedded(monkeypatch):
    """The vendored xterm.js bundle and its stylesheet are loaded and
    verified via SHA-256, ready to be inlined like Mermaid."""
    # The cast player relies on vendored xterm.js (MIT): bundle + CSS verified
    # via SHA-256, embedded inline in the report like Mermaid.
    bundle = proof._xterm_bundle()
    #: xterm JS and CSS are present and substantial in the package resources
    assert len(bundle) > 100_000
    assert "Terminal" in bundle

    stylesheet = proof._xterm_css()
    assert ".xterm" in stylesheet

    proof._xterm_bundle.cache_clear()
    monkeypatch.setattr(proof, "XTERM_JS_SHA256", "0" * 64)
    #: the integrity check rejects a bundle whose hash changed
    with pytest.raises(ValueError, match="bundle vendor/xterm"):
        proof._xterm_bundle()
    proof._xterm_bundle.cache_clear()


def test_cockpit_assets_are_packaged_and_sane():
    """Every packaged cockpit resource exists, is not empty, and stays
    inlinable; the shell substitutes with no missing placeholder and no
    orphan."""
    # The presentation lives in dedicated resources (cockpit/) loaded via
    # importlib.resources: every asset must exist, be non-empty, and
    # scripts/styles must stay inlinable (no premature </script>).
    from string import Template

    #: every embedded asset is non-empty and cannot prematurely close the script tag
    for name in proof.COCKPIT_RESOURCES:
        asset = proof._cockpit_asset(name)
        assert asset.strip(), f"empty cockpit asset: {name}"
        if name != proof.COCKPIT_SHELL_RESOURCE:
            assert "</script" not in asset.lower(), f"non-inlinable asset: {name}"

    shell = proof._cockpit_asset(proof.COCKPIT_SHELL_RESOURCE)
    # The shell must substitute with no missing placeholder and no orphan literal $.
    rendered = Template(shell).substitute(
        verdict="OK",
        pill="ok",
        context="ctx",
        spa_css="",
        xterm_css="",
        payload="{}",
        mermaid_bundle="",
        xterm_bundle="",
        spa_js="",
    )
    #: the complete shell substitution proves no placeholder is orphaned
    assert rendered.startswith("<!doctype html>")

    #: requesting a nonexistent asset fails outright instead of rendering an empty page
    with pytest.raises(FileNotFoundError):
        proof._cockpit_asset("cockpit/does-not-exist.js")


def test_every_artifact_type_has_a_dedicated_viewer():
    """Guard rail taxonomy => rendering: every artifact type in the closed
    taxonomy has its entry in the cockpit's VIEWERS registry."""
    # Guard rail "computed => rendered" at the artifact level: every type in
    # the closed taxonomy must have an entry in the cockpit's VIEWERS
    # registry. A collected type with no viewer breaks the build.
    from cdpx.testing.evidence import ARTIFACT_TYPES

    #: the registry must exist in the expected form before inspecting its content
    assert "const VIEWERS = {" in proof.cockpit_javascript()
    registry = proof.cockpit_javascript().split("const VIEWERS = {", 1)[1].split("};", 1)[0]
    #: a collected type with no viewer breaks the build: nothing stays invisible to the reviewer
    for artifact_type in sorted(ARTIFACT_TYPES):
        assert f"'{artifact_type}':" in registry, (
            f"artifact type without a viewer in the cockpit: {artifact_type}"
        )


def test_text_viewers_are_specialized_per_type():
    """Every textual type has a specialized viewer in the SPA (console,
    network, JSON, profiler, logs, command), not a plain download link."""
    # Every textual type has a real viewer, not a plain link: console
    # (levels + filters), network (status table), json/profiler (tree),
    # logs (numbered lines + highlighting), command (argv + exit code).
    #: each marker attests that a dedicated viewer is actually wired in the JS
    for marker in (
        "function consoleViewer",
        "data-console-level",
        "function networkViewer",
        "net-status",
        "function jsonViewer",
        "JSON_NODE_BUDGET",
        "function profilerViewer",
        "function logViewer",
        "log-hit",
        "function commandViewer",
        "transcriptSection(body, 'stderr')",
    ):
        assert marker in proof.cockpit_javascript(), f"missing text viewer: {marker}"


def test_modal_resolves_inline_content_by_path():
    """feature_inventory artifact copies (never inlined on the Python side)
    retrieve, in the modal, the embedded content from the single source
    scenario_evidence, resolved by path — no more falling back to "Content
    not embedded" when the content exists in the payload."""
    # feature_inventory duplicates every artifact at several levels (proofs,
    # matched_scenarios): inlining these copies on the Python side would
    # multiply the report's weight. The SPA therefore resolves the inline by
    # path at render time, from the single copy in scenario_evidence.suites.
    #: the index is built from scenario_evidence, the single inlined source
    for marker in (
        "const inlineByPath",
        "(data.scenario_evidence || {}).suites",
        "function resolveInline",
    ):
        assert marker in proof.cockpit_javascript(), f"missing inline-by-path resolution: {marker}"
    #: the modal enriches the artifact before choosing its viewer
    assert "resolveInline(modalState.items[modalState.index])" in proof.cockpit_javascript()


def test_cast_viewer_replays_v2_in_xterm_and_keeps_a_raw_fallback():
    """The cast player replays the asciicast v2 in xterm.js with a homemade
    toolbar (scrubber, speeds) and keeps a raw fallback view."""
    # Real player: vendored xterm.js (MIT — asciinema-player is GPL-3), driven
    # by the homemade toolbar (scrubber, speeds), raw fallback view kept.
    #: every player building block (v2 parsing, xterm lifecycle, controls,
    #: raw fallback) is present
    for marker in (
        "function parseCast",
        "header.version !== 2",
        "globalThis.Terminal",
        "terminal.reset()",
        "terminal.dispose()",
        "function castViewer",
        "data-cast-scrub",
        "data-cast-rawtoggle",
        "'asciinema': castViewer",
        "requestAnimationFrame(tick)",
    ):
        assert marker in proof.cockpit_javascript(), f"incomplete asciinema player: {marker}"


def test_cast_gate_blocks_the_verdict():
    """The cast gate is blocking: missing collection or degraded status
    turns the verdict red with an explicit cause in proof_failures."""
    # Cast gate: with no entries (or a degraded status), the verdict goes red
    # and the cause is explicit in proof_failures.
    missing = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
    )
    #: no cast collection => red verdict, one failure per expected demo
    assert missing["ok"] is False
    assert any(failure.startswith("cast missing:") for failure in missing["proof_failures"])

    degraded_casts = generated_casts()
    degraded_casts[0]["status"] = "unavailable"
    degraded = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=degraded_casts,
    )
    #: a degraded cast is blocking and names the faulty demo
    assert degraded["ok"] is False
    assert any(failure.startswith("cast unavailable:") for failure in degraded["proof_failures"])
    #: the summary exposes the entries for the SPA rendering (Run section)
    assert degraded["casts"] == degraded_casts


def test_catalog_casts_are_inlined_for_the_player(tmp_path, monkeypatch, evidence_case):
    """Catalog .cast files are inlined into the HTML payload: under the
    report's CSP, a plain link would be unplayable."""
    # Catalog .cast files (produced outside a pytest scenario) must be
    # inlined: under the report's CSP, a link alone would be unplayable.
    monkeypatch.setattr(proof, "PROOF_DIR", tmp_path)
    cast_file = tmp_path / "cli-help.cast"
    cast_file.write_text('{"version": 2}\n[0.1, "o", "ok"]\n', encoding="utf-8")

    catalog = proof.build_evidence_catalog({"commands": []}, {}, {}, {})

    entry = next(item for item in catalog if item["type"] == "asciinema")
    #: the content travels in the HTML payload, ready for xterm
    assert entry["inline_content"].startswith('{"version": 2}')
    #: no more "optional" placeholder entry: the cast is mandatory
    assert not any(item.get("status") == "optional" for item in catalog)

    if evidence_case is not None:
        # The catalog's synthetic .cast, replayable as-is in the cockpit's
        # xterm player (classified not uploadable by attach_cast).
        evidence_case.attach_cast(
            cast_file,
            "Synthetic catalog cast — replayable in the xterm player",
        )


def test_modal_and_keyboard_wiring_are_present():
    """The artifact modal and its keyboard navigation (Escape, arrows,
    groups) are wired in the SPA's JS."""
    #: opening, closing, keyboard shortcuts, and navigation context are all wired
    for marker in (
        "function openModal",
        "function closeModal",
        "'Escape'",
        "'ArrowRight'",
        "'ArrowLeft'",
        "data-modal-group",
        "ctx: {scenario, run}",
    ):
        assert marker in proof.cockpit_javascript(), f"missing modal wiring: {marker}"


def test_reading_order_timeline_and_badges_guide_the_review():
    """The review UX is guided: failures rise to the top, the run's path and
    timeline are visual, and badges announce the evidence before the
    click."""
    # Reading UX: red rises to the top ("Read first"), the path is guided,
    # the run's timeline is visual, and badges announce the evidence before
    # the click.
    #: every guidance device (read first, path, timeline, badges) is wired
    for marker in (
        "function renderReadFirst",
        "Read first",
        "function renderReadingPath",
        "function renderCommandTimeline",
        "tl-bad",
        "function decorateTopbar",
        "function failedRuns",
        "typeBadges(scenarioArtifacts(feature.matched_scenarios))",
    ):
        assert marker in proof.cockpit_javascript(), f"incomplete reading UX: {marker}"


def test_scenario_view_renders_intent_and_assertion_hierarchy():
    """The scenario view renders the intent extracted from the code:
    docstring, assertion annotations with honest status, correlation of the
    failure line, and run outputs."""
    # "Computed => rendered" for the intent extracted from the code:
    # docstring, #: assertions with honest status, failed_line correlation,
    # timeline.
    #: every computed intent field is consumed by the view, including the correlated failure
    for marker in (
        "run.intent",
        "run.assertions",
        "run.failed_line",
        "assertion.status === 'failed'",
        "function renderTestCard",
        "function artifactTimeline",
        "function typeBadges",
        "run.stdout",
        "run.stderr",
    ):
        assert marker in proof.cockpit_javascript(), f"incomplete scenario view: {marker}"


def test_build_summary_embeds_cases_focus_and_log_tails(tmp_path):
    """The summary carries the detailed JUnit cases, a focus list that
    surfaces failures, and each command's log tail."""
    cases = [
        {"classname": "tests.test_a", "name": "test_x", "time_s": 0.5, "status": "passed"},
        {"classname": "tests.test_a", "name": "test_y", "time_s": 0.1, "status": "failed"},
    ]
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml", tests=2, cases=cases),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    #: full cases returned, failures sorted to the top of the focus, log tail ready for rendering
    assert summary["junit"]["unit"]["cases"] == cases
    assert summary["junit"]["unit"]["focus"][0]["status"] == "failed"  # failures first
    assert "log_tail" in summary["commands"][0]


def test_symfony_unavailable_is_always_blocking(monkeypatch):
    """An unavailable Symfony scenario blocks the verdict even without a
    Symfony JUnit suite: the unavailability is counted and named."""
    suites = {
        "unit": [],
        "integration": [],
        "e2e": [],
        "symfony": [{"nodeid": "tests/e2e/test_e2e_symfony.py::test_x", "status": "unavailable"}],
    }
    evidence = {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}

    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=evidence,
    )
    #: the unavailability appears in the totals, turns the verdict red, and names its cause
    assert summary["totals"]["unavailable"] == 1  # visible in the hero
    assert summary["ok"] is False
    assert any("symfony evidence unavailable" in failure for failure in summary["proof_failures"])


def test_symfony_skips_are_release_blocking():
    """A single skip in the Symfony suite fails the release proof: no
    dodged test is tolerated on this gate."""
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        _minimal_suite(".proof/symfony-e2e-junit.xml", tests=2) | {"passed": 1, "skipped": 1},
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: the Symfony skip turns the verdict red and the failure names it explicitly
    assert summary["ok"] is False
    assert any("symfony tests skipped" in failure for failure in summary["proof_failures"])


def test_chrome_skips_and_missing_junit_are_release_blocking():
    """On the Chrome e2e side, a skipped test or a missing JUnit are both
    blocking for the release, each with a named failure."""
    e2e_command = proof.CommandEvidence(
        id="e2e",
        label="Chrome E2E",
        argv=["pytest"],
        log=".proof/e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    skipped = _minimal_suite(".proof/e2e-junit.xml", tests=2) | {
        "passed": 1,
        "skipped": 1,
    }
    skipped_summary = proof.build_summary(
        [e2e_command],
        _minimal_suite(".proof/unit-junit.xml"),
        skipped,
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )
    missing_summary = proof.build_summary(
        [e2e_command],
        _minimal_suite(".proof/unit-junit.xml"),
        proof._empty_suite(proof.Path(".proof/e2e-junit.xml")),
        scenario_evidence=empty_scenario_evidence(),
        cast_entries=generated_casts(),
    )

    #: a single e2e skip turns the verdict red, with its exact count in the failure
    assert skipped_summary["ok"] is False
    assert "e2e tests skipped (1)" in skipped_summary["proof_failures"]
    #: the missing required JUnit is a distinct failure, not a silent zero
    assert missing_summary["ok"] is False
    assert any("required JUnit missing" in item for item in missing_summary["proof_failures"])


def test_build_summary_fails_when_e2e_screenshot_missing():
    """An e2e scenario without an attached capture fails the proof and is
    also flagged as unmapped in the feature inventory."""
    unit = {
        "path": ".proof/unit-junit.xml",
        "exists": True,
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.0,
        "cases": [],
    }
    e2e = {
        "path": ".proof/e2e-junit.xml",
        "exists": True,
        "tests": 1,
        "passed": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.1,
        "cases": [],
    }
    command = proof.CommandEvidence(
        id="e2e",
        label="E2E",
        argv=["pytest"],
        log=".proof/e2e.log",
        exit_code=0,
        duration_s=0.1,
        status="ok",
    )
    suites = {
        "unit": [],
        "integration": [],
        "e2e": [
            {
                "nodeid": "tests/e2e/test_demo.py::test_without_shot",
                "status": "passed",
                "artifacts": [],
            }
        ],
    }
    scenario_evidence = {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}

    summary = proof.build_summary([command], unit, e2e, scenario_evidence=scenario_evidence)

    #: the faulty scenario is named in the missing-capture failure
    assert summary["ok"] is False
    assert (
        "missing e2e screenshot: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )
    #: the same nodeid is also traced as unmapped in the feature inventory
    assert (
        "feature inventory: scenario unmapped: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )


def test_classify_change_categorizes_repository_paths():
    """Classifying changed paths routes each file family to its review
    category (code, tests, docs, harness, other)."""
    #: every path family goes to the category expected by the impact map
    assert proof.classify_change("src/cdpx/proof.py") == "Product code"
    assert proof.classify_change("tests/test_proof.py") == "Tests"
    assert proof.classify_change("docs/TODO.md") == "Documentation"
    assert proof.classify_change("README.md") == "Documentation"
    assert proof.classify_change("Makefile") == "Harness / CI"
    assert proof.classify_change(".github/workflows/ci.yml") == "Harness / CI"
    #: a path outside the known families honestly stays "Other"
    assert proof.classify_change("article/post.md") == "Other"


def _impact_git_context(paths):
    return {
        "changed_files": [{"status": "M", "path": path} for path in paths],
        "generated_files": [],
        "changed_count": len(paths),
        "generated_count": 0,
    }


def test_build_impact_map_derives_entrypoints_and_change_types():
    """The impact map derives categories, entry points, and change types
    from the modified files, and flags the verified CLI surface when help
    was captured."""
    git_context = _impact_git_context(
        ["Makefile", "src/cdpx/proof.py", "tests/test_proof.py", "docs/TODO.md"]
    )

    impact = proof.build_impact_map(git_context, [{"name": "goto", "help": "opens a page"}])

    #: every modified file feeds its review category
    assert set(impact["categories"]) == {"Harness / CI", "Product code", "Tests", "Documentation"}
    #: the known entry points (make proof, module, tests) are all declared
    assert [entry["name"] for entry in impact["entrypoints"]] == [
        "make proof",
        "python -m cdpx.proof",
        "tests/test_proof.py",
    ]
    #: the change types include the verified-CLI-surface marker
    assert impact["change_types"] == ["code", "tests", "harness", "docs", "verified-cli-surface"]

    empty = proof.build_impact_map(_impact_git_context([]), [])
    #: without changes or captured help, the type is explicitly unknown
    assert empty["change_types"] == ["unknown"]
    assert empty["entrypoints"] == []


def test_build_review_guide_orders_reading_by_category():
    """The review guide orders the reading path according to the affected
    categories (harness first) and provides a fallback when nothing is
    classified."""
    impact = proof.build_impact_map(
        _impact_git_context(["Makefile", "src/cdpx/proof.py", "tests/test_proof.py", "README.md"]),
        [],
    )

    guide = proof.build_review_guide(impact)

    #: the path starts with the user contract (Makefile) then follows the layers
    assert guide["order"][0].startswith("Start with the Makefile")
    assert len(guide["order"]) == 4
    #: the reviewer's watch-outs are always provided
    assert guide["watch_outs"]

    fallback = proof.build_review_guide({"categories": {}})
    #: without a known category, a single fallback order still guides the reading
    assert len(fallback["order"]) == 1


def test_build_risks_and_unknowns_flags_versioned_generated_artifacts():
    """The change's risks/unknowns packet keeps its stable base and adds a
    dedicated unknown when git tracks generated artifacts."""
    base = proof.build_risks_and_unknowns({"generated_count": 0})

    #: the risks/unknowns base is constant when no generated artifact is tracked
    assert len(base["risks"]) == 2
    assert len(base["unknowns"]) == 3

    tracked = proof.build_risks_and_unknowns({"generated_count": 2})
    #: versioned generated artifacts trigger an additional named unknown
    assert any(item["item"] == "Versioned generated artifacts" for item in tracked["unknowns"])


def test_collect_git_context_filters_private_paths_and_splits_generated(tmp_path, monkeypatch):
    """Git collection filters out private worktree paths (AGENTS.md,
    article/, presentation/), separates generated files from real changes,
    and writes the snapshots to the requested paths."""

    def fake_run_text(argv, timeout=None, env=None):
        if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return 0, "feature/proof\n"
        if argv[:3] == ["git", "rev-parse", "--short"]:
            return 0, "abc1234\n"
        if argv[:2] == ["git", "status"]:
            return 0, (
                " M src/cdpx/proof.py\n"
                "?? .proof/report.html\n"
                " M AGENTS.md\n"
                "A  article/draft.md\n"
                "R  old.md -> presentation/deck.md\n"
            )
        return 0, " src/cdpx/proof.py | 2 +-\n"

    monkeypatch.setattr(proof, "_run_text", fake_run_text)
    status_path = tmp_path / "git-status.txt"
    diff_stat_path = tmp_path / "git-diff-stat.txt"

    context = proof.collect_git_context(status_path=status_path, diff_stat_path=diff_stat_path)

    #: branch and sha come from git output, not a static default
    assert context["branch"] == "feature/proof"
    assert context["sha"] == "abc1234"
    written = status_path.read_text(encoding="utf-8")
    #: private worktree paths (including a rename's target) never leak
    assert "AGENTS.md" not in written
    assert "article/" not in written
    assert "presentation/" not in written
    #: generated artifacts (.proof/) are separated from changes to review
    assert [item["path"] for item in context["changed_files"]] == ["src/cdpx/proof.py"]
    assert [item["path"] for item in context["generated_files"]] == [".proof/report.html"]
    assert context["changed_count"] == 1
    assert context["generated_count"] == 1
    #: the diff stat is written to the requested path for the evidence catalog
    assert "proof.py" in diff_stat_path.read_text(encoding="utf-8")


def test_collect_git_context_never_parses_failed_git_output_as_porcelain(tmp_path, monkeypatch):
    """A failed git output (timeout exit 124, partial porcelain followed by
    the timeout annotation) is never parsed as porcelain: empty lists, empty
    snapshots, only the published error code carries the diagnosis — no
    corrupted entry reaches the summary."""

    def fake_run_text(argv, timeout=None, env=None):
        if argv[:2] == ["git", "status"]:
            return 124, "M  x\ntimeout after 30.0s\n"
        if argv[:2] == ["git", "diff"]:
            return 124, " partial | 1 +\ntimeout after 30.0s\n"
        return 0, "main\n"

    monkeypatch.setattr(proof, "_run_text", fake_run_text)
    status_path = tmp_path / "git-status.txt"
    diff_stat_path = tmp_path / "git-diff-stat.txt"

    context = proof.collect_git_context(status_path=status_path, diff_stat_path=diff_stat_path)

    #: no line of the partial porcelain becomes a file entry
    assert context["changed_files"] == []
    assert context["generated_files"] == []
    #: the failure codes are published as-is for diagnosis
    assert context["status_code"] == 124
    assert context["diff_stat_code"] == 124
    #: the written snapshots are empty: no corrupted porcelain published
    assert status_path.read_text(encoding="utf-8") == ""
    assert diff_stat_path.read_text(encoding="utf-8") == ""


def _mock_symfony_docker(monkeypatch, *, up=(0, False), post_down_code=0, check_codes=None):
    # Docker fully simulated: which present, checks/downs via _run_text, up
    # streamed via _stream_to_private_file. No real container.
    calls = {"down": 0, "argv": []}
    checks = check_codes or {}

    def fake_run_text(argv, timeout=None, env=None):
        calls["argv"].append(list(argv))
        if argv[:3] == ["docker", "compose", "version"]:
            return checks.get("version", 0), "compose v2\n"
        if argv[:2] == ["docker", "info"]:
            return checks.get("info", 0), "daemon ok\n"
        if "down" in argv:
            calls["down"] += 1
            return (post_down_code if calls["down"] == 2 else 0), "down ok\n"
        raise AssertionError(f"unexpected docker command: {argv}")

    def fake_stream(argv, sink, *, env, timeout):
        sink.parent.mkdir(parents=True, exist_ok=True)
        sink.write_text("compose up transcript\n", encoding="utf-8")
        return up

    monkeypatch.setattr(proof.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(proof, "_run_text", fake_run_text)
    monkeypatch.setattr(proof, "_stream_to_private_file", fake_stream)
    return calls


def test_run_symfony_evidence_composes_and_tears_down(tmp_path, monkeypatch):
    """Docker present and healthy: Symfony collection chains checks,
    preventive down, streamed up, then final down, and publishes a green
    exit-0 log."""
    calls = _mock_symfony_docker(monkeypatch)

    command = proof.run_symfony_evidence(proof_dir=tmp_path)

    #: the healthy run is an ok exit 0, judged as such by the verdict
    assert command.exit_code == 0
    assert command.status == "ok"
    #: teardown is systematic: down before AND after the up
    assert calls["down"] == 2
    #: every down purges orphans AND volumes: the loop cannot accumulate
    #: anonymous volumes even if an image ever declares one
    for argv in calls["argv"]:
        if "down" in argv:
            assert "--remove-orphans" in argv and "--volumes" in argv
    log = (tmp_path / "symfony-e2e.log").read_text(encoding="utf-8")
    #: the log aggregates the streamed up transcript and the exit verdict
    assert "compose up transcript" in log
    assert "exit_code: 0" in log


def test_run_symfony_evidence_fails_when_final_teardown_fails(tmp_path, monkeypatch):
    """A failing final down turns the Symfony run red even if the up is
    green: leaving containers behind is a proof failure."""
    _mock_symfony_docker(monkeypatch, post_down_code=1)

    command = proof.run_symfony_evidence(proof_dir=tmp_path)

    #: the final teardown failure becomes the run's exit code
    assert command.exit_code == 1
    assert command.status == "failed"


def test_run_symfony_evidence_converts_deadline_into_exit_124(tmp_path, monkeypatch):
    """A compose up that exceeds its deadline is converted into exit 124
    with the timeout mentioned in the log, and teardown still happens."""
    calls = _mock_symfony_docker(monkeypatch, up=(124, True))

    command = proof.run_symfony_evidence(proof_dir=tmp_path, timeout=5)

    #: the deadline becomes a conventional exit-124 failure
    assert command.exit_code == 124
    assert command.status == "failed"
    #: even killed by deadline, the run returns control with containers removed
    assert calls["down"] == 2
    log = (tmp_path / "symfony-e2e.log").read_text(encoding="utf-8")
    #: the log names the interruption and its deadline
    assert "docker compose up interrupted after 5s" in log


def test_run_symfony_evidence_reports_unavailable_docker_daemon(tmp_path, monkeypatch):
    """Docker installed but daemon unreachable: collection stops at the
    checks, declares the gate unavailable, and writes the explicit
    evidence — without ever attempting the up."""
    calls = _mock_symfony_docker(monkeypatch, check_codes={"info": 1})

    command = proof.run_symfony_evidence(proof_dir=tmp_path)

    #: the unreachable daemon is an unavailable failure, not a silent skip
    assert command.exit_code == 1
    assert command.status == "unavailable"
    #: neither down nor up were attempted after the failed check
    assert calls["down"] == 0
    payload = (tmp_path / "evidence" / "symfony-scenarios.json").read_text(encoding="utf-8")
    #: the unavailability evidence is written for the cockpit and the verdict
    assert '"status": "unavailable"' in payload


def test_run_text_reports_exit_missing_binary_and_timeout():
    """_run_text captures command output and exit, converting a missing
    binary into 127 and an exceeded deadline into an annotated 124."""
    code, output = proof._run_text([sys.executable, "-c", "print('output-ok')"])
    #: a healthy command returns output and exit 0
    assert code == 0
    assert "output-ok" in output

    code, output = proof._run_text(["cdpx-nonexistent-binary-xyz"])
    #: a missing binary becomes the conventional exit 127 with the error as text
    assert code == 127
    assert output

    code, output = proof._run_text([sys.executable, "-c", "import time; time.sleep(60)"], 0.5)
    #: the deadline kills the command and annotates it as exit 124
    assert code == 124
    assert "timeout after" in output


def test_main_prints_compact_verdict_and_maps_exit_code(monkeypatch, capsys):
    """The module's CLI entry point prints a compact three-key JSON (ok,
    artifact_dir, report_html) and maps the verdict to exit code 0/1."""
    monkeypatch.setattr(
        proof,
        "generate",
        lambda: {
            "ok": True,
            "artifact_dir": ".proof",
            "report_html": ".proof/proof-report.html",
            "commands": [],
        },
    )

    exit_code = proof.main()

    printed = json.loads(capsys.readouterr().out)
    #: green verdict => exit 0 and compact JSON limited to the three contract keys
    assert exit_code == 0
    assert printed == {
        "ok": True,
        "artifact_dir": ".proof",
        "report_html": ".proof/proof-report.html",
    }

    monkeypatch.setattr(
        proof,
        "generate",
        lambda: {"ok": False, "artifact_dir": ".proof", "report_html": ".proof/x.html"},
    )
    #: red verdict => exit 1, the Make gate sees the failure
    assert proof.main() == 1


def test_sanitize_text_file_redacts_and_rewrites_in_place(tmp_path):
    """In-place sanitization of a text file redacts secrets and rewrites
    physical paths to published paths; a missing path is a no-op with no
    file creation."""
    context = RedactionContext.from_secrets(["sanitize-secret-1"])
    target = tmp_path / "junit.xml"
    target.write_text(
        "<testsuite>token=sanitize-secret-1 log=/staging/run.log</testsuite>",
        encoding="utf-8",
    )

    proof._sanitize_text_file(target, context, path_rewrites=(("/staging", ".proof"),))

    content = target.read_text(encoding="utf-8")
    #: the secret is redacted before the file joins the publishable tree
    assert "sanitize-secret-1" not in content
    assert "***" in content
    #: the staging's physical path is rewritten to the published logical path
    assert ".proof/run.log" in content
    #: the private rewrite protects the file at 0600
    assert mode(target) == 0o600

    missing = tmp_path / "absent.xml"
    proof._sanitize_text_file(missing, context)
    #: a missing path stays missing: no ghost file is created
    assert not missing.exists()


def test_build_shareable_proof_guards_reject_bad_ttl_root_and_symlinks(tmp_path):
    """The shareable staging's entry guards reject a non-positive TTL, an
    invalid proof root, and any symlink in the tree."""
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>ok</p>")

    #: a zero TTL is rejected before any write
    with pytest.raises(ArtifactError, match="TTL"):
        proof.build_shareable_proof(proof_dir, ttl=0)

    #: a nonexistent root is an invalid proof directory
    with pytest.raises(ArtifactError, match="invalid proof directory"):
        proof.build_shareable_proof(tmp_path / "absent", ttl=3600)

    (proof_dir / "evil.log").symlink_to(proof_dir / "proof-report.html")
    #: a symlink within the proofs blocks staging closed
    with pytest.raises(ArtifactError, match="symlink forbidden in proofs"):
        proof.build_shareable_proof(proof_dir, ttl=3600)
