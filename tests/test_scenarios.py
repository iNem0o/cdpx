import json
import stat
from pathlib import Path

import pytest

from cdpx import discovery, scenario_compiler, scenarios
from cdpx.action_model import TypeAction
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


def test_passive_profiler_prefers_current_document_over_late_favicon(monkeypatch):
    collector = scenarios.PassiveCollector(orchestration())
    collector.profiler_hits = [
        {
            "url": "http://shop.test/scenario/profiler/baseline",
            "link": "http://shop.test/_profiler/main",
        },
        {
            "url": "http://shop.test/favicon.ico",
            "link": "http://shop.test/_profiler/favicon",
        },
    ]
    monkeypatch.setattr(
        scenarios,
        "_current_url",
        lambda client: "http://shop.test/scenario/profiler/baseline#result",
    )
    monkeypatch.setattr(
        profiler,
        "collect_profiler_report",
        lambda client, hit, **kwargs: hit,
    )

    result = collector.profiler(object(), 1.0)

    assert result["url"] == "http://shop.test/scenario/profiler/baseline"


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


def test_parse_rejects_boolean_network_error_limit_to_match_the_schema():
    with pytest.raises(scenarios.ScenarioUsageError, match="network_errors_max must be an integer"):
        scenarios.parse(
            {
                "name": "boolean_limit",
                "context": {"base_url": "http://shop.test"},
                "steps": [{"goto": "/"}],
                "assertions": [{"network_errors_max": True}],
            }
        )


def test_load_expands_nested_fragments_with_qualified_labels_and_provenance(tmp_path):
    """A scenario compiles nested fragments depth-first at the include site,
    qualifies their labels, and retains a portable source chain for every
    expanded step."""
    fragments = tmp_path / "fragments"
    fragments.mkdir()
    (fragments / "confirm.yml").write_text(
        """
schema: cdpx.scenario-fragment/v1
name: confirmation
steps:
  - label: done
    wait_text: ["#status", "added"]
""",
        encoding="utf-8",
    )
    (fragments / "cart.yml").write_text(
        """
schema: cdpx.scenario-fragment/v1
name: add_to_cart
steps:
  - label: add
    click: "#add"
  - include:
      path: confirm.yml
      as: confirm
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "checkout.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: checkout
context:
  base_url: http://shop.test
steps:
  - label: product
    goto: /product
  - include:
      path: fragments/cart.yml
      as: cart
  - label: finish
    wait_visible: "#receipt"
""",
        encoding="utf-8",
    )

    scenario = scenarios.load(entrypoint)

    assert [step.label for step in scenario.steps] == [
        "product",
        "cart.add",
        "cart.confirm.done",
        "finish",
    ]
    assert [step.index for step in scenario.steps] == [0, 1, 2, 3]
    assert scenario.steps[2].source is not None
    assert scenario.steps[2].source.as_dict() == {
        "path": "fragments/confirm.yml",
        "step": 0,
        "include_chain": [
            {"path": "checkout.yml", "step": 1},
            {"path": "fragments/cart.yml", "step": 1},
        ],
    }
    assert scenario.composition is not None
    assert [item.path for item in scenario.composition.dependencies] == [
        "checkout.yml",
        "fragments/cart.yml",
        "fragments/confirm.yml",
    ]
    assert len(scenario.composition.sha256) == 64


def test_load_interpolates_base_url_with_workspace_placeholder_rules(tmp_path):
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: environment_base_url
context:
  base_url: "${APP_URL:-http://fallback.test}/$$health"
steps:
  - goto: /
""",
        encoding="utf-8",
    )

    resolved = scenarios.load(entrypoint, environ={"APP_URL": "http://shop.test"})
    defaulted = scenarios.load(entrypoint, environ={})

    assert resolved.base_url == "http://shop.test/$health"
    assert defaulted.base_url == "http://fallback.test/$health"


def test_load_rejects_undefined_base_url_variable_without_exposing_values(tmp_path):
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: missing_environment_base_url
context:
  base_url: "${MISSING_APP_URL}"
steps:
  - goto: /
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError) as excinfo:
        scenarios.load(
            entrypoint,
            environ={"UNRELATED_SECRET": "should-never-appear"},
        )

    message = str(excinfo.value)
    assert "context.base_url: undefined environment variable: MISSING_APP_URL" in message
    assert "should-never-appear" not in message


def test_fragment_paths_are_relative_to_the_including_file_not_cwd(tmp_path, monkeypatch):
    """Changing cwd cannot change which fragment an include resolves."""
    bundle = tmp_path / "bundle"
    nested = bundle / "flows"
    fragments = bundle / "fragments"
    other = tmp_path / "other"
    nested.mkdir(parents=True)
    fragments.mkdir()
    other.mkdir()
    (fragments / "shared.yml").write_text(
        """
schema: cdpx.scenario-fragment/v1
name: shared
steps:
  - click: "#shared"
""",
        encoding="utf-8",
    )
    entrypoint = nested / "checkout.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: checkout
context:
  base_url: http://shop.test
steps:
  - include:
      path: ../fragments/shared.yml
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(other)

    scenario = scenarios.load(entrypoint, root=bundle)

    assert [step.label for step in scenario.steps] == ["shared.000-click"]
    assert scenario.steps[0].source is not None
    assert scenario.steps[0].source.path == "fragments/shared.yml"


@pytest.mark.parametrize(
    ("include_path", "message"),
    [
        ("https://example.test/fragment.yml", "remote include forbidden"),
        ("/tmp/fragment.yml", "absolute include forbidden"),
        (r"C:\\scenarios\\fragment.yml", "absolute include forbidden"),
        ("fragments/*.yml", "glob include forbidden"),
        ("../outside.yml", "escapes scenario root"),
    ],
)
def test_load_rejects_non_portable_include_paths(tmp_path, include_path, message):
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    (tmp_path / "outside.yml").write_text("outside: true\n", encoding="utf-8")
    entrypoint = scenario_root / "checkout.yml"
    entrypoint.write_text(
        f"""
schema: cdpx.scenario/v1
name: checkout
context:
  base_url: http://shop.test
steps:
  - include:
      path: {include_path}
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError, match=message):
        scenarios.load(entrypoint, root=scenario_root)


def test_load_rejects_a_fragment_symlink_that_escapes_the_root(tmp_path):
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    outside = tmp_path / "outside.yml"
    outside.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: outside
steps:
  - click: "#outside"
""",
        encoding="utf-8",
    )
    (scenario_root / "linked.yml").symlink_to(outside)
    entrypoint = scenario_root / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: symlink_escape
context:
  base_url: http://shop.test
steps:
  - include: {path: linked.yml}
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError, match="escapes scenario root"):
        scenarios.load(entrypoint, root=scenario_root)


def test_load_rejects_fragment_cycles_with_the_complete_chain(tmp_path):
    (tmp_path / "a.yml").write_text(
        """
schema: cdpx.scenario-fragment/v1
name: a
steps:
  - include:
      path: b.yml
""",
        encoding="utf-8",
    )
    (tmp_path / "b.yml").write_text(
        """
schema: cdpx.scenario-fragment/v1
name: b
steps:
  - include:
      path: a.yml
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "root.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: root
context:
  base_url: http://shop.test
steps:
  - include:
      path: a.yml
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError, match="include cycle") as excinfo:
        scenarios.load(entrypoint)

    assert "a.yml -> b.yml -> a.yml" in str(excinfo.value)


def test_load_requires_unique_include_aliases_and_reserves_with(tmp_path):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: shared
steps:
  - click: "#button"
""",
        encoding="utf-8",
    )
    duplicate = tmp_path / "duplicate.yml"
    duplicate.write_text(
        """
schema: cdpx.scenario/v1
name: duplicate
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
  - include: {path: fragment.yml}
""",
        encoding="utf-8",
    )
    parameterized = tmp_path / "parameterized.yml"
    parameterized.write_text(
        """
schema: cdpx.scenario/v1
name: parameterized
context:
  base_url: http://shop.test
steps:
  - include:
      path: fragment.yml
      with: {selector: "#button"}
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError, match="duplicate include alias: shared"):
        scenarios.load(duplicate)
    with pytest.raises(scenarios.ScenarioUsageError, match=r"unknown field\(s\): with"):
        scenarios.load(parameterized)


@pytest.mark.parametrize(
    "invalid_yaml",
    [
        """schema: cdpx.scenario/v1
name: invalid
context: {base_url: http://shop.test}
steps: []
1: value
""",
        """schema: cdpx.scenario/v1
name: invalid
context: {base_url: http://shop.test}
steps:
  - include:
      path: fragment.yml
      1: value
""",
    ],
)
def test_load_rejects_non_string_yaml_keys_as_usage_errors(tmp_path, invalid_yaml):
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(invalid_yaml, encoding="utf-8")

    with pytest.raises(scenarios.ScenarioUsageError, match="field names must be strings: 1"):
        scenarios.load(entrypoint)


@pytest.mark.parametrize(
    ("fragment_header", "message"),
    [
        ("name: legacy_fragment", "unexpected fragment schema"),
        (
            "schema: cdpx.scenario-fragment/v1\nname: owns_context\ncontext: {}",
            r"unknown field\(s\): context",
        ),
    ],
)
def test_fragments_are_versioned_and_cannot_own_scenario_context(
    tmp_path, fragment_header, message
):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        f"""
{fragment_header}
steps:
  - click: "#button"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: root
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
""",
        encoding="utf-8",
    )

    with pytest.raises(scenarios.ScenarioUsageError, match=message):
        scenarios.load(entrypoint)


def test_load_applies_max_actions_after_fragment_expansion(tmp_path):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: shared
steps:
  - click: "#one"
  - click: "#two"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "root.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: root
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
  - wait_visible: "#done"
""",
        encoding="utf-8",
    )

    with pytest.raises(
        scenarios.ScenarioUsageError,
        match=r"--max-actions budget exceeded: 3 > 2",
    ):
        scenarios.load(entrypoint, max_actions=2)


def test_compiler_enforces_hard_include_depth_limit(tmp_path, monkeypatch):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: fragment
steps:
  - click: "#button"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: depth
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(scenario_compiler, "MAX_INCLUDE_DEPTH", 0)

    with pytest.raises(scenarios.ScenarioUsageError, match="maximum include depth exceeded"):
        scenarios.load(entrypoint)


def test_compiler_enforces_hard_file_limit(tmp_path, monkeypatch):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: fragment
steps:
  - click: "#button"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: files
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(scenario_compiler, "MAX_SCENARIO_FILES", 1)

    with pytest.raises(scenarios.ScenarioUsageError, match="scenario file limit exceeded"):
        scenarios.load(entrypoint)


def test_compiler_enforces_hard_expanded_step_limit(tmp_path, monkeypatch):
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: steps
context:
  base_url: http://shop.test
steps:
  - click: "#one"
  - click: "#two"
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(scenario_compiler, "MAX_EXPANDED_STEPS", 1)

    with pytest.raises(scenarios.ScenarioUsageError, match="step limit exceeded"):
        scenarios.load(entrypoint)


def test_load_reads_a_fragment_once_when_included_multiple_times(tmp_path, monkeypatch):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: shared
steps:
  - click: "#button"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "root.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: root
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml, as: first}
  - include: {path: fragment.yml, as: second}
""",
        encoding="utf-8",
    )
    original = Path.read_bytes
    reads = 0

    def counted(path):
        nonlocal reads
        if path.resolve() == fragment.resolve():
            reads += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)

    scenario = scenarios.load(entrypoint)

    assert [step.label for step in scenario.steps] == ["first.000-click", "second.000-click"]
    assert reads == 1


@pytest.mark.scenario(
    feature="orchestration-control",
    journey="scenario-run",
    scenario_id="orchestration-control.run-declarative-business-scenario",
    target="cdp-mock",
    proof_level="contract",
    proves=["A nominal scenario returns a pass verdict and materializes its proofs in order."],
)
def test_run_scenario_happy_path_with_checkpoint_artifacts(mock, tmp_path, evidence_case):
    """A nominal scenario (goto, click, wait_text) passes on the mock and
    materializes the checkpoint captures then the final artifacts to disk,
    in the declared order."""
    mock.on_eval(
        "getBoundingClientRect",
        json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}),
    )
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


def test_scenario_wait_visible_uses_the_declared_run_timeout(mock, tmp_path, monkeypatch):
    observed = []

    def wait_for_visible(client, selector, timeout=10.0, poll=0.05):
        observed.append((selector, timeout))
        return {"visible": True, "selector": selector, "elapsed_ms": 0.0}

    monkeypatch.setattr(scenarios.nav, "wait_for_visible", wait_for_visible)
    mock.on_eval("querySelector", True)
    scenario = scenarios.parse(
        {
            "name": "bounded-provider-wait",
            "context": {"base_url": "http://shop.test"},
            "steps": [{"wait_visible": "iframe.payment-field"}],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            timeout=30.0,
            settle=0,
            context=orchestration(),
        )

    assert result["verdict"] == "pass"
    assert observed == [("iframe.payment-field", 30.0)]


def test_run_scenario_profiler_artifact_obeys_contract(mock, tmp_path):
    """The mock pins scenario orchestration, persistence and redaction;
    framework compatibility remains owned by the Symfony runtime suite."""
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
    #: no out-of-contract field leaks into the persisted artifact
    assert "signals" not in data
    panel_calls = [
        call
        for call in mock.commands_for("Runtime.evaluate")
        if "__cdpx_profiler_panels" in call["expression"]
    ]
    #: the contract test proves that scenario collection emitted the panel
    #: fetch; the real Symfony suite proves what the collectors return
    assert len(panel_calls) == 1


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
    target="cdp-mock",
    proof_level="contract",
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
    target="cdp-mock",
    proof_level="contract",
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


def test_scenario_type_accepts_key_events_without_exposing_secret(monkeypatch):
    secret = "012345"
    monkeypatch.setenv("CHECKOUT_OTP", secret)
    scenario = scenarios.parse(
        {
            "name": "segmented-code",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "type": {
                        "selector": ".code-digit",
                        "secret_ref": "CHECKOUT_OTP",
                        "mode": "key_events",
                    }
                }
            ],
        }
    )

    prepared = scenarios.prepare(scenario, orchestration())
    action = prepared.operations[0].action
    assert isinstance(action, TypeAction)
    assert action.mode == "key_events"
    assert secret not in json.dumps(scenario.steps[0].value)


def test_scenario_frame_type_checks_origin_and_keeps_secret_out_of_evidence(
    mock, tmp_path, monkeypatch
):
    secret = "4242424242424242"
    monkeypatch.setenv("CHECKOUT_CARD", secret)
    actionable = json.dumps(
        {
            "attached": True,
            "visible": True,
            "enabled": True,
            "stable": True,
            "receives_events": True,
            "editable": False,
            "rect": {"x": 10, "y": 20, "width": 200, "height": 40},
        }
    )
    mock.on_eval("__cdpx_actionability", actionable)
    mock.script_frame("iframe.card-number", "https://frames.checkout.test/card-number.html")
    scenario = scenarios.parse(
        {
            "name": "hosted_card",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {"goto": "/checkout", "capture": ["screenshot"]},
                {
                    "frame_type": {
                        "selector": "iframe.card-number",
                        "frame_origin": "https://frames.checkout.test",
                        "secret_ref": "CHECKOUT_CARD",
                    }
                },
            ],
            "artifacts": ["network"],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            settle=0,
        )

    assert result["verdict"] == "pass"
    assert result["steps"][1]["result"] == {
        "typed": True,
        "value_masked": True,
        "selector": "iframe.card-number",
        "frame_origin": "https://frames.checkout.test",
        "cleared": False,
    }
    assert secret not in json.dumps(result)
    assert scan_canaries(result["evidence_dir"], [secret]) == []
    assert mock.commands_for("Input.insertText")[-1]["text"] == secret
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3
    assert mock.commands_for("DOM.getDocument") == [{"depth": 0}] * 3
    assert (
        mock.commands_for("DOM.querySelector")
        == [{"nodeId": 1, "selector": "iframe.card-number"}] * 3
    )
    assert mock.commands_for("DOM.describeNode") == [{"nodeId": 2}] * 3
    assert mock.commands_for("Page.getFrameTree") == [{}] * 3


def test_scenario_frame_type_selects_one_allowlisted_runtime_candidate(mock, tmp_path, monkeypatch):
    adyen_secret = "adyen-card-secret"
    checkout_secret = "checkout-card-secret"
    monkeypatch.setenv("ADYEN_CARD", adyen_secret)
    monkeypatch.setenv("CHECKOUT_CARD", checkout_secret)
    delays = []
    monkeypatch.setattr(scenarios.inputs.time, "sleep", delays.append)
    actionable = json.dumps(
        {
            "attached": True,
            "visible": True,
            "enabled": True,
            "stable": True,
            "receives_events": True,
            "editable": False,
            "rect": {"x": 10, "y": 20, "width": 200, "height": 40},
        }
    )
    mock.on_eval("__cdpx_actionability", actionable)
    mock.script_frame("iframe.checkout", "https://js.checkout.test/card-number.html")
    scenario = scenarios.parse(
        {
            "name": "hosted_card_candidates",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "frame_type": {
                        "candidates": [
                            {
                                "selector": "iframe.adyen",
                                "frame_origin": "https://adyen.test",
                                "secret_ref": "ADYEN_CARD",
                            },
                            {
                                "selector": "iframe.checkout",
                                "frame_origin": "https://js.checkout.test",
                                "secret_ref": "CHECKOUT_CARD",
                            },
                        ],
                        "mode": "key_events",
                        "key_delay_ms": 30,
                    }
                }
            ],
        }
    )
    assert scenarios.validation_result(scenario)["secret_refs"] == [
        "ADYEN_CARD",
        "CHECKOUT_CARD",
    ]

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            settle=0,
        )

    assert result["verdict"] == "pass"
    assert result["steps"][0]["result"] == {
        "typed": True,
        "value_masked": True,
        "selector": "iframe.checkout",
        "frame_origin": "https://js.checkout.test",
        "cleared": False,
        "mode": "key_events",
    }
    assert adyen_secret not in json.dumps(result)
    assert checkout_secret not in json.dumps(result)
    typed_chars = [
        command["text"]
        for command in mock.commands_for("Input.dispatchKeyEvent")
        if command["type"] == "char"
    ]
    assert "".join(typed_chars) == checkout_secret
    assert mock.commands_for("Input.insertText") == []
    assert delays == [0.03] * len(checkout_secret)


@pytest.mark.parametrize("key_delay_ms", [-1, 251, True])
def test_scenario_frame_type_rejects_unbounded_key_delays(monkeypatch, key_delay_ms):
    monkeypatch.setenv("CHECKOUT_CARD", "card-secret")
    with pytest.raises(scenarios.ScenarioUsageError, match="key_delay_ms"):
        scenarios.parse(
            {
                "name": "invalid_key_delay",
                "context": {"base_url": "http://shop.test"},
                "steps": [
                    {
                        "frame_type": {
                            "selector": "iframe.card-number",
                            "frame_origin": "https://frames.checkout.test",
                            "secret_ref": "CHECKOUT_CARD",
                            "mode": "key_events",
                            "key_delay_ms": key_delay_ms,
                        }
                    }
                ],
            }
        )


def test_scenario_frame_type_rejects_key_delay_without_key_events(monkeypatch):
    monkeypatch.setenv("CHECKOUT_CARD", "card-secret")
    with pytest.raises(scenarios.ScenarioUsageError, match="requires key_events"):
        scenarios.parse(
            {
                "name": "invalid_key_delay_mode",
                "context": {"base_url": "http://shop.test"},
                "steps": [
                    {
                        "frame_type": {
                            "selector": "iframe.card-number",
                            "frame_origin": "https://frames.checkout.test",
                            "secret_ref": "CHECKOUT_CARD",
                            "key_delay_ms": 30,
                        }
                    }
                ],
            }
        )


def test_scenario_frame_type_rejects_clear_even_when_false():
    with pytest.raises(scenarios.ScenarioUsageError, match=r"unknown field.*clear"):
        scenarios.parse(
            {
                "name": "unsupported_frame_clear",
                "context": {"base_url": "http://shop.test"},
                "steps": [
                    {
                        "frame_type": {
                            "selector": "iframe.card-number",
                            "frame_origin": "https://frames.checkout.test",
                            "secret_ref": "CHECKOUT_CARD",
                            "clear": False,
                        }
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "frame_origin",
    [
        "https://*.checkout.test",
        "https://frames.checkout.test/path",
        "https://user@frames.checkout.test",
    ],
)
def test_scenario_frame_type_requires_one_exact_origin(frame_origin):
    with pytest.raises(scenarios.ScenarioUsageError, match=r"one exact HTTP\(S\) origin"):
        scenarios.parse(
            {
                "name": "invalid_frame_origin",
                "context": {"base_url": "http://shop.test"},
                "steps": [
                    {
                        "frame_type": {
                            "selector": "iframe.card-number",
                            "frame_origin": frame_origin,
                            "secret_ref": "CHECKOUT_CARD",
                        }
                    }
                ],
            }
        )


def test_scenario_frame_type_rejects_mismatched_runtime_origin(mock, tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKOUT_CARD", "card-secret")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": False,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.script_frame("iframe.card-number", "https://evil.test/field")
    scenario = scenarios.parse(
        {
            "name": "origin_guard",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "frame_type": {
                        "selector": "iframe.card-number",
                        "frame_origin": "https://frames.checkout.test",
                        "secret_ref": "CHECKOUT_CARD",
                    }
                }
            ],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            settle=0,
        )

    assert result["verdict"] == "fail"
    assert "origin rejected" in result["steps"][0]["error"]
    assert mock.commands_for("Input.insertText") == []
    assert mock.commands_for("Input.dispatchMouseEvent") == []


def test_scenario_frame_type_rechecks_current_frame_url_after_focus(mock, tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKOUT_CARD", "card-secret")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": False,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.script_frame(
        "iframe.card-number",
        "https://frames.checkout.test/field",
        "https://evil.test/redirected",
    )
    scenario = scenarios.parse(
        {
            "name": "origin_drift_after_focus",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "frame_type": {
                        "selector": "iframe.card-number",
                        "frame_origin": "https://frames.checkout.test",
                        "secret_ref": "CHECKOUT_CARD",
                    }
                }
            ],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            settle=0,
        )

    assert result["verdict"] == "fail"
    assert "origin rejected" in result["steps"][0]["error"]
    assert len(mock.commands_for("Input.dispatchMouseEvent")) == 3
    assert mock.commands_for("Input.insertText") == []


def test_scenario_frame_type_rechecks_current_frame_url_during_paced_input(
    mock, tmp_path, monkeypatch
):
    monkeypatch.setenv("CHECKOUT_CARD", "1234")
    monkeypatch.setattr(scenarios.inputs.time, "sleep", lambda _delay: None)
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": False,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.script_frame(
        "iframe.card-number",
        "https://frames.checkout.test/field",
        "https://frames.checkout.test/field",
        "https://evil.test/redirected",
    )
    scenario = scenarios.parse(
        {
            "name": "origin_drift_during_typing",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "frame_type": {
                        "selector": "iframe.card-number",
                        "frame_origin": "https://frames.checkout.test",
                        "secret_ref": "CHECKOUT_CARD",
                        "mode": "key_events",
                        "key_delay_ms": 30,
                    }
                }
            ],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            settle=0,
        )

    typed = [
        command["text"]
        for command in mock.commands_for("Input.dispatchKeyEvent")
        if command["type"] == "char"
    ]
    assert result["verdict"] == "fail"
    assert "origin rejected" in result["steps"][0]["error"]
    assert typed == ["1"]


def test_scenario_timeout_stops_paced_frame_type_before_next_character(mock, tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKOUT_CARD", "12")
    mock.on_eval(
        "__cdpx_actionability",
        json.dumps(
            {
                "attached": True,
                "visible": True,
                "enabled": True,
                "stable": True,
                "receives_events": True,
                "editable": False,
                "rect": {"x": 1, "y": 1, "width": 10, "height": 10},
            }
        ),
    )
    mock.script_frame("iframe.card-number", "https://frames.checkout.test/field")
    scenario = scenarios.parse(
        {
            "name": "paced_frame_timeout",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "frame_type": {
                        "selector": "iframe.card-number",
                        "frame_origin": "https://frames.checkout.test",
                        "secret_ref": "CHECKOUT_CARD",
                        "mode": "key_events",
                        "key_delay_ms": 250,
                    }
                }
            ],
        }
    )

    with client_for(mock) as client:
        result = scenarios.run(
            client,
            scenario,
            evidence_root=tmp_path,
            context=orchestration("http://*.test,https://*.test"),
            timeout=0.2,
            settle=0,
        )

    typed = [
        command["text"]
        for command in mock.commands_for("Input.dispatchKeyEvent")
        if command["type"] == "char"
    ]
    assert result["verdict"] == "fail"
    assert "scenario frame_type timeout" in result["steps"][0]["error"]
    assert typed == ["1"]


@pytest.mark.parametrize(
    "scenario_tail,match",
    [
        ({"steps": [{"capture": ["screenshot"]}]}, "screenshot forbidden after frame_type"),
        ({"artifacts": ["screenshot"]}, "final screenshot forbidden"),
    ],
)
def test_scenario_frame_type_rejects_sensitive_screenshots(scenario_tail, match):
    steps = [
        {
            "frame_type": {
                "selector": "iframe.card-number",
                "frame_origin": "https://frames.checkout.test",
                "secret_ref": "CHECKOUT_CARD",
            }
        }
    ]
    if "steps" in scenario_tail:
        steps.append({"wait_visible": "#done", **scenario_tail["steps"][0]})
    raw = {
        "name": "screenshot_guard",
        "context": {"base_url": "http://shop.test"},
        "steps": steps,
        "artifacts": scenario_tail.get("artifacts", []),
    }

    with pytest.raises(scenarios.ScenarioUsageError, match=match):
        scenarios.parse(raw)


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
    target="cdp-mock",
    proof_level="contract",
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
    assert code == 0, f"stderr={err}\nstdout={out}"
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


def test_scenario_cli_expands_base_url_before_origin_preflight_and_navigation(
    mock, cli_manifest, capsys, tmp_path, monkeypatch
):
    scenario = tmp_path / "scenario.yml"
    scenario.write_text(
        """
name: environment_base_url
context:
  base_url: "${APP_URL}"
steps:
  - goto: child
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_URL", "http://shop.test/root")

    code, out, err = run_cli(
        mock,
        capsys,
        "scenario",
        "run",
        str(scenario),
        "--settle",
        "0",
    )

    assert code == 0, f"stderr={err}\nstdout={out}"
    assert mock.commands_for("Page.navigate") == [{"url": "http://shop.test/root/child"}]


def test_scenario_cli_missing_base_url_variable_is_usage_error_without_cdp_effect(
    mock, cli_manifest, capsys, tmp_path, monkeypatch
):
    scenario = tmp_path / "scenario.yml"
    scenario.write_text(
        """
name: missing_environment_base_url
context:
  base_url: "${MISSING_APP_URL}"
steps:
  - goto: /
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_APP_URL", raising=False)
    monkeypatch.setenv("UNRELATED_SECRET", "should-never-appear")

    code, out, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    assert code == 2
    assert out == ""
    assert "undefined environment variable: MISSING_APP_URL" in err
    assert "should-never-appear" not in err
    assert mock.commands == []


@pytest.mark.parametrize("expanded_url", ["https://outside.example/", "ftp://shop.test/"])
def test_scenario_cli_rejects_expanded_base_url_outside_http_allowlist_before_cdp(
    mock, cli_manifest, capsys, tmp_path, monkeypatch, expanded_url
):
    scenario = tmp_path / "scenario.yml"
    scenario.write_text(
        """
name: rejected_environment_base_url
context:
  base_url: "${APP_URL}"
steps:
  - goto: /
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("APP_URL", expanded_url)

    code, out, err = run_cli(mock, capsys, "scenario", "run", str(scenario))

    assert code == 1
    assert out == ""
    assert "origin rejected" in err or "HTTP(S) origin" in err
    assert mock.commands == []


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


def test_scenario_validate_compiles_without_a_session_or_secret_values(capsys, tmp_path):
    """Structural validation is a browser-free developer command: it
    expands fragments, reports the authority and secret references, and
    does not require the referenced environment values to exist."""
    fragment = tmp_path / "login.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: login
steps:
  - type:
      selector: "#password"
      secret_ref: VALIDATE_MISSING_PASSWORD
  - eval: window.applicationReady
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: validate_composition
context:
  base_url: http://shop.test
steps:
  - include: {path: login.yml, as: buyer}
""",
        encoding="utf-8",
    )

    code = main(["scenario", "validate", str(entrypoint)])
    output = capsys.readouterr()

    assert code == 0, output.err
    assert output.err == ""
    result = json.loads(output.out)
    assert result["schema"] == "cdpx.scenario-validation/v1"
    assert result["ok"] is True
    assert result["name"] == "validate_composition"
    assert result["required_authority"] == "privileged"
    assert result["secret_refs"] == ["VALIDATE_MISSING_PASSWORD"]
    assert [step["label"] for step in result["steps"]] == [
        "buyer.000-type",
        "buyer.001-eval",
    ]
    assert result["dependencies"][1]["path"] == "login.yml"
    assert len(result["sha256"]) == 64
    assert "_cdpx" not in result


def test_scenario_validation_reports_secret_reference_without_environment_value(monkeypatch):
    secret = "validation-secret-canary-4821"
    monkeypatch.setenv("VALIDATION_PASSWORD", secret)
    scenario = scenarios.parse(
        {
            "name": "validation_redaction",
            "context": {"base_url": "http://shop.test"},
            "steps": [
                {
                    "type": {
                        "selector": "#password",
                        "secret_ref": "VALIDATION_PASSWORD",
                    }
                }
            ],
        }
    )

    result = scenarios.validation_result(scenario)

    assert result["secret_refs"] == ["VALIDATION_PASSWORD"]
    assert secret not in json.dumps(result)


def test_scenario_cli_run_executes_composed_steps_and_reports_sources(
    mock, cli_manifest, capsys, tmp_path
):
    """The run path executes the flattened fragment with the same CDP
    protocol as inline steps and exposes qualified labels plus composition
    provenance in its result."""
    mock.on_eval("__cdpx_visible", True)
    mock.on_eval(
        "getBoundingClientRect",
        json.dumps({"x": 0, "y": 0, "width": 10, "height": 10}),
    )
    fragment = tmp_path / "interaction.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: interaction
steps:
  - label: ready
    wait_visible: "#button"
  - label: submit
    click: "#button"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: composed_run
context:
  base_url: http://shop.test
steps:
  - label: page
    goto: /
  - include: {path: interaction.yml, as: form}
""",
        encoding="utf-8",
    )

    code, out, err = run_cli(
        mock,
        capsys,
        "scenario",
        "run",
        str(entrypoint),
        "--settle",
        "0",
    )

    assert code == 0, f"stderr={err}\nstdout={out}"
    result = json.loads(out)
    assert [step["label"] for step in result["steps"]] == ["page", "form.ready", "form.submit"]
    assert result["steps"][1]["source"] == {
        "path": "interaction.yml",
        "step": 0,
        "include_chain": [{"path": "scenario.yml", "step": 1}],
    }
    assert result["composition"]["entrypoint"] == "scenario.yml"
    assert len(mock.commands_for("Page.navigate")) == 1
    assert mock.commands_for("Input.dispatchMouseEvent")


def test_scenario_cli_budget_failure_happens_before_any_cdp_effect(
    mock, cli_manifest, capsys, tmp_path
):
    fragment = tmp_path / "fragment.yml"
    fragment.write_text(
        """
schema: cdpx.scenario-fragment/v1
name: fragment
steps:
  - click: "#one"
  - click: "#two"
""",
        encoding="utf-8",
    )
    entrypoint = tmp_path / "scenario.yml"
    entrypoint.write_text(
        """
schema: cdpx.scenario/v1
name: budget
context:
  base_url: http://shop.test
steps:
  - include: {path: fragment.yml}
""",
        encoding="utf-8",
    )
    manifest = mock.cli_manifest

    code = main(
        [
            "--session",
            str(mock.cli_manifest_path),
            "--run-id",
            manifest.run_id,
            "--target",
            manifest.target_id,
            "--max-actions",
            "1",
            "scenario",
            "run",
            str(entrypoint),
        ]
    )
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert "--max-actions budget exceeded: 2 > 1" in output.err
    assert mock.commands == []
