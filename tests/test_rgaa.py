"""RGAA catalog, protocol, verdict and sample orchestration contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from tools.generate_rgaa_catalog import build

from cdpx import discovery
from cdpx.cli import main
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.commands import rgaa as rgaa_commands
from cdpx.policy import Authority, PolicyError
from cdpx.primitives import inputs
from cdpx.rgaa import provider
from cdpx.rgaa import scanner as rgaa_scanner
from cdpx.rgaa.catalog import (
    EXPECTED_COUNTS,
    SOURCE_COMMIT,
    describe_catalog,
    load_catalog,
    parse_test_selection,
)
from cdpx.rgaa.sample import compile_sample, finalize_sample_report_error, run_sample
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
        "doctype": {
            "present": not broken,
            "name": "html",
            "public_id": "",
            "system_id": "",
            "evidence_complete": True,
        },
        "language": {
            "lang": "" if broken else "fr",
            "xml_lang": "",
            "evidence_complete": True,
        },
        "title": {
            "present": not broken,
            "value": "" if broken else "Page accessible",
            "value_truncated": False,
            "evidence_complete": True,
        },
        "frames": {
            "items": [
                {
                    "target": "#frame",
                    "title_present": not broken,
                    "title": "Aide",
                    "exposed": True,
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "fields": {
            "items": [
                {
                    "target": "#email",
                    "explicit_label": not broken,
                    "implicit_label": False,
                    "aria_labelledby": False,
                    "aria_label": False,
                    "title": False,
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "links": {
            "items": [
                {
                    "target": "#account",
                    "name_sources": ["descendant-text"] if not broken else [],
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "buttons": {
            "items": [
                {
                    "target": "#send",
                    "name_sources": ["descendant-text"] if not broken else [],
                }
            ],
            "total": 1,
            "truncated": False,
        },
        "refresh_mechanisms": {"items": [], "total": 0, "truncated": False},
        "contrast": {
            "items": [
                {
                    "target": "p",
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
    assert probe["timeout"] > 0
    assert report["collector_status"]["passive-dom-css"]["nodes_examined"] >= 0
    assert report["collector_status"]["passive-dom-css"]["bytes_examined"] >= 0
    assert report["collector_status"]["passive-dom-css"]["subtree_truncated"] is False
    assert report["collector_status"]["passive-dom-css"]["execution_timed_out"] is False
    environment_probe = next(
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_rgaa_environment" in call["expression"]
    )
    assert "crypto.subtle" not in environment_probe["expression"]
    assert "value.slice(0, remaining)" in environment_probe["expression"]
    assert report["environment"]["page"]["dom_sha256"] == hashlib_sha256(b"<html>")
    assert "dom_material_base64" not in report["environment"]["page"]
    assert mock.commands_for("Accessibility.getFullAXTree") == []
    assert mock.commands_for("Accessibility.getPartialAXTree") == [
        {"backendNodeId": 1, "fetchRelatives": False}
    ]
    accessibility = report["collector_status"]["accessibility"]
    assert accessibility["ax_domain_available"] is True
    assert accessibility["target_correlation"] is False
    assert "ax_tree_collected" not in accessibility


def test_passive_probe_source_bounds_text_subtrees_siblings_and_label_lookups():
    probe = rgaa_scanner.PASSIVE_PROBE

    assert "bytes_examined" in probe
    assert "subtree_truncated" in probe
    assert "execution_timed_out" in probe
    assert ".textContent" not in probe
    assert "[...parent.children]" not in probe
    assert "querySelector(`label[for=" not in probe
    assert 'querySelectorAll("img[alt],input[type=image][alt]")' not in probe
    assert ".slice(0, length).trim()" in probe
    assert 'role: cut(element.getAttribute("role"), 64)' in probe
    assert "public_id: cut(doctype.publicId, 256)" in probe
    assert probe.index("titleEvidence(titleElement)") < probe.index("const elements = []")
    assert "while (current && depth < 64)" in probe
    assert "if (!takeNode()) return null" in probe
    assert "[...parent.children]" not in rgaa_scanner.FOCUS_STATE_PROBE


def test_runtime_evaluation_timeout_is_sent_to_chromium(mock, client):
    mock.on_eval("JSON.stringify({ok: true})", json.dumps({"ok": True}))
    rgaa_scanner._load_probe(
        client,
        42,
        "JSON.stringify({ok: true})",
        rgaa_scanner.ExecutionBudget.start(0.5),
    )

    call = mock.commands_for("Runtime.evaluate")[-1]
    assert 0 < call["timeout"] <= 500


def test_passive_scan_proves_clear_structural_failures(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation(broken=True)))

    report = scan(client)
    results = {test["id"]: test for test in report["tests"]}

    for test_id in ("2.1.1", "8.1.1", "8.5.1"):
        assert results[test_id]["verdict"] == "fail", test_id
        assert results[test_id]["findings"], test_id
    assert results["8.6.1"]["verdict"] == "needs_review"
    assert results["8.6.1"]["findings"] == []
    assert results["8.3.1"]["verdict"] == "needs_review"
    for test_id in ("3.2.1", "6.1.1", "11.1.1", "11.9.1"):
        assert results[test_id]["verdict"] == "needs_review", test_id


def test_doctype_presence_is_independent_from_doctype_validity(mock, client):
    observation = passive_observation()
    observation["doctype"] = {
        "present": True,
        "name": "custom",
        "public_id": "legacy",
        "system_id": "legacy.dtd",
        "evidence_complete": True,
    }
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(observation))

    report = scan(client, selected_tests=("8.1.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.1.1")

    assert result["verdict"] == "pass"
    assert result["evidence_complete"] is True


def test_title_relevance_distinguishes_missing_and_present_empty_title(mock, client):
    observation = passive_observation()
    observation["title"] = {"present": True, "value": "   ", "evidence_complete": True}
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(observation))

    report = scan(client, selected_tests=("8.6.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.6.1")

    assert result["verdict"] == "fail"
    assert result["findings"][0]["rule_id"] == "document-title-relevance"


@pytest.mark.parametrize(
    "title",
    [
        {"present": True, "value": "", "value_truncated": True, "evidence_complete": False},
        {
            "present": True,
            "value": "Relevant title",
            "value_truncated": True,
            "evidence_complete": False,
        },
    ],
)
def test_truncated_title_evidence_never_produces_an_automatic_failure(mock, client, title):
    observation = passive_observation()
    observation["title"] = title
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(observation))

    report = scan(client, selected_tests=("8.6.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.6.1")

    assert result["verdict"] == "needs_review"
    assert result["findings"] == []
    assert result["evidence"][0]["evidence_complete"] is False


def test_origin_policy_breach_is_never_a_collector_error(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    guards = 0

    def breach(_remaining):
        nonlocal guards
        guards += 1
        if guards == 2:
            raise PolicyError("origin rejected by the run's policy")

    with pytest.raises(PolicyError, match="origin rejected"):
        scan(client, selected_tests=("8.1.1",), origin_guard=breach)


def test_key_up_is_dispatched_when_post_keydown_policy_guard_fails(mock, client):
    with pytest.raises(PolicyError, match="origin rejected"):
        inputs.press_key(
            client,
            "Tab",
            after_key_down=lambda: (_ for _ in ()).throw(PolicyError("origin rejected")),
        )

    assert [call["type"] for call in mock.commands_for("Input.dispatchKeyEvent")] == [
        "rawKeyDown",
        "keyUp",
    ]


def test_document_drift_stops_collection_and_invalidates_rollups(mock, client):
    original = "http://site.test/page"
    mock.on_eval("window.location.href", original, original, "http://site.test/other")

    report = scan(client, selected_tests=("8.1.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.1.1")

    assert report["execution_status"] == "error"
    assert report["environment"]["state_drift"] is True
    assert report["collector_status"]["document-state"]["status"] == "error"
    assert result["verdict"] == "error"
    assert not any(
        "__cdpx_rgaa_passive" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_final_document_verification_timeout_preserves_evidence_and_report(
    mock, client, monkeypatch
):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    original_guard = rgaa_scanner._guard_document
    calls = 0

    def expire_on_final_guard(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise CDPTimeout("RGAA global deadline exceeded")
        return original_guard(*args, **kwargs)

    monkeypatch.setattr(rgaa_scanner, "_guard_document", expire_on_final_guard)

    report = scan(client, selected_tests=("8.3.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.3.1")

    assert report["execution_status"] == "partial"
    assert report["collector_status"]["final-document-verification"]["status"] == "error"
    assert result["verdict"] == "error"
    assert result["evidence"]


def test_scan_cli_final_guard_timeout_emits_json_without_stderr(
    mock, client, cli_manifest, capsys, monkeypatch
):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    completed_report = scan(client, selected_tests=("8.3.1",))

    def command_guard(*_args, **_kwargs):
        raise CDPTimeout("RGAA global deadline exceeded")

    monkeypatch.setattr(rgaa_commands, "assert_session_current", command_guard)
    monkeypatch.setattr(rgaa_commands, "scan", lambda *_args, **_kwargs: completed_report)

    code, out, err = run_cli(mock, capsys, "rgaa", "scan", "--tests", "8.3.1")

    assert code == 1 and err == ""
    report = json.loads(out)
    assert report["execution_status"] == "partial"
    assert report["collector_status"]["final-document-verification"]["status"] == "error"


def test_manual_only_cli_scan_reports_verified_current_url(mock, cli_manifest, capsys):
    code, out, err = run_cli(
        mock,
        capsys,
        "rgaa",
        "scan",
        "http://site.test/manual",
        "--tests",
        "1.1.2",
    )

    assert code == 0 and err == ""
    assert json.loads(out)["scope"]["url"] == "http://site.test/manual"


def test_manual_only_selection_skips_world_and_environment(mock, client):
    report = scan(client, selected_tests=("1.1.2",))

    assert report["execution_plan"]["collectors"] == []
    assert mock.commands_for("Page.createIsolatedWorld") == []
    assert mock.commands_for("Runtime.evaluate") == []


def test_interactive_scan_uses_trusted_tab_input_and_keeps_order_unresolved(mock, client):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    mock.on_eval("__cdpx_rgaa_focus_reset", "null")
    mock.on_eval("__cdpx_rgaa_focus_restore", True)
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
    assert report["collector_status"]["focus"]["focus_restoration"] == "completed"
    assert len(mock.commands_for("Input.dispatchKeyEvent")) == 4
    assert all(call["key"] == "Tab" for call in mock.commands_for("Input.dispatchKeyEvent"))


def test_focus_reset_timeout_still_runs_independent_restoration(mock, client, monkeypatch):
    mock.on_eval("__cdpx_rgaa_focus_reset", '{"stored": true}')
    mock.on_eval("__cdpx_rgaa_focus_restore", True)
    original_evaluate = rgaa_scanner.js.evaluate

    def timeout_after_blur(client_arg, expression, *args, **kwargs):
        value = original_evaluate(client_arg, expression, *args, **kwargs)
        if "__cdpx_rgaa_focus_reset" in expression:
            raise CDPTimeout("focus reset response timeout")
        return value

    monkeypatch.setattr(rgaa_scanner.js, "evaluate", timeout_after_blur)

    report = scan(
        client,
        scope="interactive",
        selected_tests=("10.7.1",),
        timeout=5,
    )

    focus = report["collector_status"]["focus"]
    assert report["execution_status"] == "partial"
    assert focus["focus_reset"] == "failed"
    assert focus["focus_restoration"] == "completed"
    assert focus["key_up"] == "not_attempted"
    restore = next(
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_rgaa_focus_restore" in call["expression"]
    )
    assert 0 < restore["timeout"] <= 1000


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


def test_spacing_cleanup_failure_is_reported_as_execution_error(mock, client):
    mock.on_eval("__cdpx_rgaa_text_spacing_cleanup", {"error": "cleanup failed"})
    mock.on_eval(
        "__cdpx_rgaa_text_spacing_v2",
        json.dumps({"candidates": 1, "clipped": [], "truncated": False}),
    )

    report = scan(client, scope="privileged", selected_tests=("10.12.1",))
    collector = report["collector_status"]["text-spacing"]

    assert report["execution_status"] == "partial"
    assert collector["status"] == "error"
    assert collector["cleanup"]["attempted"] is True
    assert collector["cleanup"]["completed"] is False
    assert "cleanup failed" in collector["cleanup"]["error"]


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


def test_required_hybrid_provider_failure_marks_execution_partial(mock, client):
    mock.on_eval("__cdpx_rgaa_axe_provider", {"error": "axe failed"})

    report = scan(client, engine="hybrid", selected_tests=("1.1.1",))

    assert report["execution_status"] == "partial"
    assert report["providers"][0]["status"] == "error"


def test_invalid_axe_provider_json_is_contained_in_the_full_report(mock, client):
    mock.on_eval("__cdpx_rgaa_axe_provider", "not-json")

    report = scan(client, engine="hybrid", selected_tests=("1.1.1",))

    assert len(report["tests"]) == 258
    assert report["execution_status"] == "partial"
    assert report["providers"][0]["status"] == "error"
    assert "invalid JSON" in report["providers"][0]["error"]


def test_axe_provider_shares_the_scan_deadline_across_protocol_calls():
    class TimedClient:
        def __init__(self):
            self.timeouts = []
            self.calls = []

        def send(self, method, params=None, timeout=30.0):
            self.timeouts.append(timeout)
            self.calls.append((method, params))
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "FRAME1"}}}
            if method == "Page.createIsolatedWorld":
                return {"executionContextId": 42}
            return {
                "result": {
                    "value": json.dumps(
                        {
                            "violations": [],
                            "incomplete": [],
                            "passes": [],
                            "inapplicable": [],
                        }
                    )
                }
            }

    remaining = iter((9.0, 8.0, 7.0))
    timed = TimedClient()

    provider.run_axe(timed, remaining=lambda: next(remaining))

    assert timed.timeouts == [9.0, 8.0, 7.0]
    method, params = timed.calls[-1]
    assert method == "Runtime.evaluate"
    assert params["timeout"] == 7000.0


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
    tests: [2.1.1, 10.7.1]
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
    public = first.public_plan()
    assert public["page_count"] == 2
    assert public["planned_actions"] == {
        "navigations": 2,
        "interactions": 40,
        "total": 42,
    }
    assert public["maximum_interactive_actions"] == 40
    assert public["pages"][0]["planned_actions"] == {
        "navigations": 1,
        "interactions": 20,
        "total": 21,
    }
    assert public["pages"][0]["collectors"] == ["passive-dom-css", "focus"]


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


@pytest.mark.parametrize(
    ("tests", "message"),
    [
        ('["   "]', "must not contain blank"),
        ('["2.1.1,8.3.1"]', "one ID per item"),
        ('["2.1.1", " 2.1.1 "]', "duplicate test IDs"),
    ],
)
def test_sample_manifest_validates_each_yaml_test_id_individually(tmp_path, tests, message):
    path = tmp_path / "sample.yml"
    path.write_text(
        "schema: cdpx.rgaa.sample/v1\npages:\n"
        f"  - id: home\n    url: https://site.test\n    tests: {tests}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        compile_sample(path)


def test_sample_manifest_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "sample.pipe"
    os.mkfifo(fifo)
    program = """
import sys
from cdpx.rgaa.sample import compile_sample
try:
    compile_sample(sys.argv[1])
except ValueError as error:
    print(error)
    raise SystemExit(0)
raise SystemExit("FIFO manifest unexpectedly accepted")
"""

    completed = subprocess.run(
        [sys.executable, "-c", program, str(fifo)],
        capture_output=True,
        text=True,
        timeout=1,
        check=False,
    )

    assert completed.returncode == 0
    assert "manifest must be a regular file" in completed.stdout


def test_sample_manifest_rejects_empty_oversized_and_symbolic_inputs(tmp_path):
    empty = tmp_path / "empty.yml"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="root object required"):
        compile_sample(empty)

    oversized = tmp_path / "oversized.yml"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="manifest exceeds 1 MiB"):
        compile_sample(oversized)

    valid = tmp_path / "valid.yml"
    valid.write_text(
        "schema: cdpx.rgaa.sample/v1\npages:\n  - id: home\n    url: https://site.test/\n",
        encoding="utf-8",
    )
    symbolic = tmp_path / "symbolic.yml"
    symbolic.symlink_to(valid)
    with pytest.raises(ValueError, match="unreadable manifest"):
        compile_sample(symbolic)


def test_manual_only_selection_skips_all_page_collectors(mock, client):
    report = scan(client, selected_tests=("1.1.2",))
    result = next(test for test in report["tests"] if test["id"] == "1.1.2")
    assert result["verdict"] in {"manual_only", "needs_review"}
    assert not any(
        "__cdpx_rgaa_passive" in call["expression"]
        for call in mock.commands_for("Runtime.evaluate")
    )


def test_environment_component_errors_are_advisory_and_reported_separately(
    mock, client, monkeypatch
):
    mock.on_eval(
        "__cdpx_rgaa_environment",
        json.dumps(
            {
                "dom_material_base64": "not-valid-base64***",
                "bytes_examined": 12,
                "nodes_examined": 1,
            }
        ),
    )
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    original_send = client.send

    def fail_browser_version(method, params=None, timeout=None):
        if method == "Browser.getVersion":
            raise CDPError(-32000, "browser version unavailable")
        return original_send(method, params, timeout)

    monkeypatch.setattr(client, "send", fail_browser_version)

    report = scan(client, selected_tests=("8.3.1",))
    result = next(test for test in report["tests"] if test["id"] == "8.3.1")

    assert report["execution_status"] == "partial"
    assert report["environment_status"] == {
        "browser": "error",
        "page_fingerprint": "error",
    }
    assert report["collector_status"]["environment"]["status"] == "partial"
    assert result["verdict"] == "pass"


def test_browser_environment_error_alone_is_not_reported_as_ok(mock, client):
    mock.error_methods.add("Browser.getVersion")
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))

    report = scan(client, selected_tests=("8.3.1",))

    assert report["environment_status"]["browser"] == "error"
    assert report["environment_status"]["page_fingerprint"] == "ok"
    assert report["collector_status"]["environment"]["status"] == "partial"
    assert next(test for test in report["tests"] if test["id"] == "8.3.1")["verdict"] == "pass"


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
    assert aggregated["8.3.1"]["verdict"] == "needs_review"
    assert next(item for item in result["criteria"] if item["id"] == "8.3")["verdict"] == (
        "needs_review"
    )
    assert next(item for item in result["themes"] if item["id"] == 8)["verdict"] == "fail"
    assert len(result["criteria"]) == 106
    assert result["summary"]["pages"] == 2
    assert result["summary"]["certification_claim"] is False
    assert result["actions_used"] == 2
    assert all(page["report"]["actions_used"] == 1 for page in result["pages"])
    assert all(
        page["report"]["execution_plan"]["planned_actions"]["navigations"] == 1
        for page in result["pages"]
    )
    assert result["audit_findings_present"] is True


def test_sample_navigation_error_text_preserves_prior_and_failed_page_reports(
    mock, client, tmp_path
):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: reachable
    url: http://site.test/reachable
    tests: [8.1.1]
  - id: unreachable
    url: http://site.test/unreachable
    tests: [8.1.1]
""",
        encoding="utf-8",
    )
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    mock.script_navigation_error_text(None, "net::ERR_CONNECTION_REFUSED")

    result = run_sample(client, compile_sample(path), timeout=5)

    assert result["execution_status"] == "partial"
    assert [page["page_id"] for page in result["pages"]] == ["reachable", "unreachable"]
    assert result["pages"][0]["report"]["execution_status"] == "complete"
    failed = result["pages"][1]["report"]
    assert failed["execution_status"] == "error"
    assert failed["collector_status"]["page-navigation"]["status"] == "error"
    assert len(failed["tests"]) == 258


def test_sample_partial_coverage_cannot_aggregate_to_pass(mock, client, tmp_path):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: selected
    url: http://site.test/selected
    tests: [8.3.1]
  - id: excluded
    url: http://site.test/excluded
    tests: [2.1.1]
""",
        encoding="utf-8",
    )
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))

    result = run_sample(client, compile_sample(path), timeout=5)
    aggregated = next(test for test in result["tests"] if test["id"] == "8.3.1")

    assert aggregated["coverage_complete"] is False
    assert aggregated["verdict"] == "needs_review"
    assert result["summary"]["resolved"] == 0


def test_sample_stops_at_first_policy_breach(mock, client, tmp_path):
    path = tmp_path / "sample.yml"
    path.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: first
    url: http://site.test/first
  - id: second
    url: http://site.test/second
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError, match="origin rejected"):
        run_sample(
            client,
            compile_sample(path),
            timeout=5,
            origin_guard=lambda _remaining: (_ for _ in ()).throw(PolicyError("origin rejected")),
        )

    assert [call["url"] for call in mock.commands_for("Page.navigate")] == [
        "http://site.test/first"
    ]


def test_catalog_cli_is_browser_free_and_keeps_single_json_output(capsys):
    code = main(["--limit", "5", "rgaa", "catalog", "--tests", "2.1.1"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0 and captured.err == ""
    assert payload["schema"] == "cdpx.rgaa.catalog-summary/v1"
    assert payload["selected"] == 1
    assert payload["tests"][0]["id"] == "2.1.1"


def test_catalog_cli_preserves_the_complete_normative_inventory(capsys):
    code = main(["--limit", "1", "rgaa", "catalog"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert len(payload["tests"]) == 258
    assert "tests_truncated" not in payload


def test_catalog_cli_rejects_an_explicitly_empty_test_selection(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["rgaa", "catalog", "--tests", " , "])
    captured = capsys.readouterr()

    assert excinfo.value.code == 2
    assert captured.out == ""
    assert "RGAA test selection must not be empty" in captured.err


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


def test_scan_cli_navigation_failure_returns_complete_error_report(mock, cli_manifest, capsys):
    mock.error_methods.add("Page.navigate")

    code, out, err = run_cli(
        mock,
        capsys,
        "rgaa",
        "scan",
        "http://site.test/unreachable",
        "--tests",
        "2.1.1,8.3.1",
    )

    assert code == 1 and err == ""
    report = json.loads(out)
    selected = {test["id"]: test for test in report["tests"]}
    assert len(report["tests"]) == 258
    assert selected["2.1.1"]["verdict"] == "error"
    assert selected["8.3.1"]["verdict"] == "error"
    assert report["collector_status"]["page-navigation"]["status"] == "error"
    assert report["execution_status"] == "error"
    assert report["execution_plan"]["maximum_actions"] == 1
    assert report["actions_used"] == 1


def test_scan_cli_navigation_error_text_returns_complete_error_report(mock, cli_manifest, capsys):
    mock.navigate_error_text = "net::ERR_NAME_NOT_RESOLVED"

    code, out, err = run_cli(
        mock,
        capsys,
        "rgaa",
        "scan",
        "http://site.test/unreachable",
        "--tests",
        "8.1.1",
    )

    assert code == 1 and err == ""
    report = json.loads(out)
    assert report["execution_status"] == "error"
    assert report["collector_status"]["page-navigation"]["status"] == "error"
    assert len(report["tests"]) == 258


def test_scan_cli_initial_document_verification_timeout_returns_full_json(
    mock, cli_manifest, capsys, monkeypatch
):
    monkeypatch.setattr(
        rgaa_commands,
        "verified_session_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CDPTimeout("initial document verification timed out")
        ),
    )

    code, out, err = run_cli(mock, capsys, "rgaa", "scan", "--tests", "8.3.1")

    assert code == 1 and err == ""
    report = json.loads(out)
    assert len(report["tests"]) == 258
    assert report["execution_status"] == "error"
    assert report["collector_status"]["initial-document-verification"]["status"] == "error"


def test_passive_collector_failure_is_explicit_and_preserves_catalog(mock, client):
    mock.error_methods.add("Runtime.evaluate")

    report = scan(client, selected_tests=("2.1.1", "8.3.1"))
    selected = {test["id"]: test for test in report["tests"]}

    assert len(report["tests"]) == 258
    assert selected["2.1.1"]["verdict"] == "error"
    assert selected["8.3.1"]["verdict"] == "error"
    assert report["collector_status"]["passive-dom-css"]["status"] == "error"


def test_timed_out_runtime_probe_never_terminates_target_execution(mock, client, monkeypatch):
    monkeypatch.setattr(
        "cdpx.rgaa.scanner.js.evaluate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CDPTimeout("probe timeout")),
    )

    report = scan(client, selected_tests=("8.1.1",))

    assert report["execution_status"] == "error"
    assert mock.commands_for("Runtime.terminateExecution") == []


@pytest.mark.parametrize("mode", ["complete", "partial", "error"])
@pytest.mark.parametrize(("limit", "full"), [(1, False), (2, False), (50, False), (1, True)])
def test_bounded_scan_stdout_validates_against_published_schema(
    mock, cli_manifest, capsys, limit, full, mode
):
    url = []
    expected_code = 0
    if mode == "complete":
        mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    elif mode == "partial":
        mock.on_eval("__cdpx_rgaa_passive", {"error": "probe unavailable"})
        expected_code = 1
    else:
        mock.navigate_error_text = "net::ERR_CONNECTION_REFUSED"
        url = ["http://site.test/unreachable"]
        expected_code = 1
    arguments = ["--limit", str(limit)]
    if full:
        arguments.append("--full")
    code, out, err = run_cli(
        mock,
        capsys,
        *arguments,
        "rgaa",
        "scan",
        *url,
        "--tests",
        "8.1.1",
    )

    assert code == expected_code and err == ""
    payload = json.loads(out)
    assert payload["execution_status"] == mode
    schema = json.loads(Path("schemas/rgaa-result-v1.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        payload
    )


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


@pytest.mark.parametrize(
    "mapping_key",
    ("42", "true", "? [a, b]\n"),
)
def test_sample_validate_rejects_non_text_mapping_keys_cleanly(mapping_key, tmp_path, capsys):
    invalid = tmp_path / "invalid-key.yml"
    prefix = (
        f"{mapping_key}: value\n" if not mapping_key.startswith("?") else f"{mapping_key}: value\n"
    )
    invalid.write_text(
        prefix + "schema: cdpx.rgaa.sample/v1\npages:\n  - id: home\n    url: http://site.test/\n",
        encoding="utf-8",
    )

    assert main(["rgaa", "sample", "validate", str(invalid)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "mapping keys must be strings" in captured.err


def test_sample_run_cli_emits_one_bounded_json_result(mock, cli_manifest, tmp_path, capsys):
    manifest = tmp_path / "sample.yml"
    manifest.write_text(
        """schema: cdpx.rgaa.sample/v1
pages:
  - id: baseline
    url: http://site.test/baseline
    tests: [2.1.1, 8.3.1]
  - id: regression
    url: http://site.test/regression
    tests: [2.1.1, 8.3.1]
""",
        encoding="utf-8",
    )
    mock.on_eval(
        "__cdpx_rgaa_passive",
        json.dumps(passive_observation()),
        json.dumps(passive_observation(broken=True)),
    )

    code, out, err = run_cli(
        mock,
        capsys,
        "--limit",
        "20",
        "rgaa",
        "sample",
        "run",
        str(manifest),
    )
    result = json.loads(out)

    assert code == 0 and err == ""
    assert result["schema"] == "cdpx.rgaa.sample-result/v1"
    assert result["summary"]["pages"] == 2
    assert len(result["pages"]) == 2
    assert len(result["tests"]) == 258
    assert result["summary"]["certification_claim"] is False


def test_sample_cli_final_guard_timeout_preserves_last_page_and_json(
    mock, client, cli_manifest, tmp_path, capsys, monkeypatch
):
    manifest = tmp_path / "sample.yml"
    manifest.write_text(
        "schema: cdpx.rgaa.sample/v1\npages:\n"
        "  - id: baseline\n    url: http://site.test/baseline\n    tests: [8.3.1]\n",
        encoding="utf-8",
    )
    compiled = compile_sample(manifest)
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    completed_result = run_sample(client, compiled, timeout=5)

    monkeypatch.setattr(rgaa_commands, "run_sample", lambda *_args, **_kwargs: completed_result)
    monkeypatch.setattr(
        rgaa_commands,
        "assert_session_current",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CDPTimeout("RGAA global deadline exceeded")
        ),
    )

    code, out, err = run_cli(
        mock,
        capsys,
        "rgaa",
        "sample",
        "run",
        str(manifest),
    )

    assert code == 1 and err == ""
    result = json.loads(out)
    assert result["execution_status"] == "partial"
    assert len(result["pages"]) == 1
    assert result["collector_status"]["sample-pages"]["status"] == "partial"
    assert result["collector_status"]["final-document-verification"]["status"] == "error"
    page_report = result["pages"][0]["report"]
    page_test = next(test for test in page_report["tests"] if test["id"] == "8.3.1")
    aggregate_test = next(test for test in result["tests"] if test["id"] == "8.3.1")
    assert page_report["collector_status"]["final-document-verification"]["status"] == "error"
    assert page_test["verdict"] == "error"
    assert page_test["evidence"]
    assert aggregate_test["verdict"] == "error"
    assert result["summary"]["error"] == 1


@pytest.mark.parametrize(
    ("starting_status", "expected_status"),
    [("complete", "partial"), ("partial", "partial"), ("error", "error")],
)
def test_report_finalization_never_reduces_execution_severity(
    mock, client, starting_status, expected_status
):
    mock.on_eval("__cdpx_rgaa_passive", json.dumps(passive_observation()))
    report = scan(client, selected_tests=("8.3.1",))
    report["execution_status"] = starting_status

    rgaa_scanner.finalize_report_error(report, CDPTimeout("final guard timed out"))

    assert report["execution_status"] == expected_status
    assert report["summary"]["collector_attempted"] == sum(
        status["status"] in {"ok", "partial", "error"}
        for status in report["collector_status"].values()
    )


def test_sample_finalization_keeps_all_error_sample_as_error(mock, client, tmp_path):
    manifest = tmp_path / "sample.yml"
    manifest.write_text(
        "schema: cdpx.rgaa.sample/v1\npages:\n"
        "  - id: failed\n    url: http://site.test/failed\n    tests: [8.3.1]\n",
        encoding="utf-8",
    )
    mock.navigate_error_text = "net::ERR_CONNECTION_REFUSED"
    report = run_sample(client, compile_sample(manifest), timeout=5)
    assert report["execution_status"] == "error"

    finalize_sample_report_error(report, CDPTimeout("final guard timed out"))

    assert report["execution_status"] == "error"
    assert report["collector_status"]["sample-pages"]["status"] == "error"
