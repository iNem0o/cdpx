"""Feature inventory for the proof cockpit.

The inventory is deterministic and intentionally small: humans maintain feature
Markdown files, while `make proof` derives entrypoint, code, test and evidence
coverage from those files and the collected pytest evidence.
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cdpx.proofing.markdown import render_markdown
from cdpx.proofing.scenario_models import ScenarioEvidence

FEATURES_DIR = Path("docs/features")
IGNORED_PATH_PARTS = {
    ".git",
    ".idea",
    ".proof",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
}
FEATURE_STATUSES = {"planned", "active", "validated", "deprecated"}

# Ratchet : nombre maximal de tests rattachés seulement par le test_globs large
# d'une feature, sans scénario documenté. Il ne peut que BAISSER — un
# dépassement est une violation bloquante, pour que la dette narrative ne
# recroisse jamais.
UNDOCUMENTED_SCENARIO_WARNING_BUDGET = 0

# Sections obligatoires du corps Markdown (affichées telles quelles dans
# le rapport de preuve). "Usage" porte la doc utilisateur par entrypoint.
REQUIRED_SECTIONS = (
    "Intent",
    "Usage",
    "User journeys",
    "Validation",
    "Proofs",
    "Known limitations",
)


@dataclass
class ScenarioSpec:
    id: str
    journey: str
    title: str
    ui_text: str
    report_text: str
    given: str
    when: str
    then: str
    tests: list[str]
    expected_proofs: list[str]

    def as_dict(self, feature_id: str) -> dict[str, Any]:
        scenario_id = f"{feature_id}.{self.id}"
        return {
            "id": self.id,
            "scenario_id": scenario_id,
            "journey": self.journey,
            "title": self.title,
            "ui_text": self.ui_text,
            "report_text": self.report_text,
            "given": self.given,
            "when": self.when,
            "then": self.then,
            "tests": self.tests,
            "expected_proofs": self.expected_proofs,
            "matched_tests": [],
            "matched_scenarios": [],
            "proofs": [],
            "gaps": [],
        }


@dataclass
class FeatureSpec:
    id: str
    title: str
    status: str
    summary: str
    entrypoints: list[str]
    path_globs: list[str]
    test_globs: list[str]
    docs: list[str]
    journeys: list[dict[str, str]]
    scenarios: list[ScenarioSpec]
    expected_proofs: list[str]
    source: str
    sections: list[str] = field(default_factory=list)
    body: str = ""

    def as_dict(self) -> dict[str, Any]:
        scenario_dicts = [scenario.as_dict(self.id) for scenario in self.scenarios]
        journey_dicts = []
        for journey in self.journeys:
            journey_scenarios = [
                deepcopy(scenario)
                for scenario in scenario_dicts
                if scenario["journey"] == journey.get("id")
            ]
            journey_dicts.append(
                {
                    **journey,
                    "scenarios": journey_scenarios,
                    "matched_tests": [],
                    "matched_scenarios": [],
                    "proofs": [],
                    "gaps": [],
                }
            )
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "entrypoints": self.entrypoints,
            "path_globs": self.path_globs,
            "test_globs": self.test_globs,
            "docs": self.docs,
            "journeys": journey_dicts,
            "scenarios": scenario_dicts,
            "expected_proofs": self.expected_proofs,
            "source": self.source,
            "sections": self.sections,
            "doc_html": render_markdown(self.body),
        }


def build_feature_inventory(
    help_commands: list[dict[str, str]],
    scenario_evidence: ScenarioEvidence,
    git_context: dict,
) -> dict[str, Any]:
    specs, doc_errors = load_feature_specs()
    entrypoints = discover_entrypoints(help_commands)
    paths = discover_project_paths()
    scenarios = [
        scenario for suite in scenario_evidence.get("suites", {}).values() for scenario in suite
    ]

    features: dict[str, dict[str, Any]] = {
        spec.id: {
            **spec.as_dict(),
            "matched_entrypoints": [],
            "matched_paths": [],
            "matched_tests": [],
            "matched_scenarios": [],
            "proofs": [],
            "changed_paths": [],
            "gaps": [],
        }
        for spec in specs
    }
    feature_by_entrypoint: dict[str, str] = {}
    violations = list(doc_errors)
    warnings = []
    scenario_specs: dict[str, tuple[FeatureSpec, ScenarioSpec]] = {}
    for spec in specs:
        for scenario_spec in spec.scenarios:
            scenario_id = f"{spec.id}.{scenario_spec.id}"
            if scenario_id in scenario_specs:
                violations.append(f"scenario id duplicated: {scenario_id}")
            scenario_specs[scenario_id] = (spec, scenario_spec)

    for entrypoint in entrypoints:
        owners = [spec for spec in specs if entrypoint["id"] in spec.entrypoints]
        if len(owners) == 1:
            owner = owners[0]
            feature_by_entrypoint[entrypoint["id"]] = owner.id
            features[owner.id]["matched_entrypoints"].append(entrypoint)
        elif len(owners) > 1:
            violations.append(f"entrypoint mapped multiple times: {entrypoint['id']}")
        else:
            violations.append(f"entrypoint unmapped: {entrypoint['id']}")

    for path in paths:
        owners = [spec for spec in specs if _matches_any(path, spec.path_globs)]
        if owners:
            for owner in owners:
                features[owner.id]["matched_paths"].append(path)
        elif _is_source_like(path):
            warnings.append(f"source path unmapped: {path}")

    for scenario in scenarios:
        nodeid = scenario.get("nodeid", "")
        match = _resolve_scenario_owner(scenario, specs, scenario_specs)
        if match["error"]:
            violations.append(match["error"])
            continue
        if match["warning"]:
            warnings.append(match["warning"])
        owner = match["feature"]
        if owner is None:
            violations.append(f"scenario unmapped: {nodeid}")
            continue
        scenario_spec = match["scenario"]
        enriched = _enrich_scenario(scenario, owner, scenario_spec)
        feature = features[owner.id]
        feature["matched_scenarios"].append(enriched)
        _append_unique(feature["matched_tests"], nodeid)
        for artifact in enriched.get("artifacts", []):
            proof = {
                "scenario": nodeid,
                "scenario_id": enriched.get("scenario_id", ""),
                "type": artifact.get("type", "file"),
                "label": artifact.get("label", ""),
                "path": artifact.get("path", ""),
            }
            feature["proofs"].append(proof)
        if scenario_spec is not None:
            _attach_to_journey_tree(feature, enriched, scenario_spec)

    undocumented_count = sum(
        1
        for warning in warnings
        if warning.startswith("scenario mapped only by feature test_globs")
    )
    if undocumented_count > UNDOCUMENTED_SCENARIO_WARNING_BUDGET:
        violations.append(
            "undocumented scenario warnings over budget: "
            f"{undocumented_count} > {UNDOCUMENTED_SCENARIO_WARNING_BUDGET} "
            "(documenter les scénarios ou élargir les specs, pas le budget)"
        )

    changed_paths = [item["path"] for item in git_context.get("changed_files", [])]
    for feature in features.values():
        feature["matched_entrypoints"].sort(key=lambda item: item["id"])
        feature["matched_paths"] = sorted(set(feature["matched_paths"]))
        feature["matched_tests"] = sorted(set(feature["matched_tests"]))
        feature["matched_scenarios"].sort(key=lambda item: item.get("nodeid", ""))
        _finalize_feature_journeys(feature)
        feature["changed_paths"] = [
            path for path in changed_paths if path in set(feature["matched_paths"])
        ]
        _add_feature_gaps(feature)

    return {
        "features": sorted(features.values(), key=lambda item: item["id"]),
        "entrypoints": entrypoints,
        "feature_by_entrypoint": feature_by_entrypoint,
        "totals": {
            "features": len(features),
            "entrypoints": len(entrypoints),
            "mapped_entrypoints": len(feature_by_entrypoint),
            "scenarios": len(scenarios),
            "documented_scenarios": sum(len(spec.scenarios) for spec in specs),
            "warnings": len(warnings),
            "violations": len(violations),
        },
        "violations": violations,
        "warnings": warnings,
        "docs_dir": str(FEATURES_DIR),
    }


def load_feature_specs(root: Path = FEATURES_DIR) -> tuple[list[FeatureSpec], list[str]]:
    specs = []
    errors = []
    if not root.exists():
        return [], [f"feature docs directory missing: {root}"]
    for path in sorted(root.glob("*.md")):
        try:
            spec = parse_feature_doc(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        specs.append(spec)
    ids = [spec.id for spec in specs]
    for feature_id in sorted({item for item in ids if ids.count(item) > 1}):
        errors.append(f"feature id duplicated: {feature_id}")
    if not specs:
        errors.append(f"no feature docs found in {root}")
    return specs, errors


def parse_feature_doc(path: Path) -> FeatureSpec:
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"\A\+\+\+\n(.*?)\n\+\+\+\n(.*)\Z", raw, re.S)
    if not match:
        raise ValueError(f"{path}: missing TOML front matter delimited by +++")
    metadata = tomllib.loads(match.group(1))
    body = match.group(2)
    required = (
        "id",
        "title",
        "status",
        "summary",
        "entrypoints",
        "path_globs",
        "test_globs",
        "docs",
        "journeys",
        "scenarios",
        "expected_proofs",
    )
    missing = [name for name in required if name not in metadata]
    if missing:
        raise ValueError(f"{path}: missing required keys: {', '.join(missing)}")
    feature_id = str(metadata["id"])
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", feature_id):
        raise ValueError(f"{path}: invalid id: {feature_id}")
    status = str(metadata["status"])
    if status not in FEATURE_STATUSES:
        raise ValueError(f"{path}: invalid status: {status}")
    for key in ("entrypoints", "path_globs", "test_globs", "docs", "expected_proofs"):
        if not isinstance(metadata[key], list) or not all(
            isinstance(item, str) for item in metadata[key]
        ):
            raise ValueError(f"{path}: {key} must be a list of strings")
    journeys = metadata["journeys"]
    if not isinstance(journeys, list) or not all(isinstance(item, dict) for item in journeys):
        raise ValueError(f"{path}: journeys must be a list of tables")
    journey_ids = {str(item.get("id", "")) for item in journeys}
    scenarios = parse_scenario_specs(path, metadata["scenarios"], journey_ids)
    sections = re.findall(r"^##\s+(.+)$", body, re.M)
    for heading in REQUIRED_SECTIONS:
        if heading not in sections:
            raise ValueError(f"{path}: missing section ## {heading}")
    _require_usage_headings(path, body, metadata["entrypoints"])
    return FeatureSpec(
        id=feature_id,
        title=str(metadata["title"]),
        status=status,
        summary=str(metadata["summary"]),
        entrypoints=list(metadata["entrypoints"]),
        path_globs=list(metadata["path_globs"]),
        test_globs=list(metadata["test_globs"]),
        docs=list(metadata["docs"]),
        journeys=[{str(k): str(v) for k, v in item.items()} for item in journeys],
        scenarios=scenarios,
        expected_proofs=list(metadata["expected_proofs"]),
        source=str(path),
        sections=sections,
        body=body,
    )


def _require_usage_headings(path: Path, body: str, entrypoints: list[str]) -> None:
    """Garde-fou doc utilisateur: chaque entrypoint déclaré a son `### <id>`
    dans la section Usage. Une commande sans doc casse `make proof`."""
    usage = re.search(r"^##\s+Usage\s*$(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    usage_body = usage.group(1) if usage else ""
    headings = {
        heading.strip().strip("`").strip()
        for heading in re.findall(r"^###\s+(.+)$", usage_body, re.M)
    }
    missing = [entry for entry in entrypoints if entry not in headings]
    if missing:
        raise ValueError(
            f"{path}: entrypoints sans doc utilisateur (### manquant dans ## Usage): "
            + ", ".join(missing)
        )


def parse_scenario_specs(
    path: Path,
    raw_scenarios: Any,
    journey_ids: set[str],
) -> list[ScenarioSpec]:
    if not isinstance(raw_scenarios, list) or not all(
        isinstance(item, dict) for item in raw_scenarios
    ):
        raise ValueError(f"{path}: scenarios must be a list of tables")
    required = (
        "id",
        "journey",
        "title",
        "ui_text",
        "report_text",
        "given",
        "when",
        "then",
        "tests",
        "expected_proofs",
    )
    scenarios = []
    ids = []
    for item in raw_scenarios:
        missing = [name for name in required if name not in item]
        if missing:
            raise ValueError(f"{path}: scenario missing required keys: {', '.join(missing)}")
        scenario_id = str(item["id"])
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", scenario_id):
            raise ValueError(f"{path}: invalid scenario id: {scenario_id}")
        journey = str(item["journey"])
        if journey not in journey_ids:
            raise ValueError(
                f"{path}: scenario {scenario_id} references unknown journey: {journey}"
            )
        for key in ("tests", "expected_proofs"):
            if not isinstance(item[key], list) or not all(
                isinstance(value, str) for value in item[key]
            ):
                raise ValueError(f"{path}: scenario {scenario_id} {key} must be a list of strings")
        ids.append(scenario_id)
        scenarios.append(
            ScenarioSpec(
                id=scenario_id,
                journey=journey,
                title=str(item["title"]),
                ui_text=str(item["ui_text"]),
                report_text=str(item["report_text"]),
                given=str(item["given"]),
                when=str(item["when"]),
                then=str(item["then"]),
                tests=list(item["tests"]),
                expected_proofs=list(item["expected_proofs"]),
            )
        )
    for scenario_id in sorted({item for item in ids if ids.count(item) > 1}):
        raise ValueError(f"{path}: scenario id duplicated: {scenario_id}")
    return scenarios


def discover_entrypoints(help_commands: list[dict[str, str]]) -> list[dict[str, str]]:
    entrypoints = [
        {
            "id": f"cdpx {command['name']}",
            "type": "cli",
            "source": "src/cdpx/cli.py",
            "label": command.get("help", ""),
        }
        for command in help_commands
    ]
    entrypoints.extend(_make_entrypoints())
    entrypoints.append(
        {
            "id": "python -m cdpx.proof",
            "type": "python-module",
            "source": "src/cdpx/proof.py",
            "label": "Generate validation proof cockpit",
        }
    )
    return sorted(entrypoints, key=lambda item: (item["type"], item["id"]))


def _make_entrypoints() -> list[dict[str, str]]:
    path = Path("Makefile")
    if not path.exists():
        return []
    entrypoints = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-zA-Z0-9_-]+):.*?##\s*(.+)$", line)
        if match:
            entrypoints.append(
                {
                    "id": f"make {match.group(1)}",
                    "type": "make",
                    "source": "Makefile",
                    "label": match.group(2).strip(),
                }
            )
    return entrypoints


def discover_project_paths(root: Path = Path(".")) -> list[str]:
    paths = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in IGNORED_PATH_PARTS for part in rel.split("/")):
            continue
        if any(part.endswith(".egg-info") for part in rel.split("/")):
            continue
        paths.append(rel)
    return sorted(paths)


def feature_failures(feature_inventory: dict[str, Any]) -> list[str]:
    return [f"feature inventory: {item}" for item in feature_inventory.get("violations", [])]


def _matches_any(value: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(value, pattern) for pattern in patterns)


def _append_unique(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _resolve_scenario_owner(
    scenario: Mapping[str, Any],
    specs: list[FeatureSpec],
    scenario_specs: dict[str, tuple[FeatureSpec, ScenarioSpec]],
) -> dict[str, Any]:
    nodeid = scenario.get("nodeid", "")
    explicit_scenario_id = scenario.get("scenario_id", "")
    if explicit_scenario_id:
        match = scenario_specs.get(explicit_scenario_id)
        if match is None:
            return {
                "feature": None,
                "scenario": None,
                "warning": "",
                "error": (
                    f"scenario references unknown scenario_id: {nodeid} -> {explicit_scenario_id}"
                ),
            }
        return {"feature": match[0], "scenario": match[1], "warning": "", "error": ""}

    explicit_feature = scenario.get("feature", "")
    if explicit_feature:
        owner = next((spec for spec in specs if spec.id == explicit_feature), None)
        if owner is None:
            return {
                "feature": None,
                "scenario": None,
                "warning": "",
                "error": f"scenario references unknown feature: {nodeid} -> {explicit_feature}",
            }
        scenario_spec = _match_scenario_spec(nodeid, owner.scenarios, scenario.get("journey", ""))
        warning = "" if scenario_spec else f"scenario has no documented spec: {nodeid}"
        return {"feature": owner, "scenario": scenario_spec, "warning": warning, "error": ""}

    scenario_matches = [
        (spec, scenario_spec)
        for spec in specs
        for scenario_spec in spec.scenarios
        if _matches_any(nodeid, scenario_spec.tests)
    ]
    if len(scenario_matches) == 1:
        return {
            "feature": scenario_matches[0][0],
            "scenario": scenario_matches[0][1],
            "warning": "",
            "error": "",
        }
    if len(scenario_matches) > 1:
        owners = ", ".join(
            f"{spec.id}.{scenario_spec.id}" for spec, scenario_spec in scenario_matches
        )
        return {
            "feature": None,
            "scenario": None,
            "warning": "",
            "error": f"scenario mapped multiple documented specs: {nodeid} -> {owners}",
        }

    glob_owners = [spec for spec in specs if _matches_any(nodeid, spec.test_globs)]
    if len(glob_owners) == 1:
        return {
            "feature": glob_owners[0],
            "scenario": None,
            "warning": (
                f"scenario mapped only by feature test_globs without documented scenario: {nodeid}"
            ),
            "error": "",
        }
    if len(glob_owners) > 1:
        return {
            "feature": None,
            "scenario": None,
            "warning": "",
            "error": f"scenario mapped multiple features: {nodeid}",
        }
    return {"feature": None, "scenario": None, "warning": "", "error": ""}


def _match_scenario_spec(
    nodeid: str,
    scenario_specs: list[ScenarioSpec],
    journey: str = "",
) -> ScenarioSpec | None:
    candidates = [
        scenario_spec
        for scenario_spec in scenario_specs
        if (not journey or scenario_spec.journey == journey)
        and _matches_any(nodeid, scenario_spec.tests)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _enrich_scenario(
    scenario: Mapping[str, Any],
    feature: FeatureSpec,
    scenario_spec: ScenarioSpec | None,
) -> dict[str, Any]:
    enriched = dict(scenario)
    enriched["feature"] = feature.id
    if scenario_spec is None:
        enriched.setdefault("journey", scenario.get("journey", ""))
        enriched.setdefault("scenario_id", "")
        return enriched
    enriched.update(
        {
            "journey": scenario_spec.journey,
            "scenario": scenario_spec.id,
            "scenario_id": f"{feature.id}.{scenario_spec.id}",
            "title": scenario_spec.title,
            "ui_text": scenario_spec.ui_text,
            "report_text": scenario_spec.report_text,
            "given": scenario_spec.given,
            "when": scenario_spec.when,
            "then": scenario_spec.then,
            "expected_proofs": scenario_spec.expected_proofs,
        }
    )
    return enriched


def _attach_to_journey_tree(
    feature: dict[str, Any],
    scenario: dict[str, Any],
    scenario_spec: ScenarioSpec,
) -> None:
    nodeid = scenario.get("nodeid", "")
    proof_items = []
    for artifact in scenario.get("artifacts", []):
        proof_items.append(
            {
                "scenario": nodeid,
                "scenario_id": scenario.get("scenario_id", ""),
                "type": artifact.get("type", "file"),
                "label": artifact.get("label", ""),
                "path": artifact.get("path", ""),
            }
        )

    for journey in feature.get("journeys", []):
        if journey.get("id") != scenario_spec.journey:
            continue
        _append_unique(journey["matched_tests"], nodeid)
        journey["matched_scenarios"].append(scenario)
        journey["proofs"].extend(proof_items)
        for scenario_node in journey.get("scenarios", []):
            if scenario_node.get("id") != scenario_spec.id:
                continue
            _append_unique(scenario_node["matched_tests"], nodeid)
            scenario_node["matched_scenarios"].append(scenario)
            scenario_node["proofs"].extend(proof_items)
            break
        break
    for scenario_node in feature.get("scenarios", []):
        if scenario_node.get("id") != scenario_spec.id:
            continue
        _append_unique(scenario_node["matched_tests"], nodeid)
        scenario_node["matched_scenarios"].append(scenario)
        scenario_node["proofs"].extend(proof_items)
        break


def _finalize_feature_journeys(feature: dict[str, Any]) -> None:
    for journey in feature.get("journeys", []):
        journey["matched_tests"] = sorted(set(journey.get("matched_tests", [])))
        journey["matched_scenarios"].sort(key=lambda item: item.get("nodeid", ""))
        _add_expected_proof_gaps(journey)
        for scenario in journey.get("scenarios", []):
            scenario["matched_tests"] = sorted(set(scenario.get("matched_tests", [])))
            scenario["matched_scenarios"].sort(key=lambda item: item.get("nodeid", ""))
            _add_expected_proof_gaps(scenario)
    for scenario in feature.get("scenarios", []):
        scenario["matched_tests"] = sorted(set(scenario.get("matched_tests", [])))
        scenario["matched_scenarios"].sort(key=lambda item: item.get("nodeid", ""))
        _add_expected_proof_gaps(scenario)


def _is_source_like(path: str) -> bool:
    return path.startswith(("src/", "tests/", "docs/")) or path in {
        "Makefile",
        "README.md",
        "HARNESS.md",
        "CLAUDE.md",
        "pyproject.toml",
    }


def _add_feature_gaps(feature: dict[str, Any]) -> None:
    if not feature["matched_entrypoints"]:
        feature["gaps"].append("no matched entrypoint")
    if not feature["matched_paths"]:
        feature["gaps"].append("no matched code/doc path")
    if not feature["matched_tests"]:
        feature["gaps"].append("no matched test")
    if not any(scenario.get("matched_tests") for scenario in feature.get("scenarios", [])):
        feature["gaps"].append("no documented scenario matched test evidence")
    _add_expected_proof_gaps(feature)


def _add_expected_proof_gaps(node: dict[str, Any]) -> None:
    for expected in node.get("expected_proofs", []):
        if expected == "screenshot" and not any(
            proof.get("type") == "screenshot" for proof in node.get("proofs", [])
        ):
            node["gaps"].append("expected screenshot proof missing")
        elif expected == "junit" and not node.get("matched_tests"):
            node["gaps"].append("expected junit proof missing")
