"""Versioned, bounded multi-page RGAA sample manifests."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import yaml

from cdpx.client import CDPClient
from cdpx.policy import Authority
from cdpx.primitives import nav
from cdpx.rgaa.catalog import EXPECTED_COUNTS, RGAA_VERSION, parse_test_selection, test_index
from cdpx.rgaa.scanner import VERDICTS, Engine, Scope, scan, summarize_hierarchy

MAX_SAMPLE_BYTES = 1024 * 1024
MAX_PAGES = 50
PAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


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
        if self.engine == "hybrid" or self.scope == "privileged":
            return Authority.PRIVILEGED
        if self.scope == "interactive":
            return Authority.INTERACTION
        return Authority.OBSERVATION

    def public_plan(self) -> dict[str, Any]:
        return {
            "schema": "cdpx.rgaa.sample-plan/v1",
            "rgaa_version": RGAA_VERSION,
            "source": self.source,
            "scope": self.scope,
            "engine": self.engine,
            "required_authority": self.authority.value,
            "pages": [
                {"id": page.id, "url": page.url, "tests": list(page.tests) if page.tests else None}
                for page in self.pages
            ],
            "page_count": len(self.pages),
            "composition_digest": self.digest,
        }


def _http_url(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"RGAA sample: {label} non-empty URL required")
    url = value.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"RGAA sample: {label} must be an HTTP(S) URL without credentials")
    return url


def _page_tests(value: Any, *, label: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"RGAA sample: {label}.tests must be a list of test IDs")
    return parse_test_selection(",".join(value))


def compile_sample(path: str | Path) -> CompiledSample:
    source = Path(path)
    if source.is_symlink():
        raise ValueError("RGAA sample: symbolic manifest forbidden")
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise ValueError(f"RGAA sample: unreadable manifest: {error}") from error
    if len(payload) > MAX_SAMPLE_BYTES:
        raise ValueError("RGAA sample: manifest exceeds 1 MiB")
    try:
        parsed = yaml.safe_load(payload.decode("utf-8"))
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
    return CompiledSample(str(source.resolve()), scope, engine, tuple(pages), digest)


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
        verdict = max((item["verdict"] for item in page_values), key=_PRECEDENCE.__getitem__)
        aggregated.append(
            {
                "id": test_id,
                "theme_id": known[test_id]["theme_id"],
                "criterion_id": known[test_id]["criterion_id"],
                "verdict": verdict,
                "pages": page_values,
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


def run_sample(
    client: CDPClient,
    compiled: CompiledSample,
    *,
    timeout: float,
    origin_guard: Callable[[], None] | None = None,
) -> dict[str, Any]:
    page_reports = []
    for page in compiled.pages:
        nav.navigate(client, page.url, wait="load", timeout=timeout)
        if origin_guard is not None:
            origin_guard()
        report = scan(
            client,
            scope=compiled.scope,
            engine=compiled.engine,
            selected_tests=page.tests,
            timeout=timeout,
        )
        if origin_guard is not None:
            origin_guard()
        page_reports.append({"page_id": page.id, "url": page.url, "report": report})
    tests, summary = _aggregate(page_reports)
    criteria, themes = summarize_hierarchy(tests)
    return {
        "schema": "cdpx.rgaa.sample-result/v1",
        "rgaa_version": RGAA_VERSION,
        "sample": compiled.public_plan(),
        "summary": summary,
        "themes": themes,
        "criteria": criteria,
        "tests": tests,
        "pages": page_reports,
        "limitations": [
            "The declared sample is auditable evidence, not proof that the sample "
            "is representative.",
            "Cross-page aggregation preserves unresolved and failing verdicts; "
            "it never certifies a service.",
        ],
    }
