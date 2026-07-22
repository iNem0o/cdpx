# ruff: noqa: E501
"""Generate the human proof report consumed by `./dev proof`.

The report is intentionally evidence-first: every human-facing conclusion is
derived from command exits, pytest JUnit XML, captured logs, or the CLI help
captured during the same run.

This module is the stable FACADE of the proof pipeline. Implementations
live in ``cdpx.proofing.*`` (private_io, execution, junit, gitcontext,
evidence_catalog, scenario_inline, suites, summary, artifact_policy); only
the following remain here:

- path/budget/vendor version constants (PROOF_DIR, SYMFONY_LOG,
  EVIDENCE_DIR, EVIDENCE_STORE_DIR, timeouts, bundle shas…);
- cockpit rendering (``render_html`` and its verified vendored assets);
- orchestration (``_generate``/``generate``/``main``), transactional
  staging, ``build_shareable_proof`` and retention purge;
- the ``from cdpx.proofing.x import Y as Y`` re-exports and the WRAPPERS.

Facade contract: tests import and monkeypatch everything via ``cdpx.proof``
(``proof.PROOF_DIR``, ``proof._run_text``, ``proof._stream_to_private_file``,
``proof.run_evidence``…). Every wrapper therefore resolves its patchable
dependencies FROM this module's globals AT CALL TIME and passes them
keyword-only to the extracted implementations; no ``cdpx.proofing`` module
imports ``cdpx.proof`` (no cycle) nor reads these globals itself. The
The internal wheel verifier imports ``parse_help_commands`` from here: this
entrypoint is part of the contract.
"""

from __future__ import annotations

import fcntl
import hashlib
import html
import json
import mimetypes
import os
import shutil
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cache, lru_cache
from importlib import resources
from pathlib import Path
from string import Template
from typing import Any

from cdpx.artifacts import (
    ArtifactClassification,
    ArtifactError,
    SecureArtifactWriter,
    purge_expired,
    scan_canaries,
)
from cdpx.proofing.artifact_policy import (
    _CLASSIFICATION_SEVERITY as _CLASSIFICATION_SEVERITY,
)
from cdpx.proofing.artifact_policy import (
    _PIPELINE_TOP_LEVEL_FILES as _PIPELINE_TOP_LEVEL_FILES,
)
from cdpx.proofing.artifact_policy import (
    _TEXTUAL_PROOF_SUFFIXES as _TEXTUAL_PROOF_SUFFIXES,
)
from cdpx.proofing.artifact_policy import (
    _docker_chown_remedy as _docker_chown_remedy,
)
from cdpx.proofing.artifact_policy import (
    _is_pipeline_proof_artifact as _is_pipeline_proof_artifact,
)
from cdpx.proofing.artifact_policy import (
    _load_evidence_policy as _load_evidence_policy,
)
from cdpx.proofing.artifact_policy import (
    _proof_artifact_policy as _proof_artifact_policy,
)
from cdpx.proofing.artifact_policy import (
    _purge_unmanifested_evidence as _purge_unmanifested_evidence,
)
from cdpx.proofing.artifact_policy import (
    _raise_actionable_permission_error as _raise_actionable_permission_error,
)
from cdpx.proofing.artifact_policy import (
    _sanitize_text_file as _sanitize_text_file,
)
from cdpx.proofing.cast import CAST_COMMANDS as CAST_COMMANDS
from cdpx.proofing.cast import collect_cast_evidence
from cdpx.proofing.documentation import (
    build_documentation_catalog as build_documentation_catalog,
)
from cdpx.proofing.documentation import (
    documentation_failures as documentation_failures,
)
from cdpx.proofing.evidence_catalog import (
    VALIDATION_DOC as VALIDATION_DOC,
)
from cdpx.proofing.evidence_catalog import (
    ProofPaths as ProofPaths,
)
from cdpx.proofing.evidence_catalog import (
    _junit_status as _junit_status,
)
from cdpx.proofing.evidence_catalog import (
    build_evidence_catalog as _build_evidence_catalog_impl,
)
from cdpx.proofing.evidence_catalog import (
    collect_project_inventory as collect_project_inventory,
)
from cdpx.proofing.evidence_catalog import (
    group_cases_by_module as group_cases_by_module,
)
from cdpx.proofing.evidence_catalog import (
    parse_validation_matrix as parse_validation_matrix,
)
from cdpx.proofing.evidence_policy import (
    environment_secret_values,
    proof_retention_seconds,
    redaction_context_from_environment,
)
from cdpx.proofing.execution import (
    PROOF_TIMEOUT_SCALE_ENV as PROOF_TIMEOUT_SCALE_ENV,
)

# Facade contract for the tests: these symbols (including private ones)
# remain importable AND monkeypatchable via `cdpx.proof` (`X as X` form);
# the functions that stay in this module resolve them through their globals
# at call time.
from cdpx.proofing.execution import (
    CommandEvidence as CommandEvidence,
)
from cdpx.proofing.execution import (
    _kill_process_group as _kill_process_group,
)
from cdpx.proofing.execution import (
    _read_json_or_fail as _read_json_or_fail,
)
from cdpx.proofing.execution import (
    _repo_env as _repo_env,
)
from cdpx.proofing.execution import (
    _rewrite_text_paths as _rewrite_text_paths,
)
from cdpx.proofing.execution import (
    _rewrite_tree_paths as _rewrite_tree_paths,
)
from cdpx.proofing.execution import (
    _run_text as _run_text,
)
from cdpx.proofing.execution import (
    _sanitize_argv as _sanitize_argv,
)
from cdpx.proofing.execution import (
    _stream_and_collect as _stream_and_collect_impl,
)
from cdpx.proofing.execution import (
    _stream_to_private_file as _stream_to_private_file,
)
from cdpx.proofing.execution import (
    proof_timeout_scale as proof_timeout_scale,
)
from cdpx.proofing.gitcontext import (
    GENERATED_PREFIXES as GENERATED_PREFIXES,
)
from cdpx.proofing.gitcontext import (
    PRIVATE_WORKTREE_PREFIXES as PRIVATE_WORKTREE_PREFIXES,
)
from cdpx.proofing.gitcontext import (
    build_impact_map as build_impact_map,
)
from cdpx.proofing.gitcontext import (
    build_project_risks_and_unknowns as build_project_risks_and_unknowns,
)
from cdpx.proofing.gitcontext import (
    build_review_guide as build_review_guide,
)
from cdpx.proofing.gitcontext import (
    build_risks_and_unknowns as build_risks_and_unknowns,
)
from cdpx.proofing.gitcontext import (
    classify_change as classify_change,
)
from cdpx.proofing.gitcontext import (
    collect_git_context as _collect_git_context_impl,
)
from cdpx.proofing.junit import (
    _case_focus as _case_focus,
)
from cdpx.proofing.junit import (
    _empty_suite as _empty_suite,
)
from cdpx.proofing.junit import (
    _suite_for_summary as _suite_for_summary,
)
from cdpx.proofing.junit import (
    _tail as _tail,
)
from cdpx.proofing.junit import (
    parse_help_commands as parse_help_commands,
)
from cdpx.proofing.junit import (
    parse_junit as parse_junit,
)
from cdpx.proofing.private_io import (
    _harden_tree as _harden_tree,
)
from cdpx.proofing.private_io import (
    _now as _now,
)
from cdpx.proofing.private_io import (
    _private_umask as _private_umask,
)
from cdpx.proofing.private_io import (
    _secure_dir as _secure_dir,
)
from cdpx.proofing.private_io import (
    _write_private_bytes as _write_private_bytes,
)
from cdpx.proofing.private_io import (
    _write_private_text as _write_private_text,
)
from cdpx.proofing.scenario_inline import (
    _INLINE_TYPES as _INLINE_TYPES,
)
from cdpx.proofing.scenario_inline import (
    EXCERPT_HEAD_LINES as EXCERPT_HEAD_LINES,
)
from cdpx.proofing.scenario_inline import (
    EXCERPT_TAIL_LINES as EXCERPT_TAIL_LINES,
)
from cdpx.proofing.scenario_inline import (
    INLINE_CAST_BUDGET as INLINE_CAST_BUDGET,
)
from cdpx.proofing.scenario_inline import (
    INLINE_CAST_MAX_BYTES as INLINE_CAST_MAX_BYTES,
)
from cdpx.proofing.scenario_inline import (
    INLINE_MAX_BYTES as INLINE_MAX_BYTES,
)
from cdpx.proofing.scenario_inline import (
    INLINE_TOTAL_BUDGET as INLINE_TOTAL_BUDGET,
)
from cdpx.proofing.scenario_inline import (
    _artifact_excerpt as _artifact_excerpt,
)
from cdpx.proofing.scenario_inline import (
    _inline_artifact as _inline_artifact,
)
from cdpx.proofing.scenario_inline import (
    _strip_inline_content as _strip_inline_content,
)
from cdpx.proofing.scenario_inline import (
    enrich_scenario_evidence as enrich_scenario_evidence,
)
from cdpx.proofing.scenario_inline import (
    inline_catalog_casts as inline_catalog_casts,
)
from cdpx.proofing.scenario_inline import (
    inline_scenario_artifacts as inline_scenario_artifacts,
)
from cdpx.proofing.scenario_inline import (
    load_scenario_evidence as _load_scenario_evidence_impl,
)
from cdpx.proofing.scenario_inline import (
    proof_failures_from_scenarios as proof_failures_from_scenarios,
)
from cdpx.proofing.scenario_inline import (
    scenario_totals as scenario_totals,
)
from cdpx.proofing.scenario_inline import (
    write_scenario_evidence as write_scenario_evidence,
)
from cdpx.proofing.scenario_models import (
    ScenarioEvidence,
)
from cdpx.proofing.suites import (
    SYMFONY_NODEID as SYMFONY_NODEID,
)
from cdpx.proofing.suites import (
    _write_command_log as _write_command_log,
)
from cdpx.proofing.suites import (
    run_evidence as _run_evidence_impl,
)
from cdpx.proofing.suites import (
    run_symfony_evidence as _run_symfony_evidence_impl,
)
from cdpx.proofing.suites import (
    write_symfony_unavailable_evidence as _write_symfony_unavailable_evidence_impl,
)
from cdpx.proofing.summary import (
    build_summary as _build_summary_impl,
)
from cdpx.proofing.summary import (
    cast_failures_from_entries as cast_failures_from_entries,
)
from cdpx.security.redaction import RedactionContext, redact_tree

PROOF_DIR = Path(".proof")
REPORT_HTML = PROOF_DIR / "proof-report.html"
SUMMARY_JSON = PROOF_DIR / "validation-summary.json"
UNIT_LOG = PROOF_DIR / "make-check-pytest.log"
E2E_LOG = PROOF_DIR / "e2e-chrome.log"
SYMFONY_LOG = PROOF_DIR / "symfony-e2e.log"
CLI_HELP = PROOF_DIR / "cdpx-help.txt"
GIT_STATUS = PROOF_DIR / "git-status.txt"
GIT_DIFF_STAT = PROOF_DIR / "git-diff-stat.txt"
EVIDENCE_DIR = PROOF_DIR / "evidence"
SYMFONY_JUNIT = PROOF_DIR / "symfony-e2e-junit.xml"
# Default runtime evidence store (`cdpx run-scenario`): runs accumulate
# there between sessions; the retention purge at the start of every
# `./dev proof` applies the manifested TTLs there, with no manual step.
EVIDENCE_STORE_DIR = Path(".cdpx-evidence")

# Transactional generation: the whole tree is produced in `.proof.new/`
# (same parent as `.proof`, so same filesystem), then published via an
# atomic swap at the end of a successful run. `.proof.old/` only lives for
# the duration of the swap.
PROOF_STAGING_SUFFIX = ".new"
PROOF_PREVIOUS_SUFFIX = ".old"

# Per-step deadline budgets (seconds). They bound every proof command: an
# overrun produces an exit 124 and a red verdict, never an indefinite
# block. `CDPX_PROOF_TIMEOUT_SCALE` (strictly positive float, e.g. "2" on a
# slow machine) uniformly multiplies every budget.
RUFF_TIMEOUT_S = 120.0
MYPY_TIMEOUT_S = 300.0
UNIT_TIMEOUT_S = 600.0
E2E_TIMEOUT_S = 900.0
SYMFONY_TIMEOUT_S = 900.0
CLI_HELP_TIMEOUT_S = 30.0
GIT_TIMEOUT_S = 30.0

MERMAID_VERSION = "11.16.0"
MERMAID_RESOURCE = f"vendor/mermaid-{MERMAID_VERSION}.min.js"
MERMAID_SHA256 = "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"
XTERM_VERSION = "5.5.0"
XTERM_JS_RESOURCE = f"vendor/xterm-{XTERM_VERSION}.min.js"
XTERM_CSS_RESOURCE = f"vendor/xterm-{XTERM_VERSION}.min.css"
XTERM_JS_SHA256 = "4196e242ef1cf4c2adead8d97f4a772a69576076f70b095e004b4abbb049e7bf"
XTERM_CSS_SHA256 = "f7f724aea2bb620a6482bfb8e4bdecfae1152b0c7facef55fbda61f3b6cfedb2"


def _staging_dir() -> Path:
    return PROOF_DIR.with_name(PROOF_DIR.name + PROOF_STAGING_SUFFIX)


def _previous_dir() -> Path:
    return PROOF_DIR.with_name(PROOF_DIR.name + PROOF_PREVIOUS_SUFFIX)


@contextmanager
def _exclusive_proof_lock():
    """Refuse overlapping proof writers in the same worktree."""

    lock_path = PROOF_DIR.with_name(f"{PROOF_DIR.name}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    stream = os.fdopen(fd, "w")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ArtifactError(f"proof already running for this worktree: {lock_path}") from error
        stream.seek(0)
        stream.truncate()
        stream.write(f"pid={os.getpid()}\n")
        stream.flush()
        yield
    finally:
        stream.close()


def _stream_and_collect(
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
    timeout_label: str,
) -> tuple[int, bool, str]:
    """Facade wrapper: resolves ``_stream_to_private_file`` at call time.

    Tests monkeypatch ``proof._stream_to_private_file``; passing the
    module's global at call time guarantees the patch intercepts the
    streaming of the extracted implementation.
    """

    return _stream_and_collect_impl(
        argv,
        log_path,
        env=env,
        timeout=timeout,
        timeout_label=timeout_label,
        stream=_stream_to_private_file,
    )


def run_evidence(
    id: str,
    label: str,
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None = None,
    redaction_context: RedactionContext | None = None,
    path_rewrites: Sequence[tuple[str, str]] = (),
) -> CommandEvidence:
    """Facade wrapper: streaming goes through the module's
    ``_stream_and_collect``, hence through the tests' monkeypatchable
    ``_stream_to_private_file``."""

    return _run_evidence_impl(
        id,
        label,
        argv,
        log_path,
        env=env,
        timeout=timeout,
        redaction_context=redaction_context,
        path_rewrites=path_rewrites,
        stream_and_collect=_stream_and_collect,
    )


def write_symfony_unavailable_evidence(
    reason: str,
    *,
    redaction_context: RedactionContext | None = None,
    proof_dir: Path | None = None,
) -> None:
    """Facade wrapper: SYMFONY_LOG and EVIDENCE_DIR (monkeypatchable) are
    resolved at call time then passed to the extracted implementation."""

    return _write_symfony_unavailable_evidence_impl(
        reason,
        redaction_context=redaction_context,
        proof_dir=proof_dir,
        symfony_log=SYMFONY_LOG,
        evidence_dir=EVIDENCE_DIR,
    )


def run_symfony_evidence(
    *,
    redaction_context: RedactionContext | None = None,
    proof_dir: Path | None = None,
    timeout: float | None = None,
    path_rewrites: Sequence[tuple[str, str]] = (),
) -> CommandEvidence:
    """Facade wrapper: resolves at call time everything the tests patch
    (``_run_text``, ``_stream_to_private_file`` via ``_stream_and_collect``,
    ``shutil.which``, ``SYMFONY_LOG``, ``EVIDENCE_DIR``, ``PROOF_DIR``)."""

    return _run_symfony_evidence_impl(
        redaction_context=redaction_context,
        proof_dir=proof_dir,
        timeout=timeout,
        path_rewrites=path_rewrites,
        run_text=_run_text,
        stream_and_collect=_stream_and_collect,
        which=shutil.which,
        symfony_log=SYMFONY_LOG,
        evidence_dir=EVIDENCE_DIR,
        default_proof_dir=PROOF_DIR,
    )


def collect_git_context(
    *,
    redaction_context: RedactionContext | None = None,
    status_path: Path | None = None,
    diff_stat_path: Path | None = None,
) -> dict:
    """Facade wrapper: resolves ``_run_text`` and the paths at call time.

    Tests monkeypatch ``proof._run_text`` and ``proof.PROOF_DIR``: the
    module's globals are read at call time then passed keyword-only to the
    extracted implementation.
    """

    return _collect_git_context_impl(
        redaction_context=redaction_context,
        status_path=GIT_STATUS if status_path is None else status_path,
        diff_stat_path=GIT_DIFF_STAT if diff_stat_path is None else diff_stat_path,
        run_text=_run_text,
        timeout=GIT_TIMEOUT_S,
        diff_excludes=(
            ":(exclude).proof/*",
            f":(exclude){PROOF_DIR.name}{PROOF_STAGING_SUFFIX}/*",
            f":(exclude){PROOF_DIR.name}{PROOF_PREVIOUS_SUFFIX}/*",
            ":(exclude).idea/*",
        ),
    )


def _current_proof_paths() -> ProofPaths:
    """Resolve proof paths from the facade's globals at call time.

    PROOF_DIR, SYMFONY_LOG and EVIDENCE_DIR are monkeypatched by the tests:
    late resolution guarantees the extracted implementations see the
    patched values.
    """

    return ProofPaths(
        proof_dir=PROOF_DIR,
        report_html=REPORT_HTML,
        summary_json=SUMMARY_JSON,
        unit_log=UNIT_LOG,
        e2e_log=E2E_LOG,
        symfony_log=SYMFONY_LOG,
        cli_help=CLI_HELP,
        git_status=GIT_STATUS,
        git_diff_stat=GIT_DIFF_STAT,
        evidence_dir=EVIDENCE_DIR,
        symfony_junit=SYMFONY_JUNIT,
    )


def build_evidence_catalog(
    summary: dict,
    unit: dict,
    e2e: dict,
    symfony: dict,
    *,
    proof_dir: Path | None = None,
) -> list[dict]:
    """Facade wrapper: resolves the patchable paths at call time."""

    return _build_evidence_catalog_impl(
        summary, unit, e2e, symfony, paths=_current_proof_paths(), proof_dir=proof_dir
    )


def load_scenario_evidence(root: Path = EVIDENCE_DIR) -> ScenarioEvidence:
    """Facade wrapper with a stable default bound at import time."""

    return _load_scenario_evidence_impl(root)


COCKPIT_SHELL_RESOURCE = "cockpit/shell.html"
COCKPIT_CSS_RESOURCE = "cockpit/cockpit.css"
# The cockpit JS is split into ordered parts, concatenated into a single
# IIFE: the order is hardcoded (no glob) to stay deterministic, and each
# part individually passes _cockpit_asset's anti-</script> guard. The
# concatenation restores the shared closure scope.
COCKPIT_JS_PARTS = (
    "cockpit/js/00-helpers.js",
    "cockpit/js/10-viewers.js",
    "cockpit/js/20-modal.js",
    "cockpit/js/30-mermaid-nav.js",
    "cockpit/js/40-views.js",
    "cockpit/js/50-router.js",
)
COCKPIT_RESOURCES = (COCKPIT_SHELL_RESOURCE, COCKPIT_CSS_RESOURCE, *COCKPIT_JS_PARTS)


@cache
def _cockpit_asset(name: str) -> str:
    source = resources.files("cdpx.proofing").joinpath(name).read_text("utf-8")
    if not source.strip():
        raise ValueError(f"empty cockpit asset: {name}")
    if name != COCKPIT_SHELL_RESOURCE and "</script" in source.lower():
        raise ValueError(f"cockpit asset {name} unsuitable for inline inclusion")
    return source


@cache
def cockpit_stylesheet() -> str:
    """SPA stylesheet, read (and validated) only on first render."""
    return _cockpit_asset(COCKPIT_CSS_RESOURCE)


@cache
def cockpit_javascript() -> str:
    """SPA JS bundle assembled lazily: no resource I/O at cdpx.proof import
    time, fail-fast validation happens on the first call.

    Each part ends with a newline, so the join leaves a blank line between
    shared bundle sections.
    """
    return "(function () {\n" + "\n".join(_cockpit_asset(p) for p in COCKPIT_JS_PARTS) + "})();\n"


def _json_for_html_script(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _verified_vendor_bundle(resource: str, expected_sha256: str, *, forbidden: str) -> str:
    bundle = resources.files("cdpx.proofing").joinpath(resource).read_bytes()
    digest = hashlib.sha256(bundle).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"invalid bundle {resource}: expected={expected_sha256}, got={digest}")
    source = bundle.decode("utf-8")
    if forbidden in source.lower():
        raise ValueError(f"bundle {resource} unsuitable for inline inclusion")
    return source


@lru_cache(maxsize=1)
def _mermaid_bundle() -> str:
    return _verified_vendor_bundle(MERMAID_RESOURCE, MERMAID_SHA256, forbidden="</script")


@lru_cache(maxsize=1)
def _xterm_bundle() -> str:
    return _verified_vendor_bundle(XTERM_JS_RESOURCE, XTERM_JS_SHA256, forbidden="</script")


@lru_cache(maxsize=1)
def _xterm_css() -> str:
    return _verified_vendor_bundle(XTERM_CSS_RESOURCE, XTERM_CSS_SHA256, forbidden="</style")


def render_html(summary: dict) -> str:
    verdict = "OK" if summary["ok"] else "FAILED"
    generated = html.escape(summary["generated_at"])
    payload = _json_for_html_script(summary)
    mermaid_bundle = _mermaid_bundle()
    xterm_bundle = _xterm_bundle()
    xterm_css = _xterm_css()
    pill = "ok" if summary["ok"] else "failed"
    git_context = summary["git"]
    context = (
        f"{html.escape(git_context['branch'])} @ {html.escape(git_context['sha'])} · {generated}"
    )
    shell = Template(_cockpit_asset(COCKPIT_SHELL_RESOURCE))
    return shell.substitute(
        verdict=verdict,
        pill=pill,
        context=context,
        spa_css=cockpit_stylesheet(),
        xterm_css=xterm_css,
        payload=payload,
        mermaid_bundle=mermaid_bundle,
        xterm_bundle=xterm_bundle,
        spa_js=cockpit_javascript(),
    )


def build_summary(
    commands: list[CommandEvidence],
    unit: dict,
    e2e: dict,
    symfony: dict | None = None,
    *,
    git_context: dict | None = None,
    help_commands: list[dict[str, str]] | None = None,
    scenario_evidence: ScenarioEvidence | None = None,
    cast_entries: list[dict] | None = None,
    proof_dir: Path | None = None,
) -> dict:
    """Facade wrapper: the patchable paths (PROOF_DIR, SYMFONY_LOG,
    EVIDENCE_DIR, …) are resolved at call time via ProofPaths."""

    return _build_summary_impl(
        commands,
        unit,
        e2e,
        symfony,
        git_context=git_context,
        help_commands=help_commands,
        scenario_evidence=scenario_evidence,
        cast_entries=cast_entries,
        proof_dir=proof_dir,
        paths=_current_proof_paths(),
    )


def _purge_expired_local_proofs(*, now: datetime | None = None) -> dict[str, Any]:
    """Automatic purge of expired local proofs at the start of a run.

    Applies the manifested TTLs with no manual step: expired runs from the
    runtime evidence store (via ``purge_expired``) and the whole ``.proof``
    tree if its global manifest ``artifact-manifest.json`` carries an
    overdue ``expires_at``. Fail-open on a missing/unreadable/corrupted
    manifest (kept, same contract as ``purge_expired``) and best-effort on
    PermissionError (actionable warning on stderr, the run continues). The
    transactional directories `.proof.new`/`.proof.old` are never touched
    here: they belong to ``_generate``'s swap logic.
    """

    current = now or datetime.now(UTC)
    evidence_runs: list[str] = []
    try:
        evidence_runs = purge_expired(EVIDENCE_STORE_DIR, now=current)
    except PermissionError as exc:
        # Root-owned files left by an interrupted Docker run: retention is
        # best-effort, the warning names the remedy and the run continues.
        print(
            f"warning: retention purge impossible in {EVIDENCE_STORE_DIR} "
            f"({exc}); {_docker_chown_remedy(EVIDENCE_STORE_DIR)}",
            file=sys.stderr,
        )
    for name in evidence_runs:
        print(f"retention: expired evidence run purged: {name}", file=sys.stderr)

    proof_dir_purged = False
    expires: datetime | None
    try:
        payload = json.loads((PROOF_DIR / "artifact-manifest.json").read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(payload["expires_at"])
    except OSError, KeyError, TypeError, ValueError:
        # Missing, unreadable or corrupted manifest: fail-open retention —
        # the purge never destroys a proof whose expiration is unknown.
        expires = None
    if expires is not None and current >= expires:
        try:
            shutil.rmtree(PROOF_DIR)
            proof_dir_purged = True
            print(f"retention: expired local proof purged: {PROOF_DIR}", file=sys.stderr)
        except PermissionError as exc:
            print(
                f"warning: retention purge impossible for {PROOF_DIR} "
                f"({exc}); {_docker_chown_remedy(PROOF_DIR)}",
                file=sys.stderr,
            )
    return {"evidence_runs": evidence_runs, "proof_dir": proof_dir_purged}


def build_shareable_proof(
    proof_dir: Path = PROOF_DIR,
    *,
    canaries: list[str] | None = None,
    ttl: float | None = None,
    pre_redacted_paths: set[str] | frozenset[str] | None = None,
) -> Path:
    """Build the only CI-uploadable proof tree from an explicit manifest.

    Textual proof material is already redacted when it reaches this function.
    Opaque/binary attachments remain in the private local proof and are never
    copied to staging. Evidence artifacts inherit the classification declared
    in the aggregated evidence manifests — the MIME fallback only applies to
    files the proof pipeline generates itself; anything else fails closed.
    ``pre_redacted_paths`` is reserved for text assembled exclusively from
    redacted structures plus trusted static code. A final exact-value canary
    scan fails closed, including for these preserved files.
    """

    selected_ttl = proof_retention_seconds() if ttl is None else ttl
    preserved = pre_redacted_paths or set()
    if selected_ttl <= 0:
        raise ArtifactError("strictly positive proof TTL required")
    if proof_dir.is_symlink() or not proof_dir.is_dir():
        raise ArtifactError(f"invalid proof directory: {proof_dir}")
    staging = proof_dir / "shareable"
    store_root = proof_dir / ".artifact-store"
    excluded_roots = {staging.resolve(), store_root.resolve()}
    source_paths: list[Path] = []
    for path in sorted(proof_dir.rglob("*")):
        resolved = path.resolve()
        if any(resolved == root or root in resolved.parents for root in excluded_roots):
            continue
        if path.is_symlink():
            raise ArtifactError(f"symlink forbidden in proofs: {path}")
        if path.is_file():
            source_paths.append(path)

    evidence_policy = _load_evidence_policy(proof_dir)
    if store_root.exists():
        shutil.rmtree(store_root)
    writer = SecureArtifactWriter(store_root, "proof", ttl=selected_ttl)
    for source in source_paths:
        relative = source.relative_to(proof_dir).as_posix()
        manifested = evidence_policy.get(source.resolve())
        if manifested is not None:
            # The evidence manifest is the sole authority: the MIME policy
            # can never lower a classification declared by a test.
            classification, upload_allowed = manifested
        elif _is_pipeline_proof_artifact(relative):
            classification, upload_allowed = _proof_artifact_policy(source)
        else:
            raise ArtifactError(f"unmanifested proof artifact: {relative}")
        artifact_name = f".proof/{relative}"
        if relative in preserved:
            # These files were already built exclusively from redacted
            # structures. Do not re-run trusted JavaScript through free-text
            # regexes; the final canary scan remains the publication lock.
            writer.write_bytes(
                artifact_name,
                source.read_bytes(),
                classification=classification,
                upload_allowed=upload_allowed,
                mime=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            )
        else:
            writer.register_file(
                source,
                name=artifact_name,
                classification=classification,
                upload_allowed=upload_allowed,
            )

    # Publish a read-only copy of the full private manifest so reviewers can
    # see which opaque files were deliberately withheld from CI upload.
    manifest_copy = proof_dir / "artifact-manifest.json"
    _write_private_bytes(manifest_copy, writer.manifest_path.read_bytes())
    writer.register_file(
        manifest_copy,
        name=".proof/artifact-manifest.json",
        classification=ArtifactClassification.INTERNAL,
        upload_allowed=True,
    )
    writer.build_shareable(staging)

    matches = scan_canaries(staging, canaries or [])
    if matches:
        shutil.rmtree(staging)
        raise ArtifactError(f"canary detected in shareable staging: {', '.join(matches)}")
    _harden_tree(proof_dir)
    return staging


def _generate() -> dict:
    # Environment validations BEFORE any write/destroy: an invalid
    # configuration never costs the previous proof.
    retention_seconds = proof_retention_seconds()
    timeout_scale = proof_timeout_scale()
    staging = _staging_dir()
    previous = _previous_dir()
    # Recovery of an interrupted swap: a crash between the two os.replace
    # calls of the final swap leaves the last good proof in `.proof.old`
    # with no `.proof`. Restore it BEFORE purging leftovers, otherwise the
    # rmtree below would destroy the only still-valid proof.
    if previous.exists() and not PROOF_DIR.exists():
        os.replace(previous, PROOF_DIR)
    # Automatic retention purge: the manifested TTLs apply at the start of
    # every run, AFTER recovering an interrupted swap (the restored proof
    # becomes eligible again for its own expiration) and BEFORE purging
    # staging leftovers — which remains the property of the transactional
    # logic below.
    retention_purged = _purge_expired_local_proofs()
    # Leftovers from a previous interrupted run: staging is disposable by
    # contract.
    for leftover in (staging, previous):
        if leftover.exists():
            try:
                shutil.rmtree(leftover)
            except PermissionError as exc:
                # Root-owned files left by an interrupted Docker run before
                # its final chown: actionable error rather than a raw
                # PermissionError.
                _raise_actionable_permission_error(leftover, exc)
    _secure_dir(staging)
    context = redaction_context_from_environment()
    env = _repo_env()

    # Separation of physical path (written into staging) / published
    # logical path (.proof/...): everything entering the summary, the HTML
    # report and the logs is rewritten from the physical to the logical path before
    # publication. The roots are passed WITHOUT a trailing slash:
    # _rewrite_text_paths anchors them itself (`root/` prefix or exact
    # equality), which preserves the `.proof.new` literals quoted in
    # captured code excerpts.
    publish_rewrites: tuple[tuple[str, str], ...] = (
        (str(staging.resolve()), str(PROOF_DIR.resolve())),
        (str(staging), str(PROOF_DIR)),
    )
    # Proofs written INSIDE the Symfony container already speak in
    # `.proof/…` (mounted at /workspace/.proof): we bring them back to
    # staging's physical path so they can be read during generation, before
    # the reverse rewrite.
    ingest_rewrites: tuple[tuple[str, str], ...] = ((str(PROOF_DIR), str(staging)),)

    def scaled(seconds: float) -> float:
        return seconds * timeout_scale

    evidence_dir = staging / EVIDENCE_DIR.name
    unit_xml = staging / "unit-junit.xml"
    e2e_xml = staging / "e2e-junit.xml"
    symfony_xml = staging / SYMFONY_JUNIT.name
    cli_help = staging / CLI_HELP.name

    commands = [
        run_evidence(
            "ruff-check",
            "Ruff lint",
            [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"],
            staging / "ruff-check.log",
            env=env,
            timeout=scaled(RUFF_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "ruff-format",
            "Ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "tools"],
            staging / "ruff-format.log",
            env=env,
            timeout=scaled(RUFF_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "mypy",
            "Mypy typing",
            [sys.executable, "-m", "mypy", "src/cdpx", "tools"],
            staging / "mypy.log",
            env=env,
            timeout=scaled(MYPY_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "unit",
            "Pytest unit tests",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests",
                "--ignore=tests/e2e",
                "--cov=cdpx",
                "--cov-branch",
                "--cov-report=term",
                f"--cov-report=json:{staging / 'coverage.json'}",
                "--cov-fail-under=0",
                f"--cdpx-evidence-dir={evidence_dir}",
                f"--junitxml={unit_xml}",
            ],
            staging / UNIT_LOG.name,
            env=env,
            timeout=scaled(UNIT_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "coverage-thresholds",
            "Line and branch coverage thresholds",
            [
                sys.executable,
                "-m",
                "tools.coverage_gate",
                str(staging / "coverage.json"),
                "85",
                "75",
            ],
            staging / "coverage-thresholds.log",
            env=env,
            timeout=scaled(RUFF_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "e2e",
            "Pytest E2E Chrome",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/test_e2e_chrome.py",
                "tests/e2e/test_e2e_sessions.py",
                "tests/e2e/test_e2e_runtime_network.py",
                "-v",
                f"--cdpx-evidence-dir={evidence_dir}",
                f"--junitxml={e2e_xml}",
            ],
            staging / E2E_LOG.name,
            env=env,
            timeout=scaled(E2E_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_symfony_evidence(
            redaction_context=context,
            proof_dir=staging,
            timeout=scaled(SYMFONY_TIMEOUT_S),
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "cli-help",
            "CLI help",
            [sys.executable, "-m", "cdpx.cli", "--help"],
            cli_help,
            env=env,
            timeout=scaled(CLI_HELP_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
    ]

    # A pytest run killed without running pytest_sessionfinish did not write
    # its evidence manifests: its orphaned artifacts would fail staging
    # closed with a misleading message, even though the real cause is
    # already red in the verdict. A normal failing run (exit 1) wrote its
    # manifests — the purge is then a no-op there; any other non-zero code
    # (124 deadline, 137 OOM, negative returncode from a signal, segfault)
    # can mean death without an epilogue, so we purge as soon as exit ≠ 0.
    if any(
        command.id in {"unit", "e2e", "symfony-e2e"} and command.exit_code != 0
        for command in commands
    ):
        try:
            _purge_unmanifested_evidence(staging)
        except PermissionError as exc:
            _raise_actionable_permission_error(staging, exc)

    # Native secondary proof (pty, no dependency): .cast files land in
    # staging and enter the report via the catalog (rglob). The gate
    # requires a "generated" status for every demonstration command.
    cast_entries = collect_cast_evidence(staging, env=env, redaction_context=context)

    for path in (unit_xml, e2e_xml, symfony_xml):
        _sanitize_text_file(path, context, path_rewrites=publish_rewrites)
    unit = parse_junit(unit_xml)
    e2e = parse_junit(e2e_xml)
    symfony = parse_junit(symfony_xml)
    help_commands = parse_help_commands(cli_help.read_text(encoding="utf-8", errors="replace"))
    git_context = collect_git_context(
        redaction_context=context,
        status_path=staging / GIT_STATUS.name,
        diff_stat_path=staging / GIT_DIFF_STAT.name,
    )
    scenario_evidence = _rewrite_tree_paths(load_scenario_evidence(evidence_dir), ingest_rewrites)
    summary = build_summary(
        commands,
        unit,
        e2e,
        symfony,
        git_context=git_context,
        help_commands=help_commands,
        scenario_evidence=scenario_evidence,
        cast_entries=cast_entries,
        proof_dir=staging,
    )
    summary["cli_commands"] = [command["name"] for command in help_commands]
    summary["cli_command_count"] = len(help_commands)
    # Retention observability: the purge at the start of the run is
    # attested in the published summary (validation-summary.json), not
    # just on stderr.
    summary["retention"] = {
        "retention_days": retention_seconds // (24 * 60 * 60),
        "purged": retention_purged,
    }
    # Publication: staging's physical paths become the logical .proof/…
    # paths expected by the summary, HTML and logs contract again.
    summary = _rewrite_tree_paths(summary, publish_rewrites)
    summary = redact_tree(summary, context=context, path="$.summary")
    # Inlined content only exists in the HTML payload: the on-disk JSON
    # points to artifact files, without duplication.
    lean_evidence = _strip_inline_content(summary["scenario_evidence"])
    write_scenario_evidence(
        evidence_dir,
        lean_evidence,
        redaction_context=context,
    )
    lean_catalog = [
        {key: value for key, value in item.items() if key != "inline_content"}
        for item in summary["evidence_catalog"]
    ]
    _write_private_text(
        staging / SUMMARY_JSON.name,
        json.dumps(
            {**summary, "scenario_evidence": lean_evidence, "evidence_catalog": lean_catalog},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    _write_private_text(
        staging / REPORT_HTML.name,
        render_html(summary),
    )
    try:
        _harden_tree(staging)
    except PermissionError as exc:
        _raise_actionable_permission_error(staging, exc)
    build_shareable_proof(
        staging,
        canaries=environment_secret_values(),
        ttl=retention_seconds,
        pre_redacted_paths={REPORT_HTML.name},
    )
    # Transactional swap: the previous proof is only replaced after a
    # complete and shareable staging. Any exception before this point
    # leaves `.proof` intact (the partial staging remains for diagnostics
    # and will be purged at the next run).
    if PROOF_DIR.exists():
        os.replace(PROOF_DIR, previous)
    os.replace(staging, PROOF_DIR)
    if previous.exists():
        try:
            shutil.rmtree(previous)
        except PermissionError as exc:
            # The proof is already published: an undeletable `.proof.old`
            # (root-owned files) must not turn the run red, but the error
            # must surface now rather than at the next run.
            print(
                f"warning: cleanup impossible for {previous} ({exc}); "
                f"{_docker_chown_remedy(previous)}",
                file=sys.stderr,
            )
    return summary


def generate() -> dict:
    with _private_umask(), _exclusive_proof_lock():
        return _generate()


def main() -> int:
    try:
        summary = generate()
    except ArtifactError as error:
        print(f"cdpx proof: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {k: summary[k] for k in ("ok", "artifact_dir", "report_html")}, separators=(",", ":")
        )
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
