# ruff: noqa: E501
"""Generate the human proof report consumed by `make proof`.

The report is intentionally evidence-first: every human-facing conclusion is
derived from command exits, pytest JUnit XML, captured logs, or the CLI help
captured during the same run.

Ce module est la FAÇADE stable du pipeline de preuve. Les implémentations
vivent dans ``cdpx.proofing.*`` (private_io, execution, junit, gitcontext,
evidence_catalog, scenario_inline, suites, summary, artifact_policy); ici ne
restent que:

- les constantes de chemins/budgets/versions vendor (PROOF_DIR, SYMFONY_LOG,
  EVIDENCE_DIR, EVIDENCE_STORE_DIR, timeouts, sha des bundles…);
- le rendu cockpit (``render_html`` et ses assets vendorés vérifiés);
- l'orchestration (``_generate``/``generate``/``main``), le staging
  transactionnel, ``build_shareable_proof`` et la purge de rétention;
- les ré-exports ``from cdpx.proofing.x import Y as Y`` et des WRAPPERS.

Contrat de façade: les tests importent et monkeypatchent tout via
``cdpx.proof`` (``proof.PROOF_DIR``, ``proof._run_text``,
``proof._stream_to_private_file``, ``proof.run_evidence``…). Chaque wrapper
résout donc ses dépendances patchables DANS les globals de ce module AU
MOMENT DE L'APPEL et les passe en keyword-only aux implémentations
extraites; aucun module ``cdpx.proofing`` n'importe ``cdpx.proof`` (pas de
cycle) ni ne lit ces globals lui-même. Le Makefile (smoke-dist) importe
``parse_help_commands`` d'ici: ce point d'entrée fait partie du contrat.
"""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import shutil
import sys
from collections.abc import Sequence
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
from cdpx.proofing.execution import (
    PROOF_TIMEOUT_SCALE_ENV as PROOF_TIMEOUT_SCALE_ENV,
)

# Contrat de façade pour les tests: ces symboles (y compris privés) restent
# importables ET monkeypatchables via `cdpx.proof` (forme `X as X`); les
# fonctions restées dans ce module les résolvent par leurs globals au moment
# de l'appel.
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
from cdpx.testing.evidence import (
    environment_secret_values,
    proof_retention_seconds,
    redaction_context_from_environment,
)

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
# Store d'évidence runtime par défaut (`cdpx run-scenario`): des runs s'y
# accumulent entre les sessions; la purge de rétention au début de chaque
# `make proof` y applique les TTL manifestés, sans geste manuel.
EVIDENCE_STORE_DIR = Path(".cdpx-evidence")

# Génération transactionnelle: tout l'arbre est produit dans `.proof.new/`
# (même parent que `.proof`, donc même filesystem), puis publié par bascule
# atomique en fin de run réussi. `.proof.old/` ne vit que le temps du swap.
PROOF_STAGING_SUFFIX = ".new"
PROOF_PREVIOUS_SUFFIX = ".old"

# Budgets de deadline par étape (secondes). Ils bornent chaque commande de
# preuve: un dépassement produit un exit 124 et un verdict rouge, jamais un
# blocage indéfini. `CDPX_PROOF_TIMEOUT_SCALE` (flottant strictement positif,
# ex. "2" sur machine lente) multiplie uniformément tous les budgets.
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


def _stream_and_collect(
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
    timeout_label: str,
) -> tuple[int, bool, str]:
    """Wrapper de façade: résout ``_stream_to_private_file`` à l'appel.

    Les tests monkeypatchent ``proof._stream_to_private_file``; passer le
    global du module au moment de l'appel garantit que le patch intercepte
    le streaming de l'implémentation extraite.
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
    """Wrapper de façade: le streaming passe par ``_stream_and_collect`` du
    module, donc par ``_stream_to_private_file`` monkeypatchable des tests."""

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
    """Wrapper de façade: SYMFONY_LOG et EVIDENCE_DIR (monkeypatchables) sont
    résolus au moment de l'appel puis passés à l'implémentation extraite."""

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
    """Wrapper de façade: résout à l'appel tout ce que les tests patchent
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
    """Wrapper de façade: résout ``_run_text`` et les chemins à l'appel.

    Les tests monkeypatchent ``proof._run_text`` et ``proof.PROOF_DIR``:
    les globals du module sont lus au moment de l'appel puis passés en
    keyword-only à l'implémentation extraite.
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
    """Résout les chemins de preuve depuis les globals de la façade à l'appel.

    PROOF_DIR, SYMFONY_LOG et EVIDENCE_DIR sont monkeypatchés par les tests:
    la résolution tardive garantit que les implémentations extraites voient
    les valeurs patchées.
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
    """Wrapper de façade: résout les chemins patchables au moment de l'appel."""

    return _build_evidence_catalog_impl(
        summary, unit, e2e, symfony, paths=_current_proof_paths(), proof_dir=proof_dir
    )


def load_scenario_evidence(root: Path = EVIDENCE_DIR) -> ScenarioEvidence:
    """Wrapper de façade: défaut lié à l'import, contrat historique conservé."""

    return _load_scenario_evidence_impl(root)


COCKPIT_SHELL_RESOURCE = "cockpit/shell.html"
COCKPIT_CSS_RESOURCE = "cockpit/cockpit.css"
COCKPIT_JS_RESOURCE = "cockpit/cockpit.js"
COCKPIT_RESOURCES = (COCKPIT_SHELL_RESOURCE, COCKPIT_CSS_RESOURCE, COCKPIT_JS_RESOURCE)


@cache
def _cockpit_asset(name: str) -> str:
    source = resources.files("cdpx.proofing").joinpath(name).read_text("utf-8")
    if not source.strip():
        raise ValueError(f"asset cockpit vide: {name}")
    if name != COCKPIT_SHELL_RESOURCE and "</script" in source.lower():
        raise ValueError(f"asset cockpit {name} impropre à une inclusion inline")
    return source


SPA_CSS = _cockpit_asset(COCKPIT_CSS_RESOURCE)
SPA_JS = _cockpit_asset(COCKPIT_JS_RESOURCE)


def _json_for_html_script(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def _verified_vendor_bundle(resource: str, expected_sha256: str, *, forbidden: str) -> str:
    bundle = resources.files("cdpx.proofing").joinpath(resource).read_bytes()
    digest = hashlib.sha256(bundle).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"bundle {resource} invalide: attendu={expected_sha256}, reçu={digest}")
    source = bundle.decode("utf-8")
    if forbidden in source.lower():
        raise ValueError(f"bundle {resource} impropre à une inclusion inline")
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
    verdict = "OK" if summary["ok"] else "ECHEC"
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
        spa_css=SPA_CSS,
        xterm_css=xterm_css,
        payload=payload,
        mermaid_bundle=mermaid_bundle,
        xterm_bundle=xterm_bundle,
        spa_js=SPA_JS,
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
    """Wrapper de façade: les chemins patchables (PROOF_DIR, SYMFONY_LOG,
    EVIDENCE_DIR, …) sont résolus au moment de l'appel via ProofPaths."""

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
    """Purge automatique des preuves locales expirées au début d'un run.

    Applique les TTL manifestés sans geste manuel: les runs expirés du store
    d'évidence runtime (via ``purge_expired``) et l'arbre ``.proof`` entier si
    son manifeste global ``artifact-manifest.json`` porte un ``expires_at``
    dépassé. Fail-open sur manifeste absent/illisible/corrompu (conservation,
    même contrat que ``purge_expired``) et best-effort sur PermissionError
    (avertissement actionnable sur stderr, le run continue). Les répertoires
    transactionnels `.proof.new`/`.proof.old` ne sont jamais touchés ici: ils
    appartiennent à la logique de bascule de ``_generate``.
    """

    current = now or datetime.now(UTC)
    evidence_runs: list[str] = []
    try:
        evidence_runs = purge_expired(EVIDENCE_STORE_DIR, now=current)
    except PermissionError as exc:
        # Fichiers root laissés par un run Docker interrompu: la rétention est
        # best-effort, l'avertissement nomme le remède et le run continue.
        print(
            f"avertissement: purge de rétention impossible dans {EVIDENCE_STORE_DIR} "
            f"({exc}); {_docker_chown_remedy(EVIDENCE_STORE_DIR)}",
            file=sys.stderr,
        )
    for name in evidence_runs:
        print(f"rétention: run d'évidence expiré purgé: {name}", file=sys.stderr)

    proof_dir_purged = False
    expires: datetime | None
    try:
        payload = json.loads((PROOF_DIR / "artifact-manifest.json").read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(payload["expires_at"])
    except (OSError, KeyError, TypeError, ValueError):
        # Manifeste absent, illisible ou corrompu: conservation fail-open —
        # la purge ne détruit jamais une preuve dont l'expiration est inconnue.
        expires = None
    if expires is not None and current >= expires:
        try:
            shutil.rmtree(PROOF_DIR)
            proof_dir_purged = True
            print(f"rétention: preuve locale expirée purgée: {PROOF_DIR}", file=sys.stderr)
        except PermissionError as exc:
            print(
                f"avertissement: purge de rétention impossible de {PROOF_DIR} "
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
        raise ArtifactError("TTL de proof strictement positif requis")
    if proof_dir.is_symlink() or not proof_dir.is_dir():
        raise ArtifactError(f"répertoire de preuve invalide: {proof_dir}")
    staging = proof_dir / "shareable"
    store_root = proof_dir / ".artifact-store"
    excluded_roots = {staging.resolve(), store_root.resolve()}
    source_paths: list[Path] = []
    for path in sorted(proof_dir.rglob("*")):
        resolved = path.resolve()
        if any(resolved == root or root in resolved.parents for root in excluded_roots):
            continue
        if path.is_symlink():
            raise ArtifactError(f"lien symbolique interdit dans les preuves: {path}")
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
            # Le manifeste d'évidence est la seule autorité: la politique MIME
            # ne peut jamais abaisser une classification déclarée par un test.
            classification, upload_allowed = manifested
        elif _is_pipeline_proof_artifact(relative):
            classification, upload_allowed = _proof_artifact_policy(source)
        else:
            raise ArtifactError(f"artefact de preuve non manifesté: {relative}")
        artifact_name = f".proof/{relative}"
        if relative in preserved:
            # Ces fichiers ont déjà été construits exclusivement depuis des
            # structures redacted. Ne pas repasser du JavaScript de confiance
            # dans les regex de texte libre; le scan de canaris final demeure
            # le verrou de publication.
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
        raise ArtifactError(f"canary détecté dans le staging partageable: {', '.join(matches)}")
    _harden_tree(proof_dir)
    return staging


def _generate() -> dict:
    # Validations d'environnement AVANT toute écriture/destruction: une
    # configuration invalide ne coûte jamais la preuve précédente.
    retention_seconds = proof_retention_seconds()
    timeout_scale = proof_timeout_scale()
    staging = _staging_dir()
    previous = _previous_dir()
    # Récupération d'un swap interrompu: un crash entre les deux os.replace de
    # la bascule finale laisse la dernière bonne preuve dans `.proof.old` sans
    # `.proof`. La restaurer AVANT la purge des restes, sinon le rmtree
    # ci-dessous détruirait la seule preuve encore valide.
    if previous.exists() and not PROOF_DIR.exists():
        os.replace(previous, PROOF_DIR)
    # Purge de rétention automatique: les TTL manifestés s'appliquent au début
    # de chaque run, APRÈS la récupération d'un swap interrompu (la preuve
    # restaurée redevient éligible à sa propre expiration) et AVANT la purge
    # des restes de staging — qui reste la propriété de la logique
    # transactionnelle ci-dessous.
    retention_purged = _purge_expired_local_proofs()
    # Restes d'un run précédent interrompu: le staging est jetable par contrat.
    for leftover in (staging, previous):
        if leftover.exists():
            try:
                shutil.rmtree(leftover)
            except PermissionError as exc:
                # Fichiers root laissés par un run Docker interrompu avant son
                # chown final: erreur actionnable plutôt que PermissionError brute.
                _raise_actionable_permission_error(leftover, exc)
    _secure_dir(staging)
    context = redaction_context_from_environment()
    env = _repo_env()

    # Séparation chemin physique (écrit dans le staging) / chemin logique
    # publié (.proof/...): tout ce qui entre au summary, au rapport HTML et
    # aux logs est réécrit du premier vers le second avant publication. Les
    # racines sont passées SANS slash: _rewrite_text_paths les ancre lui-même
    # (préfixe `racine/` ou valeur exactement égale), ce qui préserve les
    # littéraux `.proof.new` cités dans les extraits de code capturés.
    publish_rewrites: tuple[tuple[str, str], ...] = (
        (str(staging.resolve()), str(PROOF_DIR.resolve())),
        (str(staging), str(PROOF_DIR)),
    )
    # Les preuves écrites DANS le conteneur Symfony parlent déjà en `.proof/…`
    # (montage /workspace/.proof): on les ramène au chemin physique du staging
    # pour pouvoir les lire pendant la génération, avant réécriture inverse.
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
            [sys.executable, "-m", "ruff", "check", "src", "tests"],
            staging / "ruff-check.log",
            env=env,
            timeout=scaled(RUFF_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "ruff-format",
            "Ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "src", "tests"],
            staging / "ruff-format.log",
            env=env,
            timeout=scaled(RUFF_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "mypy",
            "Mypy typage",
            [sys.executable, "-m", "mypy", "src/cdpx"],
            staging / "mypy.log",
            env=env,
            timeout=scaled(MYPY_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
        run_evidence(
            "unit",
            "Pytest unitaires",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests",
                "--ignore=tests/e2e",
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
            "e2e",
            "Pytest E2E Chrome",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/test_e2e_chrome.py",
                "tests/e2e/test_e2e_sessions.py",
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
            "Aide CLI",
            [sys.executable, "-m", "cdpx.cli", "--help"],
            cli_help,
            env=env,
            timeout=scaled(CLI_HELP_TIMEOUT_S),
            redaction_context=context,
            path_rewrites=publish_rewrites,
        ),
    ]

    # Un pytest mort sans exécuter pytest_sessionfinish n'a pas écrit ses
    # manifestes d'évidence: ses artefacts orphelins feraient échouer le
    # staging fail-closed avec un message trompeur, alors que la cause réelle
    # est déjà rouge au verdict. Un run à échec normal (exit 1) a écrit ses
    # manifestes — la purge y est alors un no-op; tout autre code non nul
    # (124 deadline, 137 OOM, returncode négatif d'un signal, segfault) peut
    # signifier une mort sans épilogue, donc on purge dès que l'exit ≠ 0.
    if any(
        command.id in {"unit", "e2e", "symfony-e2e"} and command.exit_code != 0
        for command in commands
    ):
        try:
            _purge_unmanifested_evidence(staging)
        except PermissionError as exc:
            _raise_actionable_permission_error(staging, exc)

    # Preuve secondaire native (pty, aucune dépendance): les .cast atterrissent
    # dans le staging et entrent au rapport via le catalogue (rglob). Le portail
    # exige un statut "generated" pour chaque commande de démonstration.
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
    # Observabilité de la rétention: la purge du début de run est attestée
    # dans le summary publié (validation-summary.json), pas seulement sur
    # stderr.
    summary["retention"] = {
        "retention_days": retention_seconds // (24 * 60 * 60),
        "purged": retention_purged,
    }
    # Publication: les chemins physiques du staging redeviennent les chemins
    # logiques .proof/… attendus par le contrat du summary, du HTML et des logs.
    summary = _rewrite_tree_paths(summary, publish_rewrites)
    summary = redact_tree(summary, context=context, path="$.summary")
    # Les contenus inlinés n'existent que dans le payload HTML: les JSON disque
    # pointent vers les fichiers d'artefacts, sans duplication.
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
    # Bascule transactionnelle: la preuve précédente n'est remplacée qu'après
    # un staging complet et partageable. Toute exception avant ce point laisse
    # `.proof` intact (le staging partiel reste pour diagnostic et sera purgé
    # au prochain run).
    if PROOF_DIR.exists():
        os.replace(PROOF_DIR, previous)
    os.replace(staging, PROOF_DIR)
    if previous.exists():
        try:
            shutil.rmtree(previous)
        except PermissionError as exc:
            # La preuve est déjà publiée: un `.proof.old` non supprimable
            # (fichiers root) ne doit pas rougir le run, mais l'erreur doit
            # surfacer maintenant plutôt qu'au run suivant.
            print(
                f"avertissement: nettoyage impossible de {previous} ({exc}); "
                f"{_docker_chown_remedy(previous)}",
                file=sys.stderr,
            )
    return summary


def generate() -> dict:
    with _private_umask():
        return _generate()


def main() -> int:
    summary = generate()
    print(
        json.dumps(
            {k: summary[k] for k in ("ok", "artifact_dir", "report_html")}, separators=(",", ":")
        )
    )
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
