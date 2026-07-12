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
    writer = SecureArtifactWriter(tmp_path, "run-1")
    entry = writer.write_text(
        "logs/result.txt",
        "safe output",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    path = writer.run_dir / entry.path
    assert path.read_text(encoding="utf-8") == "safe output"
    assert mode(writer.run_dir) == 0o700 and mode(path) == 0o600
    manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
    assert mode(writer.manifest_path) == 0o600
    assert manifest["schema"] == "cdpx.artifacts/v1"
    assert manifest["artifacts"][0]["sha256"] == entry.sha256
    assert manifest["artifacts"][0]["bytes"] == len("safe output")


def test_opaque_and_secret_artifacts_can_never_be_shareable(tmp_path):
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for classification in (
        ArtifactClassification.SECRET,
        ArtifactClassification.OPAQUE_RESTRICTED,
    ):
        with pytest.raises(ArtifactError, match="non partageable"):
            writer.write_text(
                f"{classification.value}.txt",
                "content",
                classification=classification,
                upload_allowed=True,
            )


def test_writer_refuses_traversal_absolute_paths_and_symlinks(tmp_path):
    writer = SecureArtifactWriter(tmp_path, "run-1")
    for name in ("../escape.txt", "/tmp/escape.txt", "a/../../escape.txt"):
        with pytest.raises(ArtifactError, match="chemin"):
            writer.write_text(name, "x")
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = writer.run_dir / "link.txt"
    link.symlink_to(target)
    with pytest.raises(ArtifactError, match="symbolique"):
        writer.register_file(link, classification=ArtifactClassification.INTERNAL)


def test_writer_refuses_a_symbolic_artifact_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "root"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(ArtifactError, match="symbolique"):
        SecureArtifactWriter(root, "run-1")


def test_writer_redacts_text_json_and_registered_text_files(tmp_path):
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

    assert scan_canaries(writer.run_dir, [secret]) == []
    assert "***" in (writer.run_dir / "message.log").read_text(encoding="utf-8")
    assert "***" in (writer.run_dir / "result.json").read_text(encoding="utf-8")
    assert "***" in (writer.run_dir / "copy.ndjson").read_text(encoding="utf-8")


def test_shareable_staging_contains_only_manifested_allowed_files(tmp_path):
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
    assert (staging / "safe.json").exists()
    assert not (staging / "private.log").exists()
    shared_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert [item["path"] for item in shared_manifest["artifacts"]] == ["safe.json"]


def test_unmanifested_private_file_blocks_staging(tmp_path):
    writer = SecureArtifactWriter(tmp_path, "run-1")
    writer.write_text("safe.txt", "safe")
    rogue = writer.run_dir / "rogue.txt"
    rogue.write_text("rogue", encoding="utf-8")
    with pytest.raises(ArtifactError, match="non manifesté"):
        writer.build_shareable(tmp_path / "share")


def test_mutated_manifested_file_blocks_staging(tmp_path):
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text(
        "safe.txt",
        "safe",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    (writer.run_dir / "safe.txt").write_text("secret-after-redaction", encoding="utf-8")

    with pytest.raises(ArtifactError, match="intégrité"):
        writer.build_shareable(tmp_path / "share")

    assert not (tmp_path / "share").exists()


def test_missing_manifested_file_blocks_staging(tmp_path):
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").unlink()

    with pytest.raises(ArtifactError, match="introuvable"):
        writer.build_shareable(tmp_path / "share")


def test_replaced_manifested_file_symlink_blocks_staging(tmp_path):
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    artifact = writer.run_dir / "safe.txt"
    artifact.unlink()
    artifact.symlink_to(outside)

    with pytest.raises(ArtifactError, match="symbolique"):
        writer.build_shareable(tmp_path / "share")

    assert not (tmp_path / "share").exists()


def test_overly_permissive_manifested_file_blocks_staging(tmp_path):
    writer = SecureArtifactWriter(tmp_path / "private", "run-1")
    writer.write_text("safe.txt", "safe", upload_allowed=True)
    (writer.run_dir / "safe.txt").chmod(0o644)

    with pytest.raises(ArtifactError, match="permissions"):
        writer.build_shareable(tmp_path / "share")


def test_canary_scanner_and_expiration_purge(tmp_path):
    writer = SecureArtifactWriter(tmp_path, "expired", ttl=1)
    writer.write_bytes(
        "leak.bin",
        b"CANARY-SECRET",
        classification=ArtifactClassification.OPAQUE_RESTRICTED,
    )
    assert scan_canaries(writer.run_dir, ["CANARY-SECRET"]) == ["leak.bin"]
    future = datetime.now(UTC) + timedelta(seconds=2)
    assert purge_expired(tmp_path, now=future) == ["expired"]
    assert not writer.run_dir.exists()
