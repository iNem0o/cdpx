import json
import stat
from pathlib import Path

import pytest

from cdpx import discovery, scenarios
from cdpx.artifacts import scan_canaries
from cdpx.cli import main
from cdpx.client import CDPClient
from cdpx.primitives import profiler_panels


def client_for(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://shop.test/"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
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
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

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


def test_scenario_wait_visible_requires_visibility_not_only_dom_attachment(mock, tmp_path):
    mock.on_eval("__cdpx_visible", False, True)
    mock.on_eval("querySelector", True)
    scenario = scenarios.parse(
        {
            "name": "visible",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"wait_visible": "#revealed"}],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            timeout=0.5,
            settle=0,
            origins="http://*.test",
        )

    assert result["verdict"] == "pass"
    assert result["steps"][0]["result"]["visible"] is True
    visibility_checks = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_visible" in item["expression"]
    ]
    assert len(visibility_checks) == 2


def test_run_scenario_profiler_artifact_parses_real_panels(mock, tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "profiler"
    mock.on_eval("window.location.href", "http://shop.test/")
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://shop.test/",
                        "status": 200,
                        "headers": {"X-Debug-Token-Link": "http://shop.test/_profiler/fixed-token"},
                    },
                },
            }
        ]
    )
    payload = json.dumps(
        [
            {
                "panel": key,
                "status": 200,
                "html": (fixtures / f"{profiler_panels.PANEL_SOURCES[key]}.html").read_text(
                    encoding="utf-8"
                ),
            }
            for key in profiler_panels.ALL_PANELS
        ]
    )
    mock.on_eval("__cdpx_profiler_panels", payload)
    scenario = scenarios.parse(
        {
            "name": "profiler_capture",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["profiler"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    assert result["verdict"] == "pass"
    (artifact,) = [a for a in result["artifacts"] if a["type"] == "profiler"]
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    assert data["token_present"] is True
    assert "token" not in data
    assert data["panels"]["db"]["queries"] == 6
    assert data["panels"]["router"]["route"] == "scenario_profiler"
    assert "signals" not in data


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
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

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
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )

    assert result["verdict"] == "fail"
    assert [finding["code"] for finding in result["findings"]] == [
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    ]


def test_final_drain_precedes_console_and_network_assertions(mock, tmp_path, monkeypatch):
    class LateCollector(scenarios.PassiveCollector):
        def __init__(self, context=None):
            super().__init__(context)
            self.drain_count = 0

        def drain(self, client, settle):
            self.drain_count += 1
            if self.drain_count != 3:
                return
            self.console_entries.append(
                {
                    "kind": "console",
                    "type": "error",
                    "text": "late console error",
                    "ts": 2.0,
                }
            )
            self.requests["LATE"] = {
                "requestId": "LATE",
                "url": "http://shop.test/api/late",
                "method": "GET",
                "status": 500,
            }

    monkeypatch.setattr(scenarios, "PassiveCollector", LateCollector)
    scenario = scenarios.parse(
        {
            "name": "late_observability",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "assertions": [
                {"no_console_errors": True},
                {"network_errors_max": 0},
            ],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    assert result["verdict"] == "fail"
    assert [record["actual"] for record in result["assertions"]] == [1, 1]
    artifacts = {
        artifact["type"]: json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        for artifact in result["artifacts"]
    }
    assert artifacts["console"]["errors"] == result["assertions"][0]["actual"]
    assert artifacts["network"]["summary"]["errors_4xx_5xx"] == result["assertions"][1]["actual"]


def test_scenario_network_evidence_redacts_sensitive_headers(mock, tmp_path):
    mock.script_network(
        [
            {
                "method": "Network.responseReceived",
                "params": {
                    "requestId": "R1",
                    "response": {
                        "url": "http://shop.test/",
                        "status": 200,
                        "headers": {
                            "Authorization": "Bearer secret",
                            "Set-Cookie": "session=secret",
                            "Content-Type": "text/html",
                        },
                    },
                },
            }
        ]
    )
    scenario = scenarios.parse(
        {
            "name": "redacted",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["network"],
        }
    )
    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0.01, origins="http://*.test"
        )
    artifact = next(a for a in result["artifacts"] if a["type"] == "network")
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    headers = data["requests"][0]["headers"]
    assert headers == {
        "Authorization": "***",
        "Set-Cookie": "***",
        "Content-Type": "text/html",
    }


def test_strict_scenario_stops_after_redirect_before_next_mutation_or_capture(mock, tmp_path):
    mock.on_eval("window.location.href", "https://forbidden.example/redirected")
    scenario = scenarios.parse(
        {
            "name": "redirect_guard",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {"goto": "/start", "capture": ["screenshot"]},
                {"click": "#danger"},
            ],
            "artifacts": ["network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            origins="http://shop.test",
            settle=0,
        )

    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    assert [finding["code"] for finding in result["findings"]] == ["origin_refused"]
    assert result["artifacts"] == []
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_scenario_secret_ref_never_reaches_outputs_or_evidence(mock, tmp_path, monkeypatch):
    secret = "checkout-password-canary-9347"
    monkeypatch.setenv("CHECKOUT_PASSWORD", secret)
    mock.script_console(
        [{"type": "log", "args": [{"type": "string", "value": secret}], "timestamp": 1.0}]
    )
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": True,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.on_eval("__cdpx_prepare_text", True)
    scenario = scenarios.parse(
        {
            "name": "secret_ref",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "type": {
                        "selector": "#password",
                        "secret_ref": "CHECKOUT_PASSWORD",
                        "clear": True,
                    }
                }
            ],
            "artifacts": ["console", "network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    serialized = json.dumps(result, ensure_ascii=False)
    assert secret not in serialized
    assert result["steps"][0]["result"]["typed"] is True
    assert result["steps"][0]["result"]["value_masked"] is True
    assert scan_canaries(result["evidence_dir"], [secret]) == []
    chars = [item["text"] for item in mock.commands_for("Input.insertText")]
    assert "".join(chars) == secret


def test_scenario_literal_type_is_rejected_before_cdp(mock):
    with pytest.raises(scenarios.ScenarioUsageError, match="text|secret_ref"):
        scenarios.parse(
            {
                "name": "literal_type",
                "context": {"base_url": "http://shop.test"},
                "steps": [{"type": {"selector": "#field", "text": "literal"}}],
            }
        )

    assert mock.commands == []


@pytest.mark.parametrize("fails", [False, True])
def test_scenario_eval_never_persists_result_or_error(mock, tmp_path, fails):
    canary = "scenario-eval-canary-5571"
    if fails:
        mock.on_eval(
            "window.readSensitive",
            {"raw": {"exceptionDetails": {"text": f"failure contained {canary}"}}},
        )
    else:
        mock.on_eval("window.readSensitive", canary)
    scenario = scenarios.parse(
        {
            "name": "eval_result",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"eval": "window.readSensitive()"}],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    assert canary not in json.dumps(result, ensure_ascii=False)
    assert scan_canaries(result["evidence_dir"], [canary]) == []
    step = result["steps"][0]
    if fails:
        assert step["error"] == "***" and step["error_masked"] is True
    else:
        assert step["result"] == {"value": "***", "value_masked": True}


def test_scenario_artifacts_are_private_classified_and_manifested(mock, tmp_path):
    scenario = scenarios.parse(
        {
            "name": "private_evidence",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"goto": "/"}],
            "artifacts": ["screenshot", "console"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client, scenario, evidence_root=tmp_path, settle=0, origins="http://*.test"
        )

    run_dir = Path(result["evidence_dir"])
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run_dir.iterdir() if path.is_file()
    )
    classes = {artifact["type"]: artifact["classification"] for artifact in result["artifacts"]}
    assert classes == {"screenshot": "opaque-restricted", "console": "internal"}
    assert all(not artifact["upload_allowed"] for artifact in result["artifacts"])
    assert {entry["path"] for entry in manifest["artifacts"]} >= {
        "final-screenshot.png",
        "final-console.json",
        "scenario-result.json",
    }


def run_cli(mock, capsys, *argv):
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
    out = capsys.readouterr()
    return code, out.out, out.err


def test_scenario_cli_run_passes_with_json(mock, cli_manifest, capsys, tmp_path):
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
        "--settle",
        "0.01",
    )

    assert code == 0, err
    data = json.loads(out)
    assert data["name"] == "cli_pass"
    assert data["verdict"] == "pass"


def test_scenario_cli_invalid_file_exits_2(mock, cli_manifest, capsys, tmp_path):
    scenario = tmp_path / "bad.yml"
    scenario.write_text("[]\n", encoding="utf-8")

    code, _, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    assert code == 2
    assert "scénario doit être un objet" in err
