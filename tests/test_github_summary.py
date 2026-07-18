import json
import stat
from pathlib import Path

from scripts.github_summary import build_report, write_private_outputs


def test_github_summary_uses_real_proof_and_archives(tmp_path: Path, monkeypatch, evidence_case):
    """The GitHub summary is built from the measured proof, not from
    constants: PASS verdict, real counts, retention policy shown to the
    reader, and dist archive fingerprints."""
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "cdpx-0.2.0-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "cdpx-0.2.0.tar.gz").write_bytes(b"sdist")
    summary = {
        "ok": True,
        "project": {"version": "0.2.0", "cli_command_count": 31},
        "cli_command_count": 31,
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

    #: the markdown reflects the totals, suites, and counts provided by
    #: the proof, down to the exact CI artifact name
    assert "cdpx PR proof: PASS" in markdown
    assert "343 passed" in markdown
    assert "32 tests" in markdown and "7 tests" in markdown
    assert "31 commands" in markdown
    assert "pr-proof-123-1" in markdown
    #: the PR reader is warned about retention and what the upload
    #: deliberately excludes
    assert "14 days, manifested text only" in markdown
    assert (
        "Screenshots, opaque binaries, raw portal logs, wheels, and sdists are not included"
        in markdown
    )
    #: wheel and sdist are inventoried with a full SHA-256 fingerprint,
    #: verifiable by anyone who downloads the archives
    assert packaging["ok"] is True
    assert len(packaging["archives"]) == 2
    assert all(len(item["sha256"]) == 64 for item in packaging["archives"])

    if evidence_case is not None:
        evidence_case.attach_text("GitHub PR report (PASS)", markdown, filename="github-summary.md")


def test_github_summary_reports_early_failure(tmp_path: Path, evidence_case):
    """An upstream failure (validation summary absent) produces an honest
    FAIL report that cites the cause and the absence of archives."""
    markdown, packaging = build_report(
        {},
        summary_error="validation summary is absent",
        dist_dir=tmp_path / "missing",
        artifact_name="pr-proof-456-1",
        release_outcome="failure",
    )

    #: the report admits the failure, gives its exact cause, and notes the
    #: absence of both wheel and sdist
    assert "cdpx PR proof: FAIL" in markdown
    assert "validation summary is absent" in markdown
    assert "wheel=no" in markdown and "sdist=no" in markdown
    assert packaging["ok"] is False

    if evidence_case is not None:
        evidence_case.attach_text(
            "GitHub PR report (upstream FAIL)", markdown, filename="github-summary-fail.md"
        )


def test_packaging_summary_is_json_serializable(tmp_path: Path):
    """The packaging summary must be able to travel through GitHub Actions
    outputs: its JSON serialization does not fail, even when validation
    fails."""
    _, packaging = build_report(
        {"ok": False},
        summary_error=None,
        dist_dir=tmp_path,
        artifact_name="proof",
        release_outcome="failure",
    )

    json.dumps(packaging)


def test_github_summary_outputs_are_private(tmp_path: Path):
    """The summary outputs are written as private files, aligned with the
    permission discipline of the rest of the proof."""
    output_dir = tmp_path / "diagnostics"

    write_private_outputs(output_dir, "safe summary\n", {"ok": True})

    #: the diagnostics directory and files are unreadable to other
    #: accounts on the runner
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((output_dir / "github-summary.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((output_dir / "packaging-summary.json").stat().st_mode) == 0o600
