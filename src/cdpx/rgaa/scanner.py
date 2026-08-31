"""Prudent test-level RGAA resolver over integrity-isolated observations."""

# ruff: noqa: E501 -- audit messages and embedded JavaScript stay readable verbatim.

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from cdpx import __version__
from cdpx.client import CDPClient, CDPError, CDPTimeout, CDPTransportError
from cdpx.primitives import inputs, js
from cdpx.primitives.js import JSException
from cdpx.rgaa import provider
from cdpx.rgaa.catalog import (
    CATALOG_ID,
    EXPECTED_COUNTS,
    RGAA_VERSION,
    SOURCE_COMMIT,
    catalog_sha256,
    load_catalog,
)
from cdpx.rgaa.plan import (
    ACCESSIBILITY_TESTS,
    FOCUS_STEP_LIMIT,
    FOCUS_TESTS,
    PASSIVE_TESTS,
    SPACING_TESTS,
    ExecutionBudget,
    ScanPlan,
    build_scan_plan,
)
from cdpx.rgaa.probes import (
    FOCUS_RESET_PROBE,
    FOCUS_RESTORE_PROBE,
    FOCUS_STATE_PROBE,
    PASSIVE_PROBE,
    TEXT_SPACING_CLEANUP,
    TEXT_SPACING_PROBE,
)

Scope = Literal["passive", "interactive", "privileged"]
Engine = Literal["native", "hybrid"]
OriginGuard = Callable[[], None]
RULE_VERSION = 2
VERDICTS = (
    "pass",
    "fail",
    "not_applicable",
    "needs_review",
    "manual_only",
    "error",
    "not_tested",
)
_AUTO_FLAG = {
    "pass": "auto_pass",
    "fail": "auto_fail",
    "not_applicable": "auto_not_applicable",
}


class AuditInvariantError(RuntimeError):
    """The resolver or report violated an internal invariant."""


class CollectorExecutionError(ValueError):
    """A browser collector could not produce its documented observation."""


class DocumentStateDrift(ValueError):
    """The assigned top-level document changed during one audit."""


@dataclass(frozen=True)
class DocumentIdentity:
    frame_id: str
    loader_id: str
    url: str


_COLLECTOR_ERRORS = (CDPError, CDPTimeout, CDPTransportError, JSException, CollectorExecutionError)


def _finding(
    rule_id: str,
    message: str,
    *,
    target: str | None = None,
    observed: Any = None,
    severity: str = "serious",
    status: str = "potential",
) -> dict[str, Any]:
    finding = {
        "rule_id": rule_id,
        "severity": severity,
        "status": status,
        "message": message,
    }
    if target:
        finding["target"] = target
    if observed is not None:
        finding["observed"] = observed
    return finding


def _set_result(
    result: dict[str, Any],
    verdict: str,
    *,
    confidence: str,
    findings: list[dict[str, Any]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    evidence_complete: bool = False,
) -> None:
    if verdict not in VERDICTS:
        raise AuditInvariantError(f"unknown RGAA verdict: {verdict}")
    flag = _AUTO_FLAG.get(verdict)
    capabilities = result.get("_capabilities", {})
    if flag and not capabilities.get(flag, False):
        raise AuditInvariantError(f"RGAA {result['id']}: matrix forbids automatic {verdict}")
    if verdict in {"pass", "not_applicable"} and not evidence_complete:
        raise AuditInvariantError(f"RGAA {result['id']}: {verdict} requires complete evidence")
    result["verdict"] = verdict
    result["confidence"] = confidence
    result["evidence_complete"] = evidence_complete
    if findings is not None:
        result["findings"] = findings
    if evidence is not None:
        result["evidence"] = evidence
    if limitations is not None:
        result["limitations"] = limitations


def _initial_results(selected: set[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    results, by_id = [], {}
    for test in load_catalog()["tests"]:
        profile = test["automation"]
        verdict = (
            "not_tested" if test["id"] not in selected else profile["default_unresolved_verdict"]
        )
        result = {
            "id": test["id"],
            "theme_id": test["theme_id"],
            "criterion_id": test["criterion_id"],
            "verdict": verdict,
            "automation": profile["automation_class"],
            "confidence": profile["confidence"],
            "evidence_complete": False,
            "findings": [],
            "evidence": [],
            "advisory": [],
            "limitations": [profile["limitations"]],
            "_capabilities": {
                key: bool(profile[key]) for key in ("auto_pass", "auto_fail", "auto_not_applicable")
            },
        }
        results.append(result)
        by_id[test["id"]] = result
    return results, by_id


def _document_identity(client: CDPClient, timeout: float) -> DocumentIdentity:
    response = client.send("Page.getFrameTree", timeout=timeout)
    tree = response.get("frameTree", {})
    frame = tree.get("frame", {}) if isinstance(tree, dict) else {}
    frame_id = frame.get("id") if isinstance(frame, dict) else None
    loader_id = frame.get("loaderId") if isinstance(frame, dict) else None
    url = frame.get("url") if isinstance(frame, dict) else None
    if (
        not isinstance(frame_id, str)
        or not frame_id
        or not isinstance(loader_id, str)
        or not loader_id
        or not isinstance(url, str)
        or not url
    ):
        raise CollectorExecutionError("RGAA main frame identity unavailable")
    return DocumentIdentity(frame_id, loader_id, url)


def _isolated_world(client: CDPClient, budget: ExecutionBudget, identity: DocumentIdentity) -> int:
    response = client.send(
        "Page.createIsolatedWorld",
        {
            "frameId": identity.frame_id,
            "worldName": "__cdpx_rgaa_native",
            "grantUniveralAccess": False,
        },
        timeout=budget.remaining(),
    )
    context_id = response.get("executionContextId")
    if not isinstance(context_id, int):
        raise CollectorExecutionError("RGAA isolated world unavailable")
    return context_id


def _guard_document(
    client: CDPClient,
    budget: ExecutionBudget,
    expected: DocumentIdentity,
    origin_guard: OriginGuard | None,
) -> None:
    if origin_guard:
        origin_guard()
    observed = _document_identity(client, budget.remaining())
    if observed != expected:
        raise DocumentStateDrift(
            "RGAA document state drift: "
            f"expected {expected.url} ({expected.loader_id}), "
            f"observed {observed.url} ({observed.loader_id})"
        )


def _load_probe(
    client: CDPClient,
    context_id: int,
    expression: str,
    budget: ExecutionBudget,
    *,
    await_promise: bool = True,
) -> dict[str, Any]:
    raw = js.evaluate(
        client,
        expression,
        await_promise=await_promise,
        timeout=budget.remaining(),
        context_id=context_id,
    )
    if not isinstance(raw, str):
        raise CollectorExecutionError("RGAA page probe returned no value")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CollectorExecutionError("RGAA page probe returned invalid JSON") from error
    if not isinstance(value, dict):
        raise CollectorExecutionError("RGAA page probe returned an invalid object")
    return value


def _group(observation: dict[str, Any], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = observation.get(key, {})
    group = raw if isinstance(raw, dict) else {}
    values = group.get("items", [])
    items = [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []
    return items, group


def _coverage_evidence(collector: str, group: dict[str, Any]) -> dict[str, Any]:
    return {
        "collector": collector,
        "total": group.get("total"),
        "examined": group.get("examined"),
        "truncated": bool(group.get("truncated", False)),
        "evidence_complete": bool(group.get("evidence_complete", False)),
        "coverage_scope": group.get("coverage_scope"),
    }


def _apply_passive(
    by_id: dict[str, dict[str, Any]], observation: dict[str, Any], selected: set[str]
) -> None:
    if "2.1.1" in selected:
        frames, group = _group(observation, "frames")
        missing = [item for item in frames if not item.get("title_present")]
        if missing:
            _set_result(
                by_id["2.1.1"],
                "fail",
                confidence="high",
                findings=[
                    _finding(
                        "frame-title",
                        "Frame has no title attribute.",
                        target=item.get("target"),
                        status="proven",
                    )
                    for item in missing
                ],
                evidence=[_coverage_evidence("isolated-dom", group)],
            )
        else:
            _set_result(
                by_id["2.1.1"],
                "needs_review",
                confidence="medium",
                evidence=[_coverage_evidence("isolated-dom", group)],
                limitations=[
                    "Frames in closed shadow roots and nested documents remain unobserved."
                ],
            )

    contrast = observation.get("contrast", {})
    contrast_group = contrast if isinstance(contrast, dict) else {}
    contrast_items = contrast_group.get("items", [])
    for test_id in ("3.2.1", "3.2.2", "3.2.3", "3.2.4"):
        if test_id not in selected:
            continue
        candidates = [
            item
            for item in contrast_items
            if isinstance(item, dict) and item.get("test_id") == test_id
        ]
        failures = [item for item in candidates if item.get("ratio", 0) < item.get("required", 0)]
        _set_result(
            by_id[test_id],
            "needs_review",
            confidence="medium",
            findings=[
                _finding(
                    "text-contrast-solid",
                    f"Observed ratio {item.get('ratio')}:1 is below {item.get('required')}:1; alternate mechanisms and RGAA exceptions remain to review.",
                    target=item.get("target"),
                    observed={
                        key: item.get(key)
                        for key in (
                            "ratio",
                            "required",
                            "foreground",
                            "background",
                            "font_size",
                            "font_weight",
                        )
                    },
                )
                for item in failures
            ],
            evidence=[_coverage_evidence("isolated-dom-css", contrast_group)],
            limitations=[
                "Text images, generated content, complex composition, exceptions and alternate contrast mechanisms are not fully resolved."
            ],
        )

    if "6.1.1" in selected:
        links, group = _group(observation, "links")
        unnamed = [item for item in links if not item.get("name_sources")]
        _set_result(
            by_id["6.1.1"],
            "needs_review",
            confidence="medium",
            findings=[
                _finding(
                    "link-accessible-name",
                    "No supported name source was observed; confirm with the accessibility tree and RGAA context.",
                    target=item.get("target"),
                )
                for item in unnamed
            ],
            evidence=[_coverage_evidence("isolated-dom", group)],
            limitations=[
                "Accessible-name computation and link purpose require AX and human review."
            ],
        )

    if "8.1.1" in selected:
        doctype = observation.get("doctype", {})
        complete = isinstance(doctype, dict) and bool(doctype.get("evidence_complete"))
        if not isinstance(doctype, dict) or not doctype.get("present"):
            verdict, findings = (
                "fail",
                [
                    _finding(
                        "document-type",
                        "Document has no HTML document type.",
                        observed=doctype,
                        status="proven",
                    )
                ],
            )
        else:
            verdict, findings = "pass", []
        _set_result(
            by_id["8.1.1"],
            verdict,
            confidence="high" if verdict != "needs_review" else "medium",
            findings=findings,
            evidence=[{"collector": "isolated-dom", "doctype": doctype}],
            evidence_complete=complete and verdict == "pass",
        )

    language = observation.get("language", {})
    lang = (
        str(language.get("lang") or language.get("xml_lang") or "").strip()
        if isinstance(language, dict)
        else ""
    )
    if "8.3.1" in selected:
        _set_result(
            by_id["8.3.1"],
            "pass" if lang else "needs_review",
            confidence="high" if lang else "medium",
            findings=[]
            if lang
            else [
                _finding(
                    "default-language",
                    "No language was observed on the root element; the per-text-element RGAA branch remains to review.",
                )
            ],
            evidence=[{"collector": "isolated-dom", "language": lang or None}],
            evidence_complete=bool(lang),
        )
    if "8.4.1" in selected:
        _set_result(
            by_id["8.4.1"],
            "needs_review",
            confidence="medium",
            findings=[]
            if lang
            else [
                _finding(
                    "default-language-validity",
                    "No language tag is available to validate.",
                    observed=None,
                )
            ],
            evidence=[{"collector": "isolated-dom", "language": lang or None}],
            limitations=[
                "BCP 47 syntax alone cannot prove that the tag matches the page language."
            ],
        )

    title = observation.get("title", {})
    title_present = isinstance(title, dict) and bool(title.get("present"))
    title_value = str(title.get("value", "")).strip() if isinstance(title, dict) else ""
    if "8.5.1" in selected:
        _set_result(
            by_id["8.5.1"],
            "pass" if title_present else "fail",
            confidence="high",
            findings=[]
            if title_present
            else [
                _finding(
                    "document-title-presence", "Document has no title element.", status="proven"
                )
            ],
            evidence=[{"collector": "isolated-dom", "title_present": title_present}],
            evidence_complete=title_present,
        )
    if "8.6.1" in selected:
        empty_title = title_present and not title_value
        _set_result(
            by_id["8.6.1"],
            "fail" if empty_title else "needs_review",
            confidence="high" if empty_title else "medium",
            findings=[]
            if not empty_title
            else [
                _finding("document-title-relevance", "Document title is empty.", status="proven")
            ],
            evidence=[
                {
                    "collector": "isolated-dom",
                    "title_present": title_present,
                    "title": title_value,
                }
            ],
            limitations=["A non-empty title still requires a relevance judgment."],
        )

    if "11.1.1" in selected:
        fields, group = _group(observation, "fields")
        weak = [
            item
            for item in fields
            if not any(
                item.get(key)
                for key in ("explicit_label", "aria_labelledby", "aria_label", "title")
            )
        ]
        _set_result(
            by_id["11.1.1"],
            "needs_review",
            confidence="medium",
            findings=[
                _finding(
                    "form-label",
                    "No explicit supported labelling mechanism was observed; confirm the RGAA branch in AX.",
                    target=item.get("target"),
                )
                for item in weak
            ],
            evidence=[_coverage_evidence("isolated-dom", group)],
            limitations=[
                "DOM heuristics do not implement the full accessible-name or RGAA labelling algorithm."
            ],
        )
    if "11.9.1" in selected:
        buttons, group = _group(observation, "buttons")
        unnamed = [item for item in buttons if not item.get("name_sources")]
        _set_result(
            by_id["11.9.1"],
            "needs_review",
            confidence="medium",
            findings=[
                _finding(
                    "button-accessible-name",
                    "No supported name source was observed for this form button; confirm in AX.",
                    target=item.get("target"),
                )
                for item in unnamed
            ],
            evidence=[_coverage_evidence("isolated-dom", group)],
            limitations=[
                "Button-name relevance and the complete AccName algorithm require review."
            ],
        )
    if "13.1.1" in selected:
        mechanisms, group = _group(observation, "refresh_mechanisms")
        _set_result(
            by_id["13.1.1"],
            "needs_review",
            confidence="medium",
            findings=[
                _finding(
                    "timed-refresh-candidate",
                    "A mechanism capable of refreshing content requires timing and control review.",
                    target=item.get("target"),
                    observed={"kind": item.get("kind"), "content": item.get("content")},
                    severity="moderate",
                )
                for item in mechanisms
            ],
            evidence=[_coverage_evidence("isolated-dom", group)],
            limitations=[
                "Absence of observed candidates does not prove non-applicability; script-driven changes and embedded content remain."
            ],
        )


def _collect_accessibility(client: CDPClient, budget: ExecutionBudget) -> dict[str, Any]:
    client.send("Accessibility.enable", timeout=budget.remaining())
    document = client.send(
        "DOM.getDocument",
        {"depth": 0, "pierce": False},
        timeout=budget.remaining(),
    )
    root = document.get("root", {})
    backend_node_id = root.get("backendNodeId") if isinstance(root, dict) else None
    if not isinstance(backend_node_id, int):
        raise CollectorExecutionError("RGAA accessibility root unavailable")
    response = client.send(
        "Accessibility.getPartialAXTree",
        {"backendNodeId": backend_node_id, "fetchRelatives": False},
        timeout=budget.remaining(),
    )
    nodes = response.get("nodes", [])
    if not isinstance(nodes, list):
        raise CollectorExecutionError("RGAA accessibility tree unavailable")
    return {
        "nodes": len(nodes),
        "ax_tree_collected": "bounded-root",
        "evidence_complete": False,
        "exposed_values": False,
    }


def _collect_focus(
    client: CDPClient,
    context_id: int,
    budget: ExecutionBudget,
    origin_guard: OriginGuard | None,
) -> dict[str, Any]:
    if origin_guard:
        origin_guard()
    reset_token = js.evaluate(
        client, FOCUS_RESET_PROBE, timeout=budget.remaining(), context_id=context_id
    )
    observations, seen = [], set()
    wrapped = False
    restoration = "not_possible"
    try:
        for _ in range(FOCUS_STEP_LIMIT):
            budget.consume("RGAA Tab traversal")
            if origin_guard:
                origin_guard()
            inputs.press_key(
                client,
                "Tab",
                remaining=budget.remaining,
                after_key_down=origin_guard,
            )
            if origin_guard:
                origin_guard()
            raw = js.evaluate(
                client,
                FOCUS_STATE_PROBE,
                await_promise=True,
                timeout=budget.remaining(),
                context_id=context_id,
            )
            if raw is None:
                break
            if not isinstance(raw, str):
                raise CollectorExecutionError("RGAA focus probe returned no value")
            try:
                state = json.loads(raw)
            except json.JSONDecodeError as error:
                raise CollectorExecutionError("RGAA focus probe returned invalid JSON") from error
            if not isinstance(state, dict) or not isinstance(state.get("target"), str):
                raise CollectorExecutionError("RGAA focus probe returned an invalid object")
            target = state["target"]
            if target in seen:
                wrapped = True
                break
            seen.add(target)
            observations.append(state)
    finally:
        try:
            if isinstance(reset_token, str):
                token = json.loads(reset_token)
                if isinstance(token, dict) or token is None:
                    restored = js.evaluate(
                        client,
                        FOCUS_RESTORE_PROBE.replace("__CDPX_FOCUS_TOKEN__", json.dumps(token)),
                        timeout=budget.remaining(),
                        context_id=context_id,
                    )
                    restoration = "completed" if restored is True else "failed"
        except _COLLECTOR_ERRORS + (json.JSONDecodeError,):
            restoration = "failed"
    return {
        "items": observations,
        "steps": len(observations),
        "wrapped": wrapped,
        "truncated": len(observations) >= FOCUS_STEP_LIMIT and not wrapped,
        "evidence_complete": False,
        "coverage_scope": "top-level and open-shadow focus chain; frames, closed shadow roots and visual deltas require review",
        "focus_restoration": restoration,
    }


def _apply_focus(
    by_id: dict[str, dict[str, Any]], focus: dict[str, Any], selected: set[str]
) -> None:
    evidence = [{"collector": "trusted-input+isolated-dom-css", **focus}]
    if "10.7.1" in selected:
        _set_result(
            by_id["10.7.1"],
            "needs_review",
            confidence="medium",
            evidence=evidence,
            limitations=[
                "Collected focus styles are observations; absence of outline or shadow is never treated as proof of failure."
            ],
        )
    if "12.8.1" in selected:
        _set_result(
            by_id["12.8.1"],
            "needs_review",
            confidence="medium",
            evidence=evidence,
            limitations=[
                "A bounded tab sequence cannot establish semantic coherence for every focusable target."
            ],
        )


def _collect_spacing(client: CDPClient, context_id: int, budget: ExecutionBudget) -> dict[str, Any]:
    token = f"cdpx-{context_id}"
    expression = TEXT_SPACING_PROBE.replace("__CDPX_SPACING_TOKEN__", json.dumps(token))
    observation: dict[str, Any] | None = None
    cleanup: dict[str, Any] = {"attempted": True, "completed": False}
    try:
        observation = _load_probe(client, context_id, expression, budget)
    finally:
        try:
            js.evaluate(client, TEXT_SPACING_CLEANUP, timeout=1.0, context_id=context_id)
            cleanup["completed"] = True
        except _COLLECTOR_ERRORS as error:
            cleanup["error"] = str(error)
    if observation is None:
        raise CollectorExecutionError("RGAA text-spacing observation unavailable")
    observation["cleanup"] = cleanup
    return observation


def _apply_spacing(by_id: dict[str, dict[str, Any]], spacing: dict[str, Any]) -> None:
    clipped = spacing.get("clipped", [])
    findings = [
        _finding(
            "text-spacing-clipping",
            "Text-spacing emulation introduced overflow under a clipping overflow policy; readability and exceptions require review.",
            target=item.get("target"),
            observed=item,
        )
        for item in clipped
        if isinstance(item, dict)
    ]
    _set_result(
        by_id["10.12.1"],
        "needs_review",
        confidence="medium",
        findings=findings,
        evidence=[{"collector": "isolated-runtime-dom-layout", **spacing}],
        limitations=[
            "Detected geometry is not sufficient to prove readability or every RGAA exception."
        ],
    )


def _mark_error(
    by_id: dict[str, dict[str, Any]],
    selected: set[str],
    affected: set[str] | frozenset[str],
    collector: str,
) -> None:
    for test_id in selected & set(affected):
        _set_result(
            by_id[test_id],
            "error",
            confidence="none",
            limitations=[f"Required collector did not complete: {collector}."],
        )


def _environment(
    client: CDPClient, context_id: int, budget: ExecutionBudget, url: str
) -> dict[str, Any]:
    environment: dict[str, Any] = {
        "cdpx_version": __version__,
        "native_resolver_version": RULE_VERSION,
        "final_url": url,
    }
    try:
        version = client.send("Browser.getVersion", timeout=budget.remaining())
        environment["browser"] = {
            key: version.get(key)
            for key in ("product", "userAgent", "jsVersion", "protocolVersion")
        }
    except _COLLECTOR_ERRORS as error:
        environment["browser_error"] = str(error)
    expression = r"""
    // __cdpx_rgaa_environment_v2
    (() => {
      const NODE_LIMIT = 5000, BYTE_LIMIT = 262144, encoder = new TextEncoder();
      const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      const chunks = []; let nodes = 0, bytes = 0, clipped = false, node = walker.currentNode;
      while (node && nodes < NODE_LIMIT && bytes < BYTE_LIMIT) {
        const value = node.nodeType === Node.TEXT_NODE ? String(node.nodeValue || "") : `<${node.localName}>`;
        const remaining = BYTE_LIMIT - bytes, boundedValue = value.slice(0, remaining);
        const raw = encoder.encode(boundedValue);
        if (boundedValue.length < value.length) clipped = true;
        if (raw.byteLength > remaining) clipped = true;
        const encoded = raw.slice(0, remaining);
        chunks.push(encoded); bytes += encoded.byteLength; nodes += 1; node = walker.nextNode();
      }
      const material = new Uint8Array(bytes); let offset = 0;
      for (const chunk of chunks) { material.set(chunk, offset); offset += chunk.byteLength; }
      let binary = "";
      for (let start = 0; start < material.length; start += 32768) binary += String.fromCharCode(...material.subarray(start, start + 32768));
      return JSON.stringify({user_agent: navigator.userAgent, locale: navigator.language, viewport: {width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio, visual_scale: visualViewport?.scale || 1}, media: {forced_colors: matchMedia("(forced-colors: active)").matches, prefers_contrast_more: matchMedia("(prefers-contrast: more)").matches, prefers_dark: matchMedia("(prefers-color-scheme: dark)").matches}, dom_material_base64: btoa(binary), nodes_examined: nodes, bytes_examined: bytes, truncated: clipped || Boolean(node), hash_complete: !clipped && !node});
    })()
    """
    try:
        page = _load_probe(client, context_id, expression, budget)
        encoded_material = page.pop("dom_material_base64", None)
        if not isinstance(encoded_material, str):
            raise CollectorExecutionError("RGAA DOM fingerprint material unavailable")
        try:
            material = base64.b64decode(encoded_material, validate=True)
        except (binascii.Error, ValueError) as error:
            raise CollectorExecutionError("RGAA DOM fingerprint material invalid") from error
        if len(material) > 262144 or page.get("bytes_examined") != len(material):
            raise CollectorExecutionError("RGAA DOM fingerprint material exceeded its bound")
        page["dom_sha256"] = hashlib.sha256(material).hexdigest()
        environment["page"] = page
    except _COLLECTOR_ERRORS as error:
        environment["page_error"] = str(error)
    return environment


def _rollup_verdict(values: list[str]) -> str:
    for verdict in ("fail", "error", "needs_review", "manual_only", "not_tested"):
        if verdict in values:
            return verdict
    return "pass" if "pass" in values else "not_applicable"


def summarize_hierarchy(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_test = {result["id"]: result for result in results}
    criteria, themes = [], []
    for topic in load_catalog()["topics"]:
        topic_results, topic_criteria = [], []
        for criterion in topic["criteria"]:
            criterion_results = [by_test[test_id] for test_id in criterion["test_ids"]]
            topic_results.extend(criterion_results)
            counts = Counter(item["verdict"] for item in criterion_results)
            item = {
                "id": criterion["id"],
                "theme_id": topic["id"],
                "title": criterion["title"],
                "verdict": _rollup_verdict([result["verdict"] for result in criterion_results]),
                "tests": len(criterion_results),
                **{verdict: counts[verdict] for verdict in VERDICTS},
            }
            criteria.append(item)
            topic_criteria.append(item)
        topic_counts = Counter(item["verdict"] for item in topic_results)
        themes.append(
            {
                "id": topic["id"],
                "title": topic["title"],
                "verdict": _rollup_verdict([item["verdict"] for item in topic_results]),
                "criteria": len(topic_criteria),
                "tests": len(topic_results),
                **{verdict: topic_counts[verdict] for verdict in VERDICTS},
            }
        )
    return criteria, themes


def _finish(
    results: list[dict[str, Any]],
    selected: set[str],
    *,
    url: str | None,
    scope: Scope,
    engine: Engine,
    plan: ScanPlan,
    collectors: dict[str, dict[str, Any]],
    providers: list[dict[str, Any]],
    environment: dict[str, Any],
    budget: ExecutionBudget,
    planned_navigations: int = 0,
    actions_start: int = 0,
) -> dict[str, Any]:
    for result in results:
        result.pop("_capabilities", None)
    counts = Counter(result["verdict"] for result in results)
    attempted = sum(
        1
        for result in results
        if result["id"] in selected
        and (result["evidence"] or result["findings"] or result["verdict"] == "error")
    )
    summary = {
        "official_tests": EXPECTED_COUNTS["tests"],
        "selected": len(selected),
        "evaluated": attempted,
        "collector_attempted": sum(
            status.get("status") in {"ok", "error"} for status in collectors.values()
        ),
        "evidence_collected": sum(
            bool(result["evidence"] or result["findings"]) for result in results
        ),
        **{verdict: counts[verdict] for verdict in VERDICTS},
        "automatically_resolved": counts["pass"] + counts["fail"] + counts["not_applicable"],
        "unresolved": counts["needs_review"] + counts["manual_only"] + counts["error"],
        "certification_claim": False,
    }
    criteria, themes = summarize_hierarchy(results)
    failed_collectors = [
        name for name, status in collectors.items() if status.get("status") == "error"
    ]
    failed_providers = [
        str(status.get("name", "provider"))
        for status in providers
        if status.get("status") == "error"
    ]
    execution_failures = failed_collectors + failed_providers
    execution_status = "partial" if execution_failures else "complete"
    if execution_failures and not any(
        status.get("status") == "ok" for status in collectors.values()
    ):
        execution_status = "error"
    return {
        "schema": "cdpx.rgaa.result/v1",
        "execution_status": execution_status,
        "audit_findings_present": any(result["findings"] for result in results),
        "rgaa_version": RGAA_VERSION,
        "catalog": {
            "id": CATALOG_ID,
            "source_commit": SOURCE_COMMIT,
            "catalog_sha256": catalog_sha256(),
        },
        "scope": {"mode": scope, "engine": engine, "url": url},
        "execution_plan": plan.public(navigations=planned_navigations),
        "environment": environment,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "themes": themes,
        "criteria": criteria,
        "collector_status": collectors,
        "providers": providers,
        "tests": results,
        "actions_used": budget.actions_used - actions_start,
        "limitations": [
            "This is an automated/assisted evidence report, never an RGAA certification.",
            "Partial evidence and unresolved human/AT work are never counted as passes.",
            "Chromium AX is not a substitute for NVDA, JAWS or VoiceOver testing.",
        ],
    }


def scan_error_report(
    *,
    scope: Scope,
    engine: Engine,
    selected_tests: tuple[str, ...] | None,
    error: Exception,
    budget: ExecutionBudget,
    planned_navigations: int = 0,
    actions_start: int = 0,
) -> dict[str, Any]:
    all_ids = {test["id"] for test in load_catalog()["tests"]}
    selected = set(selected_tests) if selected_tests is not None else all_ids
    results, by_id = _initial_results(selected)
    _mark_error(by_id, selected, selected, "page-navigation")
    plan = build_scan_plan(selected, scope=scope, engine=engine)
    return _finish(
        results,
        selected,
        url=None,
        scope=scope,
        engine=engine,
        plan=plan,
        collectors={"page-navigation": {"status": "error", "error": str(error)}},
        providers=[],
        environment={"cdpx_version": __version__, "native_resolver_version": RULE_VERSION},
        budget=budget,
        planned_navigations=planned_navigations,
        actions_start=actions_start,
    )


def scan(
    client: CDPClient,
    *,
    scope: Scope = "passive",
    engine: Engine = "native",
    selected_tests: tuple[str, ...] | None = None,
    timeout: float = 15.0,
    budget: ExecutionBudget | None = None,
    origin_guard: OriginGuard | None = None,
    planned_navigations: int = 0,
    actions_start: int = 0,
) -> dict[str, Any]:
    if scope not in {"passive", "interactive", "privileged"} or engine not in {"native", "hybrid"}:
        raise ValueError("unknown RGAA scope or engine")
    all_ids = {test["id"] for test in load_catalog()["tests"]}
    selected = set(selected_tests) if selected_tests is not None else all_ids
    unknown = selected - all_ids
    if unknown:
        raise ValueError(f"unknown RGAA test id(s): {', '.join(sorted(unknown))}")
    active_budget = budget or ExecutionBudget.start(timeout)
    plan = build_scan_plan(selected, scope=scope, engine=engine)
    results, by_id = _initial_results(selected)
    collectors: dict[str, dict[str, Any]] = {}
    providers: list[dict[str, Any]] = []
    if not any((plan.passive, plan.accessibility, plan.focus, plan.spacing, plan.axe)):
        for name in ("passive-dom-css", "accessibility", "focus", "text-spacing"):
            collectors[name] = {"status": "skipped", "reason": "no selected test requires it"}
        return _finish(
            results,
            selected,
            url=None,
            scope=scope,
            engine=engine,
            plan=plan,
            collectors=collectors,
            providers=providers,
            environment={"cdpx_version": __version__, "native_resolver_version": RULE_VERSION},
            budget=active_budget,
            planned_navigations=planned_navigations,
            actions_start=actions_start,
        )
    try:
        if origin_guard:
            origin_guard()
        identity = _document_identity(client, active_budget.remaining())
        if origin_guard:
            origin_guard()
        context_id = _isolated_world(client, active_budget, identity)
        _guard_document(client, active_budget, identity, origin_guard)
        url = identity.url
        environment = _environment(client, context_id, active_budget, url)
        collectors["environment"] = {
            "status": "error" if "page_error" in environment else "ok",
            "bounded": True,
            "hash_complete": environment.get("page", {}).get("hash_complete", False),
        }
        if "page_error" in environment:
            collectors["environment"]["error"] = environment["page_error"]
            _mark_error(by_id, selected, selected, "environment")
        _guard_document(client, active_budget, identity, origin_guard)
    except DocumentStateDrift as error:
        _mark_error(by_id, selected, selected, "document-state")
        return _finish(
            results,
            selected,
            url=None,
            scope=scope,
            engine=engine,
            plan=plan,
            collectors={"document-state": {"status": "error", "error": str(error)}},
            providers=providers,
            environment={
                "cdpx_version": __version__,
                "native_resolver_version": RULE_VERSION,
                "state_drift": True,
            },
            budget=active_budget,
            planned_navigations=planned_navigations,
            actions_start=actions_start,
        )
    except _COLLECTOR_ERRORS as error:
        _mark_error(by_id, selected, selected, "isolated-world")
        return _finish(
            results,
            selected,
            url=None,
            scope=scope,
            engine=engine,
            plan=plan,
            collectors={"isolated-world": {"status": "error", "error": str(error)}},
            providers=providers,
            environment={"cdpx_version": __version__, "native_resolver_version": RULE_VERSION},
            budget=active_budget,
            planned_navigations=planned_navigations,
            actions_start=actions_start,
        )

    def state_drift_report(error: DocumentStateDrift) -> dict[str, Any]:
        collectors["document-state"] = {"status": "error", "error": str(error)}
        environment["state_drift"] = True
        _mark_error(by_id, selected, selected, "document-state")
        return _finish(
            results,
            selected,
            url=url,
            scope=scope,
            engine=engine,
            plan=plan,
            collectors=collectors,
            providers=providers,
            environment=environment,
            budget=active_budget,
            planned_navigations=planned_navigations,
            actions_start=actions_start,
        )

    if plan.passive:
        try:
            _guard_document(client, active_budget, identity, origin_guard)
            observation = _load_probe(client, context_id, PASSIVE_PROBE, active_budget)
            _guard_document(client, active_budget, identity, origin_guard)
            _apply_passive(by_id, observation, selected)
            collectors["passive-dom-css"] = {"status": "ok", "isolated_world": True}
        except DocumentStateDrift as error:
            return state_drift_report(error)
        except _COLLECTOR_ERRORS as error:
            collectors["passive-dom-css"] = {
                "status": "error",
                "error": str(error),
                "isolated_world": True,
            }
            _mark_error(by_id, selected, PASSIVE_TESTS, "passive-dom-css")
    else:
        collectors["passive-dom-css"] = {
            "status": "skipped",
            "reason": "no selected test requires it",
        }

    if plan.accessibility:
        try:
            _guard_document(client, active_budget, identity, origin_guard)
            ax = _collect_accessibility(client, active_budget)
            _guard_document(client, active_budget, identity, origin_guard)
            collectors["accessibility"] = {"status": "ok", **ax}
            for test_id in selected & set(ACCESSIBILITY_TESTS):
                by_id[test_id]["evidence"].append({"collector": "accessibility", **ax})
        except DocumentStateDrift as error:
            return state_drift_report(error)
        except _COLLECTOR_ERRORS as error:
            collectors["accessibility"] = {"status": "error", "error": str(error)}
            _mark_error(by_id, selected, ACCESSIBILITY_TESTS, "accessibility")
    else:
        collectors["accessibility"] = {
            "status": "skipped",
            "reason": "no selected test requires it",
        }

    if plan.focus:
        try:
            focus = _collect_focus(client, context_id, active_budget, origin_guard)
            _apply_focus(by_id, focus, selected)
            collectors["focus"] = {
                "status": "ok" if focus["focus_restoration"] == "completed" else "error",
                "steps": focus["steps"],
                "truncated": focus["truncated"],
                "focus_restoration": focus["focus_restoration"],
            }
            if focus["focus_restoration"] != "completed":
                _mark_error(by_id, selected, FOCUS_TESTS, "focus-restoration")
        except DocumentStateDrift as error:
            return state_drift_report(error)
        except _COLLECTOR_ERRORS as error:
            collectors["focus"] = {"status": "error", "error": str(error)}
            _mark_error(by_id, selected, FOCUS_TESTS, "focus")
    else:
        collectors["focus"] = {
            "status": "skipped",
            "reason": "scope or selection does not require it",
        }

    if plan.spacing:
        try:
            _guard_document(client, active_budget, identity, origin_guard)
            spacing = _collect_spacing(client, context_id, active_budget)
            _guard_document(client, active_budget, identity, origin_guard)
            _apply_spacing(by_id, spacing)
            collectors["text-spacing"] = {
                "status": "ok" if spacing["cleanup"]["completed"] else "error",
                "truncated": spacing.get("truncated", False),
                "cleanup": spacing["cleanup"],
            }
            if not spacing["cleanup"]["completed"]:
                _mark_error(by_id, selected, SPACING_TESTS, "text-spacing-cleanup")
        except DocumentStateDrift as error:
            return state_drift_report(error)
        except _COLLECTOR_ERRORS as error:
            collectors["text-spacing"] = {
                "status": "error",
                "error": str(error),
                "cleanup": "attempted",
            }
            _mark_error(by_id, selected, SPACING_TESTS, "text-spacing")
    else:
        collectors["text-spacing"] = {
            "status": "skipped",
            "reason": "scope or selection does not require it",
        }

    if plan.axe:
        try:
            _guard_document(client, active_budget, identity, origin_guard)
            axe = provider.run_axe(client, remaining=active_budget.remaining)
            _guard_document(client, active_budget, identity, origin_guard)
            providers.append(
                {key: value for key, value in axe.items() if key != "result"} | {"status": "ok"}
            )
            for test_id, observations in provider.mapped_observations(axe).items():
                if test_id in selected:
                    by_id[test_id]["advisory"].extend(observations)
        except DocumentStateDrift as error:
            return state_drift_report(error)
        except _COLLECTOR_ERRORS as error:
            providers.append(
                {
                    "name": "axe-core",
                    "status": "error",
                    "error": str(error),
                    "verdict_authority": "advisory_only",
                }
            )
    elif engine == "hybrid":
        providers.append(
            {
                "name": "axe-core",
                "status": "skipped",
                "reason": "no selected test has an axe mapping",
                "verdict_authority": "advisory_only",
            }
        )

    for result in results:
        if result["id"] not in selected:
            _set_result(
                result, "not_tested", confidence="none", findings=[], evidence=[], limitations=[]
            )
    try:
        _guard_document(client, active_budget, identity, origin_guard)
    except DocumentStateDrift as error:
        return state_drift_report(error)
    return _finish(
        results,
        selected,
        url=url,
        scope=scope,
        engine=engine,
        plan=plan,
        collectors=collectors,
        providers=providers,
        environment=environment,
        budget=active_budget,
        planned_navigations=planned_navigations,
        actions_start=actions_start,
    )
