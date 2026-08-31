"""RGAA catalog, protocol, verdict and sample orchestration contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tools.generate_rgaa_catalog import build

from cdpx import discovery
from cdpx.cli import main
from cdpx.client import CDPClient
from cdpx.policy import Authority
from cdpx.rgaa import provider
from cdpx.rgaa.catalog import (
    EXPECTED_COUNTS,
    SOURCE_COMMIT,
    describe_catalog,
    load_catalog,
    parse_test_selection,
)
from cdpx.rgaa.sample import compile_sample, run_sample
from cdpx.rgaa.scanner import scan


@pytest.fixture()
def client(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://site.test/page"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    with CDPClient(target["webSocketDebuggerUrl"], timeout=5) as connected:
        yield connected


def run_cli(mock, capsys, *argv: str):
    manifest = mock.cli_manifest
    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--timeout",
            "5",
            *argv,
        ]
    )
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def passive_observation(*, broken: bool = False) -> dict:
    return {
        "url": "http://site.test/rgaa.html",
        "doctype": {"present": not broken, "name": "html", "public_id": "", "system_id": ""},
        "language": {"lang": "" if broken else "fr", "xml_lang": ""},
        "title": {"present": not broken, "value": "" if broken else "Page accessible"},
        "frames": {
            "items": [
                {
                    "selector": "#frame",
                    "title_present": not broken,
                    "title": "Aide",
                    "aria_hidden": False,
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "fields": {
            "items": [
                {
                    "selector": "#email",
                    "labelled": not broken,
                    "mechanisms": ["label-for"] if not broken else [],
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "links": {
            "items": [{"selector": "#account", "name": "Compte" if not broken else ""}],
            "total": 1,
            "truncated": False,
        },
        "buttons": {
            "items": [
                {
                    "selector": "#send",
                    "name": "Envoyer" if not broken else "",
                    "visible_name": "Envoyer",
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "meta_refresh": {"items": [], "total": 0, "truncated": False},
        "contrast": {
            "items": [
                {
                    "selector": "p",
                    "test_id": "3.2.1",
                    "ratio": 2.1 if broken else 12.5,
                    "required": 4.5,
                    "foreground": "rgb(0, 0, 0)",
                    "background": "rgb(255, 255, 255)",
                    "font_size": 16,
                    "font_weight": 400,
                }
            ],
            "total": 1,
            "unresolved": 0,
            "truncated": False,
        },
    }


def test_catalog_is_offline_complete_and_generator_join_is_deterministic():
    catalog = load_catalog()
    generated, matrix, manifest = build()

    assert catalog["counts"] == EXPECTED_COUNTS == {"themes": 13, "criteria": 106, "tests": 258}
    assert len(catalog["tests"]) == len(matrix) == 258
    assert {test["id"] for test in catalog["tests"]} == {row["official_test_id"] for row in matrix}
    assert generated == catalog
    assert manifest["source_commit"] == catalog["source_commit"] == SOURCE_COMMIT
    assert catalog["runtime_fetch"] is False


def test_catalog_selection_rejects_unknown_ids_and_exposes_official_statements():
    selected = parse_test_selection("2.1.1, 8.3.1,2.1.1")
    assert selected == ("2.1.1", "8.3.1")
    described = describe_catalog(selected)
    assert described["selected"] == 2
    assert [test["id"] for test in described["tests"]] == ["2.1.1", "8.3.1"]
    assert all(test["statement"] for test in described["tests"])
    with pytest.raises(ValueError, match="unknown RGAA test"):
        parse_test_selection("999.1.1")


def test_passive_scan_emits_exact_probe_and_prudent_verdicts(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))

    report = scan(client, selected_tests=("2.1.1", "3.2.1", "6.1.1", "8.3.1", "11.1.1"))
    results = {test["id"]: test for test in report["tests"]}

    assert report["summary"]["official_tests"] == 258
    assert report["summary"]["selected"] == 5
    assert report["summary"]["certification_claim"] is False
    assert results["2.1.1"]["verdict"] == "needs_review"
    assert results["3.2.1"]["verdict"] == "needs_review"
    assert results["6.1.1"]["verdict"] == "needs_review"
    assert results["8.3.1"]["verdict"] == "pass"
    assert results["11.1.1"]["verdict"] == "needs_review"
    assert results["1.1.1"]["verdict"] == "not_tested"
    assert len(report["themes"]) == 13
    assert len(report["criteria"]) == 106
    assert next(item for item in report["criteria"] if item["id"] == "3.2")["verdict"] == (
        "needs_review"
    )
    probe = next(
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_rgaa_passive" in call["expression"]
    )
    assert probe["awaitPromise"] is True and probe["returnByValue"] is True
    assert probe["contextId"] == 42


def test_passive_scan_proves_clear_structural_failures(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation(broken=True)))

    report = scan(client)
    results = {test["id"]: test for test in report["tests"]}

    for test_id in ("2.1.1", "8.1.1", "8.3.1", "8.5.1", "8.6.1"):
        assert results[test_id]["verdict"] == "fail", test_id
        assert results[test_id]["findings"], test_id
    for test_id in ("3.2.1", "6.1.1", "11.1.1", "11.9.1"):
        assert results[test_id]["verdict"] == "needs_review", test_id


def test_interactive_scan_uses_trusted_tab_input_and_keeps_order_unresolved(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    focus = {
        "target": "#account",
        "tag": "a",
        "role": None,
        "outline_style": "none",
        "outline_width": "0px",
        "outline_color": "rgb(0, 0, 0)",
        "box_shadow": "none",
        "focus_visible_match": True,
        "indicator_detected": False,
    }
    mock.on_eval("__cdpx_rgaa_focus_state", json.dumps(focus), json.dumps(focus))

    report = scan(client, scope="interactive", selected_tests=("10.7.1", "12.8.1"))
    results = {test["id"]: test for test in report["tests"]}

    assert results["10.7.1"]["verdict"] == "needs_review"
    assert results["12.8.1"]["verdict"] == "needs_review"
    assert len(mock.commands_for("Input.dispatchKeyEvent")) == 4
    assert all(call["key"] == "Tab" for call in mock.commands_for("Input.dispatchKeyEvent"))


def test_privileged_spacing_probe_reports_new_clipping(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    mock.on_eval("__cdpx_rgaa_focus_state", "null")
    mock.on_eval(
        "__cdpx_rgaa_text_spacing",
        json.dumps(
            {
                "candidates": 12,
                "clipped": [{"target": "#card", "horizontal": True, "vertical": False}],
                "clipped_total": 1,
            }
        ),
    )

    report = scan(client, scope="privileged", selected_tests=("10.12.1",))
    result = next(test for test in report["tests"] if test["id"] == "10.12.1")

    assert result["verdict"] == "needs_review"
    assert result["findings"][0]["target"] == "#card"
    assert "__cdpx_rgaa_text_spacing" in mock.commands_for("Runtime.evaluate")[-1]["expression"]


def test_hybrid_axe_observations_are_isolated_bounded_and_never_oracle_verdicts(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    axe_result = {
        "violations": [
            {
                "id": "image-alt",
                "impact": "critical",
                "tags": ["wcag2a"],
                "nodes": [
                    {"target": ["#hero"], "impact": "critical", "failure_summary": "Fix alt"}
                ],
                "nodes_total": 1,
            }
        ],
        "incomplete": [],
        "passes": [],
        "inapplicable": [],
    }
    mock.on_eval("__cdpx_rgaa_axe_provider", json.dumps(axe_result))

    report = scan(client, engine="hybrid", selected_tests=("1.1.1",))
    result = next(test for test in report["tests"] if test["id"] == "1.1.1")

    assert result["verdict"] == "needs_review"
    assert result["advisory"][0]["provider_outcome"] == "violations"
    assert result["advisory"][0]["verdict_authority"] == "advisory_only"
    assert report["providers"][0]["isolated_world"] is True
    assert [call["worldName"] for call in mock.commands_for("Page.createIsolatedWorld")] == [
        "__cdpx_rgaa_native",
        "__cdpx_rgaa_axe",
    ]
    axe_call = mock.commands_for("Runtime.evaluate")[-1]
    assert axe_call["contextId"] == 42
    assert hashlib_sha256(Path(provider.AXE_PATH).read_bytes()) == provider.AXE_HASH


def hashlib_sha256(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def test_sample_manifest_is_bounded_deterministic_and_declares_max_authority(tmp_path):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
rgaa_version: 4.1.2
scope: interactive
engine: native
base_url: http://site.test
pages:
  - id: home
    url: /
    tests: [2.1.1, 8.3.1]
  - id: checkout
    url: /checkout
""",
        encoding="utf-8",
    )

    first = compile_sample(path)
    second = compile_sample(path)

    assert first.digest == second.digest
    assert first.authority is Authority.INTERACTION
    assert [page.url for page in first.pages] == ["http://site.test/", "http://site.test/checkout"]
    assert first.public_plan()["page_count"] == 2


def test_sample_manifest_rejects_credentials_and_non_schema_test_shortcuts(tmp_path):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: private
    url: https://:secret@site.test/
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="without credentials"):
        compile_sample(path)

    path.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: home
    url: https://site.test/
    tests: 2.1.1,8.3.1
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must be a list"):
        compile_sample(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("schema: cdpx.rgaa.sample/v1\nschema: duplicate\npages: []\n", "duplicate key"),
        (
            "schema: cdpx.rgaa.sample/v1\npages:\n"
            "  - id: home\n    url: https://site.test\n    tests: []\n",
            "must not be empty",
        ),
        (
            "schema: cdpx.rgaa.sample/v1\npages:\n"
            "  - id: home\n    url: https://site.test\n"
            "    tests: [2.1.1, 2.1.1]\n",
            "duplicate test IDs",
        ),
    ],
)
def test_sample_manifest_rejects_ambiguous_yaml_and_test_traps(tmp_path, payload, message):
    path = tmp_path / "sample.yml"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        compile_sample(path)


def test_manual_only_selection_skips_all_page_collectors(mock, client):
    report = scan(client, selected_tests=("1.1.2",))
    result = next(test for test in report["tests"] if test["id"] == "1.1.2")
    assert result["verdict"] in {"manual_only", "needs_review"}
    assert not any(
        "__cdpx_rgaa_passive" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_truncated_contrast_never_becomes_an_automatic_verdict(mock, client):
    observation = passive_observation()
    observation["contrast"]["truncated"] = True
    observation["contrast"]["total"] = 201
    observation["contrast"]["examined"] = 200
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(observation))
    report = scan(client, selected_tests=("3.2.1",))
    result = next(test for test in report["tests"] if test["id"] == "3.2.1")
    assert result["verdict"] == "needs_review"
    assert result["evidence"][0]["truncated"] is True


def test_sample_run_navigates_declared_pages_and_aggregates_failures(mock, client, tmp_path):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
scope: passive
engine: native
pages:
  - id: good
    url: http://site.test/good
  - id: broken
    url: http://site.test/broken
""",
        encoding="utf-8",
    )
    mock.on_eval(
        "__cdpx_rgaa_passive",
        json.dumps(passive_observation()),
        json.dumps(passive_observation(broken=True)),
    )

    result = run_sample(client, compile_sample(path), timeout=5)
    aggregated = {test["id"]: test for test in result["tests"]}

    assert [call["url"] for call in mock.commands_for("Page.navigate")] == [
        "http://site.test/good",
        "http://site.test/broken",
    ]
    assert aggregated["8.3.1"]["verdict"] == "fail"
    assert next(item for item in result["criteria"] if item["id"] == "8.3")["verdict"] == ("fail")
    assert next(item for item in result["themes"] if item["id"] == 8)["verdict"] == "fail"
    assert len(result["criteria"]) == 106
    assert result["summary"]["pages"] == 2
    assert result["summary"]["certification_claim"] is False


def test_catalog_cli_is_browser_free_and_keeps_single_json_output(capsys):
    code = main(["--limit", "5", "rgaa", "catalog", "--tests", "2.1.1"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0 and captured.err == ""
    assert payload["schema"] == "cdpx.rgaa.catalog-summary/v1"
    assert payload["selected"] == 1
    assert payload["tests"][0]["id"] == "2.1.1"


def test_scan_cli_uses_supervised_session_and_bounds_full_catalog(mock, cli_manifest, capsys):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))

    code, out, err = run_cli(
        mock,
        capsys,
        "--limit",
        "20",
        "rgaa",
        "scan",
        "--tests",
        "2.1.1,8.3.1",
    )
    payload = json.loads(out)

    assert code == 0 and err == ""
    assert payload["summary"]["official_tests"] == 258
    assert payload["summary"]["selected"] == 2
    assert len(payload["tests"]) == 258
    assert payload["_cdpx"]["content_trust"] == "untrusted"
    assert any(
        "__cdpx_rgaa_passive" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_scan_cli_rejects_insufficient_action_budget_before_browser_effects(
    mock, cli_manifest, capsys
):
    code, out, err = run_cli(
        mock,
        capsys,
        "--max-actions",
        "19",
        "rgaa",
        "scan",
        "--scope",
        "interactive",
        "--tests",
        "10.7.1",
    )
    assert code == 2 and out == ""
    assert "20 required" in err
    assert mock.commands_for("Page.createIsolatedWorld") == []


def test_native_isolation_failure_still_returns_complete_error_report(mock, client):
    mock.error_methods.add("Page.createIsolatedWorld")
    report = scan(client, selected_tests=("2.1.1", "8.3.1"))
    assert len(report["tests"]) == 258
    selected = {test["id"]: test for test in report["tests"]}
    assert selected["2.1.1"]["verdict"] == "error"
    assert selected["8.3.1"]["verdict"] == "error"
    assert report["collector_status"]["isolated-world"]["status"] == "error"


def test_sample_validate_cli_is_browser_free_and_reports_usage_errors(tmp_path, capsys):
    valid = tmp_path / "valid.yml"
    valid.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: home
    url: http://site.test/
""",
        encoding="utf-8",
    )
    invalid = tmp_path / "invalid.yml"
    invalid.write_text("schema: wrong\npages: []\n", encoding="utf-8")

    assert main(["rgaa", "sample", "validate", str(valid)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["required_authority"] == "observation"
    assert plan["page_count"] == 1

    assert main(["rgaa", "sample", "validate", str(invalid)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "schema must be cdpx.rgaa.sample/v1" in captured.err
