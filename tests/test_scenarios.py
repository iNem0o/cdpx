import json
import stat
from pathlib import Path

import pytest

from cdpx import discovery, scenarios
from cdpx.artifacts import scan_canaries
from cdpx.cli import main
from cdpx.client import CDPClient
from cdpx.orchestration import OrchestrationContext
from cdpx.primitives import profiler


def client_for(mock):
    target_id = next(iter(mock.targets))
    mock.targets[target_id]["url"] = "http://shop.test/"
    target = discovery.pick_page("127.0.0.1", mock.http_port, target_id)
    return CDPClient(target["webSocketDebuggerUrl"], timeout=5)


def orchestration(origins: str = "http://*.test") -> OrchestrationContext:
    return OrchestrationContext.from_origins(origins)


def test_parse_scenario_with_step_capture():
    """The parser turns a complete declarative scenario into a typed object
    that preserves the emulation context and the captures attached to each step."""
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

    #: the name, the context's emulation, and the per-step captures survive
    #: parsing with no loss and no overwriting default value
    assert scenario.name == "checkout_guest_add_to_cart"
    assert scenario.emulation == "mobile"
    assert scenario.steps[0].capture == ["screenshot", "console"]


def test_parse_rejects_unknown_field():
    """An unknown key in the YAML is a usage error right at parsing: a
    typo cannot silently disable a step or an assertion."""
    #: the rejection names the faulty field before any contact with a browser
    with pytest.raises(scenarios.ScenarioUsageError, match="unknown field"):
        scenarios.parse(
            {
                "name": "bad",
                "context": {"base_url": "http://x.test"},
                "steps": [{"goto": "/"}],
                "unexpected": True,
            }
        )


def test_parse_rejects_cleartext_type_pair():
    """The [selector, text] form of a type step would put the secret in
    the clear in the YAML: it is refused right at validation, step
    position included, before any preparation or connection."""
    #: the refusal happens at parsing, localized, and names the secret_ref requirement
    with pytest.raises(
        scenarios.ScenarioUsageError, match=r"steps\[0\]\.type requires an object with secret_ref"
    ):
        scenarios.parse(
            {
                "name": "cleartext",
                "context": {"base_url": "http://shop.test"},
                "steps": [{"type": ["#password", "hunter2"]}],
            }
        )


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=["A nominal scenario returns a pass verdict and materializes its proofs in order."],
)
def test_run_scenario_happy_path_with_checkpoint_artifacts(mock, tmp_path, evidence_case):
    """A nominal scenario (goto, click, wait_text) passes on the mock and
    materializes the checkpoint captures then the final artifacts to disk,
    in the declared order."""
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
            client, scenario, evidence_root=tmp_path, settle=0.01, context=orchestration()
        )

    #: pass verdict with no findings at all: the three steps and the three
    #: observability assertions all succeeded
    assert result["verdict"] == "pass"
    assert result["findings"] == []
    assert len(result["steps"]) == 3
    artifact_types = [artifact["type"] for artifact in result["artifacts"]]
    #: the checkpoint captures precede the final artifacts, in the order
    #: the scenario requested them
    assert artifact_types == [
        "screenshot",
        "network",
        "console",
        "screenshot",
        "console",
        "network",
    ]
    #: each artifact announced in the result actually exists on disk
    assert all(Path(artifact["path"]).exists() for artifact in result["artifacts"])

    if evidence_case is not None:
        for index, artifact in enumerate(result["artifacts"]):
            label = f"{artifact['type']} #{index}"
            if artifact["type"] == "screenshot":
                evidence_case.attach_screenshot(artifact["path"], label=label)
            else:
                evidence_case.attach_file(artifact["path"], label)


def test_scenario_wait_visible_requires_visibility_not_only_dom_attachment(mock, tmp_path):
    """wait_visible is not satisfied by an element merely attached to the
    DOM: it keeps probing the page until visibility is actually acquired."""
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
            context=orchestration(),
        )

    #: the step only succeeds once visibility is actually observed
    assert result["verdict"] == "pass"
    assert result["steps"][0]["result"]["visible"] is True
    visibility_checks = [
        item
        for item in mock.commands_for("Runtime.evaluate")
        if "__cdpx_visible" in item["expression"]
    ]
    #: two visibility probes were emitted: the first response (invisible)
    #: did force a new attempt instead of concluding
    assert len(visibility_checks) == 2


def test_run_scenario_profiler_artifact_parses_real_panels(mock, tmp_path):
    """The profiler artifact follows the network's X-Debug-Token link,
    reads the real Symfony panels (HTML fixtures) and persists only
    structured metrics from them — never the token itself."""
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
                "html": (fixtures / f"{profiler.PANEL_SOURCES[key]}.html").read_text(
                    encoding="utf-8"
                ),
            }
            for key in profiler.ALL_PANELS
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
            client, scenario, evidence_root=tmp_path, settle=0.01, context=orchestration()
        )

    assert result["verdict"] == "pass"
    (artifact,) = [a for a in result["artifacts"] if a["type"] == "profiler"]
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    #: the proof attests that a token existed without ever writing its value
    assert data["token_present"] is True
    assert "token" not in data
    #: the raw HTML panels are reduced to actionable metrics (SQL query
    #: count, resolved route)
    assert data["panels"]["db"]["queries"] == 6
    assert data["panels"]["router"]["route"] == "scenario_profiler"
    #: no out-of-contract field leaks into the persisted artifact
    assert "signals" not in data


def test_run_scenario_failure_still_captures_checkpoint_and_final(mock, tmp_path):
    """A step's failure does not sacrifice the proof: the captures of the
    failed checkpoint and the final artifacts are still produced, and the
    finding designates the faulty step."""
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
            client, scenario, evidence_root=tmp_path, settle=0.01, context=orchestration()
        )

    #: the click on a missing element yields a fail verdict without raising
    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    #: the checkpoint capture and the final screenshot exist despite the
    #: interruption, and the finding does incriminate the step
    assert [artifact["type"] for artifact in result["artifacts"]] == ["console", "screenshot"]
    assert result["findings"][0]["code"] == "step_failed"


def test_run_scenario_console_and_network_assertions_fail(mock, tmp_path):
    """Observability assertions see the passive events: a console error
    and a 5xx response each suffice to produce their own dedicated finding."""
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
            client, scenario, evidence_root=tmp_path, settle=0.01, context=orchestration()
        )

    #: each violated assertion produces its own identifiable finding,
    #: instead of an undifferentiated global failure
    assert result["verdict"] == "fail"
    assert [finding["code"] for finding in result["findings"]] == [
        "assertion_no_console_errors",
        "assertion_network_errors_max",
    ]


def test_final_drain_precedes_console_and_network_assertions(mock, tmp_path, monkeypatch):
    """Events that arrive only at the very last drain still count in the
    assertions AND in the artifacts: no blind window between the last step
    and the judgment."""

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
            client, scenario, evidence_root=tmp_path, settle=0, context=orchestration()
        )

    #: the console error and the 500 injected after the last step are
    #: still counted by both assertions
    assert result["verdict"] == "fail"
    assert [record["actual"] for record in result["assertions"]] == [1, 1]
    artifacts = {
        artifact["type"]: json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        for artifact in result["artifacts"]
    }
    #: the written artifacts tell the same story as the verdict: no
    #: possible divergence between the proof and the judgment
    assert artifacts["console"]["errors"] == result["assertions"][0]["actual"]
    assert artifacts["network"]["summary"]["errors_4xx_5xx"] == result["assertions"][1]["actual"]


def test_scenario_network_evidence_redacts_sensitive_headers(mock, tmp_path):
    """The network artifact written to disk redacts headers carrying
    secrets (Authorization, Set-Cookie) while keeping harmless headers
    readable for diagnosis."""
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
            client, scenario, evidence_root=tmp_path, settle=0.01, context=orchestration()
        )
    artifact = next(a for a in result["artifacts"] if a["type"] == "network")
    data = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    headers = data["requests"][0]["headers"]
    #: only sensitive headers are replaced by the redaction marker;
    #: Content-Type stays intact for analysis
    assert headers == {
        "Authorization": "***",
        "Set-Cookie": "***",
        "Content-Type": "text/html",
    }


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=["A redirect off the allowlist stops the scenario before capture or mutation."],
)
def test_strict_scenario_stops_after_redirect_before_next_mutation_or_capture(mock, tmp_path):
    """A redirect to a non-allowed origin stops the scenario before any
    subsequent capture or mutation: guard against sending actions or
    proofs to an unexpected domain."""
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
            context=orchestration("http://shop.test"),
            settle=0,
        )

    #: the origin refusal is an explicit finding, not a generic failure
    assert result["verdict"] == "fail"
    assert result["steps"][0]["ok"] is False
    assert [finding["code"] for finding in result["findings"]] == ["origin_refused"]
    #: after the forbidden redirect, neither capture nor click were emitted:
    #: the stop precedes any interaction with the compromised page
    assert result["artifacts"] == []
    assert mock.commands_for("Input.dispatchMouseEvent") == []


@pytest.mark.scenario(
    feature="state-session",
    journey="read-session",
    scenario_id="state-session.redact-sensitive-session-data",
    proves=["A secret_ref typed on the CDP side stays absent from the result and any proof."],
)
def test_scenario_secret_ref_never_reaches_outputs_or_evidence(mock, tmp_path, monkeypatch):
    """A keystroke via secret_ref transmits the secret value to the
    browser while keeping it out of the JSON result and every proof file,
    even when the page echoes it in console."""
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

    context = orchestration()
    prepared = scenarios.prepare(scenario, context)
    monkeypatch.setenv("CHECKOUT_PASSWORD", "changed-after-preflight")
    with client_for(mock) as client:
        result = scenarios.run(
            client,
            prepared,
            evidence_root=tmp_path,
            settle=0,
        )

    serialized = json.dumps(result, ensure_ascii=False)
    #: the result announces the keystroke as masked and the canary is
    #: absent from its entire serialization
    assert secret not in serialized
    assert result["steps"][0]["result"]["typed"] is True
    assert result["steps"][0]["result"]["value_masked"] is True
    #: no file in the evidence directory contains the canary
    assert scan_canaries(result["evidence_dir"], [secret]) == []
    chars = [item["text"] for item in mock.commands_for("Input.insertText")]
    #: the secret value was nonetheless typed in full on the CDP side:
    #: masking did not amputate the input
    assert "".join(chars) == secret


def test_scenario_literal_type_is_rejected_before_cdp(mock):
    """A literal text in a type step is forbidden right at parsing: only
    the secret_ref path exists, and the rejection emits nothing to Chrome."""
    #: the refusal happens at scenario analysis, before any session
    with pytest.raises(scenarios.ScenarioUsageError, match="text|secret_ref"):
        scenarios.parse(
            {
                "name": "literal_type",
                "context": {"base_url": "http://shop.test"},
                "steps": [{"type": {"selector": "#field", "text": "literal"}}],
            }
        )

    #: no CDP command was emitted during the rejection
    assert mock.commands == []


@pytest.mark.parametrize("fails", [False, True])
def test_scenario_eval_never_persists_result_or_error(mock, tmp_path, fails):
    """The return of an eval step — value or exception message — is masked
    everywhere: JSON output and proof files, regardless of the
    evaluation's outcome."""
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
            client, scenario, evidence_root=tmp_path, settle=0, context=orchestration()
        )

    #: the canary returned by the page leaks neither into the serialized
    #: result nor into the evidence files
    assert canary not in json.dumps(result, ensure_ascii=False)
    assert scan_canaries(result["evidence_dir"], [canary]) == []
    step = result["steps"][0]
    #: whichever path (success or exception), the exposed field is the
    #: masking marker accompanied by its explicit flag
    if fails:
        assert step["error"] == "***" and step["error_masked"] is True
    else:
        assert step["result"] == {"value": "***", "value_masked": True}


def test_scenario_artifacts_are_private_classified_and_manifested(mock, tmp_path):
    """A run's artifacts are private to the owner, classified according to
    their sensitivity, forbidden from upload, and all inventoried in the
    evidence directory's manifest."""
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
            client, scenario, evidence_root=tmp_path, settle=0, context=orchestration()
        )

    run_dir = Path(result["evidence_dir"])
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    #: the evidence directory and each of its files are unreadable to any
    #: other user on the machine
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600 for path in run_dir.iterdir() if path.is_file()
    )
    classes = {artifact["type"]: artifact["classification"] for artifact in result["artifacts"]}
    #: the screenshot (opaque content) and the console each receive the
    #: appropriate classification, and nothing is declared uploadable
    assert classes == {"screenshot": "opaque-restricted", "console": "internal"}
    assert all(not artifact["upload_allowed"] for artifact in result["artifacts"])
    #: the manifest references every produced artifact, result included
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


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    proves=["The scenario run subcommand returns exit 0 and a single JSON object on stdout."],
)
def test_scenario_cli_run_passes_with_json(mock, cli_manifest, capsys, tmp_path, evidence_case):
    """The scenario run subcommand reads a YAML file, executes the
    scenario on the supervised session, and honors the CLI contract:
    exit 0 and a single JSON object carrying the verdict on stdout."""
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

    #: exit 0 and a stdout parsable as JSON: the agent pipe can consume
    #: the verdict without cleanup
    assert code == 0, err
    data = json.loads(out)
    assert data["name"] == "cli_pass"
    assert data["verdict"] == "pass"

    if evidence_case is not None:
        evidence_case.attach_command_output(
            "scenario run (in-process)",
            ["cdpx", "scenario", "run", scenario.name, "--settle", "0.01"],
            out,
            err,
            code,
        )


def test_scenario_cli_invalid_file_exits_2(mock, cli_manifest, capsys, tmp_path):
    """An invalid scenario file is treated as a usage error: exit 2 and
    the diagnostic on stderr, never on stdout."""
    scenario = tmp_path / "bad.yml"
    scenario.write_text("[]\n", encoding="utf-8")

    code, _, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    #: code 2 reserves the exit for usage errors, and the explanation
    #: goes out on the diagnostic channel
    assert code == 2
    assert "scenario must be a YAML object" in err
