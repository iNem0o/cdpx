"""Building the proof summary (verdict, totals, catalog, gates).

``build_summary`` receives via ``ProofPaths`` the paths that the
`cdpx.proof` facade lets tests monkeypatch (PROOF_DIR, SYMFONY_LOG,
EVIDENCE_DIR, …), resolved by it at call time. No symbol in this module
reads `cdpx.proof` at runtime.
"""

from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from cdpx.proofing.cast import CAST_COMMANDS
from cdpx.proofing.documentation import (
    build_documentation_catalog,
    documentation_failures,
)
from cdpx.proofing.evidence_catalog import (
    ProofPaths,
    build_evidence_catalog,
    collect_project_inventory,
    group_cases_by_module,
    parse_validation_matrix,
)
from cdpx.proofing.execution import CommandEvidence
from cdpx.proofing.features import build_feature_inventory, feature_failures
from cdpx.proofing.gitcontext import build_project_risks_and_unknowns
from cdpx.proofing.junit import _empty_suite, _suite_for_summary, _tail
from cdpx.proofing.private_io import _now
from cdpx.proofing.scenario_inline import (
    enrich_scenario_evidence,
    inline_scenario_artifacts,
    load_scenario_evidence,
    proof_failures_from_scenarios,
)
from cdpx.proofing.scenario_models import ScenarioEvidence


def cast_failures_from_entries(cast_entries: list[dict] | None) -> list[str]:
    """Cast gate: every demonstration command must have its .cast generated."""

    by_id = {str(entry.get("id", "")): entry for entry in (cast_entries or [])}
    failures = []
    for cast_id, _argv in CAST_COMMANDS:
        entry = by_id.get(cast_id)
        if entry is None:
            failures.append(f"cast missing: {cast_id}")
        elif entry.get("status") != "generated":
            failures.append(f"cast {entry.get('status', 'unknown')}: {cast_id}")
    return failures


def build_summary(
    commands: list[CommandEvidence],
    unit: dict,
    e2e: dict,
    symfony: dict | None = None,
    *,
    git_context: dict | None = None,
    help_commands: list[dict[str, str]] | None = None,
    scenario_evidence: ScenarioEvidence | None = None,
    cast_entries: list[dict] | None = None,
    proof_dir: Path | None = None,
    paths: ProofPaths,
) -> dict:
    symfony = symfony or _empty_suite(paths.symfony_junit)
    git_context = git_context or {
        "branch": "unknown",
        "sha": "unknown",
        "changed_files": [],
        "generated_files": [],
        "changed_count": 0,
        "generated_count": 0,
        "status_path": str(paths.git_status),
        "diff_stat_path": str(paths.git_diff_stat),
    }
    help_commands = help_commands or []
    project = collect_project_inventory(help_commands)
    validation_matrix = parse_validation_matrix()
    coverage_groups = group_cases_by_module(unit, e2e, symfony)
    scenario_evidence = scenario_evidence or load_scenario_evidence(
        paths.evidence_dir if proof_dir is None else proof_dir / paths.evidence_dir.name
    )
    feature_inventory = build_feature_inventory(help_commands, scenario_evidence, git_context)
    documentation = build_documentation_catalog()
    scenario_evidence = enrich_scenario_evidence(scenario_evidence, feature_inventory)
    scenario_evidence = inline_scenario_artifacts(scenario_evidence)
    scenario_failures = proof_failures_from_scenarios(scenario_evidence)
    feature_inventory_failures = feature_failures(feature_inventory)
    documentation_catalog_failures = documentation_failures(documentation)
    risk_packet = build_project_risks_and_unknowns()
    failed_tests = (
        unit["failures"]
        + unit["errors"]
        + e2e["failures"]
        + e2e["errors"]
        + symfony["failures"]
        + symfony["errors"]
    )
    command_failures = [
        f"command failed: {command.label} ({command.log})"
        for command in commands
        if command.exit_code != 0
    ]
    suite_by_command = {"unit": unit, "e2e": e2e, "symfony-e2e": symfony}
    command_ids = {command.id for command in commands}
    suite_failures = []
    for command_id, suite in suite_by_command.items():
        if command_id not in command_ids:
            continue
        if not suite.get("exists", True):
            suite_failures.append(f"required JUnit missing: {suite.get('path', command_id)}")
        elif suite.get("parse_error"):
            suite_failures.append(
                f"required JUnit unreadable: {suite.get('path', command_id)} "
                f"({suite['parse_error']})"
            )
        elif suite.get("tests", 0) == 0:
            suite_failures.append(f"required JUnit empty: {suite.get('path', command_id)}")
        if command_id in {"e2e", "symfony-e2e"} and suite.get("skipped", 0):
            suite_failures.append(f"{command_id} tests skipped ({suite['skipped']})")
    if "cli-help" in command_ids and project["cli_command_count"] != 31:
        suite_failures.append(
            f"CLI contract expected 31 commands, found {project['cli_command_count']}"
        )
    unavailable = sum(
        1
        for suite in scenario_evidence.get("suites", {}).values()
        for scenario in suite
        if scenario.get("status") == "unavailable"
    )
    symfony_failures = []
    if unavailable:
        symfony_failures.append(f"symfony evidence unavailable ({unavailable} scenarios)")
    if symfony["skipped"]:
        symfony_failures.append(f"symfony tests skipped ({symfony['skipped']})")
    cast_gate_failures = cast_failures_from_entries(cast_entries)
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
        and not feature_inventory_failures
        and not documentation_catalog_failures
        and not symfony_failures
        and not suite_failures
        and not cast_gate_failures
    )
    summary = {
        "ok": ok,
        "generated_at": _now(),
        "artifact_dir": str(paths.proof_dir),
        "report_html": str(paths.report_html),
        "unit_log": str(paths.unit_log),
        "e2e_log": str(paths.e2e_log),
        "symfony_log": str(paths.symfony_log),
        "cli_help": str(paths.cli_help),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "chrome_or_chromium": bool(
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or shutil.which("google-chrome")
                or shutil.which("chrome")
            ),
        },
        "commands": [
            {**asdict(command), "log_tail": _tail(Path(command.log))} for command in commands
        ],
        "junit": {
            "unit": _suite_for_summary(unit),
            "e2e": _suite_for_summary(e2e),
            "symfony": _suite_for_summary(symfony),
        },
        "totals": {
            "tests": unit["tests"] + e2e["tests"] + symfony["tests"],
            "passed": unit["passed"] + e2e["passed"] + symfony["passed"],
            "skipped": unit["skipped"] + e2e["skipped"] + symfony["skipped"],
            "failed": failed_tests,
            "unavailable": unavailable,
        },
        "git": git_context,
        "project": project,
        "validation_matrix": validation_matrix,
        "coverage_groups": coverage_groups,
        "scenario_evidence": scenario_evidence,
        "scenario_totals": scenario_evidence["totals"],
        "feature_inventory": feature_inventory,
        "documentation": documentation,
        "casts": list(cast_entries or []),
        "proof_failures": scenario_failures
        + feature_inventory_failures
        + documentation_catalog_failures
        + command_failures
        + symfony_failures
        + suite_failures
        + cast_gate_failures,
        "risks": risk_packet["risks"],
        "unknowns": risk_packet["unknowns"],
    }
    summary["evidence_catalog"] = build_evidence_catalog(
        summary, unit, e2e, symfony, paths=paths, proof_dir=proof_dir
    )
    return summary
