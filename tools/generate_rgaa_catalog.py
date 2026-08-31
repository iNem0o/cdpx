"""Build the pinned RGAA 4.1.2 catalog and exhaustive automation matrix.

The runtime never downloads normative material.  Maintainers vendor the three
official JSON files, record their exact provenance, then run this deterministic
generator.  Any cardinality or join drift fails closed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src/cdpx/rgaa/data/4.1.2"
DOC_MATRIX = ROOT / "docs/rgaa/03-rgaa-test-automation-matrix.csv"
SOURCE_COMMIT = "ca4019f95073b6cbd2482a16e9f12b52d8de678d"
SOURCE_REPOSITORY = "DISIC/accessibilite.numerique.gouv.fr"
EXPECTED = {"themes": 13, "criteria": 106, "tests": 258}


RULE_PROFILES: dict[str, dict[str, Any]] = {
    "2.1.1": {
        "class": "deterministic",
        "rules": ["frame-title"],
        "collectors": ["dom"],
        "auto_pass": False,
        "auto_fail": True,
        "auto_not_applicable": False,
        "confidence": "high",
    },
    "3.2.1": {
        "class": "deterministic_partial",
        "rules": ["text-contrast-solid"],
        "collectors": ["dom", "css"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "3.2.2": {
        "class": "deterministic_partial",
        "rules": ["text-contrast-solid"],
        "collectors": ["dom", "css"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "3.2.3": {
        "class": "deterministic_partial",
        "rules": ["text-contrast-solid"],
        "collectors": ["dom", "css"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "3.2.4": {
        "class": "deterministic_partial",
        "rules": ["text-contrast-solid"],
        "collectors": ["dom", "css"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "6.1.1": {
        "class": "assisted",
        "rules": ["link-accessible-name"],
        "collectors": ["dom", "accessibility"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "8.1.1": {
        "class": "deterministic",
        "rules": ["document-type"],
        "collectors": ["dom"],
        "auto_pass": True,
        "auto_fail": True,
        "auto_not_applicable": False,
        "confidence": "high",
    },
    "8.3.1": {
        "class": "deterministic",
        "rules": ["default-language"],
        "collectors": ["dom"],
        "auto_pass": True,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "high",
    },
    "8.4.1": {
        "class": "assisted",
        "rules": ["default-language-validity"],
        "collectors": ["dom"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "8.5.1": {
        "class": "deterministic",
        "rules": ["document-title-presence"],
        "collectors": ["dom"],
        "auto_pass": True,
        "auto_fail": True,
        "auto_not_applicable": False,
        "confidence": "high",
    },
    "8.6.1": {
        "class": "assisted",
        "rules": ["document-title-relevance"],
        "collectors": ["dom"],
        "auto_pass": False,
        "auto_fail": True,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "10.7.1": {
        "class": "interactive_assisted",
        "rules": ["focus-indicator"],
        "collectors": ["input", "dom", "css"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "10.12.1": {
        "class": "emulated_assisted",
        "rules": ["text-spacing"],
        "collectors": ["dom", "css", "runtime"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "11.1.1": {
        "class": "assisted",
        "rules": ["form-label"],
        "collectors": ["dom", "accessibility"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "11.9.1": {
        "class": "assisted",
        "rules": ["button-accessible-name"],
        "collectors": ["dom", "accessibility"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "12.8.1": {
        "class": "interactive_assisted",
        "rules": ["tab-order-evidence"],
        "collectors": ["input", "dom", "accessibility"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
    "13.1.1": {
        "class": "assisted",
        "rules": ["meta-refresh"],
        "collectors": ["dom"],
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "confidence": "medium",
    },
}


AXE_MAPPINGS: dict[str, list[dict[str, str]]] = {
    "image-alt": [{"test_id": "1.1.1", "strength": "advisory"}],
    "frame-title": [{"test_id": "2.1.1", "strength": "strong-observation"}],
    "color-contrast": [
        {"test_id": test_id, "strength": "advisory"}
        for test_id in ("3.2.1", "3.2.2", "3.2.3", "3.2.4")
    ],
    "link-name": [{"test_id": "6.1.1", "strength": "advisory"}],
    "html-has-lang": [{"test_id": "8.3.1", "strength": "strong-observation"}],
    "html-lang-valid": [{"test_id": "8.4.1", "strength": "advisory"}],
    "document-title": [{"test_id": "8.5.1", "strength": "strong-observation"}],
    "label": [{"test_id": "11.1.1", "strength": "advisory"}],
    "button-name": [{"test_id": "11.9.1", "strength": "advisory"}],
    "bypass": [{"test_id": "12.7.1", "strength": "advisory"}],
    "meta-refresh": [{"test_id": "13.1.1", "strength": "advisory"}],
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _join_text(values: list[Any]) -> str:
    return " | ".join(
        value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        for value in values
    )


def _default_profile(test_id: str, methodology: str) -> dict[str, Any]:
    lowered = methodology.lower()
    assistive = any(
        marker in lowered
        for marker in (
            "technologies d’assistance",
            "technologies d'assistance",
            "lecteur d’écran",
            "lecteur d'ecran",
            "pac ",
            "microsoft office",
            "epub",
            "fichier odt",
        )
    )
    interaction = any(
        marker in lowered
        for marker in ("touche tab", "tabulation", "clavier", "dispositif de pointage")
    )
    visual = any(
        marker in lowered
        for marker in ("contraste", "visuellement", "lisible", "couleur", "clignot")
    )
    external_document = any(
        marker in lowered for marker in ("pdf", "microsoft office", "epub", "odt")
    )
    automation_class = "manual_only" if assistive or external_document else "review"
    return {
        "official_test_id": test_id,
        "detectable": False,
        "auto_applicability": False,
        "auto_pass": False,
        "auto_fail": False,
        "auto_not_applicable": False,
        "human_judgment": True,
        "assistive_technology": assistive,
        "visual_review": visual,
        "interaction": interaction,
        "multi_state": interaction,
        "multi_page": "ensemble de pages" in lowered or "site web" in lowered,
        "external_document": external_document,
        "automation_class": automation_class,
        "native_rule_ids": [],
        "external_engine_mappings": [],
        "required_cdp_collectors": [],
        "required_evidence": ["expert_review"],
        "default_unresolved_verdict": "manual_only"
        if automation_class == "manual_only"
        else "needs_review",
        "confidence": "none",
        "limitations": "No automatic RGAA conclusion is safe for this test.",
        "rule_version": 1,
    }


def _profile(test_id: str, methodology: str) -> dict[str, Any]:
    profile = _default_profile(test_id, methodology)
    configured = RULE_PROFILES.get(test_id)
    if configured is None:
        return profile
    profile.update(
        {
            "detectable": True,
            "auto_applicability": True,
            "auto_pass": configured["auto_pass"],
            "auto_fail": configured["auto_fail"],
            "auto_not_applicable": configured["auto_not_applicable"],
            "automation_class": configured["class"],
            "native_rule_ids": configured["rules"],
            "required_cdp_collectors": configured["collectors"],
            "required_evidence": ["bounded_observation"],
            "default_unresolved_verdict": "needs_review",
            "confidence": configured["confidence"],
            "limitations": "Only the explicitly modeled structural/rendered subset is concluded.",
        }
    )
    return profile


def build() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    criteria_source = _read_json(DATA / "criteres.json")
    methodologies = _read_json(DATA / "methodologies.json")
    glossary = _read_json(DATA / "glossaire.json")
    topics: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    criteria_count = 0

    for topic in criteria_source["topics"]:
        topic_id = int(topic["number"])
        topic_entry = {"id": topic_id, "title": topic["topic"], "criteria": []}
        for wrapped in topic["criteria"]:
            source = wrapped["criterium"]
            criterion_id = f"{topic_id}.{source['number']}"
            criteria_count += 1
            criterion_test_ids: list[str] = []
            for test_number, statement in source["tests"].items():
                test_id = f"{criterion_id}.{test_number}"
                if test_id not in methodologies:
                    raise SystemExit(f"missing methodology: {test_id}")
                profile = _profile(test_id, methodologies[test_id])
                provider_mappings = [
                    {"provider": "axe-core", "rule_id": rule_id, **mapping}
                    for rule_id, mappings in AXE_MAPPINGS.items()
                    for mapping in mappings
                    if mapping["test_id"] == test_id
                ]
                profile["external_engine_mappings"] = provider_mappings
                test = {
                    "id": test_id,
                    "theme_id": topic_id,
                    "theme_title": topic["topic"],
                    "criterion_id": criterion_id,
                    "criterion_title": source["title"],
                    "statement": statement,
                    "methodology": methodologies[test_id],
                    "special_cases": source.get("particularCases", []),
                    "technical_notes": source.get("technicalNote", []),
                    "references": source.get("references", []),
                    "automation": profile,
                }
                tests.append(test)
                criterion_test_ids.append(test_id)
            topic_entry["criteria"].append(
                {
                    "id": criterion_id,
                    "title": source["title"],
                    "test_ids": criterion_test_ids,
                }
            )
        topics.append(topic_entry)

    ids = [test["id"] for test in tests]
    if len(topics) != EXPECTED["themes"]:
        raise SystemExit(f"RGAA theme cardinality drift: {len(topics)}")
    if criteria_count != EXPECTED["criteria"]:
        raise SystemExit(f"RGAA criterion cardinality drift: {criteria_count}")
    if len(tests) != EXPECTED["tests"]:
        raise SystemExit(f"RGAA test cardinality drift: {len(tests)}")
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate RGAA test id")
    if set(ids) != set(methodologies):
        missing = sorted(set(ids) - set(methodologies))
        orphaned = sorted(set(methodologies) - set(ids))
        raise SystemExit(f"methodology join drift: missing={missing}, orphaned={orphaned}")

    source_manifest: dict[str, Any] = {
        "schema": "cdpx.rgaa.source/v1",
        "rgaa_version": "4.1.2",
        "repository": SOURCE_REPOSITORY,
        "source_commit": SOURCE_COMMIT,
        "fetched_at": "2026-08-30T00:00:00Z",
        "generator_version": 1,
        "files": [
            {
                "path": f"RGAA/{name}",
                "sha256": _sha256(DATA / name),
                "license": "Licence Ouverte 2.0",
                "role": role,
            }
            for name, role in (
                ("criteres.json", "criteria"),
                ("methodologies.json", "methodologies"),
                ("glossaire.json", "glossary"),
            )
        ],
    }
    source_digest = hashlib.sha256(
        "".join(item["sha256"] for item in source_manifest["files"]).encode()
    ).hexdigest()
    catalog = {
        "schema": "cdpx.rgaa.catalog/v1",
        "catalog_id": "rgaa-4.1.2",
        "rgaa_version": "4.1.2",
        "source_commit": SOURCE_COMMIT,
        "source_digest": source_digest,
        "counts": EXPECTED,
        "wcag_version": str(criteria_source["wcag"]["version"]),
        "topics": topics,
        "tests": tests,
        "glossary_entries": len(glossary["glossary"]),
        "runtime_fetch": False,
    }
    matrix = [
        {
            "official_test_id": test["id"],
            "theme_id": test["theme_id"],
            "theme_title": test["theme_title"],
            "criterion_id": test["criterion_id"],
            "criterion_title": test["criterion_title"],
            "official_statement_ref": f"catalog.json#/tests/{index}/statement",
            "methodology_ref": f"methodologies.json#/{test['id']}",
            "special_cases": _join_text(test["special_cases"]),
            **test["automation"],
        }
        for index, test in enumerate(tests)
    ]
    return catalog, matrix, source_manifest


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _matrix_csv(matrix: list[dict[str, Any]]) -> str:
    columns = [
        "official_test_id",
        "theme_id",
        "theme_title",
        "criterion_id",
        "criterion_title",
        "official_statement_ref",
        "methodology_ref",
        "special_cases",
        "detectable",
        "auto_applicability",
        "auto_pass",
        "auto_fail",
        "auto_not_applicable",
        "human_judgment",
        "assistive_technology",
        "visual_review",
        "interaction",
        "multi_state",
        "multi_page",
        "external_document",
        "automation_class",
        "native_rule_ids",
        "external_engine_mappings",
        "required_cdp_collectors",
        "required_evidence",
        "default_unresolved_verdict",
        "confidence",
        "limitations",
        "rule_version",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for item in matrix:
        row = dict(item)
        for key, value in list(row.items()):
            if isinstance(value, list | dict):
                row[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        writer.writerow({column: row.get(column, "") for column in columns})
    return stream.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated files drift")
    args = parser.parse_args()
    catalog, matrix, manifest = build()
    outputs = {
        DATA / "catalog.json": _json_bytes(catalog),
        DATA / "automation-matrix.json": _json_bytes(matrix),
        DATA / "source-manifest.json": _json_bytes(manifest),
        DOC_MATRIX: _matrix_csv(matrix).encode(),
    }
    if args.check:
        drift = [
            str(path.relative_to(ROOT))
            for path, payload in outputs.items()
            if not path.is_file() or path.read_bytes() != payload
        ]
        if drift:
            raise SystemExit(f"generated RGAA artifact drift: {', '.join(drift)}")
        return
    for path, payload in outputs.items():
        path.write_bytes(payload)


if __name__ == "__main__":
    main()
