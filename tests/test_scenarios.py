import json
from pathlib import Path

import pytest

from cdpx import discovery, scenarios
from cdpx.cli import main
from cdpx.client import CDPClient


def client_for(mock):
    target = discovery.pick_page("127.0.0.1", mock.http_port)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


def test_parse_scenario_with_step_capture():
    scenario = scenarios.parse(
        {
            "name": "checkout_guest_add_to_cart",
            "context": {"base_url": "http://shop.localhost", "emulation": "mobile"},
            "steps": [
                {
                    "label": "product",
                    "goto": "/produit/42",
                    "capture": ["screenshot", "console"],
                },
                {"wait_text": ["#count", "1"]},
            ],
            "assertions": [{"text_contains": ["#count", "1"]}],
            "artifacts": ["network"],
        }
    )

    assert scenario.name == "checkout_guest_add_to_cart"
    assert scenario.emulation == "mobile"
    assert scenario.steps[0].capture == ["screenshot", "console"]


def test_parse_rejects_unknown_field():
    with pytest.raises(scenarios.ScenarioUsageError, match="champ"):
        scenarios.parse(
            {
                "name": "bad",
                "context": {"base_url": "http://x.test"},
                "steps": [{"goto": "/"}],
                "unexpected": True,
            }
        )


def test_run_scenario_happy_path_with_checkpoint_artifacts(mock, tmp_path):
    mock.on_eval("getBoundingClientRect", json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}))
    mock.on_eval("innerText", "0", "1", "1")
    mock.on_eval("querySelector", True)
    scenario = scenarios.parse(
        {
            "name": "cart",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {"label": "product", "goto": "/product", "capture": ["screenshot", "network"]},
                {"label": "add", "click": "#add", "capture": ["console"]},
                {"label": "cart_count", "wait_text": ["#cart-count", "1"]},
            ],
            "assertions": [
                {"no_console_errors": True},
                {"network_errors_max": 0},
                {"text_contains": ["#cart-count", "1"]},
            ],
            "artifacts": ["screenshot", "console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(client, scenario, evidence_root=tmp_path, settle=0.01)

    assert result["verdict"] == "pass"
    assert result["findings"] == []
    assert len(result["steps"]) == 3
    artifact_types = [artifact["type"] for artifact in result["artifacts"]]
    assert artifact_types == [
        "screenshot",
        "network",
        "console",
        "screenshot",
        "console",
        "network",
    ]
    assert all(Path(artifact["path"]).exists() for artifact in result["artifacts"])


def test_run_scenario_failure_still_captures_checkpoint_and_final(mock, tmp_path):
    mock.on_eval("getBoundingClientRect", None)
    scenario = scenarios.parse(
        {
            "name": "missing_button",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"label": "broken_click", "click": "#missing", "capture": ["console"]}],
            "artifacts": ["screenshot"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(client, scenario, evidence_root=tmp_path, settle=0.01)

    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    assert [artifact["type"] for artifact in result["artifacts"]] == ["console", "screenshot"]
    assert result["findings"][0]["code"] == "step_failed"


def test_run_scenario_console_and_network_assertions_fail(mock, tmp_path):
    mock.script_console(
        [{"type": "error", "args": [{"type": "string", "value": "boom"}], "timestamp": 1.0}]
    )
    mock.script_network(
        [
            {
                "method": "Network.requestWillBeSent",
                "params": {
                    "requestId": "R1",
                    "type": "Fetch",
                    "request": {"url": "http://shop.test/api", "method": "GET"},
                },
            },
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {"url": "http://shop.test/api", "status": 500},
                },
            },
        ]
    )
    scenario = scenarios.parse(
        {
            "name": "bad_observability",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "assertions": [{"no_console_errors": True}, {"network_errors_max": 0}],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(client, scenario, evidence_root=tmp_path, settle=0.01)

    assert result["verdict"] == "fail"
    assert [finding["code"] for finding in result["findings"]] == [
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    ]


def run_cli(mock, capsys, *argv):
    code = main(["--port", str(mock.http_port), "--timeout", "5", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_scenario_cli_run_passes_with_json(mock, capsys, tmp_path):
    scenario = tmp_path / "scenario.yml"
    scenario.write_text(
        """
name: cli_pass
context:
  base_url: http://shop.test
steps:
  - goto: /
artifacts:
  - network
""",
        encoding="utf-8",
    )

    code, out, err = run_cli(
        mock,
        capsys,
        "scenario",
        "run",
        str(scenario),
        "--evidence-dir",
        str(tmp_path / "evidence"),
        "--settle",
        "0.01",
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["name"] == "cli_pass"
    assert data["verdict"] == "pass"


def test_scenario_cli_invalid_file_exits_2(mock, capsys, tmp_path):
    scenario = tmp_path / "bad.yml"
    scenario.write_text("[]\n", encoding="utf-8")

    code, _, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    assert code == 2
    assert "scénario doit être un objet" in err
