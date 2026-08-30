"""Prudent test-level RGAA resolver over bounded CDP observations."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Literal

from cdpx.client import CDPClient, CDPError, CDPTimeout
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
from cdpx.rgaa.probes import FOCUS_STATE_PROBE, PASSIVE_PROBE, TEXT_SPACING_PROBE

Scope = Literal["passive", "interactive", "privileged"]
Engine = Literal["native", "hybrid"]
VERDICTS = (
    "pass",
    "fail",
    "not_applicable",
    "needs_review",
    "manual_only",
    "error",
    "not_tested",
)


def _finding(
    rule_id: str,
    message: str,
    *,
    target: str | None = None,
    observed: Any = None,
    severity: str = "serious",
) -> dict[str, Any]:
    finding = {"rule_id": rule_id, "severity": severity, "message": message}
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
) -> None:
    if verdict not in VERDICTS:
        raise ValueError(f"unknown RGAA verdict: {verdict}")
    result["verdict"] = verdict
    result["confidence"] = confidence
    if findings is not None:
        result["findings"] = findings
    if evidence is not None:
        result["evidence"] = evidence
    if limitations is not None:
        result["limitations"] = limitations


def _initial_results(selected: set[str]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    results = []
    by_id = {}
    for test in load_catalog()["tests"]:
        profile = test["automation"]
        if test["id"] not in selected:
            verdict = "not_tested"
        else:
            verdict = profile["default_unresolved_verdict"]
        result = {
            "id": test["id"],
            "theme_id": test["theme_id"],
            "criterion_id": test["criterion_id"],
            "verdict": verdict,
            "automation": profile["automation_class"],
            "confidence": profile["confidence"],
            "findings": [],
            "evidence": [],
            "advisory": [],
            "limitations": [profile["limitations"]],
        }
        results.append(result)
        by_id[test["id"]] = result
    return results, by_id


def _load_probe(client: CDPClient, expression: str, *, timeout: float) -> dict[str, Any]:
    raw = js.evaluate(client, expression, await_promise=True, timeout=timeout)
    if not isinstance(raw, str):
        raise ValueError("RGAA page probe returned no value")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("RGAA page probe returned invalid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("RGAA page probe returned an invalid object")
    return value


def _items(observation: dict[str, Any], key: str) -> list[dict[str, Any]]:
    group = observation.get(key, {})
    items = group.get("items", []) if isinstance(group, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _apply_passive(by_id: dict[str, dict[str, Any]], observation: dict[str, Any]) -> None:
    frames = _items(observation, "frames")
    if not frames:
        _set_result(by_id["2.1.1"], "not_applicable", confidence="high")
    else:
        missing = [item for item in frames if not item.get("title_present")]
        _set_result(
            by_id["2.1.1"],
            "fail" if missing else "pass",
            confidence="high",
            findings=[
                _finding(
                    "frame-title", "Frame has no title attribute.", target=item.get("selector")
                )
                for item in missing
            ],
            evidence=[{"collector": "dom", "frames": len(frames)}],
        )

    contrast = observation.get("contrast", {})
    contrast_items = contrast.get("items", []) if isinstance(contrast, dict) else []
    unresolved = int(contrast.get("unresolved", 0)) if isinstance(contrast, dict) else 0
    for test_id in ("3.2.1", "3.2.2", "3.2.3", "3.2.4"):
        candidates = [
            item
            for item in contrast_items
            if isinstance(item, dict) and item.get("test_id") == test_id
        ]
        failures = [item for item in candidates if item.get("ratio", 0) < item.get("required", 0)]
        findings = [
            _finding(
                "text-contrast-solid",
                f"Contrast ratio {item.get('ratio')}:1 is below {item.get('required')}:1.",
                target=item.get("selector"),
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
        ]
        if failures:
            verdict = "fail"
        elif unresolved:
            verdict = "needs_review"
        elif candidates:
            verdict = "pass"
        else:
            verdict = "not_applicable"
        _set_result(
            by_id[test_id],
            verdict,
            confidence="high" if verdict in {"pass", "fail", "not_applicable"} else "medium",
            findings=findings,
            evidence=[
                {
                    "collector": "dom-css",
                    "solid_candidates": len(candidates),
                    "complex_background_candidates": unresolved,
                }
            ],
            limitations=(
                ["Complex, transparent, gradient, image and composited backgrounds require review."]
                if unresolved
                else []
            ),
        )

    links = _items(observation, "links")
    blank_links = [item for item in links if not item.get("name")]
    if not links:
        link_verdict = "not_applicable"
    elif blank_links:
        link_verdict = "fail"
    else:
        link_verdict = "needs_review"
    _set_result(
        by_id["6.1.1"],
        link_verdict,
        confidence="high" if link_verdict in {"fail", "not_applicable"} else "medium",
        findings=[
            _finding(
                "link-accessible-name", "Link has no accessible name.", target=item.get("selector")
            )
            for item in blank_links
        ],
        evidence=[{"collector": "dom", "links": len(links)}],
        limitations=[]
        if blank_links
        else ["Link purpose and surrounding context remain semantic judgments."],
    )

    doctype = observation.get("doctype", {})
    if not isinstance(doctype, dict) or not doctype.get("present"):
        doctype_verdict = "fail"
        doctype_findings = [_finding("document-type", "Document has no DOCTYPE.")]
    elif str(doctype.get("name", "")).lower() != "html":
        doctype_verdict = "fail"
        doctype_findings = [
            _finding("document-type", "Document type is not HTML.", observed=doctype)
        ]
    elif not doctype.get("public_id") and not doctype.get("system_id"):
        doctype_verdict = "pass"
        doctype_findings = []
    else:
        doctype_verdict = "needs_review"
        doctype_findings = []
    _set_result(
        by_id["8.1.1"],
        doctype_verdict,
        confidence="high" if doctype_verdict != "needs_review" else "medium",
        findings=doctype_findings,
        evidence=[{"collector": "dom", "doctype": doctype}],
        limitations=[]
        if doctype_verdict != "needs_review"
        else ["Legacy public/system identifiers require validation."],
    )

    language = observation.get("language", {})
    lang = ""
    if isinstance(language, dict):
        lang = str(language.get("lang") or language.get("xml_lang") or "").strip()
    _set_result(
        by_id["8.3.1"],
        "pass" if lang else "fail",
        confidence="high",
        findings=[]
        if lang
        else [_finding("default-language", "Document has no default language.")],
        evidence=[{"collector": "dom", "language": lang or None}],
    )
    plausible_language = bool(re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", lang))
    if not lang or not plausible_language:
        language_verdict = "fail"
        language_findings = [
            _finding(
                "default-language-validity",
                "Default language code is absent or structurally invalid.",
                observed=lang or None,
            )
        ]
    else:
        language_verdict = "needs_review"
        language_findings = []
    _set_result(
        by_id["8.4.1"],
        language_verdict,
        confidence="high" if language_verdict == "fail" else "medium",
        findings=language_findings,
        evidence=[
            {"collector": "dom", "language": lang or None, "syntax_plausible": plausible_language}
        ],
        limitations=[
            "A syntactically plausible code does not prove that it matches the page language."
        ],
    )

    title = observation.get("title", {})
    title_present = isinstance(title, dict) and bool(title.get("present"))
    title_value = str(title.get("value", "")).strip() if isinstance(title, dict) else ""
    _set_result(
        by_id["8.5.1"],
        "pass" if title_present else "fail",
        confidence="high",
        findings=[]
        if title_present
        else [_finding("document-title-presence", "Document has no title element.")],
        evidence=[{"collector": "dom", "title_present": title_present}],
    )
    _set_result(
        by_id["8.6.1"],
        "needs_review" if title_value else "fail",
        confidence="medium" if title_value else "high",
        findings=[]
        if title_value
        else [_finding("document-title-relevance", "Document title is empty.")],
        evidence=[{"collector": "dom", "title": title_value}],
        limitations=["A non-empty title still requires a relevance judgment."],
    )

    fields = _items(observation, "fields")
    unlabelled = [item for item in fields if not item.get("labelled")]
    field_verdict = "not_applicable" if not fields else ("fail" if unlabelled else "pass")
    _set_result(
        by_id["11.1.1"],
        field_verdict,
        confidence="high",
        findings=[
            _finding(
                "form-label",
                "Form field has no supported label mechanism.",
                target=item.get("selector"),
            )
            for item in unlabelled
        ],
        evidence=[{"collector": "dom", "fields": len(fields)}],
    )

    buttons = _items(observation, "buttons")
    unnamed_buttons = [item for item in buttons if not item.get("name")]
    if not buttons:
        button_verdict = "not_applicable"
    elif unnamed_buttons:
        button_verdict = "fail"
    else:
        button_verdict = "needs_review"
    _set_result(
        by_id["11.9.1"],
        button_verdict,
        confidence="high" if button_verdict in {"fail", "not_applicable"} else "medium",
        findings=[
            _finding(
                "button-accessible-name",
                "Button has no accessible name.",
                target=item.get("selector"),
            )
            for item in unnamed_buttons
        ],
        evidence=[{"collector": "dom", "buttons": len(buttons)}],
        limitations=[]
        if unnamed_buttons
        else ["Name relevance and visible-label consistency require review."],
    )

    refreshes = _items(observation, "meta_refresh")
    _set_result(
        by_id["13.1.1"],
        "not_applicable" if not refreshes else "needs_review",
        confidence="high" if not refreshes else "medium",
        findings=[
            _finding(
                "meta-refresh",
                "A meta refresh requires expert review of timing controls.",
                target=item.get("selector"),
                observed=item.get("content"),
                severity="moderate",
            )
            for item in refreshes
        ],
        evidence=[{"collector": "dom", "meta_refresh": len(refreshes)}],
        limitations=[
            "Script, SVG, canvas, object and embed refresh mechanisms are not "
            "exhaustively inferred."
        ],
    )


def _collect_focus(client: CDPClient, *, timeout: float, steps: int = 20) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _ in range(steps):
        inputs.press_key(client, "Tab")
        raw = js.evaluate(client, FOCUS_STATE_PROBE, timeout=timeout)
        if not isinstance(raw, str):
            raise ValueError("RGAA focus probe returned no value")
        try:
            state = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("RGAA focus probe returned invalid JSON") from error
        if state is None:
            break
        if not isinstance(state, dict):
            raise ValueError("RGAA focus probe returned an invalid object")
        selector = state.get("selector")
        if not isinstance(selector, str) or not selector:
            break
        if selector in seen:
            break
        seen.add(selector)
        observations.append(state)
    return observations


def _apply_interactive(
    client: CDPClient, by_id: dict[str, dict[str, Any]], *, timeout: float
) -> None:
    focus = _collect_focus(client, timeout=timeout)
    invisible = [item for item in focus if not item.get("indicator_detected")]
    focus_verdict = "not_applicable" if not focus else ("fail" if invisible else "needs_review")
    _set_result(
        by_id["10.7.1"],
        focus_verdict,
        confidence="medium" if focus else "high",
        findings=[
            _finding(
                "focus-indicator",
                "No CSS outline or box-shadow focus indicator was detected.",
                target=item.get("selector"),
                observed=item,
            )
            for item in invisible
        ],
        evidence=[{"collector": "input-dom-css", "focus_sequence": focus}],
        limitations=[
            "Visual focus can use mechanisms outside the CSS outline/box-shadow heuristic."
        ],
    )
    _set_result(
        by_id["12.8.1"],
        "not_applicable" if not focus else "needs_review",
        confidence="medium" if focus else "high",
        evidence=[
            {
                "collector": "input-dom",
                "forward_tab_sequence": [item.get("selector") for item in focus],
            }
        ],
        limitations=[
            "DOM order evidence cannot establish that the tab order is semantically coherent."
        ],
    )


def _apply_spacing(client: CDPClient, by_id: dict[str, dict[str, Any]], *, timeout: float) -> None:
    spacing = _load_probe(client, TEXT_SPACING_PROBE, timeout=timeout)
    candidates = int(spacing.get("candidates", 0))
    clipped = spacing.get("clipped", [])
    clipped = (
        [item for item in clipped if isinstance(item, dict)] if isinstance(clipped, list) else []
    )
    verdict = "not_applicable" if not candidates else ("fail" if clipped else "needs_review")
    _set_result(
        by_id["10.12.1"],
        verdict,
        confidence="medium" if candidates else "high",
        findings=[
            _finding(
                "text-spacing",
                "Text spacing introduced new clipping or overflow.",
                target=item.get("selector"),
                observed=item,
            )
            for item in clipped
        ],
        evidence=[{"collector": "runtime-dom-layout", **spacing}],
        limitations=[
            "Absence of detected clipping does not prove that every transformed "
            "text remains readable."
        ],
    )


def _rollup_verdict(values: list[str]) -> str:
    """Conservatively collapse child verdicts without inventing compliance."""
    for verdict in ("fail", "error", "needs_review", "manual_only", "not_tested"):
        if verdict in values:
            return verdict
    if "pass" in values:
        return "pass"
    return "not_applicable"


def summarize_hierarchy(
    results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the official criterion/theme hierarchy from test-level truth."""
    by_test = {result["id"]: result for result in results}
    criteria: list[dict[str, Any]] = []
    themes: list[dict[str, Any]] = []
    for topic in load_catalog()["topics"]:
        topic_results: list[dict[str, Any]] = []
        topic_criteria: list[dict[str, Any]] = []
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


def _summaries(
    results: list[dict[str, Any]], selected: set[str]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    counts = Counter(result["verdict"] for result in results)
    summary = {
        "official_tests": EXPECTED_COUNTS["tests"],
        "selected": len(selected),
        "evaluated": sum(counts[verdict] for verdict in VERDICTS if verdict != "not_tested"),
        **{verdict: counts[verdict] for verdict in VERDICTS},
        "resolved": counts["pass"] + counts["fail"] + counts["not_applicable"],
        "certification_claim": False,
    }
    criteria, themes = summarize_hierarchy(results)
    return summary, criteria, themes


def scan(
    client: CDPClient,
    *,
    scope: Scope = "passive",
    engine: Engine = "native",
    selected_tests: tuple[str, ...] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    if scope not in {"passive", "interactive", "privileged"}:
        raise ValueError(f"unknown RGAA scope: {scope}")
    if engine not in {"native", "hybrid"}:
        raise ValueError(f"unknown RGAA engine: {engine}")
    catalog = load_catalog()
    all_ids = {test["id"] for test in catalog["tests"]}
    selected = set(selected_tests) if selected_tests is not None else set(all_ids)
    unknown = selected - all_ids
    if unknown:
        raise ValueError(f"unknown RGAA test id(s): {', '.join(sorted(unknown))}")
    results, by_id = _initial_results(selected)

    observation = _load_probe(client, PASSIVE_PROBE, timeout=timeout)
    _apply_passive(by_id, observation)
    if scope in {"interactive", "privileged"}:
        _apply_interactive(client, by_id, timeout=timeout)
    if scope == "privileged":
        _apply_spacing(client, by_id, timeout=timeout)

    providers: list[dict[str, Any]] = []
    if engine == "hybrid":
        try:
            axe = provider.run_axe(client, timeout=timeout)
        except (CDPError, CDPTimeout, JSException, ValueError) as error:
            providers.append({"name": "axe-core", "status": "error", "error": str(error)})
            for test_ids in provider.AXE_TO_RGAA.values():
                for test_id in test_ids:
                    if test_id in selected and by_id[test_id]["verdict"] in {
                        "needs_review",
                        "manual_only",
                    }:
                        _set_result(
                            by_id[test_id],
                            "error",
                            confidence="none",
                            limitations=["The requested advisory provider did not complete."],
                        )
        else:
            providers.append(
                {key: value for key, value in axe.items() if key != "result"} | {"status": "ok"}
            )
            for test_id, observations in provider.mapped_observations(axe).items():
                if test_id in selected:
                    by_id[test_id]["advisory"].extend(observations)

    for result in results:
        if result["id"] not in selected:
            _set_result(
                result, "not_tested", confidence="none", findings=[], evidence=[], limitations=[]
            )

    summary, criteria, themes = _summaries(results, selected)
    return {
        "schema": "cdpx.rgaa.result/v1",
        "rgaa_version": RGAA_VERSION,
        "catalog": {
            "id": CATALOG_ID,
            "source_commit": SOURCE_COMMIT,
            "catalog_sha256": catalog_sha256(),
        },
        "scope": {"mode": scope, "engine": engine, "url": observation.get("url")},
        "summary": summary,
        "themes": themes,
        "criteria": criteria,
        "providers": providers,
        "tests": results,
        "limitations": [
            "This result is an automated/assisted evidence report, never an RGAA certification.",
            "needs_review and manual_only are unresolved work, not passes.",
            "Chromium accessibility data is not a substitute for NVDA, JAWS or VoiceOver testing.",
        ],
    }
