from cdpx import proof
from cdpx.cli import build_parser


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


def test_parse_help_commands_uses_captured_argparse_help():
    help_text = build_parser().format_help()

    commands = proof.parse_help_commands(help_text)

    names = {command["name"] for command in commands}
    assert {"goto", "seo", "vitals", "replay"}.issubset(names)
    assert any(command["help"] for command in commands if command["name"] == "seo")


def test_build_summary_preserves_legacy_artifact_keys():
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
    for route in ("#/features", "#/cli", "#/validation", "#/gaps", "#/run", "#/project"):
        assert route in html


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
