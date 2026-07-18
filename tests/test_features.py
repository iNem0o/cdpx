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

## Intent
Demo.

## Usage

### `cdpx demo`

Runs the demo. Output: `{"demo": true}`.

## User journeys
Demo.

## Validation
Demo.

## Proofs
Demo.

## Known limitations
Demo.
"""


def test_parse_feature_doc_requires_structured_markdown(tmp_path):
    """A valid feature sheet (TOML front-matter + required sections) is
    parsed into a complete spec: entrypoints, journeys, scenarios, and
    anchored HTML rendering of the user doc."""
    path = tmp_path / "demo.md"
    path.write_text(DEMO_DOC, encoding="utf-8")

    spec = parse_feature_doc(path)

    #: the front-matter feeds every facet of the spec (identity, journeys,
    #: scenarios) and the Usage section produces an anchored HTML heading
    #: that the cockpit can target by link
    assert spec.id == "demo-feature"
    assert spec.entrypoints == ["cdpx demo"]
    assert spec.journeys[0]["id"] == "demo"
    assert spec.scenarios[0].id == "demo-happy-path"
    assert "Usage" in spec.sections
    assert '<h3 id="cdpx-demo"><code>cdpx demo</code></h3>' in spec.as_dict()["doc_html"]


def test_parse_feature_doc_requires_usage_section(tmp_path):
    """A sheet without a ## Usage section is rejected at parsing: the user
    doc is not an optional field of the sheet contract."""
    path = tmp_path / "demo.md"
    path.write_text(
        DEMO_DOC.replace("## Usage", "## Other").replace("### `cdpx demo`", ""),
        encoding="utf-8",
    )
    #: the error names the missing section to guide the sheet's author
    with pytest.raises(ValueError, match="missing section ## Usage"):
        parse_feature_doc(path)


def test_parse_feature_doc_requires_usage_heading_per_entrypoint(tmp_path):
    """Every entrypoint declared in the front-matter must have its heading
    in the Usage section: an entrypoint without usage instructions fails
    the sheet's parsing."""
    path = tmp_path / "demo.md"
    # the Usage section exists but does not document the declared entrypoint
    path.write_text(DEMO_DOC.replace("### `cdpx demo`", "### `cdpx other`"), encoding="utf-8")
    #: the error cites the orphan entrypoint rather than a generic message
    with pytest.raises(ValueError, match="cdpx demo"):
        parse_feature_doc(path)


def test_usage_heading_outside_usage_section_does_not_count(tmp_path):
    """An entrypoint heading moved outside the Usage section does not
    satisfy the doc requirement: it must be where the user looks for it,
    not just anywhere in the sheet."""
    path = tmp_path / "demo.md"
    moved = DEMO_DOC.replace('### `cdpx demo`\n\nRuns the demo. Output: `{"demo": true}`.\n', "")
    moved = moved.replace(
        "## Known limitations\nDemo.", "## Known limitations\nDemo.\n\n### `cdpx demo`\n"
    )
    #: fixture guard: the heading still exists in the document
    assert "### `cdpx demo`" in moved  # present, but outside the Usage section
    path.write_text(moved, encoding="utf-8")
    #: despite its presence elsewhere, the entrypoint is judged undocumented
    with pytest.raises(ValueError, match="cdpx demo"):
        parse_feature_doc(path)


def test_load_feature_specs_reads_project_catalog():
    """The catalog of sheets actually shipped in the repository loads
    without error: this test breaks as soon as a committed sheet becomes
    invalid."""
    specs, errors = load_feature_specs()

    ids = {spec.id for spec in specs}
    #: zero parsing errors on the real sheets, and the product's pivotal
    #: features are indeed present in the catalog
    assert errors == []
    assert "harness-proof-cockpit" in ids
    assert "browser-navigation" in ids


def test_build_feature_inventory_maps_entrypoints_and_scenarios():
    """The inventory cross-references CLI commands, executed tests, and
    changed files to attach each proof to its feature, journey, and
    documented scenario."""
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
        [{"name": "tabs", "help": "tab management"}],
        scenarios,
        {"changed_files": [{"path": "src/cdpx/discovery.py"}]},
    )

    #: the passed unit test traces back to the feature via the tabs
    #: entrypoint, with no inventory failure raised
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
    #: the same test is visible at both the scenario AND journey levels,
    #: accompanied by the human-facing text in the report
    assert scenario["matched_tests"] == ["tests/test_cli.py::test_tabs_list"]
    assert journey_scenario["matched_tests"] == ["tests/test_cli.py::test_tabs_list"]
    assert len(journey_scenario["matched_scenarios"]) == 1
    assert scenario["matched_scenarios"][0]["ui_text"]
    #: files changed that touch the feature are traced to link the diff to
    #: the proofs that cover it
    assert feature["changed_paths"] == ["src/cdpx/discovery.py"]


def test_build_feature_inventory_maps_explicit_scenario_id():
    """A test carrying an explicit scenario_id is attached directly to the
    documented scenario, even if its path matches no sheet glob."""
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
        [{"name": "tabs", "help": "tab management"}],
        scenarios,
        {"changed_files": []},
    )

    feature = next(item for item in inventory["features"] if item["id"] == "browser-navigation")
    #: the explicit marker is enough for attachment, and the scenario comes
    #: out enriched with its documented Given for the report
    assert feature["matched_scenarios"][0]["scenario_id"] == (
        "browser-navigation.open-page-success"
    )
    assert feature["matched_scenarios"][0]["given"]


def test_project_features_expose_user_doc_html():
    """Every shipped sheet documents each of its entrypoints in its user
    HTML: a global guard against a published command with no usage
    instructions visible in the cockpit."""
    specs, errors = load_feature_specs()
    assert errors == []
    for spec in specs:
        doc = spec.as_dict()["doc_html"]
        for entrypoint in spec.entrypoints:
            #: every declared entrypoint appears in the rendered HTML doc of
            #: its own sheet, otherwise the message names the offending sheet
            assert f"<code>{entrypoint}</code>" in doc, f"{spec.id}: missing doc {entrypoint}"


def test_undocumented_scenario_warning_budget_is_a_ratchet(tmp_path, monkeypatch):
    """A test matched by the sheet but by no documented scenario consumes
    the warning budget; at budget 0, it's a violation — the ratchet forbids
    any regression in documentation coverage."""
    # Synthetic catalog: a sheet's test_globs wider than the documented
    # scenarios' tests -> mapping with no documented scenario. The real catalog
    # has none left (budget 0); this test keeps the guard alive.
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
                    # matched by tests/test_extra.py::* (sheet) but by no scenario
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
        [{"name": "demo", "help": "demo"}], scenarios, {"changed_files": []}
    )
    #: exceeding the budget becomes an inventory violation, hence a gate
    #: blocking reason rather than a mere warning
    assert any(
        "undocumented scenario warnings over budget" in item for item in inventory["violations"]
    )


def test_build_feature_inventory_fails_unmapped_public_entrypoint():
    """A public CLI command absent from every feature sheet is an inventory
    failure: it is impossible to expose an entrypoint the docs ignore."""
    inventory = build_feature_inventory(
        [{"name": "unknown", "help": "new command"}],
        {"suites": {"unit": [], "integration": [], "e2e": []}, "files": [], "totals": {}},
        {"changed_files": []},
    )

    #: the failure names the orphan command to make obvious which sheet
    #: still needs to be written
    assert "feature inventory: entrypoint unmapped: cdpx unknown" in feature_failures(inventory)
