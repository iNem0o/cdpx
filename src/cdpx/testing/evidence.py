"""Reusable pytest evidence capture for proof reports."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cdpx.artifacts import REDACTION_POLICY_VERSION, ArtifactClassification
from cdpx.private_files import atomic_write_bytes
from cdpx.proofing.evidence_policy import (
    ARTIFACT_TYPES as ARTIFACT_TYPES,
)
from cdpx.proofing.evidence_policy import (
    DEFAULT_EVIDENCE_TTL as DEFAULT_EVIDENCE_TTL,
)
from cdpx.proofing.evidence_policy import (
    EVIDENCE_SCHEMA as EVIDENCE_SCHEMA,
)
from cdpx.proofing.evidence_policy import (
    PROOF_RETENTION_ENV as PROOF_RETENTION_ENV,
)
from cdpx.proofing.evidence_policy import (
    SCENARIOS_SCHEMA as SCENARIOS_SCHEMA,
)
from cdpx.proofing.evidence_policy import (
    environment_secret_values as environment_secret_values,
)
from cdpx.proofing.evidence_policy import (
    proof_retention_days as proof_retention_days,
)
from cdpx.proofing.evidence_policy import (
    proof_retention_seconds as proof_retention_seconds,
)
from cdpx.proofing.evidence_policy import (
    redaction_context_from_environment as redaction_context_from_environment,
)
from cdpx.security.redaction import (
    RedactionContext,
    redact_text,
    redact_tree,
)
from cdpx.testing.intent import (
    TestIntent,
    extract_intent,
    failure_location,
    mark_failed_assertion,
)

E2E_PREFIX = "tests/e2e/"
INTEGRATION_MODULES = {
    "tests/test_cli.py",
    "tests/test_discovery_and_client.py",
    "tests/test_fixture_server.py",
}
_TYPE_BY_SUFFIX = {
    ".png": "screenshot",
    ".jpg": "screenshot",
    ".jpeg": "screenshot",
    ".webp": "screenshot",
    ".webm": "video",
    ".mp4": "video",
    ".cast": "asciinema",
    ".json": "json",
    ".log": "logs",
    ".ndjson": "logs",
    ".txt": "logs",
}
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIMES = {
    "application/json",
    "application/javascript",
    "application/sql",
    "application/xml",
    "application/x-ndjson",
    "image/svg+xml",
}


def _secure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"dossier de preuve symbolique interdit: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ValueError(f"dossier de preuve requis: {path}")
    path.chmod(0o700)


def _write_private_bytes(path: Path, data: bytes) -> None:
    atomic_write_bytes(path, data)


def _write_private_text(path: Path, value: str) -> None:
    _write_private_bytes(path, value.encode("utf-8"))


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _is_textual(mime: str, path: Path) -> bool:
    return (
        mime.startswith(_TEXT_MIME_PREFIXES)
        or mime in _TEXT_MIMES
        or path.suffix.lower()
        in {
            ".cast",
            ".json",
            ".log",
            ".md",
            ".ndjson",
            ".txt",
            ".xml",
            ".yml",
            ".yaml",
        }
    )


def _attachment_name(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        raise ValueError(f"nom de preuve invalide: {value}")
    return path.name


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
    sha256: str
    classification: str
    upload_allowed: bool
    redaction_policy: str = REDACTION_POLICY_VERSION
    created_at: str = field(default_factory=utc_now)
    excerpt: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "path": self.path,
            "bytes": self.bytes,
            "mime": self.mime,
            "sha256": self.sha256,
            "classification": self.classification,
            "upload_allowed": self.upload_allowed,
            "redaction_policy": self.redaction_policy,
            "created_at": self.created_at,
            "excerpt": self.excerpt,
            "meta": self.meta,
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
    intent: str = ""
    intent_line: int = 0
    assertions: list[dict[str, Any]] = field(default_factory=list)
    failed_line: int = 0
    started_at: str = field(default_factory=utc_now)
    duration_s: float = 0.0
    status: str = "running"
    phase: str = ""
    message: str = ""
    stdout: str = ""
    stderr: str = ""
    artifacts: list[EvidenceArtifact] = field(default_factory=list)
    redaction_context: RedactionContext = field(default_factory=redaction_context_from_environment)

    @property
    def slug(self) -> str:
        return slugify(self.nodeid)

    @property
    def artifact_dir(self) -> Path:
        return self.root / "artifacts" / self.suite / self.slug

    def attach_file(
        self,
        path: str | Path,
        label: str,
        type: str | None = None,
        *,
        classification: ArtifactClassification | None = None,
        upload_allowed: bool | None = None,
        excerpt: str = "",
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        src = Path(path)
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"fichier de preuve invalide: {src}")
        _secure_dir(self.artifact_dir)
        safe_stem = slugify(redact_text(src.stem, context=self.redaction_context, path="$.name"))
        dest = self.artifact_dir / f"{safe_stem}{src.suffix.lower()}"
        artifact_type = type or _TYPE_BY_SUFFIX.get(src.suffix.lower(), "file")
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(f"type d'artefact de preuve inconnu: {artifact_type}")
        mime = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        textual = _is_textual(mime, src)
        selected_classification = classification or (
            ArtifactClassification.INTERNAL if textual else ArtifactClassification.OPAQUE_RESTRICTED
        )
        selected_upload = textual if upload_allowed is None else upload_allowed
        if selected_classification in {
            ArtifactClassification.SECRET,
            ArtifactClassification.OPAQUE_RESTRICTED,
        }:
            selected_upload = False
        data = src.read_bytes()
        if textual:
            decoded = data.decode("utf-8", errors="replace")
            if mime == "application/json" or src.suffix.lower() == ".json":
                try:
                    payload = json.loads(decoded)
                except json.JSONDecodeError:
                    cleaned = redact_text(
                        decoded,
                        context=self.redaction_context,
                        path=f"$.artifacts.{dest.name}",
                    )
                else:
                    cleaned = (
                        json.dumps(
                            redact_tree(
                                payload,
                                context=self.redaction_context,
                                path=f"$.artifacts.{dest.name}",
                            ),
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n"
                    )
            else:
                cleaned = redact_text(
                    decoded,
                    context=self.redaction_context,
                    path=f"$.artifacts.{dest.name}",
                )
            data = cleaned.encode("utf-8")
        _write_private_bytes(dest, data)
        artifact = EvidenceArtifact(
            type=artifact_type,
            label=redact_text(label, context=self.redaction_context, path="$.artifact.label"),
            path=str(dest),
            bytes=len(data),
            mime=mime,
            sha256=hashlib.sha256(data).hexdigest(),
            classification=selected_classification.value,
            upload_allowed=selected_upload,
            excerpt=redact_text(
                excerpt, context=self.redaction_context, path=f"$.artifacts.{dest.name}.excerpt"
            ),
            meta=redact_tree(
                dict(meta or {}),
                context=self.redaction_context,
                path=f"$.artifacts.{dest.name}.meta",
            ),
        )
        self.artifacts.append(artifact)
        return artifact.as_dict()

    def attach_text(self, label: str, text: str, filename: str | None = None) -> dict[str, Any]:
        name = _attachment_name(filename or f"{slugify(label)}.txt")
        _secure_dir(self.artifact_dir)
        dest = self.artifact_dir / name
        _write_private_text(
            dest,
            redact_text(text, context=self.redaction_context, path=f"$.artifacts.{name}"),
        )
        return self.attach_file(
            dest,
            label,
            "logs",
            classification=ArtifactClassification.INTERNAL,
            upload_allowed=True,
        )

    def attach_json(self, label: str, data: Any, filename: str | None = None) -> dict[str, Any]:
        name = _attachment_name(filename or f"{slugify(label)}.json")
        _secure_dir(self.artifact_dir)
        dest = self.artifact_dir / name
        cleaned = redact_tree(data, context=self.redaction_context, path=f"$.artifacts.{name}")
        _write_private_text(dest, json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
        return self.attach_file(
            dest,
            label,
            "json",
            classification=ArtifactClassification.INTERNAL,
            upload_allowed=True,
        )

    def attach_screenshot(self, path: str | Path, label: str = "Screenshot") -> dict[str, Any]:
        return self.attach_file(
            path,
            label,
            "screenshot",
            classification=ArtifactClassification.OPAQUE_RESTRICTED,
            upload_allowed=False,
        )

    def attach_command_output(
        self,
        label: str,
        argv: list[str],
        stdout: str,
        stderr: str,
        exit_code: int,
        *,
        duration_s: float | None = None,
        excerpt_lines: int = 40,
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Preuve secondaire: transcript complet d'une commande + extrait lisible."""

        name = _attachment_name(filename or f"{slugify(label)}.txt")
        _secure_dir(self.artifact_dir)
        dest = self.artifact_dir / name
        transcript = "\n".join(
            [
                "$ " + " ".join(argv),
                "--- stdout ---",
                stdout.rstrip("\n"),
                "--- stderr ---",
                stderr.rstrip("\n"),
                f"--- exit_code: {exit_code} ---",
                "",
            ]
        )
        _write_private_text(
            dest,
            redact_text(transcript, context=self.redaction_context, path=f"$.artifacts.{name}"),
        )
        meta: dict[str, Any] = {"argv": list(argv), "exit_code": exit_code}
        if duration_s is not None:
            meta["duration_s"] = round(float(duration_s), 3)
        return self.attach_file(
            dest,
            label,
            "command",
            classification=ArtifactClassification.INTERNAL,
            upload_allowed=True,
            excerpt=head_tail_excerpt(stdout, excerpt_lines),
            meta=meta,
        )

    def attach_log_excerpt(
        self,
        path: str | Path,
        label: str,
        *,
        pattern: str | None = None,
        line_range: tuple[int, int] | None = None,
        context: int = 3,
        max_lines: int = 120,
    ) -> dict[str, Any]:
        """Preuve secondaire: extrait ciblé d'un log (motif ou plage de lignes).

        Sans correspondance, l'artefact est quand même produit ("aucune
        correspondance"): l'absence d'un motif est une preuve en soi.
        """

        if pattern is not None and line_range is not None:
            raise ValueError("pattern et line_range sont mutuellement exclusifs")
        src = Path(path)
        if src.is_symlink() or not src.is_file():
            raise ValueError(f"fichier de preuve invalide: {src}")
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        matched: list[int] = []
        if pattern is not None:
            regex = re.compile(pattern)
            matched = [index + 1 for index, line in enumerate(lines) if regex.search(line)]
            keep: set[int] = set()
            for lineno in matched:
                keep.update(range(max(1, lineno - context), min(len(lines), lineno + context) + 1))
            selected = sorted(keep)
        elif line_range is not None:
            first, last = line_range
            selected = list(range(max(1, first), min(len(lines), last) + 1))
        else:
            selected = list(range(1, len(lines) + 1))

        omitted = max(len(selected) - max_lines, 0)
        selected = selected[:max_lines]
        rendered: list[str] = []
        previous = 0
        for lineno in selected:
            if previous and lineno > previous + 1:
                rendered.append("…")
            rendered.append(f"{src.name}:{lineno}: {lines[lineno - 1]}")
            previous = lineno
        if omitted:
            rendered.append(f"… ({omitted} lignes omises) …")
        if not rendered:
            rendered = [f"aucune correspondance pour {pattern!r} dans {src.name}"]
        content = "\n".join(rendered)

        name = _attachment_name(f"{slugify(label)}.txt")
        _secure_dir(self.artifact_dir)
        dest = self.artifact_dir / name
        _write_private_text(
            dest,
            redact_text(content, context=self.redaction_context, path=f"$.artifacts.{name}"),
        )
        meta: dict[str, Any] = {
            "source": str(src),
            "pattern": pattern or "",
            "matched_lines": matched[:50],
            "total_lines": len(lines),
        }
        return self.attach_file(
            dest,
            label,
            "log-excerpt",
            classification=ArtifactClassification.INTERNAL,
            upload_allowed=True,
            excerpt=content,
            meta=meta,
        )

    def attach_cast(
        self,
        path: str | Path,
        label: str = "Terminal record",
    ) -> dict[str, Any]:
        """Preuve secondaire: enregistrement terminal (.cast v2), joué par xterm.js.

        Le .cast est textuel donc redacté, mais jamais uploadable: un secret
        peut être fragmenté entre événements ndjson et échapper au scan.
        """

        return self.attach_file(
            path,
            label,
            "asciinema",
            classification=ArtifactClassification.INTERNAL,
            upload_allowed=False,
        )

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
            self.message = redact_text(
                str(longrepr).splitlines()[-1][:500],
                context=self.redaction_context,
                path="$.message",
            )
        if self.status == "failed":
            located = failure_location(report, self.nodeid.split("::", 1)[0])
            if located:
                self.failed_line = located
                mark_failed_assertion(self.assertions, located)
        self.stdout = redact_text(
            getattr(report, "capstdout", "") or "",
            context=self.redaction_context,
            path="$.stdout",
        )
        self.stderr = redact_text(
            getattr(report, "capstderr", "") or "",
            context=self.redaction_context,
            path="$.stderr",
        )

    def has_artifact_type(self, type: str) -> bool:
        return any(artifact.type == type for artifact in self.artifacts)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "nodeid": self.nodeid,
            "suite": self.suite,
            "title": self.title,
            "area": self.area,
            "feature": self.feature,
            "journey": self.journey,
            "scenario_id": self.scenario_id,
            "proves": self.proves,
            "intent": self.intent,
            "intent_line": self.intent_line,
            "assertions": self.assertions,
            "failed_line": self.failed_line,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "status": self.status,
            "phase": self.phase,
            "message": self.message,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
        }
        return redact_tree(payload, context=self.redaction_context, path="$.case")


class EvidenceSession:
    def __init__(
        self,
        root: str | Path,
        suite_override: str | None = None,
        *,
        ttl: float | None = None,
        redaction_context: RedactionContext | None = None,
    ):
        selected_ttl = proof_retention_seconds() if ttl is None else ttl
        if selected_ttl <= 0:
            raise ValueError("TTL de preuve strictement positif requis")
        self.root = Path(root)
        self.suite_override = suite_override
        self.cases: dict[str, EvidenceCase] = {}
        self._intent_cache: dict[str, TestIntent | None] = {}
        self.redaction_context = redaction_context or redaction_context_from_environment()
        self.created_at = datetime.now(UTC)
        self.expires_at = self.created_at + timedelta(seconds=selected_ttl)
        _secure_dir(self.root)

    def case_for_item(self, item: Any) -> EvidenceCase:
        nodeid = item.nodeid
        if nodeid in self.cases:
            return self.cases[nodeid]
        metadata = marker_metadata(item)
        suite = self.suite_override or classify_nodeid(nodeid)
        intent = self._intent_for_item(item)
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
            intent=intent.docstring if intent else "",
            intent_line=intent.line if intent else 0,
            assertions=[assertion.as_dict() for assertion in intent.assertions] if intent else [],
            redaction_context=self.redaction_context,
        )
        self.cases[nodeid] = case
        return case

    def _intent_for_item(self, item: Any) -> TestIntent | None:
        # Les tests paramétrés partagent la même fonction: extraction unique,
        # chaque case reçoit ses propres dicts (as_dict) pour la corrélation.
        func = getattr(item, "function", None)
        if func is None:
            return None
        key = f"{getattr(func, '__module__', '')}.{getattr(func, '__qualname__', '')}"
        if key not in self._intent_cache:
            self._intent_cache[key] = extract_intent(func)
        return self._intent_cache[key]

    def write(self) -> list[str]:
        paths = []
        by_suite: dict[str, list[dict[str, Any]]] = {}
        for case in self.cases.values():
            by_suite.setdefault(case.suite, []).append(case.as_dict())
        for suite, cases in by_suite.items():
            path = self.root / f"{suite}-scenarios.json"
            payload = {
                "schema": SCENARIOS_SCHEMA,
                "suite": suite,
                "generated_at": utc_now(),
                "count": len(cases),
                "scenarios": sorted(cases, key=lambda item: item["nodeid"]),
            }
            cleaned = redact_tree(
                payload,
                context=self.redaction_context,
                path=f"$.suites.{suite}",
            )
            _write_private_text(path, json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
            paths.append(str(path))
        self._write_manifest([Path(path) for path in paths])
        return paths

    def _write_manifest(self, scenario_paths: list[Path]) -> None:
        artifact_paths = [
            Path(artifact.path) for case in self.cases.values() for artifact in case.artifacts
        ]
        metadata_by_path = {
            Path(artifact.path).resolve(): artifact
            for case in self.cases.values()
            for artifact in case.artifacts
        }
        entries = []
        for path in sorted(scenario_paths + artifact_paths):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"preuve non manifestable: {path}")
            relative = path.resolve().relative_to(self.root.resolve()).as_posix()
            metadata = metadata_by_path.get(path.resolve())
            data = path.read_bytes()
            entries.append(
                {
                    "path": relative,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "mime": (
                        metadata.mime
                        if metadata is not None
                        else mimetypes.guess_type(path.name)[0] or "application/json"
                    ),
                    "classification": (
                        metadata.classification
                        if metadata is not None
                        else ArtifactClassification.INTERNAL.value
                    ),
                    "upload_allowed": metadata.upload_allowed if metadata is not None else True,
                    "redaction_policy": REDACTION_POLICY_VERSION,
                    "created_at": (
                        metadata.created_at if metadata is not None else _iso(self.created_at)
                    ),
                }
            )
        payload = {
            "schema": EVIDENCE_SCHEMA,
            "created_at": _iso(self.created_at),
            "expires_at": _iso(self.expires_at),
            "redaction_policy": REDACTION_POLICY_VERSION,
            "artifacts": entries,
            "redaction": self.redaction_context.report.as_dict(),
        }
        # Nom dérivé du jeu de suites: les sessions pytest successives d'une
        # même génération de preuve (unit/integration, e2e, symfony) écrivent
        # des manifestes distincts au lieu de s'écraser, et un re-run de la
        # même session remplace le sien (déterminisme, pas d'accumulation).
        suites = sorted({case.suite for case in self.cases.values()})
        # Repli déterministe "session": un manifeste sans cas garde un nom
        # stable entre runs standalone au lieu d'accumuler des noms aléatoires.
        stem = slugify("-".join(suites)) if suites else "session"
        _write_private_text(
            self.root / f"evidence-manifest-{stem}.json",
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )


def head_tail_excerpt(text: str, max_lines: int, head: int = 10) -> str:
    """Extrait tête+queue d'un texte long, avec marqueur d'omission honnête."""

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.rstrip("\n")
    tail = max(max_lines - head, 1)
    omitted = len(lines) - head - tail
    return "\n".join([*lines[:head], f"… ({omitted} lignes omises) …", *lines[-tail:]])


def start_timer() -> float:
    return time.monotonic()
