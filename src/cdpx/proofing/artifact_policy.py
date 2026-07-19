"""Classification and purge policy for proof artifacts.

Only files produced by the pipeline itself can be classified by the MIME
policy; everything else must be covered by an evidence manifest, otherwise
staging fails closed. No symbol in this module reads `cdpx.proof` at
runtime: the facade re-exports these primitives.
"""

from __future__ import annotations

import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from cdpx.artifacts import ArtifactClassification, ArtifactError
from cdpx.proofing.evidence_policy import EVIDENCE_SCHEMA
from cdpx.proofing.execution import _read_json_or_fail, _rewrite_text_paths
from cdpx.proofing.private_io import _write_private_text
from cdpx.security.redaction import RedactionContext, redact_text

_TEXTUAL_PROOF_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".log",
    ".md",
    ".txt",
    ".xml",
    ".yml",
    ".yaml",
}

# Explicit, bounded allowlist of files produced by the proof pipeline itself
# (outside pytest sessions): only they can be classified by the MIME policy.
# Any other file must be covered by an evidence manifest, otherwise staging
# fails closed. The names mirror the path constants of the `cdpx.proof`
# facade (REPORT_HTML, SUMMARY_JSON, …), frozen at import as part of the
# published contract.
_PIPELINE_TOP_LEVEL_FILES = frozenset(
    {
        "proof-report.html",
        "validation-summary.json",
        "make-check-pytest.log",
        "e2e-chrome.log",
        "symfony-e2e.log",
        "cdpx-help.txt",
        "git-status.txt",
        "git-diff-stat.txt",
        "symfony-e2e-junit.xml",
        "unit-junit.xml",
        "e2e-junit.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "artifact-manifest.json",
    }
)
# Increasing restriction order for multi-manifest merging.
_CLASSIFICATION_SEVERITY: dict[ArtifactClassification, int] = {
    ArtifactClassification.PUBLIC: 0,
    ArtifactClassification.INTERNAL: 1,
    ArtifactClassification.OPAQUE_RESTRICTED: 2,
    ArtifactClassification.SECRET: 3,
}


def _sanitize_text_file(
    path: Path,
    context: RedactionContext,
    path_rewrites: Sequence[tuple[str, str]] = (),
) -> None:
    if not path.exists() or path.is_symlink():
        return
    value = path.read_text(encoding="utf-8", errors="replace")
    cleaned = redact_text(
        _rewrite_text_paths(value, path_rewrites), context=context, path=f"$.files.{path.name}"
    )
    _write_private_text(path, cleaned)


def _proof_artifact_policy(path: Path) -> tuple[ArtifactClassification, bool]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime.startswith("text/") or path.suffix.lower() in _TEXTUAL_PROOF_SUFFIXES:
        return ArtifactClassification.INTERNAL, True
    return ArtifactClassification.OPAQUE_RESTRICTED, False


def _is_pipeline_proof_artifact(relative: str) -> bool:
    parts = Path(relative).parts
    if len(parts) == 1:
        return parts[0] in _PIPELINE_TOP_LEVEL_FILES or parts[0].endswith(".cast")
    if len(parts) == 2 and parts[0] == "evidence":
        # *-scenarios.json files are rewritten by _generate() after the runs
        # (symfony-scenarios.json can even exist without a manifest); the
        # manifests themselves are metadata produced by the sessions.
        name = parts[1]
        return name.endswith("-scenarios.json") or (
            name.startswith("evidence-manifest") and name.endswith(".json")
        )
    return False


def _load_evidence_policy(proof_dir: Path) -> dict[Path, tuple[ArtifactClassification, bool]]:
    """Aggregate evidence manifests into a policy keyed by resolved path.

    Manifests written by pytest sessions are the sole authority for
    classifying evidence artifacts: when manifests overlap, the most
    restrictive classification wins and upload is allowed only if all of
    them allow it.
    """

    evidence_root = (proof_dir / "evidence").resolve()
    policy: dict[Path, tuple[ArtifactClassification, bool]] = {}
    redaction_policies: set[str] = set()
    for manifest_path in sorted((proof_dir / "evidence").glob("evidence-manifest*.json")):
        payload = _read_json_or_fail(manifest_path, "unreadable evidence manifest")
        if not isinstance(payload, dict) or payload.get("schema") != EVIDENCE_SCHEMA:
            raise ArtifactError(f"unexpected evidence manifest schema: {manifest_path}")
        redaction_policies.add(str(payload.get("redaction_policy")))
        for entry in payload.get("artifacts", []):
            try:
                resolved = (evidence_root / str(entry["path"])).resolve()
                classification = ArtifactClassification(str(entry["classification"]))
                upload_allowed = bool(entry["upload_allowed"])
            except (KeyError, TypeError, ValueError) as e:
                raise ArtifactError(
                    f"invalid evidence manifest entry in {manifest_path}: {e}"
                ) from e
            if resolved != evidence_root and evidence_root not in resolved.parents:
                raise ArtifactError(f"manifested path outside evidence: {entry['path']}")
            previous = policy.get(resolved)
            if previous is not None:
                if _CLASSIFICATION_SEVERITY[previous[0]] > _CLASSIFICATION_SEVERITY[classification]:
                    classification = previous[0]
                upload_allowed = upload_allowed and previous[1]
            policy[resolved] = (classification, upload_allowed)
    if len(redaction_policies) > 1:
        raise ArtifactError(
            "heterogeneous redaction policies across evidence manifests: "
            + ", ".join(sorted(redaction_policies))
        )
    return policy


def _docker_chown_remedy(root: Path) -> str:
    """Standard remedy for root-owned files left by an interrupted Docker run."""

    return (
        f'fix with `docker run --rm -v "$PWD/{root.name}:/t" alpine '
        'chown -R "$(id -u):$(id -g)" /t` then re-run'
    )


def _raise_actionable_permission_error(root: Path, exc: PermissionError) -> NoReturn:
    """Convert a staging PermissionError into an actionable error.

    A Symfony container killed before its final chown leaves root-owned
    files in the tree: rather than a raw PermissionError mid-run, we name
    the offending directory and the remedy.
    """

    raise ArtifactError(
        f"leftover staging cannot be purged: {root} (files probably "
        f"owned by root after an interrupted Docker run); {_docker_chown_remedy(root)}"
    ) from exc


def _purge_unmanifested_evidence(proof_dir: Path) -> list[str]:
    """Purge orphaned evidence artifacts from a pytest run killed without an epilogue.

    An interrupted pytest run (deadline exit 124, SIGKILL, OOM 137, segfault)
    does not run ``pytest_sessionfinish``: its already-written attach_*
    artifacts have no manifest, and shareable staging would fail closed with
    a misleading message. We remove these orphans from the tree — the killed
    suite is already a command failure visible in the verdict — rather than
    masking the real cause.
    """

    artifacts_root = proof_dir / "evidence" / "artifacts"
    if not artifacts_root.is_dir():
        return []
    policy = _load_evidence_policy(proof_dir)
    removed: list[str] = []
    for path in sorted(artifacts_root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ArtifactError(f"symlink forbidden in proofs: {path}")
        if path.is_file() and path.resolve() not in policy:
            path.unlink()
            removed.append(path.relative_to(proof_dir).as_posix())
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed
