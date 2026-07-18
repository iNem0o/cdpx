from __future__ import annotations

import json
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdpx.artifacts import (
    ArtifactClassification,
    ArtifactError,
    SecureArtifactWriter,
    purge_expired,
    scan_canaries,
)
from cdpx.security import RedactionContext


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_secure_writer_creates_private_atomic_manifest(tmp_path):
    """Every artifact is born private (0700/0600) and committed to a
    versioned manifest with a sha256 fingerprint — the integrity foundation
    on which every later sharing check rests."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    entry = writer.write_text(
        "logs/result.txt",
        "safe output",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    path = writer.run_dir / entry.path
    #: the content is intact and unreadable by any other user from the moment it is written
    assert path.read_text(encoding="utf-8") == "safe output"
    assert mode(writer.run_dir) == 0o700 and mode(path) == 0o600
    manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
    #: the manifest, itself private, seals the artifact's schema, fingerprint, and size
    assert mode(writer.manifest_path) == 0o600
    assert manifest["schema"] == "cdpx.artifacts/v1"
    assert manifest["artifacts"][0]["sha256"] == entry.sha256
    assert manifest["artifacts"][0]["bytes"] == len("safe output")


def test_opaque_and_secret_artifacts_can_never_be_shareable(tmp_path):
    """Sharing is not negotiable for sensitive classifications: requesting
    upload_allowed=True on SECRET or OPAQUE_RESTRICTED is rejected before
    the content even touches disk."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for classification in (
        ArtifactClassification.SECRET,
        ArtifactClassification.OPAQUE_RESTRICTED,
    ):
        #: the combination of sensitive classification + sharing is an error, not a warning
        with pytest.raises(ArtifactError, match="non-shareable"):
            writer.write_text(
                f"{classification.value}.txt",
                "content",
                classification=classification,
                upload_allowed=True,
            )


def test_writer_refuses_traversal_absolute_paths_and_symlinks(tmp_path):
    """No path form allows writing to or referencing outside the run
    directory: relative traversal, absolute path, and symbolic link
    are all rejected in the name of the same containment."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for name in ("../escape.txt", "/tmp/escape.txt", "a/../../escape.txt"):
        #: every escape variant (climb-up, absolute, nested traversal) is blocked
        with pytest.raises(ArtifactError, match="artifact path"):
            writer.write_text(name, "x")
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = writer.run_dir / "link.txt"
    link.symlink_to(target)
    #: a symlink dropped into the run cannot be adopted as a legitimate artifact
    with pytest.raises(ArtifactError, match="symbolic link"):
        writer.register_file(link, classification=ArtifactClassification.INTERNAL)


def test_writer_refuses_a_symbolic_artifact_root(tmp_path):
    """The artifact root itself cannot be a symlink: the run's entire
    writing cannot be silently redirected elsewhere."""
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.symlink_to(target, target_is_directory=True)

    #: the refusal happens at construction, before the slightest write
    with pytest.raises(ArtifactError, match="symbolic artifact directory"):
        SecureArtifactWriter(root, "run-1")


def test_writer_redacts_text_json_and_registered_text_files(tmp_path, evidence_case):
    """Redaction covers the three entry paths (text, JSON, registered
    file): the secret value never reaches the run's disk, whatever
    the way the artifact arrives."""
    secret = "artifact-canary-7359"
    writer = SecureArtifactWriter(
        tmp_path,
        "run-1",
        redaction_context=RedactionContext.from_secrets([secret]),
    )
    writer.write_text("message.log", f"Bearer abc.def {secret}")
    writer.write_json(
        "result.json",
        {"url": f"https://demo.test/?token={secret}", "token": secret},
    )
    source = tmp_path / "source.ndjson"
    source.write_text(f'{{"secret":"{secret}"}}\n', encoding="utf-8")
    writer.register_file(source, name="copy.ndjson")

    #: the canary scanner finds the secret nowhere, and every file
    #: carries the redaction marker where the value should have appeared
    assert scan_canaries(writer.run_dir, [secret]) == []
    message_redacted = (writer.run_dir / "message.log").read_text(encoding="utf-8")
    result_redacted = (writer.run_dir / "result.json").read_text(encoding="utf-8")
    assert "***" in message_redacted
    assert "***" in result_redacted
    assert "***" in (writer.run_dir / "copy.ndjson").read_text(encoding="utf-8")

    if evidence_case is not None:
        # We only attach the output ALREADY sanitized by the writer, never the
        # raw value: the visual proof shows the *** marker in place.
        message_proof = evidence_case.attach_text(
            "Redacted log (message.log)", message_redacted, filename="message.log"
        )
        result_proof = evidence_case.attach_text(
            "Redacted result (result.json)", result_redacted, filename="result.json"
        )
        #: the produced proof artifact never contains the canary, only
        #: the version already marked with *** that the cockpit reader sees
        assert secret not in Path(message_proof["path"]).read_text(encoding="utf-8")
        assert secret not in Path(result_proof["path"]).read_text(encoding="utf-8")


def test_shareable_staging_contains_only_manifested_allowed_files(tmp_path, evidence_case):
    """Shareable staging works as an allowlist: only files that are
    manifested AND upload-allowed are copied, and the exported manifest
    does not even betray the existence of the rest."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_json(
        "safe.json",
        {"ok": True},
        classification=ArtifactClassification.PUBLIC,
        upload_allowed=True,
    )
    writer.write_text(
        "private.log",
        "internal",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    staging = writer.build_shareable(tmp_path / "shareable")
    #: the allowed public file is copied, the non-allowed internal file stays put
    assert (staging / "safe.json").exists()
    assert not (staging / "private.log").exists()
    shared_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    #: the shared manifest lists only what was actually exported
    assert [item["path"] for item in shared_manifest["artifacts"]] == ["safe.json"]

    if evidence_case is not None:
        evidence_case.attach_json(
            "Shared staging manifest (allowlist)",
            shared_manifest,
            filename="shared-manifest.json",
        )


def test_unmanifested_private_file_blocks_staging(tmp_path):
    """A file that appears in the run without going through the writer
    blocks the entire staging: nothing unknown can slip into a share."""
    writer = SecureArtifactWriter(tmp_path, "run-1")
    writer.write_text("safe.txt", "safe")
    rogue = writer.run_dir / "rogue.txt"
    rogue.write_text("rogue", encoding="utf-8")
    #: the orphan file is treated as a compromise, not simply ignored
    with pytest.raises(ArtifactError, match="unmanifested"):
        writer.build_shareable(tmp_path / "share")


def test_mutated_manifested_file_blocks_staging(tmp_path):
    """An artifact modified after writing — hence after redaction — breaks
    the integrity check: staging refuses to propagate content that is
    no longer what was sanitized."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text(
        "safe.txt",
        "safe",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    (writer.run_dir / "safe.txt").write_text("secret-after-redaction", encoding="utf-8")

    #: the manifest's sha256 serves as a seal: any post-redaction mutation is fatal
    with pytest.raises(ArtifactError, match="integrity"):
        writer.build_shareable(tmp_path / "share")

    #: the failure is atomic — no partial sharing directory is left behind
    assert not (tmp_path / "share").exists()


def test_missing_manifested_file_blocks_staging(tmp_path):
    """The disappearance of a manifested file is a blocking anomaly:
    the export does not simply silently omit what is missing."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").unlink()

    #: a manifest promising a missing file invalidates the entire staging
    with pytest.raises(ArtifactError, match="not found"):
        writer.build_shareable(tmp_path / "share")


def test_replaced_manifested_file_symlink_blocks_staging(tmp_path):
    """Substituting a symlink for a manifested artifact does not allow
    exfiltrating an outside file via the shareable copy."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    artifact = writer.run_dir / "safe.txt"
    artifact.unlink()
    artifact.symlink_to(outside)

    #: the substituted link is unmasked at copy time, despite a manifested name
    with pytest.raises(ArtifactError, match="symbolic link"):
        writer.build_shareable(tmp_path / "share")

    #: nothing was copied: the outside content never left its place
    assert not (tmp_path / "share").exists()


def test_overly_permissive_manifested_file_blocks_staging(tmp_path):
    """Private permissions are part of the verified contract: an artifact
    that became readable by others is no longer worthy of sharing."""
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").chmod(0o644)

    #: the permission widening is detected before any copy out of the run
    with pytest.raises(ArtifactError, match="permissions"):
        writer.build_shareable(tmp_path / "share")


def test_canary_scanner_and_expiration_purge(tmp_path):
    """The canary scanner finds the secret value even inside an opaque
    binary artifact, and the TTL purge actually erases expired runs."""
    writer = SecureArtifactWriter(tmp_path, "expired", ttl=1)
    writer.write_bytes(
        "leak.bin",
        b"CANARY-SECRET",
        classification=ArtifactClassification.OPAQUE_RESTRICTED,
    )
    #: the planted canary is located even inside a non-textual binary blob
    assert scan_canaries(writer.run_dir, ["CANARY-SECRET"]) == ["leak.bin"]
    future = datetime.now(UTC) + timedelta(seconds=2)
    #: past the TTL, the run is purged from disk and its identifier reported
    assert purge_expired(tmp_path, now=future) == ["expired"]
    assert not writer.run_dir.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        "{}",
        '{"expires_at": "2026-01-01T00:00:00"}',
    ],
)
def test_expiration_purge_rejects_invalid_manifests(tmp_path, payload):
    run_dir = tmp_path / "broken"
    run_dir.mkdir()
    manifest = run_dir / "manifest.json"
    manifest.write_text(payload, encoding="utf-8")

    with pytest.raises(ArtifactError, match="invalid artifacts manifest.*broken") as error:
        purge_expired(tmp_path)

    assert error.value.__cause__ is not None
    assert run_dir.exists()


def test_expiration_purge_propagates_permission_error_as_is(tmp_path, monkeypatch):
    run_dir = tmp_path / "unreadable"
    run_dir.mkdir()
    manifest = run_dir / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def fail_manifest_read(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("permission denied")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", fail_manifest_read)

    #: PermissionError passes through without being requalified: the proof
    #: consumer recognizes it (chown remedy) and continues its run best-effort
    with pytest.raises(PermissionError):
        purge_expired(tmp_path)
    #: nothing was destroyed under doubt
    assert run_dir.exists()


def test_expiration_purge_skips_dir_without_manifest(tmp_path):
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "data.txt").write_text("preserve", encoding="utf-8")
    expired = tmp_path / "expired"
    expired.mkdir()
    (expired / "manifest.json").write_text(
        '{"expires_at": "2000-01-01T00:00:00+00:00"}', encoding="utf-8"
    )

    removed = purge_expired(tmp_path)

    #: the directory without a manifest is kept fail-open, without exception
    assert orphan.exists() and "orphan" not in removed
    #: the purge continued past the orphan: the dated expired one is indeed gone
    assert removed == ["expired"] and not expired.exists()
