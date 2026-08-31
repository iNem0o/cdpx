"""Versioned, bounded multi-page RGAA sample manifests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml

from cdpx.client import CDPClient, CDPError, CDPTimeout, CDPTransportError
from cdpx.policy import Authority, PolicyError, origin_from_url
from cdpx.primitives import nav
from cdpx.rgaa.catalog import EXPECTED_COUNTS, RGAA_VERSION, test_index
from cdpx.rgaa.plan import ExecutionBudget, build_scan_plan
from cdpx.rgaa.scanner import (
    VERDICTS,
    Engine,
    Scope,
    finalize_report_error,
    scan,
    scan_error_report,
    summarize_hierarchy,
)

MAX_SAMPLE_BYTES = 1024 * 1024
MAX_PAGES = 50
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: yaml.Loader, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping keys must be strings",
                key_node.start_mark,
            )
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key: {key}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


@dataclass(frozen=True)
class SamplePage:
    id: str
    url: str
    tests: tuple[str, ...] | None


@dataclass(frozen=True)
class CompiledSample:
    source: str
    scope: Scope
    engine: Engine
    pages: tuple[SamplePage, ...]
    digest: str

    @property
    def authority(self) -> Authority:
        known = set(test_index())
        required = Authority.OBSERVATION
        for page in self.pages:
            plan = build_scan_plan(
                set(page.tests) if page.tests is not None else known,
                scope=self.scope,
                engine=self.engine,
            )
            if plan.required_authority is Authority.PRIVILEGED:
                return Authority.PRIVILEGED
            if plan.required_authority is Authority.INTERACTION:
                required = plan.required_authority
        return required

    def public_plan(self) -> dict[str, Any]:
        known = set(test_index())
        page_plans = [
            build_scan_plan(
                set(page.tests) if page.tests is not None else known,
                scope=self.scope,
                engine=self.engine,
            )
            for page in self.pages
        ]
        interactions = sum(plan.maximum_actions for plan in page_plans)
        navigations = len(self.pages)
        return {
            "schema": "cdpx.rgaa.sample-plan/v1",
            "rgaa_version": RGAA_VERSION,
            "source": self.source,
            "scope": self.scope,
            "engine": self.engine,
            "required_authority": self.authority.value,
            "pages": [
                {
                    "id": page.id,
                    "url": page.url,
                    "tests": list(page.tests) if page.tests is not None else None,
                    "collectors": plan.public()["collectors"],
                    "planned_actions": plan.public(navigations=1)["planned_actions"],
                    "required_authority": plan.required_authority.value,
                }
                for page, plan in zip(self.pages, page_plans, strict=True)
            ],
            "page_count": len(self.pages),
            "planned_actions": {
                "navigations": navigations,
                "interactions": interactions,
                "total": navigations + interactions,
            },
            "maximum_interactive_actions": interactions,
            "composition_digest": self.digest,
        }


def _http_url(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"RGAA sample: {label} non-empty URL required")
    url = value.strip()
    try:
        origin_from_url(url)
    except PolicyError as error:
        raise ValueError(
            f"RGAA sample: {label} must be an HTTP(S) URL without credentials"
        ) from error
    return url


def _page_tests(value: Any, *, label: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"RGAA sample: {label}.tests must be a list of test IDs")
    if not value:
        raise ValueError(f"RGAA sample: {label}.tests must not be empty")
    normalized = tuple(item.strip() for item in value)
    if any(not item for item in normalized):
        raise ValueError(f"RGAA sample: {label}.tests must not contain blank test IDs")
    if any("," in item for item in normalized):
        raise ValueError(f"RGAA sample: {label}.tests requires one ID per item")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"RGAA sample: {label}.tests contains duplicate test IDs")
    unknown = sorted(set(normalized) - set(test_index()))
    if unknown:
        raise ValueError(f"RGAA sample: unknown RGAA test id(s): {', '.join(unknown)}")
    return normalized


def _read_manifest(source: Path) -> bytes:
    current = source.parent
    while current != current.parent:
        if current.is_symlink():
            raise ValueError("RGAA sample: symbolic path component forbidden")
        current = current.parent
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise ValueError(f"RGAA sample: unreadable manifest: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("RGAA sample: manifest must be a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(MAX_SAMPLE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(payload) > MAX_SAMPLE_BYTES:
        raise ValueError("RGAA sample: manifest exceeds 1 MiB")
    return payload


def compile_sample(path: str | Path) -> CompiledSample:
    source = Path(path)
    payload = _read_manifest(source)
    try:
        parsed = yaml.load(payload.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"RGAA sample: invalid YAML: {error}") from error
    if not isinstance(parsed, dict):
        raise ValueError("RGAA sample: root object required")
    allowed = {"schema", "rgaa_version", "scope", "engine", "base_url", "pages"}
    unknown = sorted(set(parsed) - allowed)
    if unknown:
        raise ValueError(f"RGAA sample: unknown field(s): {', '.join(unknown)}")
    if parsed.get("schema") != "cdpx.rgaa.sample/v1":
        raise ValueError("RGAA sample: schema must be cdpx.rgaa.sample/v1")
    if parsed.get("rgaa_version", RGAA_VERSION) != RGAA_VERSION:
        raise ValueError(f"RGAA sample: only RGAA {RGAA_VERSION} is vendored")
    scope = parsed.get("scope", "passive")
    engine = parsed.get("engine", "native")
    if scope not in {"passive", "interactive", "privileged"}:
        raise ValueError("RGAA sample: scope must be passive, interactive or privileged")
    if engine not in {"native", "hybrid"}:
        raise ValueError("RGAA sample: engine must be native or hybrid")
    base_url = parsed.get("base_url")
    if base_url is not None:
        base_url = _http_url(base_url, label="base_url")
    raw_pages = parsed.get("pages")
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= MAX_PAGES:
        raise ValueError(f"RGAA sample: pages must contain 1 to {MAX_PAGES} entries")
    pages: list[SamplePage] = []
    seen: set[str] = set()
    for index, raw_page in enumerate(raw_pages):
        label = f"pages[{index}]"
        if not isinstance(raw_page, dict):
            raise ValueError(f"RGAA sample: {label} object required")
        unknown_page = sorted(set(raw_page) - {"id", "url", "tests"})
        if unknown_page:
            raise ValueError(f"RGAA sample: {label} unknown field(s): {', '.join(unknown_page)}")
        page_id = raw_page.get("id")
        if not isinstance(page_id, str) or not PAGE_ID_RE.fullmatch(page_id):
            raise ValueError(f"RGAA sample: {label}.id invalid")
        if page_id in seen:
            raise ValueError(f"RGAA sample: duplicate page id: {page_id}")
        seen.add(page_id)
        raw_url = raw_page.get("url")
        if base_url is not None and isinstance(raw_url, str):
            raw_url = urljoin(base_url.rstrip("/") + "/", raw_url)
        url = _http_url(raw_url, label=f"{label}.url")
        pages.append(SamplePage(page_id, url, _page_tests(raw_page.get("tests"), label=label)))
    canonical = {
        "schema": "cdpx.rgaa.sample/v1",
        "rgaa_version": RGAA_VERSION,
        "scope": scope,
        "engine": engine,
        "pages": [
            {"id": page.id, "url": page.url, "tests": list(page.tests) if page.tests else None}
            for page in pages
        ],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CompiledSample(source.name, scope, engine, tuple(pages), digest)


_PRECEDENCE = {
    "not_tested": 0,
    "not_applicable": 1,
    "pass": 2,
    "manual_only": 3,
    "needs_review": 4,
    "error": 5,
    "fail": 6,
}


def _aggregate(page_reports: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    known = test_index()
    aggregated = []
    for test_id in known:
        page_values = []
        for report in page_reports:
            match = next(test for test in report["report"]["tests"] if test["id"] == test_id)
            page_values.append({"page_id": report["page_id"], "verdict": match["verdict"]})
        coverage_complete = all(item["verdict"] != "not_tested" for item in page_values)
        verdict = max((item["verdict"] for item in page_values), key=_PRECEDENCE.__getitem__)
        if not coverage_complete and verdict in {"pass", "not_applicable"}:
            verdict = "needs_review"
        aggregated.append(
            {
                "id": test_id,
                "theme_id": known[test_id]["theme_id"],
                "criterion_id": known[test_id]["criterion_id"],
                "verdict": verdict,
                "pages": page_values,
                "tested_pages": sum(item["verdict"] != "not_tested" for item in page_values),
                "excluded_pages": sum(item["verdict"] == "not_tested" for item in page_values),
                "coverage_complete": coverage_complete,
            }
        )
    counts = Counter(item["verdict"] for item in aggregated)
    summary = {
        "official_tests": EXPECTED_COUNTS["tests"],
        "pages": len(page_reports),
        **{verdict: counts[verdict] for verdict in VERDICTS},
        "resolved": counts["pass"] + counts["fail"] + counts["not_applicable"],
        "certification_claim": False,
    }
    return aggregated, summary


def finalize_sample_report_error(
    report: dict[str, Any],
    error: Exception,
    *,
    collector: str = "final-document-verification",
) -> dict[str, Any]:
    """Invalidate page evidence and rebuild a sample after its final guard fails."""
    for page in report.get("pages", []):
        if not isinstance(page, dict) or not isinstance(page.get("report"), dict):
            continue
        finalize_report_error(page["report"], error, collector=collector)

    page_reports = [
        page
        for page in report.get("pages", [])
        if isinstance(page, dict) and isinstance(page.get("report"), dict)
    ]
    tests, summary = _aggregate(page_reports)
    report["tests"] = tests
    report["summary"] = summary
    report["criteria"], report["themes"] = summarize_hierarchy(tests)
    report["execution_status"] = "partial"
    statuses = report.setdefault("collector_status", {})
    statuses["sample-pages"] = {"status": "partial"}
    statuses[collector] = {"status": "error", "error": str(error)}
    return report


def run_sample(
    client: CDPClient,
    compiled: CompiledSample,
    *,
    timeout: float,
    budget: ExecutionBudget | None = None,
    origin_guard: Callable[[float], str | None] | None = None,
) -> dict[str, Any]:
    active_budget = budget or ExecutionBudget.start(timeout)
    page_reports: list[dict[str, Any]] = []
    for page in compiled.pages:
        page_actions_start = active_budget.actions_used
        try:
            active_budget.consume(f"navigation to sample page {page.id}")
            nav.navigate(client, page.url, wait="load", timeout=active_budget.remaining())
            verified_url = page.url
            if origin_guard is not None:
                guarded_url = origin_guard(active_budget.remaining())
                if isinstance(guarded_url, str):
                    verified_url = guarded_url
            report = scan(
                client,
                scope=compiled.scope,
                engine=compiled.engine,
                selected_tests=page.tests,
                timeout=timeout,
                budget=active_budget,
                origin_guard=origin_guard,
                planned_navigations=1,
                actions_start=page_actions_start,
                document_url=verified_url,
            )
            if origin_guard is not None:
                try:
                    origin_guard(active_budget.remaining())
                except (CDPError, CDPTimeout, CDPTransportError, TimeoutError) as error:
                    finalize_report_error(report, error)
        except PolicyError:
            raise
        except (
            nav.NavigationError,
            CDPError,
            CDPTimeout,
            CDPTransportError,
            TimeoutError,
        ) as error:
            report = scan_error_report(
                scope=compiled.scope,
                engine=compiled.engine,
                selected_tests=page.tests,
                error=error,
                budget=active_budget,
                planned_navigations=1,
                actions_start=page_actions_start,
            )
        page_reports.append({"page_id": page.id, "url": page.url, "report": report})
    tests, summary = _aggregate(page_reports)
    criteria, themes = summarize_hierarchy(tests)
    page_statuses: list[str] = [item["report"]["execution_status"] for item in page_reports]
    execution_status = "complete"
    if any(status != "complete" for status in page_statuses):
        execution_status = (
            "error" if all(status == "error" for status in page_statuses) else "partial"
        )
    return {
        "schema": "cdpx.rgaa.sample-result/v1",
        "execution_status": execution_status,
        "audit_findings_present": any(
            item["report"]["audit_findings_present"] for item in page_reports
        ),
        "rgaa_version": RGAA_VERSION,
        "sample": compiled.public_plan(),
        "summary": summary,
        "themes": themes,
        "criteria": criteria,
        "tests": tests,
        "pages": page_reports,
        "collector_status": {
            "sample-pages": {"status": "ok" if execution_status == "complete" else execution_status}
        },
        "actions_used": active_budget.actions_used,
        "limitations": [
            "The declared sample is auditable evidence, not proof that the sample "
            "is representative.",
            "Cross-page aggregation preserves unresolved and failing verdicts; "
            "it never certifies a service.",
        ],
    }
