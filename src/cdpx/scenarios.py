"""Declarative business scenario runner.

A scenario is a bounded YAML orchestration layer over existing cdpx primitives.
It deliberately reuses the primitive contracts instead of becoming a shell-like
macro language.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from cdpx.action_model import (
    BrowserAction,
    ClickAction,
    EvalAction,
    GotoAction,
    KeyAction,
    TypeAction,
)
from cdpx.artifacts import ArtifactClassification, ArtifactEntry, SecureArtifactWriter
from cdpx.cdp_types import CDPEvent
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.orchestration import OrchestrationContext
from cdpx.policy import PolicyError, assert_url_allowed, parse_exact_origin
from cdpx.primitives import actions, capture, dev, emulation, inputs, js, nav, profiler
from cdpx.runtime_config import ConfigurationError, interpolate_environment_text
from cdpx.security import (
    MASK,
    RedactionContext,
    redact_headers,
    redact_text,
    redact_tree,
    redact_url,
)

STEP_ACTIONS = {
    "goto",
    "wait_visible",
    "click",
    "type",
    "frame_type",
    "key",
    "eval",
    "wait_text",
}
STEP_KEYS = STEP_ACTIONS | {"label", "capture"}
ASSERTIONS = {"no_console_errors", "network_errors_max", "text_contains"}
ARTIFACTS = {"screenshot", "console", "network", "profiler"}
SCENARIO_SCHEMA = "cdpx.scenario/v1"
FRAGMENT_SCHEMA = "cdpx.scenario-fragment/v1"
VALIDATION_SCHEMA = "cdpx.scenario-validation/v1"
ACTION_ERRORS = (
    ValueError,
    TimeoutError,
    CDPError,
    CDPTimeout,
    js.JSException,
    inputs.ElementNotFound,
)


class ScenarioUsageError(ValueError):
    """Invalid scenario file or CLI-level scenario invocation."""


@dataclass(frozen=True)
class IncludeSite:
    path: str
    step: int

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "step": self.step}


@dataclass(frozen=True)
class StepSource:
    path: str
    step: int
    include_chain: tuple[IncludeSite, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "step": self.step,
            "include_chain": [site.as_dict() for site in self.include_chain],
        }


@dataclass(frozen=True)
class ScenarioDependency:
    path: str
    sha256: str
    kind: Literal["scenario", "fragment"]

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256, "kind": self.kind}


@dataclass(frozen=True)
class ScenarioComposition:
    entrypoint: str
    sha256: str
    dependencies: tuple[ScenarioDependency, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "entrypoint": self.entrypoint,
            "sha256": self.sha256,
            "dependencies": [dependency.as_dict() for dependency in self.dependencies],
        }


@dataclass
class ScenarioStep:
    index: int
    verb: str
    value: Any
    label: str
    capture: list[str] = field(default_factory=list)
    source: StepSource | None = None


@dataclass
class Scenario:
    name: str
    base_url: str
    emulation: str | None
    steps: list[ScenarioStep]
    assertions: list[dict[str, Any]]
    artifacts: list[str]
    composition: ScenarioComposition | None = None


@dataclass(frozen=True)
class ScenarioOperation:
    step: ScenarioStep
    action: BrowserAction | None = None
    wait_kind: Literal["visible", "text"] | None = None
    selector: str | None = None
    expected: str | None = None
    frame_origin: str | None = None
    frame_candidates: tuple[tuple[str, str, str], ...] = ()
    frame_mode: str = "insert_text"
    frame_key_delay_ms: int = 0


@dataclass(frozen=True)
class PreparedScenario:
    scenario: Scenario
    context: OrchestrationContext
    operations: tuple[ScenarioOperation, ...]


@dataclass
class ScenarioRun:
    name: str
    evidence_dir: Path
    writer: SecureArtifactWriter = field(repr=False)
    findings: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    assertions: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    last_url: str | None = None
    composition: ScenarioComposition | None = None

    def finding(
        self,
        code: str,
        message: str,
        *,
        severity: str = "error",
        step: str | None = None,
    ) -> None:
        item = {"severity": severity, "code": code, "message": message}
        if step:
            item["step"] = step
        self.findings.append(item)

    @property
    def verdict(self) -> str:
        return "fail" if any(f["severity"] == "error" for f in self.findings) else "pass"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "verdict": self.verdict,
            "findings": self.findings,
            "evidence_dir": str(self.evidence_dir),
            "steps": self.steps,
            "assertions": self.assertions,
            "artifacts": self.artifacts,
        }
        if self.composition is not None:
            result["composition"] = self.composition.as_dict()
        return result


class PassiveCollector:
    def __init__(self, context: OrchestrationContext) -> None:
        self.context = context
        self.redaction = context.redaction
        self.console_entries: list[dict[str, Any]] = []
        self.requests: dict[str, dict[str, Any]] = {}
        self.profiler_hits: list[dict[str, Any]] = []

    def enable(self, client: CDPClient) -> None:
        client.send("Runtime.enable")
        client.send("Network.enable")
        self.drain(client, 0)

    def drain(self, client: CDPClient, settle: float) -> None:
        events = client.collect_events(
            settle,
            capture.CONSOLE_EVENTS + dev.NET_EVENTS + _NET_EVENTS,
        )
        self._ingest(events)

    def console(self) -> dict[str, Any]:
        errors = sum(
            1
            for entry in self.console_entries
            if entry["type"] == "error" or entry["kind"] == "exception"
        )
        return {
            "entries": self.console_entries,
            "count": len(self.console_entries),
            "errors": errors,
        }

    def network(self) -> dict[str, Any]:
        requests = list(self.requests.values())
        return {"requests": requests, "summary": _network_summary(requests)}

    def profiler(self, client: CDPClient, timeout: float) -> dict[str, Any] | None:
        if not self.profiler_hits:
            return None
        page_url = _current_url(client)
        comparable_page_url = redact_url(
            page_url,
            context=self.redaction,
            path="$.network.url",
        )
        # Browser subresources (notably /favicon.ico) can also carry Symfony's
        # profiler headers. Prefer the hit for the document that is currently
        # displayed instead of blindly taking the most recent response. Hits
        # are already redacted during ingestion, so compare against the same
        # normalized representation of the current URL.
        hit = next(
            (
                candidate
                for candidate in reversed(self.profiler_hits)
                if _same_page_url(candidate.get("url"), comparable_page_url)
            ),
            self.profiler_hits[-1],
        )
        return profiler.collect_profiler_report(
            client,
            hit,
            timeout=timeout,
            context=self.context,
            page_url=page_url,
        )

    def _ingest(self, events: list[CDPEvent]) -> None:
        self.console_entries.extend(
            capture.console_entries(
                _events(events, capture.CONSOLE_EVENTS),
                context=self.redaction,
            )
        )
        for ev in _events(events, _NET_EVENTS):
            params = ev.get("params", {})
            request_id = params.get("requestId")
            if not request_id:
                continue
            entry = self.requests.setdefault(request_id, {"requestId": request_id})
            if ev["method"] == "Network.requestWillBeSent":
                request = params.get("request", {})
                request_url = request.get("url")
                entry["url"] = (
                    redact_url(request_url, context=self.redaction, path="$.network.url")
                    if isinstance(request_url, str)
                    else request_url
                )
                entry["method"] = request.get("method")
                entry["resourceType"] = params.get("type")
                # A redirect does not emit responseReceived: its profiler
                # token only exists in redirectResponse.
                hit = dev.find_profiler_hit([ev], entry.get("url") or "")
                if hit:
                    self.profiler_hits.append(hit)
            elif ev["method"] == "Network.responseReceived":
                response = params.get("response", {})
                headers = redact_headers(
                    response.get("headers", {}),
                    context=self.redaction,
                    path="$.network.headers",
                )
                response_url = response.get("url")
                if isinstance(response_url, str):
                    entry["url"] = redact_url(
                        response_url,
                        context=self.redaction,
                        path="$.network.url",
                    )
                entry["status"] = response.get("status")
                entry["mimeType"] = response.get("mimeType")
                entry["headers"] = headers
                hit = dev.find_profiler_hit([ev], entry.get("url") or "")
                if hit:
                    self.profiler_hits.append(hit)
            elif ev["method"] == "Network.loadingFinished":
                entry["encodedBytes"] = params.get("encodedDataLength")
            elif ev["method"] == "Network.loadingFailed":
                entry["failed"] = params.get("errorText", "failed")


_NET_EVENTS = (
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
)


def _same_page_url(candidate: Any, page_url: str) -> bool:
    if not isinstance(candidate, str):
        return False
    normalized_candidate = redact_url(candidate)
    normalized_page = redact_url(page_url)
    return (
        urllib.parse.urldefrag(normalized_candidate).url
        == urllib.parse.urldefrag(normalized_page).url
    )


def load(
    path: str | Path,
    *,
    root: str | Path | None = None,
    max_actions: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> Scenario:
    from cdpx.scenario_compiler import compile_scenario

    return compile_scenario(path, root=root, max_actions=max_actions, environ=environ)


def parse(
    raw: Any,
    *,
    source: Path | None = None,
    step_sources: list[StepSource] | None = None,
    composition: ScenarioComposition | None = None,
    environ: Mapping[str, str] | None = None,
) -> Scenario:
    where = f"{source}: " if source else ""
    if not isinstance(raw, dict):
        raise ScenarioUsageError(f"{where}scenario must be a YAML object")
    _unknown(raw, {"schema", "name", "context", "steps", "assertions", "artifacts"}, where)
    schema = raw.get("schema")
    if schema is not None and schema != SCENARIO_SCHEMA:
        raise ScenarioUsageError(f"{where}unexpected scenario schema: {schema}")
    name = _required_str(raw, "name", where)
    context = _required_dict(raw, "context", where)
    _unknown(context, {"base_url", "emulation"}, f"{where}context.")
    raw_base_url = _required_str(context, "base_url", f"{where}context.")
    if environ is None:
        environ = os.environ
    try:
        base_url = interpolate_environment_text(
            raw_base_url,
            f"{where}context.base_url",
            environ,
        )
    except ConfigurationError as error:
        raise ScenarioUsageError(str(error)) from error
    if not base_url:
        raise ScenarioUsageError(f"{where}context.base_url must be a non-empty string")
    emulation_preset = context.get("emulation")
    if emulation_preset is not None and emulation_preset not in emulation.PRESETS:
        raise ScenarioUsageError(f"{where}context.emulation unknown: {emulation_preset}")
    steps = _parse_steps(raw.get("steps"), where, sources=step_sources)
    assertions = _parse_assertions(raw.get("assertions", []), where)
    artifacts = _parse_artifacts(raw.get("artifacts", []), where, "artifacts")
    _validate_sensitive_captures(steps, artifacts, where)
    return Scenario(
        name=name,
        base_url=base_url,
        emulation=emulation_preset,
        steps=steps,
        assertions=assertions,
        artifacts=artifacts,
        composition=composition,
    )


def validation_result(scenario: Scenario) -> dict[str, Any]:
    """Return a secret-free, browser-free plan for a compiled scenario."""
    rank = {"observation": 0, "interaction": 1, "privileged": 2}
    authority = "observation"
    secret_refs: set[str] = set()
    steps = []
    for step in scenario.steps:
        candidate = "observation"
        if step.verb in {"click", "type", "frame_type", "key"}:
            candidate = "interaction"
        elif step.verb == "eval":
            candidate = "privileged"
        if rank[candidate] > rank[authority]:
            authority = candidate
        if step.verb in {"type", "frame_type"} and isinstance(step.value, dict):
            secret_ref = step.value.get("secret_ref")
            if isinstance(secret_ref, str) and secret_ref:
                secret_refs.add(secret_ref)
            candidates = step.value.get("candidates")
            if isinstance(candidates, list):
                for frame_candidate in candidates:
                    candidate_secret_ref = frame_candidate.get("secret_ref")
                    if isinstance(candidate_secret_ref, str) and candidate_secret_ref:
                        secret_refs.add(candidate_secret_ref)
        item: dict[str, Any] = {
            "index": step.index,
            "label": step.label,
            "verb": step.verb,
        }
        if step.source is not None:
            item["source"] = step.source.as_dict()
        steps.append(item)
    if (
        scenario.emulation
        or "profiler" in scenario.artifacts
        or any("profiler" in step.capture for step in scenario.steps)
    ):
        authority = "privileged"
    result: dict[str, Any] = {
        "schema": VALIDATION_SCHEMA,
        "ok": True,
        "name": scenario.name,
        "required_authority": authority,
        "secret_refs": sorted(secret_refs),
        "step_count": len(scenario.steps),
        "steps": steps,
    }
    if scenario.composition is not None:
        result.update(
            {
                "entrypoint": scenario.composition.entrypoint,
                "sha256": scenario.composition.sha256,
                "dependencies": [
                    dependency.as_dict() for dependency in scenario.composition.dependencies
                ],
            }
        )
    return result


def run(
    client: CDPClient,
    scenario: Scenario | PreparedScenario,
    *,
    evidence_root: str | Path = ".cdpx-evidence",
    timeout: float = 15.0,
    settle: float = 0.5,
    context: OrchestrationContext | None = None,
    run_id: str | None = None,
    artifact_ttl: float = 86400,
) -> dict[str, Any]:
    if isinstance(scenario, PreparedScenario):
        prepared = scenario
        if context is not None and context is not prepared.context:
            raise ScenarioUsageError("scenario prepared with a different context")
    else:
        if context is None:
            raise ScenarioUsageError("orchestration context required")
        prepared = prepare(scenario, context)
    scenario_spec = prepared.scenario
    context = prepared.context
    redaction = context.redaction
    allowed_origins = context.origins
    writer = SecureArtifactWriter(
        evidence_root,
        _run_key(scenario_spec.name, run_id=run_id),
        ttl=artifact_ttl,
        redaction_context=redaction,
    )
    run_state = ScenarioRun(
        scenario_spec.name,
        writer.run_dir,
        writer,
        composition=scenario_spec.composition,
    )
    collector = PassiveCollector(context)
    collector.enable(client)
    if scenario_spec.emulation:
        emulation.emulate(client, scenario_spec.emulation)

    origin_allowed = True
    for operation in prepared.operations:
        step_ok, origin_allowed = _execute_scenario_operation(
            client,
            operation,
            scenario_spec,
            allowed_origins,
            run_state,
            collector,
            redaction,
            timeout=timeout,
            settle=settle,
        )
        if not step_ok:
            break

    collector.drain(client, settle)
    if origin_allowed:
        origin_allowed = _record_current_origin(
            client,
            allowed_origins,
            run_state,
            redaction,
            step="final",
            error_path="$.final.origin_error",
        )
    if origin_allowed:
        _run_assertions(client, collector, run_state, scenario_spec.assertions)
        origin_allowed = _record_current_origin(
            client,
            allowed_origins,
            run_state,
            redaction,
            step="assertions",
            error_path="$.assertions.origin_error",
        )
    if origin_allowed:
        _capture_many(client, collector, run_state, scenario_spec.artifacts, "final", None, timeout)
    result = redact_tree(run_state.as_dict(), context=redaction)
    writer.write_json(
        "scenario-result.json",
        result,
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    return result


def _execute_scenario_operation(
    client: CDPClient,
    operation: ScenarioOperation,
    scenario: Scenario,
    allowed_origins: tuple[str, ...],
    run_state: ScenarioRun,
    collector: PassiveCollector,
    redaction: RedactionContext,
    *,
    timeout: float,
    settle: float,
) -> tuple[bool, bool]:
    step = operation.step
    record = {
        "index": step.index,
        "label": step.label,
        "verb": step.verb,
        "ok": True,
    }
    if step.source is not None:
        record["source"] = step.source.as_dict()
    started = time.monotonic()
    try:
        _assert_origin(client, scenario, step, allowed_origins)
        result = _run_operation(client, operation, timeout, allowed_origins)
        if step.verb == "goto":
            run_state.last_url = _absolute_url(scenario.base_url, step.value)
        record["result"] = _persistable_step_result(step, result, redaction)
    except ACTION_ERRORS as error:
        record["ok"] = False
        if step.verb == "eval":
            redaction.mark("$.step.error")
            safe_error = MASK
            record["error_masked"] = True
        else:
            safe_error = redact_text(str(error), context=redaction, path="$.step.error")
        record["error"] = safe_error
        run_state.finding("step_failed", safe_error, step=step.label)
    finally:
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
        run_state.steps.append(record)
        collector.drain(client, settle)

    origin_allowed = _record_current_origin(
        client,
        allowed_origins,
        run_state,
        redaction,
        step=step.label,
        error_path="$.step.origin_error",
        record=record,
        update_last_url=step.verb == "goto",
    )
    if origin_allowed:
        _capture_many(
            client,
            collector,
            run_state,
            step.capture,
            step.label,
            step.index,
            timeout,
        )
    return bool(record["ok"]), origin_allowed


def _record_current_origin(
    client: CDPClient,
    allowed_origins: tuple[str, ...],
    run_state: ScenarioRun,
    redaction: RedactionContext,
    *,
    step: str,
    error_path: str,
    record: dict[str, Any] | None = None,
    update_last_url: bool = False,
) -> bool:
    try:
        actual_url = _assert_current_origin(client, allowed_origins)
    except ACTION_ERRORS as error:
        safe_error = redact_text(str(error), context=redaction, path=error_path)
        if record is not None and record["ok"]:
            record["ok"] = False
            record["error"] = safe_error
        run_state.finding("origin_refused", safe_error, step=step)
        return False
    if update_last_url:
        run_state.last_url = actual_url
    return True


def _parse_steps(
    value: Any,
    where: str,
    *,
    sources: list[StepSource] | None = None,
) -> list[ScenarioStep]:
    if not isinstance(value, list) or not value:
        raise ScenarioUsageError(f"{where}steps must be a non-empty list")
    if sources is not None and len(sources) != len(value):
        raise RuntimeError("compiled scenario step provenance mismatch")
    steps = []
    for index, item in enumerate(value):
        source = sources[index] if sources is not None else None
        prefix = _step_prefix(where, index, source)
        if not isinstance(item, dict):
            raise ScenarioUsageError(f"{prefix}must be an object")
        _unknown(item, STEP_KEYS, prefix)
        verbs = [key for key in STEP_ACTIONS if key in item]
        if len(verbs) != 1:
            raise ScenarioUsageError(f"{prefix}must declare exactly one action")
        verb = verbs[0]
        label = item.get("label") or f"{index:03d}-{verb}"
        if not isinstance(label, str) or not label:
            raise ScenarioUsageError(f"{prefix}label must be a non-empty string")
        capture_items = _parse_artifacts(item.get("capture", []), where, f"steps[{index}].capture")
        _validate_step_value(verb, item[verb], prefix)
        steps.append(
            ScenarioStep(
                index=index,
                verb=verb,
                value=item[verb],
                label=label,
                capture=capture_items,
                source=source,
            )
        )
    return steps


def _step_prefix(where: str, index: int, source: StepSource | None) -> str:
    if source is None:
        return f"{where}steps[{index}]."
    locations = [*source.include_chain, IncludeSite(source.path, source.step)]
    return " -> ".join(f"{item.path}:steps[{item.step}]" for item in locations) + "."


def _parse_assertions(value: Any, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScenarioUsageError(f"{where}assertions must be a list")
    assertions = []
    for index, item in enumerate(value):
        prefix = f"{where}assertions[{index}]."
        if not isinstance(item, dict):
            raise ScenarioUsageError(f"{prefix}must be an object")
        _unknown(item, ASSERTIONS, prefix)
        if len(item) != 1:
            raise ScenarioUsageError(f"{prefix}must declare exactly one assertion")
        name, assertion_value = next(iter(item.items()))
        if name == "no_console_errors" and not isinstance(assertion_value, bool):
            raise ScenarioUsageError(f"{prefix}{name} must be boolean")
        if name == "network_errors_max" and (
            not isinstance(assertion_value, int) or isinstance(assertion_value, bool)
        ):
            raise ScenarioUsageError(f"{prefix}{name} must be an integer")
        if name == "text_contains":
            _require_pair(assertion_value, f"{prefix}{name}")
        assertions.append(item)
    return assertions


def _parse_artifacts(value: Any, where: str, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScenarioUsageError(f"{where}{field_name} must be a list")
    for item in value:
        if item not in ARTIFACTS:
            raise ScenarioUsageError(f"{where}{field_name}: unknown artifact: {item}")
    return list(value)


def _validate_step_value(verb: str, value: Any, prefix: str) -> None:
    if verb in {"goto", "wait_visible", "click", "key", "eval"}:
        if not isinstance(value, str) or not value:
            raise ScenarioUsageError(f"{prefix}{verb} must be a non-empty string")
    elif verb == "wait_text":
        _require_pair(value, f"{prefix}{verb}")
    elif verb in {"type", "frame_type"}:
        if isinstance(value, dict):
            fields = {"selector", "secret_ref"}
            if verb == "type":
                fields.add("clear")
                fields.add("mode")
            if verb == "frame_type":
                fields.add("frame_origin")
                fields.add("candidates")
                fields.add("mode")
                fields.add("key_delay_ms")
            _unknown(value, fields, f"{prefix}{verb}.")
            candidates = value.get("candidates")
            has_candidates = isinstance(candidates, list) and bool(candidates)
            if verb == "type" and not isinstance(value.get("selector"), str):
                raise ScenarioUsageError(f"{prefix}{verb}.selector must be a string")
            has_secret_ref = isinstance(value.get("secret_ref"), str) and bool(value["secret_ref"])
            if verb == "type" and not has_secret_ref:
                raise ScenarioUsageError(f"{prefix}{verb} requires secret_ref")
            if verb == "type" and "clear" in value and not isinstance(value["clear"], bool):
                raise ScenarioUsageError(f"{prefix}{verb}.clear must be boolean")
            if value.get("mode", "insert_text") not in {
                "insert_text",
                "key_events",
            }:
                raise ScenarioUsageError(f"{prefix}{verb}.mode must be insert_text or key_events")
            if verb == "frame_type":
                key_delay_ms = value.get("key_delay_ms", 0)
                if (
                    not isinstance(key_delay_ms, int)
                    or isinstance(key_delay_ms, bool)
                    or not 0 <= key_delay_ms <= 250
                ):
                    raise ScenarioUsageError(
                        f"{prefix}{verb}.key_delay_ms must stay between 0 and 250"
                    )
                if key_delay_ms and value.get("mode", "insert_text") != "key_events":
                    raise ScenarioUsageError(
                        f"{prefix}{verb}.key_delay_ms requires key_events mode"
                    )
                has_single = (
                    isinstance(value.get("selector"), str)
                    and bool(value["selector"])
                    and isinstance(value.get("frame_origin"), str)
                    and bool(value["frame_origin"])
                )
                if has_single == has_candidates:
                    raise ScenarioUsageError(
                        f"{prefix}{verb} requires either selector/frame_origin or candidates"
                    )
                if has_candidates:
                    if has_secret_ref:
                        raise ScenarioUsageError(
                            f"{prefix}{verb}.secret_ref must be declared on each candidate"
                        )
                    assert isinstance(candidates, list)
                    for index, candidate in enumerate(candidates):
                        candidate_prefix = f"{prefix}{verb}.candidates[{index}]."
                        if not isinstance(candidate, dict):
                            raise ScenarioUsageError(f"{candidate_prefix}must be an object")
                        _unknown(
                            candidate,
                            {"selector", "frame_origin", "secret_ref"},
                            candidate_prefix,
                        )
                        if (
                            not isinstance(candidate.get("selector"), str)
                            or not candidate["selector"]
                        ):
                            raise ScenarioUsageError(
                                f"{candidate_prefix}selector must be a non-empty string"
                            )
                        if (
                            not isinstance(candidate.get("frame_origin"), str)
                            or not candidate["frame_origin"]
                        ):
                            raise ScenarioUsageError(
                                f"{candidate_prefix}frame_origin must be a non-empty string"
                            )
                        _validate_exact_frame_origin(
                            candidate["frame_origin"],
                            f"{candidate_prefix}frame_origin",
                        )
                        if (
                            not isinstance(candidate.get("secret_ref"), str)
                            or not candidate["secret_ref"]
                        ):
                            raise ScenarioUsageError(
                                f"{candidate_prefix}secret_ref must be a non-empty string"
                            )
                else:
                    _validate_exact_frame_origin(
                        value["frame_origin"],
                        f"{prefix}{verb}.frame_origin",
                    )
                    if not has_secret_ref:
                        raise ScenarioUsageError(f"{prefix}{verb} requires secret_ref")
        else:
            # The [selector, text] form would put the secret in plaintext in
            # the YAML: refused at validation time, with the step's position.
            raise ScenarioUsageError(f"{prefix}{verb} requires an object with secret_ref")


def _validate_exact_frame_origin(value: str, field: str) -> None:
    try:
        parse_exact_origin(value)
    except PolicyError as exc:
        raise ScenarioUsageError(
            f"{field} must be one exact HTTP(S) origin without wildcard, path, or credentials"
        ) from exc


def prepare(scenario: Scenario, context: OrchestrationContext) -> PreparedScenario:
    operations: list[ScenarioOperation] = []
    for step in scenario.steps:
        if step.verb == "goto":
            operation = ScenarioOperation(
                step,
                action=GotoAction(_absolute_url(scenario.base_url, step.value)),
            )
        elif step.verb == "wait_visible":
            operation = ScenarioOperation(step, wait_kind="visible", selector=step.value)
        elif step.verb == "wait_text":
            selector, expected = step.value
            operation = ScenarioOperation(
                step,
                wait_kind="text",
                selector=selector,
                expected=expected,
            )
        elif step.verb == "click":
            operation = ScenarioOperation(step, action=ClickAction(step.value))
        elif step.verb == "key":
            operation = ScenarioOperation(step, action=KeyAction(step.value))
        elif step.verb == "eval":
            operation = ScenarioOperation(step, action=EvalAction(step.value))
        elif step.verb == "type":
            operation = ScenarioOperation(
                step,
                action=_type_action(step.value, context=context.redaction),
            )
        elif step.verb == "frame_type":
            candidates = step.value.get("candidates")
            frame_candidates = (
                tuple(
                    (
                        candidate["selector"],
                        candidate["frame_origin"],
                        _secret_text(candidate["secret_ref"], context=context.redaction),
                    )
                    for candidate in candidates
                )
                if isinstance(candidates, list)
                else ()
            )
            frame_origin = step.value.get("frame_origin")
            if isinstance(frame_origin, str):
                assert_url_allowed(frame_origin, context.origins)
            for _, candidate_origin, _ in frame_candidates:
                assert_url_allowed(candidate_origin, context.origins)
            operation = ScenarioOperation(
                step,
                action=(
                    # Runtime candidate selection remains a composed frame
                    # operation, but its representative action makes the
                    # interaction authority explicit during preflight.
                    TypeAction(
                        frame_candidates[0][0],
                        frame_candidates[0][2],
                        mode=step.value.get("mode", "insert_text"),
                    )
                    if frame_candidates
                    else _type_action(step.value, context=context.redaction)
                ),
                frame_origin=frame_origin,
                frame_candidates=frame_candidates,
                frame_mode=step.value.get("mode", "insert_text"),
                frame_key_delay_ms=step.value.get("key_delay_ms", 0),
            )
        else:  # pragma: no cover - the parser validates STEP_ACTIONS
            raise ScenarioUsageError(f"unknown action: {step.verb}")
        operations.append(operation)
    return PreparedScenario(scenario, context, tuple(operations))


def _run_operation(
    client: CDPClient,
    operation: ScenarioOperation,
    timeout: float,
    allowed_origins: tuple[str, ...],
) -> dict:
    if operation.step.verb == "frame_type":
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise CDPTimeout(f"scenario frame_type timeout after {timeout}s")
            return budget

        if operation.frame_candidates:
            return inputs.type_text_in_candidate_frame(
                client,
                operation.frame_candidates,
                mode=operation.frame_mode,
                key_delay_ms=operation.frame_key_delay_ms,
                remaining=remaining,
            )
        assert isinstance(operation.action, TypeAction)
        assert operation.frame_origin is not None
        return inputs.type_text_in_frame(
            client,
            operation.action.selector,
            operation.action.text,
            frame_origin=operation.frame_origin,
            mode=operation.frame_mode,
            key_delay_ms=operation.frame_key_delay_ms,
            remaining=remaining,
        )
    if operation.action is not None:
        return actions.run_action(
            client,
            operation.action,
            timeout,
            origin_guard=lambda: assert_url_allowed(_current_url(client), allowed_origins),
        )
    if operation.wait_kind == "visible" and operation.selector is not None:
        return nav.wait_for_visible(client, operation.selector, timeout=timeout)
    if (
        operation.wait_kind == "text"
        and operation.selector is not None
        and operation.expected is not None
    ):
        return _wait_text(client, operation.selector, operation.expected, timeout)
    raise ScenarioUsageError(f"operation not materialized: {operation.step.label}")


def _type_action(value: Any, *, context: RedactionContext) -> TypeAction:
    if isinstance(value, dict):
        secret_ref = value["secret_ref"]
        text = _secret_text(secret_ref, context=context)
        selector = value.get("selector")
        assert isinstance(selector, str)
        return TypeAction(
            selector,
            text,
            clear=bool(value.get("clear")),
            mode=value.get("mode", "insert_text"),
        )
    raise ScenarioUsageError("scenario type requires secret_ref")


def _secret_text(secret_ref: str, *, context: RedactionContext) -> str:
    if secret_ref not in os.environ:
        raise ScenarioUsageError(f"secret_ref not found: {secret_ref}")
    text = os.environ[secret_ref]
    context.register_secret(text)
    return text


def _validate_sensitive_captures(
    steps: list[ScenarioStep], artifacts: list[str], where: str
) -> None:
    sensitive = False
    for step in steps:
        if step.verb == "frame_type":
            sensitive = True
        if sensitive and "screenshot" in step.capture:
            raise ScenarioUsageError(
                f"{where}steps[{step.index}].capture: screenshot forbidden after frame_type"
            )
    if sensitive and "screenshot" in artifacts:
        raise ScenarioUsageError(
            f"{where}artifacts: final screenshot forbidden when frame_type is used"
        )


def _persistable_step_result(
    step: ScenarioStep,
    result: dict[str, Any],
    context: RedactionContext,
) -> dict[str, Any]:
    if step.verb != "eval":
        safe = redact_tree(result, context=context)
        return safe if isinstance(safe, dict) else {"redacted": True}
    context.mark(f"$.steps[{step.index}].result.value")
    return {"value": MASK, "value_masked": True}


def _wait_text(client: CDPClient, selector: str, expected: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_text = None
    while True:
        last_text = js.get_text(client, selector)["text"]
        if last_text is not None and expected in last_text:
            return {"selector": selector, "text": last_text, "contains": expected}
        if time.monotonic() >= deadline:
            raise CDPTimeout(f"text not found after {timeout}s: {selector} contains {expected}")
        time.sleep(0.05)


def _run_assertions(
    client: CDPClient,
    collector: PassiveCollector,
    run_state: ScenarioRun,
    assertions: list[dict[str, Any]],
) -> None:
    for assertion in assertions:
        name, expected = next(iter(assertion.items()))
        record = {"name": name, "expected": expected, "ok": True}
        if name == "no_console_errors":
            errors = collector.console()["errors"]
            record["actual"] = errors
            record["ok"] = not expected or errors == 0
        elif name == "network_errors_max":
            errors = _network_errors(collector.network()["summary"])
            record["actual"] = errors
            record["ok"] = errors <= expected
        elif name == "text_contains":
            selector, text = expected
            actual = js.get_text(client, selector)["text"]
            record["actual"] = actual
            record["ok"] = actual is not None and text in actual
        if not record["ok"]:
            run_state.finding(
                f"assertion_{name}",
                f"assertion failed: {name}",
            )
        run_state.assertions.append(redact_tree(record, context=collector.redaction))


def _capture_many(
    client: CDPClient,
    collector: PassiveCollector,
    run_state: ScenarioRun,
    artifacts: list[str],
    label: str,
    index: int | None,
    timeout: float,
) -> None:
    for artifact in artifacts:
        try:
            _capture_one(client, collector, run_state, artifact, label, index, timeout)
        except ACTION_ERRORS as e:
            run_state.finding(
                "artifact_failed",
                f"{artifact} proof unavailable: {e}",
                step=label,
            )


def _capture_one(
    client: CDPClient,
    collector: PassiveCollector,
    run_state: ScenarioRun,
    artifact: str,
    label: str,
    index: int | None,
    timeout: float,
) -> None:
    stem = f"final-{artifact}" if index is None else f"{index:03d}-{slugify(label)}-{artifact}"
    if artifact == "screenshot":
        result = capture.screenshot(client, str(run_state.evidence_dir / f"{stem}.png"))
        entry = run_state.writer.register_file(
            result["path"],
            classification=ArtifactClassification.OPAQUE_RESTRICTED,
            upload_allowed=False,
        )
        run_state.artifacts.append(_artifact("screenshot", label, entry, run_state.evidence_dir))
        return
    if artifact == "console":
        entry = run_state.writer.write_json(f"{stem}.json", collector.console())
        run_state.artifacts.append(_artifact("console", label, entry, run_state.evidence_dir))
        return
    if artifact == "network":
        entry = run_state.writer.write_json(f"{stem}.json", collector.network())
        run_state.artifacts.append(_artifact("network", label, entry, run_state.evidence_dir))
        return
    if artifact == "profiler":
        profiler_result = collector.profiler(client, timeout)
        if profiler_result is None and run_state.last_url:
            profiler_result = dev.profiler(
                client,
                run_state.last_url,
                timeout=timeout,
                context=collector.context,
            )
        if profiler_result is None:
            run_state.finding(
                "profiler_unavailable",
                "X-Debug-Token-Link/X-Debug-Token header not found",
                severity="warning",
                step=label,
            )
            return
        entry = run_state.writer.write_json(
            f"{stem}.json",
            redact_tree(profiler_result, context=collector.redaction),
        )
        run_state.artifacts.append(_artifact("profiler", label, entry, run_state.evidence_dir))


def _artifact(
    kind: str,
    label: str,
    entry: ArtifactEntry,
    evidence_dir: Path,
) -> dict[str, Any]:
    return {
        "type": kind,
        "label": label,
        "path": str(evidence_dir / entry.path),
        "bytes": entry.bytes,
        "mime": entry.mime,
        "sha256": entry.sha256,
        "classification": entry.classification,
        "upload_allowed": entry.upload_allowed,
    }


def _assert_origin(
    client: CDPClient,
    scenario: Scenario,
    step: ScenarioStep,
    origins: tuple[str, ...],
) -> None:
    if step.verb == "goto":
        assert_url_allowed(_absolute_url(scenario.base_url, step.value), origins)
    else:
        assert_url_allowed(_current_url(client), origins)


def _assert_current_origin(client: CDPClient, origins: tuple[str, ...]) -> str:
    current_url = _current_url(client)
    assert_url_allowed(current_url, origins)
    return current_url


def _current_url(client: CDPClient) -> str:
    current_url = js.evaluate(client, "window.location.href")
    if not isinstance(current_url, str):
        raise ScenarioUsageError("current URL cannot be determined")
    return current_url


def _absolute_url(base_url: str, value: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", value)


def _events(events: list[CDPEvent], names: tuple[str, ...]) -> list[CDPEvent]:
    return [event for event in events if event.get("method") in names]


def _network_summary(requests: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(requests),
        "failed": sum(1 for r in requests if r.get("failed")),
        "errors_4xx_5xx": sum(1 for r in requests if (r.get("status") or 0) >= 400),
        "bytes": sum(r.get("encodedBytes") or 0 for r in requests),
    }


def _network_errors(summary: dict[str, int]) -> int:
    return summary.get("failed", 0) + summary.get("errors_4xx_5xx", 0)


def _run_key(name: str, *, run_id: str | None = None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    prefix = f"{run_id}-" if run_id else ""
    return slugify(f"{prefix}{name}-{stamp}")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return slug[:120] or "scenario"


def _required_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ScenarioUsageError(f"{where}{key} must be a non-empty string")
    return value


def _required_dict(data: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ScenarioUsageError(f"{where}{key} must be an object")
    return value


def _require_pair(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not isinstance(value[1], str)
    ):
        raise ScenarioUsageError(f"{label} must be [selector, text]")


def _unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
    non_string = [key for key in data if not isinstance(key, str)]
    if non_string:
        rendered = ", ".join(sorted(repr(key) for key in non_string))
        raise ScenarioUsageError(f"{where}field names must be strings: {rendered}")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ScenarioUsageError(f"{where}unknown field(s): {', '.join(unknown)}")
