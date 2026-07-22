"""Pytest scenario evidence: loading, totals, artifact inlining.

Inlining embeds the textual content of artifacts into the cockpit payload
(the report's CSP forbids any fetch). No symbol in this module reads
`cdpx.proof` at runtime: the facade re-exports these functions and resolves
its own patchable constant ``EVIDENCE_DIR`` itself (default of
``load_scenario_evidence``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from cdpx.proofing.evidence_policy import (
    SCENARIOS_SCHEMA,
    redaction_context_from_environment,
)
from cdpx.proofing.execution import _read_json_or_fail
from cdpx.proofing.private_io import _now, _secure_dir, _write_private_text
from cdpx.proofing.scenario_models import (
    Scenario,
    ScenarioEvidence,
    ScenarioTotals,
    validated_scenario_file,
)
from cdpx.security.redaction import RedactionContext, redact_tree

# Inlining only concerns text: the report's CSP (connect-src 'none')
# forbids any fetch, so whatever the viewers display must travel inside the
# embedded JSON. Binaries remain local links.
_INLINE_TYPES = frozenset(
    {"command", "log-excerpt", "logs", "json", "console", "network", "profiler", "asciinema"}
)
INLINE_MAX_BYTES = 16 * 1024
# .cast files are the player's raw material: a dedicated, larger cap, but
# still well below MAX_CAST_BYTES to contain the report's weight.
INLINE_CAST_MAX_BYTES = 256 * 1024
INLINE_TOTAL_BUDGET = 2 * 1024 * 1024
INLINE_CAST_BUDGET = 1 * 1024 * 1024
EXCERPT_HEAD_LINES = 10
EXCERPT_TAIL_LINES = 30
# An e2e scenario proves itself with a capture of what actually ran: pixels
# for browser scenarios, a full command transcript for browserless ones
# (real Docker, launcher). Both carry the observed output, not a claim.
E2E_CAPTURE_TYPES = frozenset({"screenshot", "command"})


def load_scenario_evidence(root: Path) -> ScenarioEvidence:
    suites: dict[str, list[Scenario]] = {"unit": [], "integration": [], "e2e": [], "symfony": []}
    files: list[str] = []
    if not root.exists():
        return {"suites": suites, "files": files, "totals": scenario_totals(suites)}
    for path in sorted(root.glob("*-scenarios.json")):
        # Localized fail-closed validation: a corrupted file, an unknown
        # schema, or a structurally wrong one is named here, rather than
        # exploding into an anonymous KeyError/TypeError when computing
        # totals or rendering the cockpit. Schema-v1 payloads without a
        # `schema` key remain accepted by the current reader.
        decoded = _read_json_or_fail(path, "unreadable scenarios JSON")
        payload = validated_scenario_file(
            decoded, source=str(path), expected_schema=SCENARIOS_SCHEMA
        )
        suite = str(payload.get("suite", path.stem.removesuffix("-scenarios")))
        suites.setdefault(suite, []).extend(payload.get("scenarios", []))
        files.append(str(path))
    return {"suites": suites, "files": files, "totals": scenario_totals(suites)}


def scenario_totals(suites: dict[str, list[Scenario]]) -> ScenarioTotals:
    scenarios = [scenario for items in suites.values() for scenario in items]
    e2e = suites.get("e2e", [])
    symfony = suites.get("symfony", [])
    screenshots = sum(
        1
        for scenario in scenarios
        for artifact in scenario.get("artifacts", [])
        if artifact.get("type") == "screenshot"
    )
    missing_e2e = [
        scenario["nodeid"]
        for scenario in e2e
        if scenario.get("status") != "skipped"
        and not any(
            artifact.get("type") in E2E_CAPTURE_TYPES for artifact in scenario.get("artifacts", [])
        )
    ]
    return {
        "scenarios": len(scenarios),
        "unit": len(suites.get("unit", [])),
        "integration": len(suites.get("integration", [])),
        "e2e": len(e2e),
        "symfony": len(symfony),
        "screenshots": screenshots,
        "missing_e2e_captures": missing_e2e,
    }


def proof_failures_from_scenarios(scenario_evidence: ScenarioEvidence) -> list[str]:
    failures = []
    for nodeid in scenario_evidence["totals"]["missing_e2e_captures"]:
        failures.append(f"missing e2e capture (screenshot or command transcript): {nodeid}")
    return failures


def enrich_scenario_evidence(
    scenario_evidence: ScenarioEvidence, feature_inventory: dict[str, Any]
) -> ScenarioEvidence:
    by_suite_and_nodeid: dict[tuple[str, str], Scenario] = {}
    for feature in feature_inventory.get("features", []):
        for scenario in feature.get("matched_scenarios", []):
            key = (scenario.get("suite", ""), scenario.get("nodeid", ""))
            by_suite_and_nodeid[key] = scenario

    suites: dict[str, list[Scenario]] = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        suites[suite] = [
            by_suite_and_nodeid.get((suite, scenario.get("nodeid", "")), scenario)
            for scenario in scenarios
        ]
    return {
        **scenario_evidence,
        "suites": suites,
        "totals": scenario_totals(suites),
    }


def _artifact_excerpt(text: str) -> str:
    lines = text.splitlines()
    limit = EXCERPT_HEAD_LINES + EXCERPT_TAIL_LINES
    if len(lines) <= limit:
        return text.rstrip("\n")
    omitted = len(lines) - limit
    return "\n".join(
        [
            *lines[:EXCERPT_HEAD_LINES],
            f"… ({omitted} lines truncated) …",
            *lines[-EXCERPT_TAIL_LINES:],
        ]
    )


def _inline_artifact(entry: dict, remaining: int) -> int:
    if entry.get("type") not in _INLINE_TYPES:
        return remaining
    raw_path = str(entry.get("path", ""))
    path = Path(raw_path)
    if not raw_path or path.is_symlink() or not path.is_file():
        entry["inline_skipped"] = "unreadable"
        return remaining
    size = path.stat().st_size
    unit_cap = INLINE_CAST_MAX_BYTES if entry.get("type") == "asciinema" else INLINE_MAX_BYTES
    if size > unit_cap or size > remaining:
        entry["inline_skipped"] = "size" if size > unit_cap else "budget"
        entry["truncated"] = True
        if not entry.get("excerpt"):
            entry["excerpt"] = _artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"))
        return remaining
    entry["inline_content"] = path.read_text(encoding="utf-8", errors="replace")
    entry["truncated"] = False
    return remaining - size


def inline_catalog_casts(catalog: list[dict], *, budget: int = INLINE_CAST_BUDGET) -> list[dict]:
    """Inline the catalog's .cast files: the cockpit player requires ``inline_content``.

    Without this inlining, a cast produced outside a pytest scenario would
    just be a table link — unplayable under the report's CSP (no fetch
    allowed).
    """

    remaining = budget
    for entry in catalog:
        if entry.get("type") == "asciinema" and entry.get("path"):
            remaining = _inline_artifact(entry, remaining)
    return catalog


def inline_scenario_artifacts(
    scenario_evidence: ScenarioEvidence, *, budget: int = INLINE_TOTAL_BUDGET
) -> ScenarioEvidence:
    """Inline the content of textual artifacts into the cockpit payload.

    Beyond the per-unit cap or the global budget, the artifact is
    represented by a head+tail excerpt and marked truncated: the rendering
    stays honest.
    """

    remaining = budget
    suites: dict[str, list[Scenario]] = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        rebuilt: list[Scenario] = []
        for scenario in scenarios:
            artifacts: list[dict[str, Any]] = []
            for artifact in scenario.get("artifacts", []):
                entry = dict(artifact)
                remaining = _inline_artifact(entry, remaining)
                artifacts.append(entry)
            rebuilt.append(cast(Scenario, {**scenario, "artifacts": artifacts}))
        suites[suite] = rebuilt
    return {**scenario_evidence, "suites": suites}


def _strip_inline_content(scenario_evidence: ScenarioEvidence) -> ScenarioEvidence:
    """Strip inlined content before writing back to disk (already present in files)."""

    suites: dict[str, list[Scenario]] = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        rebuilt: list[Scenario] = []
        for scenario in scenarios:
            artifacts = [
                {key: value for key, value in artifact.items() if key != "inline_content"}
                for artifact in scenario.get("artifacts", [])
            ]
            rebuilt.append(cast(Scenario, {**scenario, "artifacts": artifacts}))
        suites[suite] = rebuilt
    return {**scenario_evidence, "suites": suites}


def write_scenario_evidence(
    root: Path,
    scenario_evidence: ScenarioEvidence,
    *,
    redaction_context: RedactionContext | None = None,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    _secure_dir(root)
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        if not scenarios:
            continue
        path = root / f"{suite}-scenarios.json"
        payload = {
            "schema": SCENARIOS_SCHEMA,
            "suite": suite,
            "generated_at": _now(),
            "count": len(scenarios),
            "scenarios": sorted(scenarios, key=lambda item: item["nodeid"]),
        }
        cleaned = redact_tree(payload, context=context, path=f"$.scenarios.{suite}")
        _write_private_text(path, json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
