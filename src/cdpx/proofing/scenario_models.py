"""Typed models for ``*-scenarios.json`` files (schema ``cdpx.scenarios/v2``).

These TypedDicts describe the data contract shared between the pytest
evidence plugin (writer), ``cdpx.proof`` (reader/rewriter), and the HTML
cockpit (consumer of the embedded payload). Most fields are optional
(``total=False``): schema-v1 payloads and synthetic scenarios (e.g. Symfony
``unavailable``) only carry a subset. Only ``nodeid`` is required — it is
the correlation key of the entire pipeline.
"""

from __future__ import annotations

from typing import Any, Required, TypedDict, cast

from cdpx.artifacts import ArtifactError


class ScenarioAssertion(TypedDict, total=False):
    """``#:``-annotated assertion extracted from the test code."""

    line: int
    end_line: int
    text: str
    code_excerpt: str
    kind: str
    status: str


class ScenarioArtifact(TypedDict, total=False):
    """Artifact attached to a scenario (capture, log, json, cast, …)."""

    type: str
    label: str
    path: str
    bytes: int
    mime: str
    created_at: str
    excerpt: str
    # Inline fields added at render time (inline_scenario_artifacts): the
    # content travels in the HTML payload, never in the JSON rewritten to disk.
    inline_content: str
    inline_skipped: str
    truncated: bool


class Scenario(TypedDict, total=False):
    """Documented scenario from a pytest run (or synthetic, e.g. Symfony)."""

    nodeid: Required[str]
    suite: str
    title: str
    area: str
    feature: str
    journey: str
    scenario_id: str
    proves: list[str]
    intent: str
    intent_line: int
    assertions: list[ScenarioAssertion]
    failed_line: int
    started_at: str
    duration_s: float
    status: str
    phase: str
    message: str
    stdout: str
    stderr: str
    artifacts: list[ScenarioArtifact]
    scenario: str
    ui_text: str
    report_text: str
    given: str
    when: str
    then: str
    expected_proofs: list[str]


class ScenarioFile(TypedDict, total=False):
    """Versioned root of a ``*-scenarios.json`` file.

    ``schema`` is absent from schema-v1 payloads and is accepted by readers;
    any value other than ``cdpx.scenarios/v2`` is an error.
    """

    schema: str
    suite: str
    generated_at: str
    count: int
    scenarios: list[Scenario]


class ScenarioTotals(TypedDict):
    """Totals aggregated by ``scenario_totals`` for the cockpit hero."""

    scenarios: int
    unit: int
    integration: int
    e2e: int
    symfony: int
    screenshots: int
    missing_e2e_screenshots: list[str]


class ScenarioEvidence(TypedDict):
    """Aggregated evidence returned by ``load_scenario_evidence``."""

    suites: dict[str, list[Scenario]]
    files: list[str]
    totals: ScenarioTotals


_SCENARIO_STRINGS = (
    "suite",
    "title",
    "area",
    "feature",
    "journey",
    "scenario_id",
    "intent",
    "started_at",
    "status",
    "phase",
    "message",
    "stdout",
    "stderr",
    "scenario",
    "ui_text",
    "report_text",
    "given",
    "when",
    "then",
)
_SCENARIO_INTS = ("intent_line", "failed_line")
_SCENARIO_STRING_LISTS = ("proves", "expected_proofs")
_ASSERTION_STRINGS = ("text", "code_excerpt", "kind", "status")
_ASSERTION_INTS = ("line", "end_line")
_ARTIFACT_STRINGS = (
    "type",
    "label",
    "path",
    "mime",
    "created_at",
    "excerpt",
    "inline_content",
    "inline_skipped",
)


def validated_scenario_file(payload: Any, *, source: str, expected_schema: str) -> ScenarioFile:
    """Validate raw JSON and construct a fresh strict ``ScenarioFile``."""

    if not isinstance(payload, dict):
        raise ArtifactError(f"{source}: root expected as JSON object, got {type(payload).__name__}")
    schema = payload.get("schema")
    if schema is not None and schema != expected_schema:
        raise ArtifactError(
            f"{source}: unexpected scenarios schema: expected={expected_schema}, got={schema}"
        )
    raw_scenarios = payload.get("scenarios", [])
    if not isinstance(raw_scenarios, list):
        raise ArtifactError(
            f"{source}: `scenarios` must be a list, got {type(raw_scenarios).__name__}"
        )
    normalized: dict[str, Any] = {
        "scenarios": [
            _validated_scenario(value, source=source, index=index)
            for index, value in enumerate(raw_scenarios)
        ]
    }
    if schema is not None:
        normalized["schema"] = schema
    _copy_optional_scalars(payload, normalized, ("suite", "generated_at"), str, source)
    _copy_optional_scalars(payload, normalized, ("count",), int, source)
    return cast(ScenarioFile, normalized)


def _validated_scenario(value: Any, *, source: str, index: int) -> Scenario:
    where = f"{source}: scenarios[{index}]"
    if not isinstance(value, dict):
        raise ArtifactError(f"{where} must be a JSON object")
    nodeid = value.get("nodeid")
    if not isinstance(nodeid, str) or not nodeid:
        raise ArtifactError(f"{where} without a non-empty textual `nodeid`")
    normalized: dict[str, Any] = {"nodeid": nodeid}
    _copy_optional_scalars(value, normalized, _SCENARIO_STRINGS, str, where)
    _copy_optional_scalars(value, normalized, _SCENARIO_INTS, int, where)
    if "duration_s" in value:
        duration = value["duration_s"]
        if isinstance(duration, bool) or not isinstance(duration, int | float):
            raise ArtifactError(f"{where}: `duration_s` must be numeric")
        normalized["duration_s"] = float(duration)
    for field in _SCENARIO_STRING_LISTS:
        if field in value:
            normalized[field] = _string_list(value[field], f"{where}: `{field}`")
    if "assertions" in value:
        normalized["assertions"] = _validated_assertions(value["assertions"], where)
    if "artifacts" in value:
        normalized["artifacts"] = _validated_artifacts(value["artifacts"], where)
    return cast(Scenario, normalized)


def _validated_assertions(value: Any, where: str) -> list[ScenarioAssertion]:
    if not isinstance(value, list):
        raise ArtifactError(f"{where}: `assertions` must be a list")
    assertions: list[ScenarioAssertion] = []
    for index, raw in enumerate(value):
        item_where = f"{where}: assertions[{index}]"
        if not isinstance(raw, dict):
            raise ArtifactError(f"{item_where} must be an object")
        normalized: dict[str, Any] = {}
        _copy_optional_scalars(raw, normalized, _ASSERTION_STRINGS, str, item_where)
        _copy_optional_scalars(raw, normalized, _ASSERTION_INTS, int, item_where)
        assertions.append(cast(ScenarioAssertion, normalized))
    return assertions


def _validated_artifacts(value: Any, where: str) -> list[ScenarioArtifact]:
    if not isinstance(value, list):
        raise ArtifactError(f"{where}: `artifacts` must be a list")
    artifacts: list[ScenarioArtifact] = []
    for index, raw in enumerate(value):
        item_where = f"{where}: artifacts[{index}]"
        if not isinstance(raw, dict):
            raise ArtifactError(f"{item_where} must be an object")
        normalized: dict[str, Any] = {}
        _copy_optional_scalars(raw, normalized, _ARTIFACT_STRINGS, str, item_where)
        _copy_optional_scalars(raw, normalized, ("bytes",), int, item_where)
        _copy_optional_scalars(raw, normalized, ("truncated",), bool, item_where)
        artifacts.append(cast(ScenarioArtifact, normalized))
    return artifacts


def _copy_optional_scalars(
    source: dict[str, Any],
    target: dict[str, Any],
    fields: tuple[str, ...],
    expected_type: type,
    where: str,
) -> None:
    for field in fields:
        if field not in source:
            continue
        value = source[field]
        if expected_type is int and isinstance(value, bool):
            valid = False
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            raise ArtifactError(f"{where}: `{field}` must be of type {expected_type.__name__}")
        target[field] = value


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ArtifactError(f"{where} must be a list of strings")
    return list(value)
