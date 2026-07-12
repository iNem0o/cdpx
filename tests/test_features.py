import pytest

from cdpx.proofing.features import (
    build_feature_inventory,
    feature_failures,
    load_feature_specs,
    parse_feature_doc,
)

DEMO_DOC = """+++
id = "demo-feature"
title = "Demo feature"
status = "active"
summary = "Demo"
entrypoints = ["cdpx demo"]
path_globs = ["src/demo.py"]
test_globs = ["tests/test_demo.py::*"]
docs = ["docs/DEMO.md"]
expected_proofs = ["junit"]

[[journeys]]
id = "demo"
title = "Run demo"
entrypoint = "cdpx demo"

[[scenarios]]
id = "demo-happy-path"
journey = "demo"
title = "Run demo successfully"
ui_text = "Demo can be run."
report_text = "This scenario explains demo behavior for humans."
given = "Demo exists."
when = "The user runs demo."
then = "The result is visible."
tests = ["tests/test_demo.py::test_demo"]
expected_proofs = ["junit"]
+++

## Intention
Demo.

## Usage

### `cdpx demo`

Lance la démo. Sortie: `{"demo": true}`.

## Parcours utilisateur
Demo.

## Validation
Demo.

## Preuves
Demo.

## Limites connues
Demo.
"""


def test_parse_feature_doc_requires_structured_markdown(tmp_path):
    path = tmp_path / "demo.md"
    path.write_text(DEMO_DOC, encoding="utf-8")

    spec = parse_feature_doc(path)

    assert spec.id == "demo-feature"
    assert spec.entrypoints == ["cdpx demo"]
    assert spec.journeys[0]["id"] == "demo"
    assert spec.scenarios[0].id == "demo-happy-path"
    assert "Usage" in spec.sections
    assert "<h3><code>cdpx demo</code></h3>" in spec.as_dict()["doc_html"]


def test_parse_feature_doc_requires_usage_section(tmp_path):
    path = tmp_path / "demo.md"
    path.write_text(
        DEMO_DOC.replace("## Usage", "## Autre").replace("### `cdpx demo`", ""),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing section ## Usage"):
        parse_feature_doc(path)


def test_parse_feature_doc_requires_usage_heading_per_entrypoint(tmp_path):
    path = tmp_path / "demo.md"
    # la section Usage existe mais ne documente pas l'entrypoint déclaré
    path.write_text(DEMO_DOC.replace("### `cdpx demo`", "### `cdpx autre`"), encoding="utf-8")
    with pytest.raises(ValueError, match="cdpx demo"):
        parse_feature_doc(path)


def test_usage_heading_outside_usage_section_does_not_count(tmp_path):
    path = tmp_path / "demo.md"
    moved = DEMO_DOC.replace('### `cdpx demo`\n\nLance la démo. Sortie: `{"demo": true}`.\n', "")
    moved = moved.replace(
        "## Limites connues\nDemo.", "## Limites connues\nDemo.\n\n### `cdpx demo`\n"
    )
    assert "### `cdpx demo`" in moved  # bien présent, mais hors section Usage
    path.write_text(moved, encoding="utf-8")
    with pytest.raises(ValueError, match="cdpx demo"):
        parse_feature_doc(path)


def test_load_feature_specs_reads_project_catalog():
    specs, errors = load_feature_specs()

    ids = {spec.id for spec in specs}
    assert errors == []
    assert "harness-proof-cockpit" in ids
    assert "browser-navigation" in ids


def test_build_feature_inventory_maps_entrypoints_and_scenarios():
    scenarios = {
        "suites": {
            "unit": [
                {
                    "nodeid": "tests/test_cli.py::test_tabs_list",
                    "status": "passed",
                    "artifacts": [],
                }
            ],
            "integration": [],
            "e2e": [],
        },
        "files": [],
        "totals": {"scenarios": 1},
    }

    inventory = build_feature_inventory(
        [{"name": "tabs", "help": "gestion des onglets"}],
        scenarios,
        {"changed_files": [{"path": "src/cdpx/discovery.py"}]},
    )

    assert feature_failures(inventory) == []
    feature = next(item for item in inventory["features"] if item["id"] == "browser-navigation")
    assert feature["matched_entrypoints"][0]["id"] == "cdpx tabs"
    assert "tests/test_cli.py::test_tabs_list" in feature["matched_tests"]
    scenario = next(
        item for item in feature["scenarios"] if item["id"] == "wait-for-rendered-state"
    )
    journey = next(item for item in feature["journeys"] if item["id"] == "wait-spa-content")
    journey_scenario = next(
        item for item in journey["scenarios"] if item["id"] == "wait-for-rendered-state"
    )
    assert scenario["matched_tests"] == ["tests/test_cli.py::test_tabs_list"]
    assert journey_scenario["matched_tests"] == ["tests/test_cli.py::test_tabs_list"]
    assert len(journey_scenario["matched_scenarios"]) == 1
    assert scenario["matched_scenarios"][0]["ui_text"]
    assert feature["changed_paths"] == ["src/cdpx/discovery.py"]


def test_build_feature_inventory_maps_explicit_scenario_id():
    scenarios = {
        "suites": {
            "unit": [
                {
                    "nodeid": "tests/custom.py::test_demo",
                    "scenario_id": "browser-navigation.open-page-success",
                    "status": "passed",
                    "artifacts": [],
                }
            ],
            "integration": [],
            "e2e": [],
        },
        "files": [],
        "totals": {"scenarios": 1},
    }

    inventory = build_feature_inventory(
        [{"name": "tabs", "help": "gestion des onglets"}],
        scenarios,
        {"changed_files": []},
    )

    feature = next(item for item in inventory["features"] if item["id"] == "browser-navigation")
    assert feature["matched_scenarios"][0]["scenario_id"] == (
        "browser-navigation.open-page-success"
    )
    assert feature["matched_scenarios"][0]["given"]


def test_project_features_expose_user_doc_html():
    specs, errors = load_feature_specs()
    assert errors == []
    for spec in specs:
        doc = spec.as_dict()["doc_html"]
        for entrypoint in spec.entrypoints:
            assert f"<code>{entrypoint}</code>" in doc, f"{spec.id}: doc manquante {entrypoint}"


def test_undocumented_scenario_warning_budget_is_a_ratchet(tmp_path, monkeypatch):
    # Catalogue synthétique: un test_globs de fiche plus large que les tests
    # des scénarios documentés -> mapping sans scénario documenté. Le vrai catalogue n'en a
    # plus aucun (budget 0); ce test garde le garde-fou vivant.
    from cdpx.proofing import features as features_module

    path = tmp_path / "demo.md"
    path.write_text(
        DEMO_DOC.replace(
            'test_globs = ["tests/test_demo.py::*"]',
            'test_globs = ["tests/test_demo.py::*", "tests/test_extra.py::*"]',
        ),
        encoding="utf-8",
    )
    spec = parse_feature_doc(path)
    monkeypatch.setattr(features_module, "load_feature_specs", lambda: ([spec], []))
    monkeypatch.setattr(features_module, "UNDOCUMENTED_SCENARIO_WARNING_BUDGET", 0)
    scenarios = {
        "suites": {
            "unit": [
                {
                    # matché par tests/test_extra.py::* (fiche) mais par aucun scénario
                    "nodeid": "tests/test_extra.py::test_undocumented",
                    "status": "passed",
                    "artifacts": [],
                }
            ],
            "integration": [],
            "e2e": [],
        },
        "files": [],
        "totals": {"scenarios": 1},
    }
    inventory = build_feature_inventory(
        [{"name": "demo", "help": "démo"}], scenarios, {"changed_files": []}
    )
    assert any(
        "undocumented scenario warnings over budget" in item for item in inventory["violations"]
    )


def test_build_feature_inventory_fails_unmapped_public_entrypoint():
    inventory = build_feature_inventory(
        [{"name": "unknown", "help": "new command"}],
        {"suites": {"unit": [], "integration": [], "e2e": []}, "files": [], "totals": {}},
        {"changed_files": []},
    )

    assert "feature inventory: entrypoint unmapped: cdpx unknown" in feature_failures(inventory)
