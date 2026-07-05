"""Reusable pytest evidence capture for proof reports."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

E2E_PREFIX = "tests/e2e/"
INTEGRATION_MODULES = {
    "tests/test_cli.py",
    "tests/test_discovery_and_client.py",
    "tests/test_fixture_server.py",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return slug[:180] or "scenario"


def classify_nodeid(nodeid: str) -> str:
    path = nodeid.split("::", 1)[0]
    if path.startswith(E2E_PREFIX):
        return "e2e"
    if path in INTEGRATION_MODULES:
        return "integration"
    return "unit"


def marker_metadata(item: Any) -> dict[str, Any]:
    marker = item.get_closest_marker("scenario")
    if marker is None:
        return {}
    data: dict[str, Any] = {}
    if marker.args:
        data["title"] = marker.args[0]
    data.update(marker.kwargs)
    proves = data.get("proves")
    if isinstance(proves, str):
        data["proves"] = [proves]
    return data


@dataclass
class EvidenceArtifact:
    type: str
    label: str
    path: str
    bytes: int
    mime: str
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "path": self.path,
            "bytes": self.bytes,
            "mime": self.mime,
            "created_at": self.created_at,
        }


@dataclass
class EvidenceCase:
    nodeid: str
    root: Path
    suite: str
    title: str
    area: str = ""
    feature: str = ""
    journey: str = ""
    scenario_id: str = ""
    proves: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=utc_now)
    duration_s: float = 0.0
    status: str = "running"
    phase: str = ""
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    artifacts: list[EvidenceArtifact] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return slugify(self.nodeid)

    @property
    def artifact_dir(self) -> Path:
        return self.root / "artifacts" / self.suite / self.slug

    def attach_file(self, path: str | Path, label: str, type: str | None = None) -> dict[str, Any]:
        src = Path(path)
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        dest = self.artifact_dir / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        artifact_type = type or dest.suffix.lstrip(".") or "file"
        mime = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        artifact = EvidenceArtifact(
            type=artifact_type,
            label=label,
            path=str(dest),
            bytes=dest.stat().st_size,
            mime=mime,
        )
        self.artifacts.append(artifact)
        return artifact.as_dict()

    def attach_text(self, label: str, text: str, filename: str | None = None) -> dict[str, Any]:
        name = filename or f"{slugify(label)}.txt"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        dest = self.artifact_dir / name
        dest.write_text(text, encoding="utf-8")
        return self.attach_file(dest, label, "logs")

    def attach_json(self, label: str, data: Any, filename: str | None = None) -> dict[str, Any]:
        name = filename or f"{slugify(label)}.json"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        dest = self.artifact_dir / name
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.attach_file(dest, label, "json")

    def attach_screenshot(self, path: str | Path, label: str = "Screenshot") -> dict[str, Any]:
        return self.attach_file(path, label, "screenshot")

    def set_report(self, report: Any) -> None:
        self.duration_s = round(float(getattr(report, "duration", 0.0) or 0.0), 3)
        self.phase = getattr(report, "when", "")
        outcome = getattr(report, "outcome", "")
        if outcome == "passed":
            self.status = "passed"
        elif outcome == "skipped":
            self.status = "skipped"
        elif outcome == "failed":
            self.status = "failed"
        else:
            self.status = outcome or self.status
        longrepr = getattr(report, "longreprtext", None)
        if longrepr:
            self.message = str(longrepr).splitlines()[-1][:500]
        self.stdout = getattr(report, "capstdout", "") or ""
        self.stderr = getattr(report, "capstderr", "") or ""

    def has_artifact_type(self, type: str) -> bool:
        return any(artifact.type == type for artifact in self.artifacts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodeid": self.nodeid,
            "suite": self.suite,
            "title": self.title,
            "area": self.area,
            "feature": self.feature,
            "journey": self.journey,
            "scenario_id": self.scenario_id,
            "proves": self.proves,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }


class EvidenceSession:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.cases: dict[str, EvidenceCase] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def case_for_item(self, item: Any) -> EvidenceCase:
        nodeid = item.nodeid
        if nodeid in self.cases:
            return self.cases[nodeid]
        metadata = marker_metadata(item)
        suite = classify_nodeid(nodeid)
        case = EvidenceCase(
            nodeid=nodeid,
            root=self.root,
            suite=suite,
            title=metadata.get("title") or nodeid.rsplit("::", 1)[-1],
            area=metadata.get("area", ""),
            feature=metadata.get("feature", ""),
            journey=metadata.get("journey", ""),
            scenario_id=metadata.get("scenario_id", ""),
            proves=list(metadata.get("proves", [])),
        )
        self.cases[nodeid] = case
        return case

    def write(self) -> list[str]:
        paths = []
        by_suite: dict[str, list[dict[str, Any]]] = {}
        for case in self.cases.values():
            by_suite.setdefault(case.suite, []).append(case.as_dict())
        for suite, cases in by_suite.items():
            path = self.root / f"{suite}-scenarios.json"
            payload = {
                "suite": suite,
                "generated_at": utc_now(),
                "count": len(cases),
                "scenarios": sorted(cases, key=lambda item: item["nodeid"]),
            }
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            paths.append(str(path))
        return paths


def start_timer() -> float:
    return time.monotonic()
