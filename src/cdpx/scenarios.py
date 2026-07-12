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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from cdpx.artifacts import ArtifactClassification, ArtifactEntry, SecureArtifactWriter
from cdpx.client import CDPClient, CDPError, CDPTimeout
from cdpx.policy import assert_url_allowed, parse_origins
from cdpx.primitives import actions, advanced, capture, dev, inputs, js, nav, profiler_panels
from cdpx.security import (
    MASK,
    RedactionContext,
    redact_headers,
    redact_text,
    redact_tree,
    redact_url,
)

STEP_ACTIONS = {"goto", "wait_visible", "click", "type", "key", "eval", "wait_text"}
STEP_KEYS = STEP_ACTIONS | {"label", "capture"}
ASSERTIONS = {"no_console_errors", "network_errors_max", "text_contains"}
ARTIFACTS = {"screenshot", "console", "network", "profiler"}
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


@dataclass
class ScenarioStep:
    index: int
    verb: str
    value: Any
    label: str
    capture: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    base_url: str
    emulation: str | None
    steps: list[ScenarioStep]
    assertions: list[dict[str, Any]]
    artifacts: list[str]


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
        return {
            "name": self.name,
            "verdict": self.verdict,
            "findings": self.findings,
            "evidence_dir": str(self.evidence_dir),
            "steps": self.steps,
            "assertions": self.assertions,
            "artifacts": self.artifacts,
        }


class PassiveCollector:
    def __init__(self, context: RedactionContext | None = None) -> None:
        self.redaction = context or RedactionContext()
        self.allowed_origins: tuple[str, ...] | None = None
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
        return profiler_panels.collect(
            client,
            self.profiler_hits[-1],
            timeout=timeout,
            context=self.redaction,
            allowed_origins=self.allowed_origins,
            page_url=_current_url(client),
        )

    def _ingest(self, events: list[dict[str, Any]]) -> None:
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
                # Une redirection n'émet pas de responseReceived: son token
                # profiler n'existe que dans redirectResponse.
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


def load(path: str | Path) -> Scenario:
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except OSError as e:
        raise ScenarioUsageError(f"scénario illisible: {source}: {e}") from e
    except yaml.YAMLError as e:
        raise ScenarioUsageError(f"YAML invalide: {source}: {e}") from e
    return parse(raw, source=source)


def parse(raw: Any, *, source: Path | None = None) -> Scenario:
    where = f"{source}: " if source else ""
    if not isinstance(raw, dict):
        raise ScenarioUsageError(f"{where}le scénario doit être un objet YAML")
    _unknown(raw, {"name", "context", "steps", "assertions", "artifacts"}, where)
    name = _required_str(raw, "name", where)
    context = _required_dict(raw, "context", where)
    _unknown(context, {"base_url", "emulation"}, f"{where}context.")
    base_url = _required_str(context, "base_url", f"{where}context.")
    emulation = context.get("emulation")
    if emulation is not None and emulation not in advanced.PRESETS:
        raise ScenarioUsageError(f"{where}context.emulation inconnu: {emulation}")
    steps = _parse_steps(raw.get("steps"), where)
    assertions = _parse_assertions(raw.get("assertions", []), where)
    artifacts = _parse_artifacts(raw.get("artifacts", []), where, "artifacts")
    return Scenario(
        name=name,
        base_url=base_url,
        emulation=emulation,
        steps=steps,
        assertions=assertions,
        artifacts=artifacts,
    )


def run(
    client: CDPClient,
    scenario: Scenario,
    *,
    evidence_root: str | Path = ".cdpx-evidence",
    timeout: float = 15.0,
    settle: float = 0.5,
    origins: str | None = None,
    strict_origins: bool = False,
    redaction_context: RedactionContext | None = None,
    run_id: str | None = None,
    artifact_ttl: float = 86400,
) -> dict[str, Any]:
    redaction = redaction_context or RedactionContext()
    for step in scenario.steps:
        if step.verb == "type":
            _type_action(step.value, context=redaction)
    allowed_origins = parse_origins(origins, required=strict_origins) or None
    writer = SecureArtifactWriter(
        evidence_root,
        _run_key(scenario.name, run_id=run_id),
        ttl=artifact_ttl,
        redaction_context=redaction,
    )
    run_state = ScenarioRun(scenario.name, writer.run_dir, writer)
    collector = PassiveCollector(redaction)
    collector.allowed_origins = allowed_origins
    collector.enable(client)
    if scenario.emulation:
        advanced.emulate(client, scenario.emulation)

    origin_blocked = False
    for step in scenario.steps:
        record = {
            "index": step.index,
            "label": step.label,
            "verb": step.verb,
            "ok": True,
        }
        started = time.monotonic()
        try:
            _assert_origin(client, scenario, step, origins, strict=strict_origins)
            result = _run_step(client, scenario, step, timeout, redaction)
            if step.verb == "goto":
                run_state.last_url = _absolute_url(scenario.base_url, step.value)
            record["result"] = _persistable_step_result(step, result, redaction)
        except ACTION_ERRORS as e:
            record["ok"] = False
            if step.verb == "eval":
                redaction.mark("$.step.error")
                safe_error = MASK
                record["error_masked"] = True
            else:
                safe_error = redact_text(str(e), context=redaction, path="$.step.error")
            record["error"] = safe_error
            run_state.finding("step_failed", safe_error, step=step.label)
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
            run_state.steps.append(record)
            collector.drain(client, settle)
            if strict_origins:
                try:
                    actual_url = _assert_current_origin(client, origins)
                    if step.verb == "goto":
                        run_state.last_url = actual_url
                except ACTION_ERRORS as e:
                    origin_blocked = True
                    safe_error = redact_text(
                        str(e),
                        context=redaction,
                        path="$.step.origin_error",
                    )
                    if record["ok"]:
                        record["ok"] = False
                        record["error"] = safe_error
                    run_state.finding("origin_refused", safe_error, step=step.label)
            if not origin_blocked:
                _capture_many(
                    client,
                    collector,
                    run_state,
                    step.capture,
                    step.label,
                    step.index,
                    timeout,
                )
        if not record["ok"]:
            break

    collector.drain(client, settle)
    if strict_origins and not origin_blocked:
        try:
            _assert_current_origin(client, origins)
        except ACTION_ERRORS as e:
            origin_blocked = True
            run_state.finding(
                "origin_refused",
                redact_text(str(e), context=redaction, path="$.final.origin_error"),
                step="final",
            )
    if not origin_blocked:
        _run_assertions(client, collector, run_state, scenario.assertions)
        if strict_origins:
            try:
                _assert_current_origin(client, origins)
            except ACTION_ERRORS as e:
                origin_blocked = True
                run_state.finding(
                    "origin_refused",
                    redact_text(
                        str(e),
                        context=redaction,
                        path="$.assertions.origin_error",
                    ),
                    step="assertions",
                )
    if not origin_blocked:
        _capture_many(client, collector, run_state, scenario.artifacts, "final", None, timeout)
    result = redact_tree(run_state.as_dict(), context=redaction)
    writer.write_json(
        "scenario-result.json",
        result,
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=False,
    )
    return result


def _parse_steps(value: Any, where: str) -> list[ScenarioStep]:
    if not isinstance(value, list) or not value:
        raise ScenarioUsageError(f"{where}steps doit être une liste non vide")
    steps = []
    for index, item in enumerate(value):
        prefix = f"{where}steps[{index}]."
        if not isinstance(item, dict):
            raise ScenarioUsageError(f"{prefix}doit être un objet")
        _unknown(item, STEP_KEYS, prefix)
        verbs = [key for key in STEP_ACTIONS if key in item]
        if len(verbs) != 1:
            raise ScenarioUsageError(f"{prefix}doit déclarer exactement une action")
        verb = verbs[0]
        label = item.get("label") or f"{index:03d}-{verb}"
        if not isinstance(label, str) or not label:
            raise ScenarioUsageError(f"{prefix}label doit être une chaîne non vide")
        capture_items = _parse_artifacts(item.get("capture", []), where, f"steps[{index}].capture")
        _validate_step_value(verb, item[verb], prefix)
        steps.append(
            ScenarioStep(
                index=index,
                verb=verb,
                value=item[verb],
                label=label,
                capture=capture_items,
            )
        )
    return steps


def _parse_assertions(value: Any, where: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScenarioUsageError(f"{where}assertions doit être une liste")
    assertions = []
    for index, item in enumerate(value):
        prefix = f"{where}assertions[{index}]."
        if not isinstance(item, dict):
            raise ScenarioUsageError(f"{prefix}doit être un objet")
        _unknown(item, ASSERTIONS, prefix)
        if len(item) != 1:
            raise ScenarioUsageError(f"{prefix}doit déclarer exactement une assertion")
        name, assertion_value = next(iter(item.items()))
        if name == "no_console_errors" and not isinstance(assertion_value, bool):
            raise ScenarioUsageError(f"{prefix}{name} doit être booléen")
        if name == "network_errors_max" and not isinstance(assertion_value, int):
            raise ScenarioUsageError(f"{prefix}{name} doit être entier")
        if name == "text_contains":
            _require_pair(assertion_value, f"{prefix}{name}")
        assertions.append(item)
    return assertions


def _parse_artifacts(value: Any, where: str, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ScenarioUsageError(f"{where}{field_name} doit être une liste")
    for item in value:
        if item not in ARTIFACTS:
            raise ScenarioUsageError(f"{where}{field_name}: artifact inconnu: {item}")
    return list(value)


def _validate_step_value(verb: str, value: Any, prefix: str) -> None:
    if verb in {"goto", "wait_visible", "click", "key", "eval"}:
        if not isinstance(value, str) or not value:
            raise ScenarioUsageError(f"{prefix}{verb} doit être une chaîne non vide")
    elif verb == "wait_text":
        _require_pair(value, f"{prefix}{verb}")
    elif verb == "type":
        if isinstance(value, list):
            _require_pair(value, f"{prefix}{verb}")
        elif isinstance(value, dict):
            _unknown(value, {"selector", "text", "secret_ref", "clear"}, f"{prefix}{verb}.")
            if not isinstance(value.get("selector"), str):
                raise ScenarioUsageError(f"{prefix}{verb}.selector doit être une chaîne")
            has_text = isinstance(value.get("text"), str)
            has_secret_ref = isinstance(value.get("secret_ref"), str) and bool(value["secret_ref"])
            if has_text == has_secret_ref:
                raise ScenarioUsageError(f"{prefix}{verb} exige exactement text ou secret_ref")
            if "clear" in value and not isinstance(value["clear"], bool):
                raise ScenarioUsageError(f"{prefix}{verb}.clear doit être booléen")
        else:
            raise ScenarioUsageError(f"{prefix}{verb} doit être [selector, text] ou un objet")


def _run_step(
    client: CDPClient,
    scenario: Scenario,
    step: ScenarioStep,
    timeout: float,
    context: RedactionContext,
) -> dict:
    if step.verb == "goto":
        return actions.run_action(
            client,
            ["goto", _absolute_url(scenario.base_url, step.value)],
            timeout,
        )
    if step.verb == "wait_visible":
        return nav.wait_for_visible(client, step.value, timeout=min(timeout, 10.0))
    if step.verb == "click":
        return actions.run_action(client, ["click", step.value], timeout)
    if step.verb == "key":
        return actions.run_action(client, ["key", step.value], timeout)
    if step.verb == "eval":
        return actions.run_action(client, ["eval", step.value], timeout)
    if step.verb == "type":
        return actions.run_action(client, _type_action(step.value, context=context), timeout)
    if step.verb == "wait_text":
        selector, expected = step.value
        return _wait_text(client, selector, expected, timeout)
    raise ScenarioUsageError(f"action inconnue: {step.verb}")


def _type_action(value: Any, *, context: RedactionContext) -> list[str]:
    if isinstance(value, dict):
        if "secret_ref" in value:
            secret_ref = value["secret_ref"]
            if secret_ref not in os.environ:
                raise ScenarioUsageError(f"secret_ref introuvable: {secret_ref}")
            text = os.environ[secret_ref]
        else:
            text = value["text"]
        context.register_secret(text)
        action = ["type", value["selector"], text]
        if value.get("clear"):
            action.append("--clear")
        return action
    context.register_secret(value[1])
    return ["type", value[0], value[1]]


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
            raise CDPTimeout(f"texte introuvable après {timeout}s: {selector} contient {expected}")
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
                f"assertion échouée: {name}",
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
                f"preuve {artifact} indisponible: {e}",
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
                context=collector.redaction,
                allowed_origins=collector.allowed_origins,
            )
        if profiler_result is None:
            run_state.finding(
                "profiler_unavailable",
                "header X-Debug-Token-Link/X-Debug-Token introuvable",
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
    origins: str | None,
    *,
    strict: bool = False,
) -> None:
    if strict:
        allowed = parse_origins(origins, required=True)
        if step.verb == "goto":
            assert_url_allowed(_absolute_url(scenario.base_url, step.value), allowed)
        else:
            assert_url_allowed(_current_url(client), allowed)
        return
    if not origins or step.verb not in actions.MUTATING_VERBS:
        return
    current_url = _current_url(client)
    advanced.assert_origin_allowed(step.verb, current_url, origins)


def _assert_current_origin(client: CDPClient, origins: str | None) -> str:
    current_url = _current_url(client)
    assert_url_allowed(current_url, parse_origins(origins, required=True))
    return current_url


def _current_url(client: CDPClient) -> str:
    current_url = js.evaluate(client, "window.location.href")
    if not isinstance(current_url, str):
        raise ScenarioUsageError("URL courante indéterminable")
    return current_url


def _absolute_url(base_url: str, value: str) -> str:
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", value)


def _events(events: list[dict[str, Any]], names: tuple[str, ...]) -> list[dict[str, Any]]:
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
        raise ScenarioUsageError(f"{where}{key} doit être une chaîne non vide")
    return value


def _required_dict(data: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ScenarioUsageError(f"{where}{key} doit être un objet")
    return value


def _require_pair(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not isinstance(value[1], str)
    ):
        raise ScenarioUsageError(f"{label} doit être [selector, texte]")


def _unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ScenarioUsageError(f"{where}champ(s) inconnu(s): {', '.join(unknown)}")
