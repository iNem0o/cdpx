"""Catalogue d'évidence, inventaire projet et matrice de validation.

``build_evidence_catalog`` reçoit les chemins de preuve via ``ProofPaths``:
la façade `cdpx.proof` les résout depuis ses globals (monkeypatchables) au
moment de l'appel. Aucun symbole de ce module ne lit `cdpx.proof` à
l'exécution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cdpx.proofing.scenario_inline import inline_catalog_casts

VALIDATION_DOC = Path("docs/VALIDATION.md")


@dataclass(frozen=True)
class ProofPaths:
    """Chemins de preuve résolus par la façade au moment de l'appel.

    Ils reflètent les constantes patchables de `cdpx.proof` (PROOF_DIR,
    SYMFONY_LOG, EVIDENCE_DIR, …): les implémentations extraites ne lisent
    jamais ces globals elles-mêmes.
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
    # Racine physique parcourue pour les preuves visuelles/casts; les chemins
    # canoniques publiés restent dérivés des constantes de la façade.
    scan_root = paths.proof_dir if proof_dir is None else proof_dir
    catalog = [
        {
            "type": "rapport-html",
            "name": "Rapport humain projet",
            "path": str(paths.report_html),
            "status": "generated",
            "roi": "Point d'entrée humain: verdict, périmètre, milestones et preuves repliables.",
        },
        {
            "type": "resume-json",
            "name": "Résumé machine",
            "path": str(paths.summary_json),
            "status": "generated",
            "roi": "Signal compact pour CI/handoff sans relire tous les logs.",
        },
        {
            "type": "junit",
            "name": "Tests unitaires JUnit",
            "path": unit.get("path", str(paths.proof_dir / "unit-junit.xml")),
            "status": "passed"
            if unit.get("failures", 0) + unit.get("errors", 0) == 0
            else "failed",
            "roi": f"{unit.get('tests', 0)} tests unitaires structurés.",
        },
        {
            "type": "junit",
            "name": "E2E Chrome JUnit",
            "path": e2e.get("path", str(paths.proof_dir / "e2e-junit.xml")),
            "status": "passed" if e2e.get("failures", 0) + e2e.get("errors", 0) == 0 else "failed",
            "roi": (
                f"{e2e.get('tests', 0)} scénarios navigateur Chrome, "
                f"{e2e.get('skipped', 0)} skip déclaré."
            ),
        },
        {
            "type": "junit",
            "name": "Symfony E2E JUnit",
            "path": symfony.get("path", str(paths.symfony_junit)),
            "status": _junit_status(symfony),
            "roi": (
                f"{symfony.get('tests', 0)} scénario Symfony réel, "
                f"{symfony.get('skipped', 0)} indisponibilité/skip déclaré."
            ),
        },
        {
            "type": "logs",
            "name": "Logs unitaires",
            "path": str(paths.unit_log),
            "status": "generated",
            "roi": "Transcript terminal reproductible.",
        },
        {
            "type": "logs",
            "name": "Logs E2E Chrome",
            "path": str(paths.e2e_log),
            "status": "generated",
            "roi": "Transcript navigateur réel; Chrome absent est bloquant.",
        },
        {
            "type": "logs",
            "name": "Logs Symfony E2E",
            "path": str(paths.symfony_log),
            "status": next(
                (
                    command.get("status", "generated")
                    for command in summary.get("commands", [])
                    if command.get("id") == "symfony-e2e"
                ),
                "generated",
            ),
            "roi": "Transcript Docker Compose, politique d'indisponibilité et teardown.",
        },
        {
            "type": "surface-publique",
            "name": "Aide CLI capturée",
            "path": str(paths.cli_help),
            "status": "generated",
            "roi": "Contrat public exposé par le binaire.",
        },
        {
            "type": "git",
            "name": "Snapshot Git",
            "path": str(paths.git_status),
            "status": "generated",
            "roi": "Provenance du run et état local au moment de la preuve.",
        },
        {
            "type": "git",
            "name": "Diff stat",
            "path": str(paths.git_diff_stat),
            "status": "generated",
            "roi": "Contexte local sans ouvrir le diff complet.",
        },
        {
            "type": "scenarios",
            "name": "Scénarios pytest documentés",
            "path": str(paths.evidence_dir),
            "status": "generated",
            "roi": "Association test par test entre statut, logs et artefacts.",
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
                    "roi": "Preuve visuelle ou replay terminal ajouté au rapport.",
                }
            )
    if not any(item["type"] == "screenshot" for item in catalog):
        catalog.append(
            {
                "type": "screenshot",
                "name": "Capture UI",
                "path": "",
                "status": "not-needed",
                "roi": "Non générée automatiquement; utile seulement pour prouver un delta visuel.",
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

    milestone_docs = sorted(str(path) for path in Path("docs/milestones").glob("*.md"))
    docs = [
        path
        for path in (
            "README.md",
            "HARNESS.md",
            "docs/CONTEXT.md",
            "docs/PRIMITIVES.md",
            "docs/ROADMAP.md",
            "docs/TODO.md",
            "docs/VALIDATION.md",
        )
        if Path(path).exists()
    ]

    # Source unique de version: cdpx.__version__ (pyproject la lit en dynamic).
    from cdpx import __version__ as version

    return {
        "name": "cdpx",
        "version": version,
        "mission": (
            "CLI de primitives Chrome DevTools Protocol pour agents de dev et humains "
            "qui pilotent des audits navigateur."
        ),
        "cli_command_count": len(help_commands),
        "cli_commands": [command["name"] for command in help_commands],
        "fixture_count": len(fixtures),
        "fixture_kinds": fixture_kinds,
        "fixtures": fixtures,
        "docs": docs,
        "milestone_docs": milestone_docs,
    }


def parse_validation_matrix() -> list[dict[str, str]]:
    if not VALIDATION_DOC.exists():
        return []
    rows = []
    for line in VALIDATION_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "Milestone" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append({"milestone": cells[0], "proof": cells[1]})
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
