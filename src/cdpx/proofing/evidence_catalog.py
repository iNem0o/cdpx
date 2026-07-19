"""Evidence catalog, project inventory, and validation matrix.

``build_evidence_catalog`` receives proof paths via ``ProofPaths``: the
`cdpx.proof` facade resolves them from its globals (monkeypatchable) at call
time. No symbol in this module reads `cdpx.proof` at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cdpx.proofing.scenario_inline import inline_catalog_casts

VALIDATION_DOC = Path("docs/VALIDATION.md")


@dataclass(frozen=True)
class ProofPaths:
    """Proof paths resolved by the facade at call time.

    They mirror the patchable constants of `cdpx.proof` (PROOF_DIR,
    SYMFONY_LOG, EVIDENCE_DIR, …): the extracted implementations never read
    these globals themselves.
    """

    proof_dir: Path
    report_html: Path
    summary_json: Path
    unit_log: Path
    e2e_log: Path
    symfony_log: Path
    cli_help: Path
    git_status: Path
    git_diff_stat: Path
    evidence_dir: Path
    symfony_junit: Path


def _junit_status(suite: dict) -> str:
    if not suite.get("exists", True):
        return "unavailable"
    return "passed" if suite.get("failures", 0) + suite.get("errors", 0) == 0 else "failed"


def build_evidence_catalog(
    summary: dict,
    unit: dict,
    e2e: dict,
    symfony: dict,
    *,
    paths: ProofPaths,
    proof_dir: Path | None = None,
) -> list[dict]:
    # Physical root walked for visual proofs/casts; the published canonical
    # paths remain derived from the facade's constants.
    scan_root = paths.proof_dir if proof_dir is None else proof_dir
    catalog = [
        {
            "type": "html-report",
            "name": "Project human report",
            "path": str(paths.report_html),
            "status": "generated",
            "roi": "Human entry point: verdict, scope, capabilities and collapsible proofs.",
        },
        {
            "type": "json-summary",
            "name": "Machine summary",
            "path": str(paths.summary_json),
            "status": "generated",
            "roi": "Compact signal for CI/handoff without re-reading all logs.",
        },
        {
            "type": "junit",
            "name": "Unit tests JUnit",
            "path": unit.get("path", str(paths.proof_dir / "unit-junit.xml")),
            "status": "passed"
            if unit.get("failures", 0) + unit.get("errors", 0) == 0
            else "failed",
            "roi": f"{unit.get('tests', 0)} structured unit tests.",
        },
        {
            "type": "junit",
            "name": "E2E Chrome JUnit",
            "path": e2e.get("path", str(paths.proof_dir / "e2e-junit.xml")),
            "status": "passed" if e2e.get("failures", 0) + e2e.get("errors", 0) == 0 else "failed",
            "roi": (
                f"{e2e.get('tests', 0)} Chrome browser scenarios, "
                f"{e2e.get('skipped', 0)} declared skip."
            ),
        },
        {
            "type": "junit",
            "name": "Symfony E2E JUnit",
            "path": symfony.get("path", str(paths.symfony_junit)),
            "status": _junit_status(symfony),
            "roi": (
                f"{symfony.get('tests', 0)} real Symfony scenario, "
                f"{symfony.get('skipped', 0)} declared unavailability/skip."
            ),
        },
        {
            "type": "logs",
            "name": "Unit logs",
            "path": str(paths.unit_log),
            "status": "generated",
            "roi": "Reproducible terminal transcript.",
        },
        {
            "type": "logs",
            "name": "E2E Chrome logs",
            "path": str(paths.e2e_log),
            "status": "generated",
            "roi": "Real browser transcript; missing Chrome is blocking.",
        },
        {
            "type": "logs",
            "name": "Symfony E2E logs",
            "path": str(paths.symfony_log),
            "status": next(
                (
                    command.get("status", "generated")
                    for command in summary.get("commands", [])
                    if command.get("id") == "symfony-e2e"
                ),
                "generated",
            ),
            "roi": "Docker Compose transcript, unavailability policy and teardown.",
        },
        {
            "type": "public-surface",
            "name": "Captured CLI help",
            "path": str(paths.cli_help),
            "status": "generated",
            "roi": "Public contract exposed by the binary.",
        },
        {
            "type": "git",
            "name": "Git snapshot",
            "path": str(paths.git_status),
            "status": "generated",
            "roi": "Run provenance and local state at proof time.",
        },
        {
            "type": "git",
            "name": "Diff stat",
            "path": str(paths.git_diff_stat),
            "status": "generated",
            "roi": "Local context without opening the full diff.",
        },
        {
            "type": "scenarios",
            "name": "Documented pytest scenarios",
            "path": str(paths.evidence_dir),
            "status": "generated",
            "roi": "Test-by-test association between status, logs and artifacts.",
        },
    ]
    for pattern, evidence_type in (
        ("*.png", "screenshot"),
        ("*.webm", "video"),
        ("*.mp4", "video"),
        ("*.cast", "asciinema"),
    ):
        for path in sorted(scan_root.rglob(pattern)):
            catalog.append(
                {
                    "type": evidence_type,
                    "name": path.name,
                    "path": str(path),
                    "status": "generated",
                    "roi": "Visual proof or terminal replay added to the report.",
                }
            )
    if not any(item["type"] == "screenshot" for item in catalog):
        catalog.append(
            {
                "type": "screenshot",
                "name": "UI capture",
                "path": "",
                "status": "not-needed",
                "roi": "Not generated automatically; useful only to prove a visual delta.",
            }
        )
    inline_catalog_casts(catalog)
    return catalog


def collect_project_inventory(help_commands: list[dict[str, str]]) -> dict:
    fixtures = sorted(str(path) for path in Path("tests/fixtures").glob("*") if path.is_file())
    fixture_kinds: dict[str, int] = {}
    for path in fixtures:
        suffix = Path(path).suffix.lstrip(".") or "none"
        fixture_kinds[suffix] = fixture_kinds.get(suffix, 0) + 1

    docs = [
        path
        for path in (
            "README.md",
            "HARNESS.md",
            "docs/CONTEXT.md",
            "docs/GITHUB.md",
            "docs/PRIMITIVES.md",
            "docs/RELEASING.md",
            "docs/SESSION-LIFECYCLE.md",
            "docs/VALIDATION.md",
        )
        if Path(path).exists()
    ]

    # Single version source: pyproject reads cdpx.__version__ dynamically.
    from cdpx import __version__ as version

    return {
        "name": "cdpx",
        "version": version,
        "mission": (
            "Chrome DevTools Protocol primitives CLI for dev agents and humans "
            "driving browser audits."
        ),
        "cli_command_count": len(help_commands),
        "cli_commands": [command["name"] for command in help_commands],
        "fixture_count": len(fixtures),
        "fixture_kinds": fixture_kinds,
        "fixtures": fixtures,
        "docs": docs,
    }


def parse_validation_matrix() -> list[dict[str, str]]:
    if not VALIDATION_DOC.exists():
        return []
    rows = []
    in_matrix = False
    for line in VALIDATION_DOC.read_text(encoding="utf-8").splitlines():
        if line == "## Capability matrix":
            in_matrix = True
            continue
        if in_matrix and line.startswith("## "):
            break
        if not in_matrix:
            continue
        if not line.startswith("|") or "---" in line or "Capability" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append({"capability": cells[0], "proof": cells[1]})
    return rows


def group_cases_by_module(unit: dict, e2e: dict, symfony: dict | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    for suite_name, suite in (("unit", unit), ("e2e", e2e), ("symfony", symfony or {"cases": []})):
        for case in suite["cases"]:
            module = case["classname"].split(".")[-1] or suite_name
            group = groups.setdefault(
                module,
                {"module": module, "suite": suite_name, "tests": 0, "failed": 0, "skipped": 0},
            )
            group["tests"] += 1
            if case["status"] in {"failed", "error"}:
                group["failed"] += 1
            elif case["status"] == "skipped":
                group["skipped"] += 1
    return sorted(groups.values(), key=lambda item: (item["suite"], item["module"]))
