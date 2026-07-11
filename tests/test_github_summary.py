import json
from pathlib import Path

from scripts.github_summary import build_report


def test_github_summary_uses_real_proof_and_archives(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "cdpx-0.2.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "cdpx-0.2.0.tar.gz").write_bytes(b"sdist")
    summary = {
        "ok": True,
        "project": {"version": "0.2.0", "cli_command_count": 30},
        "cli_command_count": 30,
        "totals": {"passed": 343, "failed": 0, "skipped": 0, "unavailable": 0},
        "commands": [{"id": "e2e", "status": "ok"}, {"id": "symfony-e2e", "status": "ok"}],
        "junit": {"e2e": {"tests": 32, "skipped": 0}, "symfony": {"tests": 7, "skipped": 0}},
        "feature_inventory": {"totals": {"violations": 0, "warnings": 0}},
        "proof_failures": [],
    }

    markdown, packaging = build_report(
        summary,
        summary_error=None,
        dist_dir=dist,
        artifact_name="pr-proof-123-1",
        release_outcome="success",
    )

    assert "cdpx PR proof: PASS" in markdown
    assert "343 passed" in markdown
    assert "32 tests" in markdown and "7 tests" in markdown
    assert "30 commands" in markdown
    assert "pr-proof-123-1" in markdown
    assert packaging["ok"] is True
    assert len(packaging["archives"]) == 2
    assert all(len(item["sha256"]) == 64 for item in packaging["archives"])


def test_github_summary_reports_early_failure(tmp_path: Path):
    markdown, packaging = build_report(
        {},
        summary_error="validation summary is absent",
        dist_dir=tmp_path / "missing",
        artifact_name="pr-proof-456-1",
        release_outcome="failure",
    )

    assert "cdpx PR proof: FAIL" in markdown
    assert "validation summary is absent" in markdown
    assert "wheel=no" in markdown and "sdist=no" in markdown
    assert packaging["ok"] is False


def test_packaging_summary_is_json_serializable(tmp_path: Path):
    _, packaging = build_report(
        {"ok": False},
        summary_error=None,
        dist_dir=tmp_path,
        artifact_name="proof",
        release_outcome="failure",
    )

    json.dumps(packaging)
