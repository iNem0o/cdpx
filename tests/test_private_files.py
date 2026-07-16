"""Contrat commun de publication atomique des fichiers privés."""

import stat

import pytest

from cdpx import private_files


def test_atomic_write_bytes_publishes_complete_private_file(tmp_path):
    destination = tmp_path / "artifact.bin"

    private_files.atomic_write_bytes(destination, b"complete")

    assert destination.read_bytes() == b"complete"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_preserves_previous_file_and_cleans_temp_on_publish_failure(
    tmp_path, monkeypatch
):
    destination = tmp_path / "artifact.txt"
    destination.write_text("previous", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("publish failed")

    monkeypatch.setattr(private_files.os, "replace", fail_replace)

    with pytest.raises(OSError, match="publish failed"):
        private_files.atomic_write_text(destination, "replacement")

    assert destination.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.glob(".*.tmp")) == []
