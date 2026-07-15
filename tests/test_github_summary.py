import json
import stat
from pathlib import Path

from scripts.github_summary import build_report, write_private_outputs


def test_github_summary_uses_real_proof_and_archives(tmp_path: Path, monkeypatch, evidence_case):
    """Le résumé GitHub est construit depuis la preuve mesurée, pas depuis
    des constantes: verdict PASS, compteurs réels, politique de rétention
    affichée au lecteur et empreintes des archives du dist."""
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

    #: le markdown reflète les totaux, suites et compteurs fournis par la
    #: preuve, jusqu'au nom exact de l'artefact CI
    assert "cdpx PR proof: PASS" in markdown
    assert "343 passed" in markdown
    assert "32 tests" in markdown and "7 tests" in markdown
    assert "31 commands" in markdown
    assert "pr-proof-123-1" in markdown
    #: le lecteur du PR est prévenu de la rétention et de ce que l'upload
    #: exclut volontairement
    assert "14 days, manifested text only" in markdown
    assert (
        "Screenshots, opaque binaries, raw portal logs, wheels, and sdists are not included"
        in markdown
    )
    #: wheel et sdist sont inventoriés avec une empreinte SHA-256 complète,
    #: vérifiable par quiconque télécharge les archives
    assert packaging["ok"] is True
    assert len(packaging["archives"]) == 2
    assert all(len(item["sha256"]) == 64 for item in packaging["archives"])

    if evidence_case is not None:
        evidence_case.attach_text(
            "Rapport PR GitHub (PASS)", markdown, filename="github-summary.md"
        )


def test_github_summary_reports_early_failure(tmp_path: Path, evidence_case):
    """Un échec en amont (résumé de validation absent) produit un rapport
    FAIL honnête qui cite la cause et l'absence des archives."""
    markdown, packaging = build_report(
        {},
        summary_error="validation summary is absent",
        dist_dir=tmp_path / "missing",
        artifact_name="pr-proof-456-1",
        release_outcome="failure",
    )

    #: le rapport avoue l'échec, en donne la cause exacte et constate
    #: l'absence de wheel comme de sdist
    assert "cdpx PR proof: FAIL" in markdown
    assert "validation summary is absent" in markdown
    assert "wheel=no" in markdown and "sdist=no" in markdown
    assert packaging["ok"] is False

    if evidence_case is not None:
        evidence_case.attach_text(
            "Rapport PR GitHub (FAIL en amont)", markdown, filename="github-summary-fail.md"
        )


def test_packaging_summary_is_json_serializable(tmp_path: Path):
    """Le résumé packaging doit pouvoir traverser les outputs GitHub
    Actions: sa sérialisation JSON n'échoue pas, même en cas d'échec de la
    validation."""
    _, packaging = build_report(
        {"ok": False},
        summary_error=None,
        dist_dir=tmp_path,
        artifact_name="proof",
        release_outcome="failure",
    )

    json.dumps(packaging)


def test_github_summary_outputs_are_private(tmp_path: Path):
    """Les sorties du résumé sont écrites en fichiers privés, alignées sur
    la discipline de permissions du reste de la preuve."""
    output_dir = tmp_path / "diagnostics"

    write_private_outputs(output_dir, "safe summary\n", {"ok": True})

    #: dossier et fichiers de diagnostic sont illisibles pour les autres
    #: comptes du runner
    assert stat.S_IMODE(output_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((output_dir / "github-summary.md").stat().st_mode) == 0o600
    assert stat.S_IMODE((output_dir / "packaging-summary.json").stat().st_mode) == 0o600
