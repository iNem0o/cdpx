"""Évidence de scénarios pytest: chargement, totaux, inline des artefacts.

L'inline embarque le contenu textuel des artefacts dans le payload du cockpit
(la CSP du rapport interdit tout fetch). Aucun symbole de ce module ne lit
`cdpx.proof` à l'exécution: la façade ré-exporte ces fonctions et résout
elle-même sa constante patchable ``EVIDENCE_DIR`` (défaut de
``load_scenario_evidence``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from cdpx.proofing.execution import _read_json_or_fail
from cdpx.proofing.private_io import _now, _secure_dir, _write_private_text
from cdpx.proofing.scenario_models import (
    Scenario,
    ScenarioEvidence,
    ScenarioTotals,
    validated_scenario_file,
)
from cdpx.security.redaction import RedactionContext, redact_tree
from cdpx.testing.evidence import (
    SCENARIOS_SCHEMA,
    redaction_context_from_environment,
)

# L'inline ne concerne que le textuel: la CSP du rapport (connect-src 'none')
# interdit tout fetch, donc ce que les visualiseurs affichent doit voyager
# dans le JSON embarqué. Les binaires restent des liens locaux.
_INLINE_TYPES = frozenset(
    {"command", "log-excerpt", "logs", "json", "console", "network", "profiler", "asciinema"}
)
INLINE_MAX_BYTES = 16 * 1024
# Les .cast sont la matière première du player: cap dédié plus large, mais
# toujours très en deçà de MAX_CAST_BYTES pour contenir le poids du rapport.
INLINE_CAST_MAX_BYTES = 256 * 1024
INLINE_TOTAL_BUDGET = 2 * 1024 * 1024
INLINE_CAST_BUDGET = 1 * 1024 * 1024
EXCERPT_HEAD_LINES = 10
EXCERPT_TAIL_LINES = 30


def load_scenario_evidence(root: Path) -> ScenarioEvidence:
    suites: dict[str, list[Scenario]] = {"unit": [], "integration": [], "e2e": [], "symfony": []}
    files: list[str] = []
    if not root.exists():
        return {"suites": suites, "files": files, "totals": scenario_totals(suites)}
    for path in sorted(root.glob("*-scenarios.json")):
        # Validation localisée fail-closed: un fichier corrompu, d'un schéma
        # inconnu ou structurellement faux est nommé ici, plutôt que d'exploser
        # en KeyError/TypeError anonyme au calcul des totaux ou au rendu du
        # cockpit. Les payloads legacy v1 (sans clé `schema`) restent acceptés
        # tels quels: la tolérance des lecteurs évite tout migrateur.
        decoded = _read_json_or_fail(path, "JSON de scénarios illisible")
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
            artifact.get("type") == "screenshot" for artifact in scenario.get("artifacts", [])
        )
    ]
    return {
        "scenarios": len(scenarios),
        "unit": len(suites.get("unit", [])),
        "integration": len(suites.get("integration", [])),
        "e2e": len(e2e),
        "symfony": len(symfony),
        "screenshots": screenshots,
        "missing_e2e_screenshots": missing_e2e,
    }


def proof_failures_from_scenarios(scenario_evidence: ScenarioEvidence) -> list[str]:
    failures = []
    for nodeid in scenario_evidence["totals"]["missing_e2e_screenshots"]:
        failures.append(f"missing e2e screenshot: {nodeid}")
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
            f"… ({omitted} lignes tronquées) …",
            *lines[-EXCERPT_TAIL_LINES:],
        ]
    )


def _inline_artifact(entry: dict, remaining: int) -> int:
    if entry.get("type") not in _INLINE_TYPES:
        return remaining
    raw_path = str(entry.get("path", ""))
    path = Path(raw_path)
    if not raw_path or path.is_symlink() or not path.is_file():
        entry["inline_skipped"] = "illisible"
        return remaining
    size = path.stat().st_size
    unit_cap = INLINE_CAST_MAX_BYTES if entry.get("type") == "asciinema" else INLINE_MAX_BYTES
    if size > unit_cap or size > remaining:
        entry["inline_skipped"] = "taille" if size > unit_cap else "budget"
        entry["truncated"] = True
        if not entry.get("excerpt"):
            entry["excerpt"] = _artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"))
        return remaining
    entry["inline_content"] = path.read_text(encoding="utf-8", errors="replace")
    entry["truncated"] = False
    return remaining - size


def inline_catalog_casts(catalog: list[dict], *, budget: int = INLINE_CAST_BUDGET) -> list[dict]:
    """Inline les .cast du catalogue: le player du cockpit exige ``inline_content``.

    Sans cet inline, un cast produit hors scénario pytest ne serait qu'un lien
    de tableau — injouable sous la CSP du rapport (aucun fetch autorisé).
    """

    remaining = budget
    for entry in catalog:
        if entry.get("type") == "asciinema" and entry.get("path"):
            remaining = _inline_artifact(entry, remaining)
    return catalog


def inline_scenario_artifacts(
    scenario_evidence: ScenarioEvidence, *, budget: int = INLINE_TOTAL_BUDGET
) -> ScenarioEvidence:
    """Inline le contenu des artefacts textuels dans le payload du cockpit.

    Au-delà du cap unitaire ou du budget global, l'artefact est représenté
    par un extrait tête+queue et marqué truncated: le rendu reste honnête.
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
    """Retire les contenus inlinés avant réécriture disque (déjà présents en fichiers)."""

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
