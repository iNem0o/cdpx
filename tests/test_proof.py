import json
import stat
from datetime import datetime

import pytest

from cdpx import proof
from cdpx.artifacts import ArtifactError
from cdpx.cli import build_parser
from cdpx.security.redaction import RedactionContext


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_repo_env_is_allowlisted_and_excludes_credentials(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/tmp/home")
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("CDPX_TEST_SECRET", "cdpx-secret")
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")

    env = proof._repo_env()

    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/tmp/home"
    assert "PYTHONPATH" in env
    assert "GITHUB_TOKEN" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "CDPX_TEST_SECRET" not in env
    assert env["CDPX_PROOF_RETENTION_DAYS"] == "30"


def test_run_evidence_redacts_command_and_output_and_uses_private_mode(tmp_path, monkeypatch):
    secret = "proof-secret-123"
    context = RedactionContext.from_secrets([secret])

    class Completed:
        returncode = 0
        stdout = f"token={secret}\nBearer abcdefghijk"

    monkeypatch.setattr(proof.subprocess, "run", lambda *args, **kwargs: Completed())
    log = tmp_path / "logs" / "command.log"

    proof.run_evidence(
        "secret",
        "Secret",
        ["tool", secret],
        log,
        env={"PATH": "/usr/bin"},
        redaction_context=context,
    )

    contents = log.read_text(encoding="utf-8")
    assert secret not in contents
    assert "***" in contents
    assert mode(log.parent) == 0o700
    assert mode(log) == 0o600


def test_build_shareable_proof_allowlists_sanitized_text_and_excludes_opaque(tmp_path):
    proof_dir = tmp_path / ".proof"
    report = '<script>const graph={data:[1,2]};const icon="data:image/png;base64,abc";</script>'
    proof._write_private_text(proof_dir / "proof-report.html", report)
    proof._write_private_text(proof_dir / "validation-summary.json", '{"ok": true}\n')
    proof._write_private_bytes(proof_dir / "evidence" / "shot.png", b"\x89PNG\r\n")

    staging = proof.build_shareable_proof(
        proof_dir,
        canaries=["never-present"],
        ttl=7200,
        pre_redacted_paths={"proof-report.html"},
    )

    assert (staging / ".proof" / "proof-report.html").exists()
    assert (staging / ".proof" / "validation-summary.json").exists()
    assert not (staging / ".proof" / "evidence" / "shot.png").exists()
    public_manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    assert public_manifest["expires_at"] > public_manifest["created_at"]
    assert all(item["upload_allowed"] for item in public_manifest["artifacts"])
    assert mode(staging) == 0o700
    assert mode(staging / "manifest.json") == 0o600
    assert mode(staging / ".proof" / "proof-report.html") == 0o600
    assert (staging / ".proof" / "proof-report.html").read_text(encoding="utf-8") == report
    private_manifest = json.loads(
        (proof_dir / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    screenshot = next(
        item for item in private_manifest["artifacts"] if item["path"].endswith("shot.png")
    )
    assert screenshot["classification"] == "opaque-restricted"
    assert screenshot["upload_allowed"] is False


def test_build_shareable_proof_fails_closed_on_canary(tmp_path):
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "unsafe.log", "leaked-canary")

    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(proof_dir, canaries=["leaked-canary"])

    assert not (proof_dir / "shareable").exists()


def test_pre_redacted_report_still_fails_closed_on_canary(tmp_path):
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>leaked-canary</p>")

    with pytest.raises(ArtifactError, match="canary"):
        proof.build_shareable_proof(
            proof_dir,
            canaries=["leaked-canary"],
            pre_redacted_paths={"proof-report.html"},
        )

    assert not (proof_dir / "shareable").exists()


def test_build_shareable_proof_uses_validated_environment_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "30")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    staging = proof.build_shareable_proof(proof_dir)

    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    created = datetime.fromisoformat(manifest["created_at"])
    expires = datetime.fromisoformat(manifest["expires_at"])
    assert (expires - created).days == 30


def test_build_shareable_proof_rejects_invalid_environment_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "unbounded")
    proof_dir = tmp_path / ".proof"
    proof._write_private_text(proof_dir / "proof-report.html", "<p>safe</p>")

    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.build_shareable_proof(proof_dir)

    assert not (proof_dir / "shareable").exists()


def test_generate_rejects_invalid_retention_before_replacing_existing_proof(tmp_path, monkeypatch):
    proof_dir = tmp_path / ".proof"
    proof_dir.mkdir()
    marker = proof_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setenv("CDPX_PROOF_RETENTION_DAYS", "0")

    with pytest.raises(ValueError, match="CDPX_PROOF_RETENTION_DAYS"):
        proof.generate()

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_project_unknowns_describe_private_screenshot_scope():
    packet = proof.build_project_risks_and_unknowns()
    screenshot = next(
        item for item in packet["unknowns"] if item["item"] == "Portée des captures visuelles"
    )

    assert ".proof/evidence/" in screenshot["why"]
    assert "exclues du staging partageable" in screenshot["why"]
    assert "sans le conserver" not in screenshot["why"]


def empty_scenario_evidence():
    suites = {"unit": [], "integration": [], "e2e": []}
    return {"suites": suites, "files": [], "totals": proof.scenario_totals(suites)}


def test_parse_junit_extracts_counts_and_cases(tmp_path):
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

    assert parsed["tests"] == 3
    assert parsed["passed"] == 1
    assert parsed["failures"] == 1
    assert parsed["skipped"] == 1
    assert parsed["cases"][1]["status"] == "failed"
    assert parsed["cases"][1]["message"] == "assertion failed"


def test_parse_junit_reports_malformed_xml(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite>", encoding="utf-8")

    parsed = proof.parse_junit(junit)

    assert parsed["exists"] is True
    assert parsed["tests"] == 0
    assert parsed["parse_error"]


def test_parse_help_commands_uses_captured_argparse_help():
    help_text = build_parser().format_help()

    commands = proof.parse_help_commands(help_text)

    names = {command["name"] for command in commands}
    assert {"goto", "seo", "vitals", "replay"}.issubset(names)
    assert any(command["help"] for command in commands if command["name"] == "seo")


def test_build_summary_preserves_historical_artifact_keys():
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

    summary = proof.build_summary([command], unit, e2e, scenario_evidence=empty_scenario_evidence())

    assert summary["ok"] is True
    assert summary["unit_log"] == ".proof/make-check-pytest.log"
    assert summary["e2e_log"] == ".proof/e2e-chrome.log"
    assert summary["report_html"] == ".proof/proof-report.html"


def test_build_summary_adds_project_evidence_sections():
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
    )

    assert summary["project"]["name"] == "cdpx"
    assert summary["project"]["cli_command_count"] >= 20
    assert summary["project"]["fixture_count"] >= 1
    assert summary["validation_matrix"]
    assert summary["coverage_groups"] == []
    assert any(item["type"] == "junit" for item in summary["evidence_catalog"])
    assert summary["unknowns"]


def test_build_summary_includes_symfony_suite_and_catalog():
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
    )

    assert summary["ok"] is True
    assert summary["symfony_log"] == ".proof/symfony-e2e.log"
    assert summary["junit"]["symfony"]["tests"] == 1
    assert summary["totals"]["tests"] == 4
    assert any(item["name"] == "Symfony E2E JUnit" for item in summary["evidence_catalog"])


def test_write_symfony_unavailable_evidence_is_explicit(tmp_path, monkeypatch):
    proof_dir = tmp_path / ".proof"
    monkeypatch.setattr(proof, "PROOF_DIR", proof_dir)
    monkeypatch.setattr(proof, "EVIDENCE_DIR", proof_dir / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", proof_dir / "symfony-e2e.log")
    proof.SYMFONY_LOG.parent.mkdir(parents=True)
    proof.SYMFONY_LOG.write_text("docker unavailable\n", encoding="utf-8")

    proof.write_symfony_unavailable_evidence("Docker daemon unavailable")

    payload = (proof.EVIDENCE_DIR / "symfony-scenarios.json").read_text(encoding="utf-8")
    assert '"suite": "symfony"' in payload
    assert '"status": "unavailable"' in payload
    assert "Docker daemon unavailable" in payload


def test_run_symfony_evidence_fails_when_docker_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(proof, "EVIDENCE_DIR", tmp_path / "evidence")
    monkeypatch.setattr(proof, "SYMFONY_LOG", tmp_path / "symfony.log")
    monkeypatch.setattr(proof.shutil, "which", lambda _name: None)

    command = proof.run_symfony_evidence()

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
    # Garde-fou "calculé => rendu": toute clé de premier niveau du summary doit
    # être lue par la SPA (data.<clé>), sauf celles rendues par le shell HTML ou
    # purement méta (chemins d'artefacts, duplicats bruts).
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        help_commands=proof.parse_help_commands(build_parser().format_help()),
        scenario_evidence=empty_scenario_evidence(),
    )
    shell_keys = {"ok", "generated_at", "git"}  # rendus par render_html directement
    meta_keys = {"artifact_dir", "report_html", "unit_log", "e2e_log", "symfony_log"}
    meta_keys.add("scenario_evidence")  # duplicat brut de feature_inventory/matched_scenarios
    for key in summary:
        if key in shell_keys | meta_keys or f"data.{key}" in proof.SPA_JS:
            continue
        raise AssertionError(f"clé du summary calculée mais jamais rendue par la SPA: {key}")


def test_render_html_embeds_payload_verdict_and_routes():
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
    )
    html = proof.render_html(summary)
    assert 'id="report-data"' in html and '"ok": true'.replace(" ", "") in html.replace(" ", "")
    assert ">OK<" in html
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
    assert "securityLevel: 'strict'" in html
    assert "connect-src 'none'" in html
    assert "<script src=" not in html


def test_build_summary_exposes_curated_documentation_catalog():
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
    )

    documentation = summary["documentation"]
    assert documentation["schema"] == "cdpx.docs/v1"
    assert documentation["violations"] == []
    assert any(
        document["path"] == "docs/SESSION-LIFECYCLE.md" for document in documentation["documents"]
    )
    assert not any(failure.startswith("documentation:") for failure in summary["proof_failures"])


def test_mermaid_vendor_bundle_is_integrity_checked_and_embedded(monkeypatch):
    bundle = proof._mermaid_bundle()
    assert len(bundle) > 3_000_000
    assert "mermaid" in bundle.lower()

    proof._mermaid_bundle.cache_clear()
    monkeypatch.setattr(proof, "MERMAID_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="bundle Mermaid"):
        proof._mermaid_bundle()
    proof._mermaid_bundle.cache_clear()


def test_cockpit_assets_are_packaged_and_sane():
    # La présentation vit dans des ressources dédiées (cockpit/) chargées via
    # importlib.resources: chaque asset doit exister, être non vide, et les
    # scripts/styles doivent rester inlinables (pas de </script> prématuré).
    from string import Template

    for name in proof.COCKPIT_RESOURCES:
        asset = proof._cockpit_asset(name)
        assert asset.strip(), f"asset cockpit vide: {name}"
        if name != proof.COCKPIT_SHELL_RESOURCE:
            assert "</script" not in asset.lower(), f"asset non inlinable: {name}"

    shell = proof._cockpit_asset(proof.COCKPIT_SHELL_RESOURCE)
    # Le shell doit se substituer sans placeholder manquant ni $ littéral orphelin.
    rendered = Template(shell).substitute(
        verdict="OK",
        pill="ok",
        context="ctx",
        spa_css="",
        payload="{}",
        mermaid_bundle="",
        spa_js="",
    )
    assert rendered.startswith("<!doctype html>")

    with pytest.raises(FileNotFoundError):
        proof._cockpit_asset("cockpit/does-not-exist.js")


def test_build_summary_embeds_cases_focus_and_log_tails(tmp_path):
    cases = [
        {"classname": "tests.test_a", "name": "test_x", "time_s": 0.5, "status": "passed"},
        {"classname": "tests.test_a", "name": "test_y", "time_s": 0.1, "status": "failed"},
    ]
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml", tests=2, cases=cases),
        _minimal_suite(".proof/e2e-junit.xml"),
        scenario_evidence=empty_scenario_evidence(),
    )
    assert summary["junit"]["unit"]["cases"] == cases
    assert summary["junit"]["unit"]["focus"][0]["status"] == "failed"  # échecs d'abord
    assert "log_tail" in summary["commands"][0]


def test_symfony_unavailable_is_always_blocking(monkeypatch):
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
    assert summary["totals"]["unavailable"] == 1  # visible dans le hero
    assert summary["ok"] is False
    assert any("symfony evidence unavailable" in failure for failure in summary["proof_failures"])


def test_symfony_skips_are_release_blocking():
    summary = proof.build_summary(
        [_ok_command()],
        _minimal_suite(".proof/unit-junit.xml"),
        _minimal_suite(".proof/e2e-junit.xml"),
        _minimal_suite(".proof/symfony-e2e-junit.xml", tests=2) | {"passed": 1, "skipped": 1},
        scenario_evidence=empty_scenario_evidence(),
    )

    assert summary["ok"] is False
    assert any("symfony tests skipped" in failure for failure in summary["proof_failures"])


def test_chrome_skips_and_missing_junit_are_release_blocking():
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
    )
    missing_summary = proof.build_summary(
        [e2e_command],
        _minimal_suite(".proof/unit-junit.xml"),
        proof._empty_suite(proof.Path(".proof/e2e-junit.xml")),
        scenario_evidence=empty_scenario_evidence(),
    )

    assert skipped_summary["ok"] is False
    assert "e2e tests skipped (1)" in skipped_summary["proof_failures"]
    assert missing_summary["ok"] is False
    assert any("required JUnit missing" in item for item in missing_summary["proof_failures"])


def test_build_summary_fails_when_e2e_screenshot_missing():
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

    assert summary["ok"] is False
    assert (
        "missing e2e screenshot: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )
    assert (
        "feature inventory: scenario unmapped: tests/e2e/test_demo.py::test_without_shot"
        in summary["proof_failures"]
    )
