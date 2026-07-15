# ruff: noqa: E501
"""Generate the human proof report consumed by `make proof`.

The report is intentionally evidence-first: every human-facing conclusion is
derived from command exits, pytest JUnit XML, captured logs, or the CLI help
captured during the same run.
"""

from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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
    scan_canaries,
)
from cdpx.proofing.cast import CAST_COMMANDS, collect_cast_evidence
from cdpx.proofing.documentation import (
    build_documentation_catalog,
    documentation_failures,
)
from cdpx.proofing.features import build_feature_inventory, feature_failures
from cdpx.security.redaction import RedactionContext, redact_text, redact_tree
from cdpx.testing.evidence import (
    EVIDENCE_SCHEMA,
    PROOF_RETENTION_ENV,
    SCENARIOS_SCHEMA,
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
SYMFONY_NODEID = "tests/e2e/test_e2e_symfony.py::test_profiler_reads_real_symfony_web_profiler"

# Génération transactionnelle: tout l'arbre est produit dans `.proof.new/`
# (même parent que `.proof`, donc même filesystem), puis publié par bascule
# atomique en fin de run réussi. `.proof.old/` ne vit que le temps du swap.
PROOF_STAGING_SUFFIX = ".new"
PROOF_PREVIOUS_SUFFIX = ".old"

# Budgets de deadline par étape (secondes). Ils bornent chaque commande de
# preuve: un dépassement produit un exit 124 et un verdict rouge, jamais un
# blocage indéfini. `CDPX_PROOF_TIMEOUT_SCALE` (flottant strictement positif,
# ex. "2" sur machine lente) multiplie uniformément tous les budgets.
PROOF_TIMEOUT_SCALE_ENV = "CDPX_PROOF_TIMEOUT_SCALE"
RUFF_TIMEOUT_S = 120.0
MYPY_TIMEOUT_S = 300.0
UNIT_TIMEOUT_S = 600.0
E2E_TIMEOUT_S = 900.0
SYMFONY_TIMEOUT_S = 900.0
CLI_HELP_TIMEOUT_S = 30.0
GIT_TIMEOUT_S = 30.0

GENERATED_PREFIXES = (".proof/", ".idea/")
PRIVATE_WORKTREE_PREFIXES = ("AGENTS.md", "article/", "presentation/")
VALIDATION_DOC = Path("docs/VALIDATION.md")
MERMAID_VERSION = "11.16.0"
MERMAID_RESOURCE = f"vendor/mermaid-{MERMAID_VERSION}.min.js"
MERMAID_SHA256 = "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"
XTERM_VERSION = "5.5.0"
XTERM_JS_RESOURCE = f"vendor/xterm-{XTERM_VERSION}.min.js"
XTERM_CSS_RESOURCE = f"vendor/xterm-{XTERM_VERSION}.min.css"
XTERM_JS_SHA256 = "4196e242ef1cf4c2adead8d97f4a772a69576076f70b095e004b4abbb049e7bf"
XTERM_CSS_SHA256 = "f7f724aea2bb620a6482bfb8e4bdecfae1152b0c7facef55fbda61f3b6cfedb2"

_ALLOWED_ENV_NAMES = {
    "CI",
    "COLORTERM",
    "HOME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    PROOF_RETENTION_ENV,
    "SHELL",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
    "VIRTUAL_ENV",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
}
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


@dataclass
class CommandEvidence:
    id: str
    label: str
    argv: list[str]
    log: str
    exit_code: int
    duration_s: float
    status: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _secure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ArtifactError(f"dossier de preuve symbolique interdit: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise ArtifactError(f"dossier de preuve requis: {path}")
    path.chmod(0o700)


def _write_private_bytes(path: Path, data: bytes) -> None:
    _secure_dir(path.parent)
    if path.is_symlink():
        raise ArtifactError(f"lien symbolique interdit: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _write_private_text(path: Path, value: str) -> None:
    _write_private_bytes(path, value.encode("utf-8"))


def _harden_tree(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ArtifactError(f"lien symbolique interdit dans les preuves: {path}")
        path.chmod(0o700 if path.is_dir() else 0o600)
    root.chmod(0o700)


@contextmanager
def _private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _sanitize_argv(argv: list[str], context: RedactionContext) -> list[str]:
    return [
        redact_text(value, context=context, path=f"$.argv[{index}]")
        for index, value in enumerate(argv)
    ]


def _repo_env() -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name in _ALLOWED_ENV_NAMES}
    src = str(Path("src").resolve())
    env["PYTHONPATH"] = src
    return env


def _staging_dir() -> Path:
    return PROOF_DIR.with_name(PROOF_DIR.name + PROOF_STAGING_SUFFIX)


def _previous_dir() -> Path:
    return PROOF_DIR.with_name(PROOF_DIR.name + PROOF_PREVIOUS_SUFFIX)


def proof_timeout_scale(environ: dict[str, str] | None = None) -> float:
    """Facteur d'échelle des deadlines, validé fail-closed comme la rétention."""

    values = os.environ if environ is None else environ
    raw = values.get(PROOF_TIMEOUT_SCALE_ENV)
    if raw is None:
        return 1.0
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", raw) or float(raw) <= 0:
        raise ValueError(f"{PROOF_TIMEOUT_SCALE_ENV} doit être un flottant strictement positif")
    return float(raw)


def _rewrite_text_paths(value: str, rewrites: Sequence[tuple[str, str]]) -> str:
    for physical, logical in rewrites:
        value = value.replace(physical, logical)
    return value


def _rewrite_tree_paths(value: Any, rewrites: Sequence[tuple[str, str]]) -> Any:
    """Applique les réécritures de chemins à toutes les chaînes d'un arbre JSON."""

    if isinstance(value, str):
        return _rewrite_text_paths(value, rewrites)
    if isinstance(value, list):
        return [_rewrite_tree_paths(item, rewrites) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite_tree_paths(item, rewrites) for key, item in value.items()}
    return value


def _stream_to_private_file(
    argv: list[str],
    sink: Path,
    *,
    env: dict[str, str],
    timeout: float | None,
) -> tuple[int, bool]:
    """Exécute ``argv`` en streamant stdout+stderr bruts dans ``sink`` (0600).

    La sortie n'est jamais bufferisée en mémoire: le fichier grossit au fil de
    l'exécution (observable via tail -f) et la deadline est monotone. Retourne
    (exit_code, timed_out): 127 si le binaire est introuvable, 124 après kill
    sur dépassement de deadline.
    """

    _secure_dir(sink.parent)
    if sink.is_symlink():
        raise ArtifactError(f"lien symbolique interdit: {sink}")
    sink.unlink(missing_ok=True)
    fd = os.open(sink, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=Path.cwd(),
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            stream.write((str(exc) + "\n").encode("utf-8"))
            return 127, False
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Deadline dépassée: kill puis drain du statut. Ce qui a déjà été
            # streamé dans le fichier reste disponible pour le log final.
            proc.kill()
            proc.wait()
            return 124, True
    return exit_code, False


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
    context = redaction_context or redaction_context_from_environment()
    started = _now()
    start = time.monotonic()
    _secure_dir(log_path.parent)
    safe_argv = _sanitize_argv(
        [_rewrite_text_paths(value, path_rewrites) for value in argv], context
    )
    header = [
        f"$ {' '.join(safe_argv)}",
        f"started_at: {started}",
        "",
        "--- output ---",
    ]
    # Flux brut streamé dans un fichier privé *.partial (progression observable,
    # mémoire bornée), puis redaction du texte complet et écriture atomique du
    # log final: un secret à cheval sur deux chunks ne peut pas y échapper.
    partial = log_path.with_name(f"{log_path.name}.partial")
    exit_code, timed_out = _stream_to_private_file(argv, partial, env=env, timeout=timeout)
    raw = partial.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        raw += f"\ntimeout: commande interrompue après {timeout}s (exit 124)\n"
    output = redact_text(
        _rewrite_text_paths(raw, path_rewrites), context=context, path=f"$.commands.{id}.stdout"
    )
    duration = time.monotonic() - start
    footer = ["", "--- result ---", f"exit_code: {exit_code}", f"duration_s: {duration:.3f}", ""]
    _write_private_text(log_path, "\n".join(header) + "\n" + output + "\n".join(footer))
    partial.unlink(missing_ok=True)
    return CommandEvidence(
        id=id,
        label=label,
        argv=argv,
        log=str(log_path),
        exit_code=exit_code,
        duration_s=round(duration, 3),
        status="ok" if exit_code == 0 else "failed",
    )


def _run_text(
    argv: list[str],
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output + f"\ntimeout after {timeout}s\n"
    except FileNotFoundError as exc:
        return 127, f"{exc}\n"
    return proc.returncode, proc.stdout


def _write_command_log(
    log_path: Path,
    argv: list[str],
    started: str,
    body: str,
    result: str,
    *,
    redaction_context: RedactionContext | None = None,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    _secure_dir(log_path.parent)
    _write_private_text(
        log_path,
        "\n".join(
            [
                f"$ {' '.join(_sanitize_argv(argv, context))}",
                f"started_at: {started}",
                "",
                "--- output ---",
                redact_text(body, context=context, path="$.command.body").rstrip(),
                "",
                "--- result ---",
                redact_text(result, context=context, path="$.command.result").rstrip(),
                "",
            ]
        ),
    )


def write_symfony_unavailable_evidence(
    reason: str,
    *,
    redaction_context: RedactionContext | None = None,
    evidence_dir: Path | None = None,
    log_path: Path | None = None,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    # Les défauts sont résolus à l'appel pour rester monkeypatchables et
    # permettre au pipeline de cibler l'arbre de staging.
    evidence_dir = EVIDENCE_DIR if evidence_dir is None else evidence_dir
    log_path = SYMFONY_LOG if log_path is None else log_path
    _secure_dir(evidence_dir)
    safe_reason = redact_text(reason, context=context, path="$.symfony.reason")
    payload = {
        "schema": SCENARIOS_SCHEMA,
        "suite": "symfony",
        "generated_at": _now(),
        "count": 1,
        "scenarios": [
            {
                "nodeid": SYMFONY_NODEID,
                "suite": "symfony",
                "title": "Symfony Docker portal unavailable",
                "area": "developer diagnostics",
                "feature": "dev-profiler-diff",
                "journey": "read-profiler",
                "scenario_id": "dev-profiler-diff.read-symfony-profiler",
                "proves": [
                    "Symfony Docker e2e was requested by proof generation.",
                    "Docker was unavailable, so the real Symfony scenario did not run.",
                ],
                "intent": "",
                "intent_line": 0,
                "assertions": [],
                "failed_line": 0,
                "started_at": _now(),
                "duration_s": 0.0,
                "status": "unavailable",
                "phase": "setup",
                "message": safe_reason,
                "stdout": "",
                "stderr": "",
                "artifacts": [
                    {
                        "type": "logs",
                        "label": "Symfony e2e availability log",
                        "path": str(log_path),
                        "bytes": log_path.stat().st_size if log_path.exists() else 0,
                        "mime": "text/plain",
                        "created_at": _now(),
                    }
                ],
            }
        ],
    }
    _write_private_text(
        evidence_dir / "symfony-scenarios.json",
        json.dumps(redact_tree(payload, context=context), ensure_ascii=False, indent=2) + "\n",
    )


def run_symfony_evidence(
    *,
    redaction_context: RedactionContext | None = None,
    proof_dir: Path | None = None,
    timeout: float | None = None,
    path_rewrites: Sequence[tuple[str, str]] = (),
) -> CommandEvidence:
    context = redaction_context or redaction_context_from_environment()
    # Résolution à l'appel: sans proof_dir explicite, les globals (donc les
    # monkeypatchs de tests) font foi; le pipeline passe l'arbre de staging.
    log_path = SYMFONY_LOG if proof_dir is None else proof_dir / SYMFONY_LOG.name
    evidence_dir = EVIDENCE_DIR if proof_dir is None else proof_dir / EVIDENCE_DIR.name
    argv = [
        "docker",
        "compose",
        "-f",
        "docker-compose.symfony-e2e.yml",
        "up",
        "--build",
        "--abort-on-container-exit",
        "--exit-code-from",
        "cdpx",
    ]
    started = _now()
    start = time.monotonic()
    _secure_dir(evidence_dir)
    compose_env = _repo_env()
    compose_env["CDPX_E2E_UID"] = str(os.getuid())
    compose_env["CDPX_E2E_GID"] = str(os.getgid())
    # Le volume `.proof` du compose est paramétré: le conteneur monte l'arbre
    # cible (staging pendant `make proof`, `./.proof` par défaut via Makefile).
    compose_env["CDPX_PROOF_DIR"] = str((PROOF_DIR if proof_dir is None else proof_dir).resolve())

    checks: list[str] = []
    if shutil.which("docker") is None:
        reason = "Docker CLI not found; Symfony e2e is required for release proof."
        _write_command_log(
            log_path,
            argv,
            started,
            reason,
            "status: unavailable\nexit_code: 1",
            redaction_context=context,
        )
        write_symfony_unavailable_evidence(
            reason, redaction_context=context, evidence_dir=evidence_dir, log_path=log_path
        )
        return CommandEvidence(
            id="symfony-e2e",
            label="Symfony E2E Docker",
            argv=argv,
            log=str(log_path),
            exit_code=1,
            duration_s=round(time.monotonic() - start, 3),
            status="unavailable",
        )

    for check_argv in (["docker", "compose", "version"], ["docker", "info"]):
        code, output = _run_text(check_argv, timeout=15, env=compose_env)
        checks.append(f"$ {' '.join(check_argv)}\n{output.rstrip()}\nexit_code: {code}")
        if code != 0:
            reason = (
                "Docker is installed but unavailable; Symfony e2e is required for release proof."
            )
            body = "\n\n".join(checks + [reason])
            _write_command_log(
                log_path,
                argv,
                started,
                body,
                "status: unavailable\nexit_code: 1",
                redaction_context=context,
            )
            write_symfony_unavailable_evidence(
                reason, redaction_context=context, evidence_dir=evidence_dir, log_path=log_path
            )
            return CommandEvidence(
                id="symfony-e2e",
                label="Symfony E2E Docker",
                argv=argv,
                log=str(log_path),
                exit_code=1,
                duration_s=round(time.monotonic() - start, 3),
                status="unavailable",
            )

    down_argv = [
        "docker",
        "compose",
        "-f",
        "docker-compose.symfony-e2e.yml",
        "down",
        "--remove-orphans",
    ]
    pre_code, pre_output = _run_text(down_argv, timeout=60, env=compose_env)
    up_partial = log_path.with_name(f"{log_path.name}.partial")
    try:
        # Le `up` est streamé dans un fichier privé (progression observable,
        # mémoire bornée) et borné par deadline: kill sur dépassement, exit 124.
        up_code, up_timed_out = _stream_to_private_file(
            argv, up_partial, env=compose_env, timeout=timeout
        )
    finally:
        # Même une interruption/exception/deadline pendant `up` doit rendre la
        # main avec les conteneurs et réseaux Compose supprimés.
        post_code, post_output = _run_text(down_argv, timeout=60, env=compose_env)
    up_output = up_partial.read_text(encoding="utf-8", errors="replace")
    if up_timed_out:
        up_output += f"\ntimeout: docker compose up interrompu après {timeout}s (exit 124)\n"
    duration = time.monotonic() - start
    body = "\n\n".join(
        checks
        + [
            f"$ {' '.join(down_argv)}\n{pre_output.rstrip()}\nexit_code: {pre_code}",
            f"$ {' '.join(argv)}\n{up_output.rstrip()}\nexit_code: {up_code}",
            f"$ {' '.join(down_argv)}\n{post_output.rstrip()}\nexit_code: {post_code}",
        ]
    )
    result_code = up_code if up_code != 0 else post_code
    _write_command_log(
        log_path,
        argv,
        started,
        _rewrite_text_paths(body, path_rewrites),
        f"exit_code: {result_code}\nduration_s: {duration:.3f}",
        redaction_context=context,
    )
    up_partial.unlink(missing_ok=True)
    return CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=argv,
        log=str(log_path),
        exit_code=result_code,
        duration_s=round(duration, 3),
        status="ok" if result_code == 0 else "failed",
    )


def collect_git_context(
    *,
    redaction_context: RedactionContext | None = None,
    status_path: Path | None = None,
    diff_stat_path: Path | None = None,
) -> dict:
    context = redaction_context or redaction_context_from_environment()
    status_path = GIT_STATUS if status_path is None else status_path
    diff_stat_path = GIT_DIFF_STAT if diff_stat_path is None else diff_stat_path
    branch_code, branch = _run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], GIT_TIMEOUT_S)
    sha_code, sha = _run_text(["git", "rev-parse", "--short", "HEAD"], GIT_TIMEOUT_S)
    status_code, status = _run_text(["git", "status", "--short"], GIT_TIMEOUT_S)
    stat_code, stat = _run_text(
        [
            "git",
            "diff",
            "--stat",
            "--",
            ".",
            ":(exclude).proof/*",
            f":(exclude){PROOF_DIR.name}{PROOF_STAGING_SUFFIX}/*",
            f":(exclude){PROOF_DIR.name}{PROOF_PREVIOUS_SUFFIX}/*",
            ":(exclude).idea/*",
        ],
        GIT_TIMEOUT_S,
    )

    safe_status_lines = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path == "AGENTS.md" or path.startswith(PRIVATE_WORKTREE_PREFIXES[1:]):
            continue
        safe_status_lines.append(line)
    status = redact_text("\n".join(safe_status_lines), context=context, path="$.git.status")
    if status:
        status += "\n"
    stat = redact_text(stat, context=context, path="$.git.diff_stat")
    _write_private_text(status_path, status)
    _write_private_text(diff_stat_path, stat)

    changed_files = []
    generated_files = []
    for line in status.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        item = {"status": line[:2].strip() or "?", "path": path}
        if path.startswith(GENERATED_PREFIXES):
            generated_files.append(item)
        else:
            changed_files.append(item)

    return {
        "branch": redact_text(branch.strip(), context=context, path="$.git.branch")
        if branch_code == 0
        else "unknown",
        "sha": sha.strip() if sha_code == 0 else "unknown",
        "status_code": status_code,
        "diff_stat_code": stat_code,
        "changed_files": changed_files,
        "generated_files": generated_files,
        "changed_count": len(changed_files),
        "generated_count": len(generated_files),
        "status_path": str(status_path),
        "diff_stat_path": str(diff_stat_path),
    }


def classify_change(path: str) -> str:
    if path.startswith("src/"):
        return "Code produit"
    if path.startswith("tests/"):
        return "Tests"
    if path.startswith("docs/") or path in {
        "README.md",
        "HARNESS.md",
        "CLAUDE.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "SUPPORT.md",
    }:
        return "Documentation"
    if path in {"Makefile", "pyproject.toml", "Dockerfile"} or path.startswith(".github/"):
        return "Harness / CI"
    return "Autre"


def build_impact_map(git_context: dict, help_commands: list[dict[str, str]]) -> dict:
    changed_files = git_context["changed_files"]
    categories: dict[str, list[str]] = {}
    for item in changed_files:
        categories.setdefault(classify_change(item["path"]), []).append(item["path"])

    paths = {item["path"] for item in changed_files}
    entrypoints = []
    if "Makefile" in paths:
        entrypoints.append(
            {
                "name": "make proof",
                "type": "Make target",
                "evidence": "Makefile",
                "review_focus": "Commande publique de génération du rapport.",
            }
        )
    if "src/cdpx/proof.py" in paths:
        entrypoints.append(
            {
                "name": "python -m cdpx.proof",
                "type": "Python module",
                "evidence": "src/cdpx/proof.py",
                "review_focus": "Collecte, classification et rendu HTML des preuves.",
            }
        )
    if "tests/test_proof.py" in paths:
        entrypoints.append(
            {
                "name": "tests/test_proof.py",
                "type": "Unit tests",
                "evidence": "tests/test_proof.py",
                "review_focus": "Parsing JUnit, aide CLI et résumé historique.",
            }
        )

    change_types = []
    if any(path.startswith("src/") for path in paths):
        change_types.append("code")
    if any(path.startswith("tests/") for path in paths):
        change_types.append("tests")
    if "Makefile" in paths or any(path.startswith(".github/") for path in paths):
        change_types.append("harness")
    if any(path.startswith("docs/") or path in {"README.md", "HARNESS.md"} for path in paths):
        change_types.append("docs")
    if help_commands:
        change_types.append("surface-cli-verifiee")

    return {
        "change_types": change_types or ["unknown"],
        "categories": categories,
        "entrypoints": entrypoints,
    }


def build_review_guide(impact: dict) -> dict:
    order = []
    categories = impact["categories"]
    if "Harness / CI" in categories:
        order.append("Commencer par Makefile: vérifier le contrat utilisateur de `make proof`.")
    if "Code produit" in categories:
        order.append("Lire `src/cdpx/proof.py`: collecte, verdict, résumé JSON, rendu HTML.")
    if "Tests" in categories:
        order.append("Lire `tests/test_proof.py`: verrouillage du parsing et des clés historiques.")
    if "Documentation" in categories:
        order.append("Finir par README/HARNESS/VALIDATION: alignement du contrat public.")
    if not order:
        order.append(
            "Lire les fichiers listés dans la carte d'impact, du point d'entrée vers les preuves."
        )

    watch_outs = [
        "Le verdict doit être dérivé des commandes et des JUnit, pas d'un statut statique.",
        "Les artefacts lourds doivent rester repliables et traçables pour éviter le bruit en PR.",
        "Les chemins de preuves doivent rester relatifs et ouvrables depuis le dépôt.",
        "Les preuves optionnelles absentes doivent être déclarées comme unknowns, pas simulées.",
    ]
    return {"order": order, "watch_outs": watch_outs}


def build_risks_and_unknowns(git_context: dict) -> dict:
    risks = [
        {
            "risk": "`make proof` devient plus strict.",
            "mitigation": (
                "Les outils Python passent par `python -m ...`; le rapport est écrit même "
                "en cas d'échec."
            ),
            "rollback": "Revenir à l'ancienne cible Makefile si nécessaire.",
        },
        {
            "risk": "Rapport trop verbeux pour une PR.",
            "mitigation": "Résumé court; logs et détails secondaires en sections repliables.",
            "rollback": "Réduire les sections dans `render_html` sans toucher à la collecte.",
        },
    ]
    unknowns = [
        {
            "item": "Rendu GitHub exact du HTML",
            "why": "Le rapport est un artefact HTML, pas une page rendue dans la PR GitHub.",
            "how_to_verify": (
                "Télécharger l'artefact `proof` puis ouvrir `.proof/proof-report.html`."
            ),
        },
        {
            "item": "Casts de démonstration",
            "why": (
                "L'enregistreur natif (pty) fait partie du portail: un cast manquant "
                "ou dégradé fait échouer `make proof`."
            ),
            "how_to_verify": "Ouvrir le rapport et jouer les casts du catalogue de preuves.",
        },
        {
            "item": "Screenshot produit",
            "why": "Changement harness/rapport, pas delta UI produit.",
            "how_to_verify": "Pour une PR UI, ajouter une capture dans `.proof/`.",
        },
    ]
    if git_context["generated_count"]:
        unknowns.append(
            {
                "item": "Artefacts générés versionnés",
                "why": "Le dépôt suit déjà certains fichiers `.proof`.",
                "how_to_verify": (
                    "Vérifier `git status --short`; `.proof/` doit rester un artefact CI ignoré."
                ),
            }
        )
    return {"risks": risks, "unknowns": unknowns}


def _junit_status(suite: dict) -> str:
    if not suite.get("exists", True):
        return "unavailable"
    return "passed" if suite.get("failures", 0) + suite.get("errors", 0) == 0 else "failed"


def build_evidence_catalog(
    summary: dict,
    unit: dict,
    e2e: dict,
    symfony: dict,
    *,
    proof_dir: Path | None = None,
) -> list[dict]:
    # Racine physique parcourue pour les preuves visuelles/casts; les chemins
    # canoniques publiés restent dérivés des constantes module.
    scan_root = PROOF_DIR if proof_dir is None else proof_dir
    catalog = [
        {
            "type": "rapport-html",
            "name": "Rapport humain projet",
            "path": str(REPORT_HTML),
            "status": "generated",
            "roi": "Point d'entrée humain: verdict, périmètre, milestones et preuves repliables.",
        },
        {
            "type": "resume-json",
            "name": "Résumé machine",
            "path": str(SUMMARY_JSON),
            "status": "generated",
            "roi": "Signal compact pour CI/handoff sans relire tous les logs.",
        },
        {
            "type": "junit",
            "name": "Tests unitaires JUnit",
            "path": unit.get("path", str(PROOF_DIR / "unit-junit.xml")),
            "status": "passed"
            if unit.get("failures", 0) + unit.get("errors", 0) == 0
            else "failed",
            "roi": f"{unit.get('tests', 0)} tests unitaires structurés.",
        },
        {
            "type": "junit",
            "name": "E2E Chrome JUnit",
            "path": e2e.get("path", str(PROOF_DIR / "e2e-junit.xml")),
            "status": "passed" if e2e.get("failures", 0) + e2e.get("errors", 0) == 0 else "failed",
            "roi": (
                f"{e2e.get('tests', 0)} scénarios navigateur Chrome, "
                f"{e2e.get('skipped', 0)} skip déclaré."
            ),
        },
        {
            "type": "junit",
            "name": "Symfony E2E JUnit",
            "path": symfony.get("path", str(SYMFONY_JUNIT)),
            "status": _junit_status(symfony),
            "roi": (
                f"{symfony.get('tests', 0)} scénario Symfony réel, "
                f"{symfony.get('skipped', 0)} indisponibilité/skip déclaré."
            ),
        },
        {
            "type": "logs",
            "name": "Logs unitaires",
            "path": str(UNIT_LOG),
            "status": "generated",
            "roi": "Transcript terminal reproductible.",
        },
        {
            "type": "logs",
            "name": "Logs E2E Chrome",
            "path": str(E2E_LOG),
            "status": "generated",
            "roi": "Transcript navigateur réel; Chrome absent est bloquant.",
        },
        {
            "type": "logs",
            "name": "Logs Symfony E2E",
            "path": str(SYMFONY_LOG),
            "status": next(
                (
                    command.get("status", "generated")
                    for command in summary.get("commands", [])
                    if command.get("id") == "symfony-e2e"
                ),
                "generated",
            ),
            "roi": "Transcript Docker Compose, politique d'indisponibilité et teardown.",
        },
        {
            "type": "surface-publique",
            "name": "Aide CLI capturée",
            "path": str(CLI_HELP),
            "status": "generated",
            "roi": "Contrat public exposé par le binaire.",
        },
        {
            "type": "git",
            "name": "Snapshot Git",
            "path": str(GIT_STATUS),
            "status": "generated",
            "roi": "Provenance du run et état local au moment de la preuve.",
        },
        {
            "type": "git",
            "name": "Diff stat",
            "path": str(GIT_DIFF_STAT),
            "status": "generated",
            "roi": "Contexte local sans ouvrir le diff complet.",
        },
        {
            "type": "scenarios",
            "name": "Scénarios pytest documentés",
            "path": str(EVIDENCE_DIR),
            "status": "generated",
            "roi": "Association test par test entre statut, logs et artefacts.",
        },
    ]
    for pattern, evidence_type in (
        ("*.png", "screenshot"),
        ("*.webm", "video"),
        ("*.mp4", "video"),
        ("*.cast", "asciinema"),
    ):
        for path in sorted(scan_root.rglob(pattern)):
            catalog.append(
                {
                    "type": evidence_type,
                    "name": path.name,
                    "path": str(path),
                    "status": "generated",
                    "roi": "Preuve visuelle ou replay terminal ajouté au rapport.",
                }
            )
    if not any(item["type"] == "screenshot" for item in catalog):
        catalog.append(
            {
                "type": "screenshot",
                "name": "Capture UI",
                "path": "",
                "status": "not-needed",
                "roi": "Non générée automatiquement; utile seulement pour prouver un delta visuel.",
            }
        )
    inline_catalog_casts(catalog)
    return catalog


def collect_project_inventory(help_commands: list[dict[str, str]]) -> dict:
    fixtures = sorted(str(path) for path in Path("tests/fixtures").glob("*") if path.is_file())
    fixture_kinds: dict[str, int] = {}
    for path in fixtures:
        suffix = Path(path).suffix.lstrip(".") or "none"
        fixture_kinds[suffix] = fixture_kinds.get(suffix, 0) + 1

    milestone_docs = sorted(str(path) for path in Path("docs/milestones").glob("*.md"))
    docs = [
        path
        for path in (
            "README.md",
            "HARNESS.md",
            "docs/CONTEXT.md",
            "docs/PRIMITIVES.md",
            "docs/ROADMAP.md",
            "docs/TODO.md",
            "docs/VALIDATION.md",
        )
        if Path(path).exists()
    ]

    # Source unique de version: cdpx.__version__ (pyproject la lit en dynamic).
    from cdpx import __version__ as version

    return {
        "name": "cdpx",
        "version": version,
        "mission": (
            "CLI de primitives Chrome DevTools Protocol pour agents de dev et humains "
            "qui pilotent des audits navigateur."
        ),
        "cli_command_count": len(help_commands),
        "cli_commands": [command["name"] for command in help_commands],
        "fixture_count": len(fixtures),
        "fixture_kinds": fixture_kinds,
        "fixtures": fixtures,
        "docs": docs,
        "milestone_docs": milestone_docs,
    }


def parse_validation_matrix() -> list[dict[str, str]]:
    if not VALIDATION_DOC.exists():
        return []
    rows = []
    for line in VALIDATION_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or "---" in line or "Milestone" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append({"milestone": cells[0], "proof": cells[1]})
    return rows


def group_cases_by_module(unit: dict, e2e: dict, symfony: dict | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    for suite_name, suite in (("unit", unit), ("e2e", e2e), ("symfony", symfony or {"cases": []})):
        for case in suite["cases"]:
            module = case["classname"].split(".")[-1] or suite_name
            group = groups.setdefault(
                module,
                {"module": module, "suite": suite_name, "tests": 0, "failed": 0, "skipped": 0},
            )
            group["tests"] += 1
            if case["status"] in {"failed", "error"}:
                group["failed"] += 1
            elif case["status"] == "skipped":
                group["skipped"] += 1
    return sorted(groups.values(), key=lambda item: (item["suite"], item["module"]))


def load_scenario_evidence(root: Path = EVIDENCE_DIR) -> dict:
    suites: dict[str, list[dict]] = {"unit": [], "integration": [], "e2e": [], "symfony": []}
    files: list[str] = []
    if not root.exists():
        return {"suites": suites, "files": files, "totals": scenario_totals(suites)}
    for path in sorted(root.glob("*-scenarios.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        suite = payload.get("suite", path.stem.removesuffix("-scenarios"))
        scenarios = payload.get("scenarios", [])
        suites.setdefault(suite, []).extend(scenarios)
        files.append(str(path))
    return {"suites": suites, "files": files, "totals": scenario_totals(suites)}


def scenario_totals(suites: dict[str, list[dict]]) -> dict:
    scenarios = [scenario for items in suites.values() for scenario in items]
    e2e = suites.get("e2e", [])
    symfony = suites.get("symfony", [])
    screenshots = sum(
        1
        for scenario in scenarios
        for artifact in scenario.get("artifacts", [])
        if artifact.get("type") == "screenshot"
    )
    missing_e2e = [
        scenario["nodeid"]
        for scenario in e2e
        if scenario.get("status") != "skipped"
        and not any(
            artifact.get("type") == "screenshot" for artifact in scenario.get("artifacts", [])
        )
    ]
    return {
        "scenarios": len(scenarios),
        "unit": len(suites.get("unit", [])),
        "integration": len(suites.get("integration", [])),
        "e2e": len(e2e),
        "symfony": len(symfony),
        "screenshots": screenshots,
        "missing_e2e_screenshots": missing_e2e,
    }


def proof_failures_from_scenarios(scenario_evidence: dict) -> list[str]:
    failures = []
    for nodeid in scenario_evidence["totals"]["missing_e2e_screenshots"]:
        failures.append(f"missing e2e screenshot: {nodeid}")
    return failures


def enrich_scenario_evidence(scenario_evidence: dict, feature_inventory: dict) -> dict:
    by_suite_and_nodeid = {}
    for feature in feature_inventory.get("features", []):
        for scenario in feature.get("matched_scenarios", []):
            key = (scenario.get("suite", ""), scenario.get("nodeid", ""))
            by_suite_and_nodeid[key] = scenario

    suites = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        suites[suite] = [
            by_suite_and_nodeid.get((suite, scenario.get("nodeid", "")), scenario)
            for scenario in scenarios
        ]
    return {
        **scenario_evidence,
        "suites": suites,
        "totals": scenario_totals(suites),
    }


# L'inline ne concerne que le textuel: la CSP du rapport (connect-src 'none')
# interdit tout fetch, donc ce que les visualiseurs affichent doit voyager
# dans le JSON embarqué. Les binaires restent des liens locaux.
_INLINE_TYPES = frozenset(
    {"command", "log-excerpt", "logs", "json", "console", "network", "profiler", "asciinema"}
)
INLINE_MAX_BYTES = 16 * 1024
# Les .cast sont la matière première du player: cap dédié plus large, mais
# toujours très en deçà de MAX_CAST_BYTES pour contenir le poids du rapport.
INLINE_CAST_MAX_BYTES = 256 * 1024
INLINE_TOTAL_BUDGET = 2 * 1024 * 1024
INLINE_CAST_BUDGET = 1 * 1024 * 1024
EXCERPT_HEAD_LINES = 10
EXCERPT_TAIL_LINES = 30


def _artifact_excerpt(text: str) -> str:
    lines = text.splitlines()
    limit = EXCERPT_HEAD_LINES + EXCERPT_TAIL_LINES
    if len(lines) <= limit:
        return text.rstrip("\n")
    omitted = len(lines) - limit
    return "\n".join(
        [
            *lines[:EXCERPT_HEAD_LINES],
            f"… ({omitted} lignes tronquées) …",
            *lines[-EXCERPT_TAIL_LINES:],
        ]
    )


def _inline_artifact(entry: dict, remaining: int) -> int:
    if entry.get("type") not in _INLINE_TYPES:
        return remaining
    raw_path = str(entry.get("path", ""))
    path = Path(raw_path)
    if not raw_path or path.is_symlink() or not path.is_file():
        entry["inline_skipped"] = "illisible"
        return remaining
    size = path.stat().st_size
    unit_cap = INLINE_CAST_MAX_BYTES if entry.get("type") == "asciinema" else INLINE_MAX_BYTES
    if size > unit_cap or size > remaining:
        entry["inline_skipped"] = "taille" if size > unit_cap else "budget"
        entry["truncated"] = True
        if not entry.get("excerpt"):
            entry["excerpt"] = _artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"))
        return remaining
    entry["inline_content"] = path.read_text(encoding="utf-8", errors="replace")
    entry["truncated"] = False
    return remaining - size


def inline_catalog_casts(catalog: list[dict], *, budget: int = INLINE_CAST_BUDGET) -> list[dict]:
    """Inline les .cast du catalogue: le player du cockpit exige ``inline_content``.

    Sans cet inline, un cast produit hors scénario pytest ne serait qu'un lien
    de tableau — injouable sous la CSP du rapport (aucun fetch autorisé).
    """

    remaining = budget
    for entry in catalog:
        if entry.get("type") == "asciinema" and entry.get("path"):
            remaining = _inline_artifact(entry, remaining)
    return catalog


def inline_scenario_artifacts(
    scenario_evidence: dict, *, budget: int = INLINE_TOTAL_BUDGET
) -> dict:
    """Inline le contenu des artefacts textuels dans le payload du cockpit.

    Au-delà du cap unitaire ou du budget global, l'artefact est représenté
    par un extrait tête+queue et marqué truncated: le rendu reste honnête.
    """

    remaining = budget
    suites: dict[str, list[dict]] = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        rebuilt = []
        for scenario in scenarios:
            artifacts = []
            for artifact in scenario.get("artifacts", []):
                entry = dict(artifact)
                remaining = _inline_artifact(entry, remaining)
                artifacts.append(entry)
            rebuilt.append({**scenario, "artifacts": artifacts})
        suites[suite] = rebuilt
    return {**scenario_evidence, "suites": suites}


def _strip_inline_content(scenario_evidence: dict) -> dict:
    """Retire les contenus inlinés avant réécriture disque (déjà présents en fichiers)."""

    suites: dict[str, list[dict]] = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        rebuilt = []
        for scenario in scenarios:
            artifacts = [
                {key: value for key, value in artifact.items() if key != "inline_content"}
                for artifact in scenario.get("artifacts", [])
            ]
            rebuilt.append({**scenario, "artifacts": artifacts})
        suites[suite] = rebuilt
    return {**scenario_evidence, "suites": suites}


def write_scenario_evidence(
    root: Path,
    scenario_evidence: dict,
    *,
    redaction_context: RedactionContext | None = None,
) -> None:
    context = redaction_context or redaction_context_from_environment()
    _secure_dir(root)
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        if not scenarios:
            continue
        path = root / f"{suite}-scenarios.json"
        payload = {
            "schema": SCENARIOS_SCHEMA,
            "suite": suite,
            "generated_at": _now(),
            "count": len(scenarios),
            "scenarios": sorted(scenarios, key=lambda item: item["nodeid"]),
        }
        cleaned = redact_tree(payload, context=context, path=f"$.scenarios.{suite}")
        _write_private_text(path, json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")


def build_project_risks_and_unknowns() -> dict:
    risks = [
        {
            "risk": "Pré-requis Chrome/Chromium obligatoire.",
            "mitigation": (
                "Chrome/Chromium est obligatoire: `make proof` échoue si le binaire est absent."
            ),
            "rollback": "Installer Chrome/Chromium puis relancer `make test-e2e` ou `make proof`.",
        },
        {
            "risk": "Docker/Compose est un prérequis du portail qualité complet.",
            "mitigation": (
                "`make check`, `make proof` et `make release` échouent si Docker ou la preuve "
                "Symfony est indisponible; `make check-local` reste un diagnostic partiel."
            ),
            "rollback": "Installer Docker puis relancer `make proof` ou `make docker-symfony-e2e`.",
        },
    ]
    unknowns = [
        {
            "item": "Dépendances réseau externes",
            "why": "`make proof` cible les fixtures locales et Chrome local.",
            "how_to_verify": "Vérifier les logs réseau et les fixtures sous `tests/fixtures/`.",
        },
        {
            "item": "Portée des captures visuelles",
            "why": (
                "Les captures E2E sont conservées dans l'arbre privé `.proof/evidence/` "
                "et exclues du staging partageable; elles ne constituent pas un diff "
                "visuel exhaustif."
            ),
            "how_to_verify": (
                "Inspecter le catalogue privé et ajouter une assertion ou une baseline dédiée "
                "pour toute régression visuelle à contractualiser."
            ),
        },
        {
            "item": "Cast du run complet",
            "why": (
                "Le portail enregistre nativement les commandes de démonstration; "
                "le run `make proof` entier n'est pas auto-enregistré (durée et poids)."
            ),
            "how_to_verify": (
                "Les casts de démonstration sont générés et jugés à chaque `make proof`; "
                "pour un enregistrement du run complet, lancer `make proof` dans un "
                "enregistreur de terminal externe."
            ),
        },
    ]
    return {"risks": risks, "unknowns": unknowns}


def _int_attr(node: ET.Element, name: str) -> int:
    try:
        return int(node.attrib.get(name, "0"))
    except ValueError:
        return 0


def _float_attr(node: ET.Element, name: str) -> float:
    try:
        return float(node.attrib.get(name, "0"))
    except ValueError:
        return 0.0


def parse_junit(path: Path) -> dict:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "time_s": 0.0,
            "cases": [],
            "parse_error": None,
        }

    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (ET.ParseError, OSError) as exc:
        return {
            "path": str(path),
            "exists": True,
            "tests": 0,
            "passed": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "time_s": 0.0,
            "cases": [],
            "parse_error": str(exc),
        }
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(_int_attr(suite, "tests") for suite in suites)
    failures = sum(_int_attr(suite, "failures") for suite in suites)
    errors = sum(_int_attr(suite, "errors") for suite in suites)
    skipped = sum(_int_attr(suite, "skipped") for suite in suites)
    time_s = sum(_float_attr(suite, "time") for suite in suites)
    cases = []
    for case in root.iter("testcase"):
        status = "passed"
        message = ""
        for child_name, child_status in (
            ("failure", "failed"),
            ("error", "error"),
            ("skipped", "skipped"),
        ):
            child = case.find(child_name)
            if child is not None:
                status = child_status
                message = child.attrib.get("message", "")
                if not message:
                    text_lines = (child.text or "").strip().splitlines()
                    message = text_lines[0] if text_lines else ""
                break
        cases.append(
            {
                "classname": case.attrib.get("classname", ""),
                "name": case.attrib.get("name", ""),
                "time_s": round(_float_attr(case, "time"), 3),
                "status": status,
                "message": message,
            }
        )
    passed = max(tests - failures - errors - skipped, 0)
    return {
        "path": str(path),
        "exists": True,
        "tests": tests,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "time_s": round(time_s, 3),
        "cases": cases,
        "parse_error": None,
    }


def parse_help_commands(help_text: str) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    in_commands = False
    for line in help_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and "}" in stripped:
            in_commands = True
            continue
        if in_commands and stripped.startswith("options:"):
            break
        if not in_commands:
            continue
        match = re.match(r"^\s{4}([a-z][a-z0-9-]*)\s{2,}(.+)$", line)
        if match:
            commands.append({"name": match.group(1), "help": match.group(2).strip()})
    return commands


def _tail(path: Path, lines: int = 24) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(data[-lines:])


def _case_focus(cases: list[dict]) -> list[dict]:
    non_passed = [case for case in cases if case["status"] != "passed"]
    if non_passed:
        return non_passed[:20]
    return sorted(cases, key=lambda case: case["time_s"], reverse=True)[:20]


def _suite_for_summary(suite: dict) -> dict:
    cases = suite.get("cases", [])
    return {
        "path": suite.get("path", ""),
        "exists": suite.get("exists", True),
        "tests": suite.get("tests", 0),
        "passed": suite.get("passed", 0),
        "failures": suite.get("failures", 0),
        "errors": suite.get("errors", 0),
        "skipped": suite.get("skipped", 0),
        "time_s": suite.get("time_s", 0.0),
        "parse_error": suite.get("parse_error"),
        # cases + focus embarqués: la vue Run du rapport montre chaque test et
        # les échecs/plus lents sans rouvrir les XML JUnit.
        "cases": cases,
        "focus": _case_focus(cases),
    }


def _empty_suite(path: Path) -> dict:
    return {
        "path": str(path),
        "exists": False,
        "tests": 0,
        "passed": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_s": 0.0,
        "cases": [],
        "parse_error": None,
    }


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


def cast_failures_from_entries(cast_entries: list[dict] | None) -> list[str]:
    """Portail cast: chaque commande de démonstration doit avoir son .cast généré."""

    by_id = {str(entry.get("id", "")): entry for entry in (cast_entries or [])}
    failures = []
    for cast_id, _argv in CAST_COMMANDS:
        entry = by_id.get(cast_id)
        if entry is None:
            failures.append(f"cast missing: {cast_id}")
        elif entry.get("status") != "generated":
            failures.append(f"cast {entry.get('status', 'unknown')}: {cast_id}")
    return failures


def build_summary(
    commands: list[CommandEvidence],
    unit: dict,
    e2e: dict,
    symfony: dict | None = None,
    *,
    git_context: dict | None = None,
    help_commands: list[dict[str, str]] | None = None,
    scenario_evidence: dict | None = None,
    cast_entries: list[dict] | None = None,
    proof_dir: Path | None = None,
) -> dict:
    symfony = symfony or _empty_suite(SYMFONY_JUNIT)
    git_context = git_context or {
        "branch": "unknown",
        "sha": "unknown",
        "changed_files": [],
        "generated_files": [],
        "changed_count": 0,
        "generated_count": 0,
        "status_path": str(GIT_STATUS),
        "diff_stat_path": str(GIT_DIFF_STAT),
    }
    help_commands = help_commands or []
    project = collect_project_inventory(help_commands)
    validation_matrix = parse_validation_matrix()
    coverage_groups = group_cases_by_module(unit, e2e, symfony)
    scenario_evidence = scenario_evidence or load_scenario_evidence(
        EVIDENCE_DIR if proof_dir is None else proof_dir / EVIDENCE_DIR.name
    )
    feature_inventory = build_feature_inventory(help_commands, scenario_evidence, git_context)
    documentation = build_documentation_catalog()
    scenario_evidence = enrich_scenario_evidence(scenario_evidence, feature_inventory)
    scenario_evidence = inline_scenario_artifacts(scenario_evidence)
    scenario_failures = proof_failures_from_scenarios(scenario_evidence)
    feature_inventory_failures = feature_failures(feature_inventory)
    documentation_catalog_failures = documentation_failures(documentation)
    risk_packet = build_project_risks_and_unknowns()
    failed_tests = (
        unit["failures"]
        + unit["errors"]
        + e2e["failures"]
        + e2e["errors"]
        + symfony["failures"]
        + symfony["errors"]
    )
    command_failures = [
        f"command failed: {command.label} ({command.log})"
        for command in commands
        if command.exit_code != 0
    ]
    suite_by_command = {"unit": unit, "e2e": e2e, "symfony-e2e": symfony}
    command_ids = {command.id for command in commands}
    suite_failures = []
    for command_id, suite in suite_by_command.items():
        if command_id not in command_ids:
            continue
        if not suite.get("exists", True):
            suite_failures.append(f"required JUnit missing: {suite.get('path', command_id)}")
        elif suite.get("parse_error"):
            suite_failures.append(
                f"required JUnit unreadable: {suite.get('path', command_id)} "
                f"({suite['parse_error']})"
            )
        elif suite.get("tests", 0) == 0:
            suite_failures.append(f"required JUnit empty: {suite.get('path', command_id)}")
        if command_id in {"e2e", "symfony-e2e"} and suite.get("skipped", 0):
            suite_failures.append(f"{command_id} tests skipped ({suite['skipped']})")
    if "cli-help" in command_ids and project["cli_command_count"] != 31:
        suite_failures.append(
            f"CLI contract expected 31 commands, found {project['cli_command_count']}"
        )
    unavailable = sum(
        1
        for suite in scenario_evidence.get("suites", {}).values()
        for scenario in suite
        if scenario.get("status") == "unavailable"
    )
    symfony_failures = []
    if unavailable:
        symfony_failures.append(f"symfony evidence unavailable ({unavailable} scenarios)")
    if symfony["skipped"]:
        symfony_failures.append(f"symfony tests skipped ({symfony['skipped']})")
    cast_gate_failures = cast_failures_from_entries(cast_entries)
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
        and not feature_inventory_failures
        and not documentation_catalog_failures
        and not symfony_failures
        and not suite_failures
        and not cast_gate_failures
    )
    summary = {
        "ok": ok,
        "generated_at": _now(),
        "artifact_dir": str(PROOF_DIR),
        "report_html": str(REPORT_HTML),
        "unit_log": str(UNIT_LOG),
        "e2e_log": str(E2E_LOG),
        "symfony_log": str(SYMFONY_LOG),
        "cli_help": str(CLI_HELP),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "chrome_or_chromium": bool(
                shutil.which("chromium")
                or shutil.which("chromium-browser")
                or shutil.which("google-chrome")
                or shutil.which("chrome")
            ),
        },
        "commands": [
            {**asdict(command), "log_tail": _tail(Path(command.log))} for command in commands
        ],
        "junit": {
            "unit": _suite_for_summary(unit),
            "e2e": _suite_for_summary(e2e),
            "symfony": _suite_for_summary(symfony),
        },
        "totals": {
            "tests": unit["tests"] + e2e["tests"] + symfony["tests"],
            "passed": unit["passed"] + e2e["passed"] + symfony["passed"],
            "skipped": unit["skipped"] + e2e["skipped"] + symfony["skipped"],
            "failed": failed_tests,
            "unavailable": unavailable,
        },
        "git": git_context,
        "project": project,
        "validation_matrix": validation_matrix,
        "coverage_groups": coverage_groups,
        "scenario_evidence": scenario_evidence,
        "scenario_totals": scenario_evidence["totals"],
        "feature_inventory": feature_inventory,
        "documentation": documentation,
        "casts": list(cast_entries or []),
        "proof_failures": scenario_failures
        + feature_inventory_failures
        + documentation_catalog_failures
        + command_failures
        + symfony_failures
        + suite_failures
        + cast_gate_failures,
        "risks": risk_packet["risks"],
        "unknowns": risk_packet["unknowns"],
    }
    summary["evidence_catalog"] = build_evidence_catalog(
        summary, unit, e2e, symfony, proof_dir=proof_dir
    )
    return summary


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


# Allowlist explicite et bornée des fichiers produits par le pipeline de
# preuve lui-même (hors sessions pytest): eux seuls peuvent être classés par
# la politique MIME. Tout autre fichier doit être couvert par un manifeste
# d'évidence, sinon le staging échoue fermé.
_PIPELINE_TOP_LEVEL_FILES = frozenset(
    {
        REPORT_HTML.name,
        SUMMARY_JSON.name,
        UNIT_LOG.name,
        E2E_LOG.name,
        SYMFONY_LOG.name,
        CLI_HELP.name,
        GIT_STATUS.name,
        GIT_DIFF_STAT.name,
        SYMFONY_JUNIT.name,
        "unit-junit.xml",
        "e2e-junit.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "artifact-manifest.json",
    }
)
# Ordre de restriction croissant pour la fusion multi-manifestes.
_CLASSIFICATION_SEVERITY: dict[ArtifactClassification, int] = {
    ArtifactClassification.PUBLIC: 0,
    ArtifactClassification.INTERNAL: 1,
    ArtifactClassification.OPAQUE_RESTRICTED: 2,
    ArtifactClassification.SECRET: 3,
}


def _is_pipeline_proof_artifact(relative: str) -> bool:
    parts = Path(relative).parts
    if len(parts) == 1:
        return parts[0] in _PIPELINE_TOP_LEVEL_FILES or parts[0].endswith(".cast")
    if len(parts) == 2 and parts[0] == "evidence":
        # Les *-scenarios.json sont réécrits par _generate() après les runs
        # (symfony-scenarios.json peut même exister sans manifeste); les
        # manifestes eux-mêmes sont des métadonnées produites par les sessions.
        name = parts[1]
        return name.endswith("-scenarios.json") or (
            name.startswith("evidence-manifest") and name.endswith(".json")
        )
    return False


def _load_evidence_policy(proof_dir: Path) -> dict[Path, tuple[ArtifactClassification, bool]]:
    """Agrège les manifestes d'évidence en une politique par chemin résolu.

    Les manifestes écrits par les sessions pytest sont la seule autorité de
    classification des artefacts d'évidence: en cas de doublon entre
    manifestes, la classification la plus restrictive gagne et l'upload n'est
    permis que si tous l'autorisent.
    """

    evidence_root = (proof_dir / "evidence").resolve()
    policy: dict[Path, tuple[ArtifactClassification, bool]] = {}
    redaction_policies: set[str] = set()
    for manifest_path in sorted((proof_dir / "evidence").glob("evidence-manifest*.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ArtifactError(f"manifeste d'évidence illisible: {manifest_path}") from e
        if not isinstance(payload, dict) or payload.get("schema") != EVIDENCE_SCHEMA:
            raise ArtifactError(f"schéma de manifeste d'évidence inattendu: {manifest_path}")
        redaction_policies.add(str(payload.get("redaction_policy")))
        for entry in payload.get("artifacts", []):
            try:
                resolved = (evidence_root / str(entry["path"])).resolve()
                classification = ArtifactClassification(str(entry["classification"]))
                upload_allowed = bool(entry["upload_allowed"])
            except (KeyError, TypeError, ValueError) as e:
                raise ArtifactError(
                    f"entrée de manifeste d'évidence invalide dans {manifest_path}: {e}"
                ) from e
            if resolved != evidence_root and evidence_root not in resolved.parents:
                raise ArtifactError(f"chemin manifesté hors de l'évidence: {entry['path']}")
            previous = policy.get(resolved)
            if previous is not None:
                if _CLASSIFICATION_SEVERITY[previous[0]] > _CLASSIFICATION_SEVERITY[classification]:
                    classification = previous[0]
                upload_allowed = upload_allowed and previous[1]
            policy[resolved] = (classification, upload_allowed)
    if len(redaction_policies) > 1:
        raise ArtifactError(
            "politiques de redaction hétérogènes entre manifestes d'évidence: "
            + ", ".join(sorted(redaction_policies))
        )
    return policy


def _purge_unmanifested_evidence(proof_dir: Path) -> list[str]:
    """Purge les artefacts d'évidence orphelins d'un pytest tué par deadline.

    Un pytest interrompu (exit 124) n'exécute pas ``pytest_sessionfinish``:
    ses artefacts attach_* déjà écrits n'ont aucun manifeste, et le staging
    partageable échouerait fermé avec un message trompeur. On retire ces
    orphelins de l'arbre — la suite tuée est déjà un échec de commande visible
    au verdict — plutôt que de masquer la cause réelle.
    """

    artifacts_root = proof_dir / "evidence" / "artifacts"
    if not artifacts_root.is_dir():
        return []
    policy = _load_evidence_policy(proof_dir)
    removed: list[str] = []
    for path in sorted(artifacts_root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ArtifactError(f"lien symbolique interdit dans les preuves: {path}")
        if path.is_file() and path.resolve() not in policy:
            path.unlink()
            removed.append(path.relative_to(proof_dir).as_posix())
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


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
    # Restes d'un run précédent interrompu: le staging est jetable par contrat.
    for leftover in (staging, previous):
        if leftover.exists():
            try:
                shutil.rmtree(leftover)
            except PermissionError as exc:
                # Un run Docker interrompu avant son chown final laisse des
                # fichiers root dans le staging: message actionnable plutôt
                # qu'une PermissionError brute au milieu de la génération.
                raise ArtifactError(
                    f"staging résiduel non purgeable: {leftover} (fichiers appartenant "
                    "probablement à root après un run Docker interrompu); réparer avec "
                    f'`docker run --rm -v "$PWD/{leftover.name}:/t" alpine '
                    'chown -R "$(id -u):$(id -g)" /t` puis relancer'
                ) from exc
    _secure_dir(staging)
    context = redaction_context_from_environment()
    env = _repo_env()

    # Séparation chemin physique (écrit dans le staging) / chemin logique
    # publié (.proof/...): tout ce qui entre au summary, au rapport HTML et
    # aux logs est réécrit du premier vers le second avant publication.
    publish_rewrites: tuple[tuple[str, str], ...] = (
        (str(staging.resolve()), str(PROOF_DIR.resolve())),
        (str(staging), str(PROOF_DIR)),
    )
    # Les preuves écrites DANS le conteneur Symfony parlent déjà en `.proof/…`
    # (montage /workspace/.proof): on les ramène au chemin physique du staging
    # pour pouvoir les lire pendant la génération, avant réécriture inverse.
    ingest_rewrites: tuple[tuple[str, str], ...] = ((f"{PROOF_DIR}/", f"{staging}/"),)

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

    # Un pytest tué par deadline n'a pas écrit ses manifestes d'évidence: ses
    # artefacts orphelins feraient échouer le staging fail-closed avec un
    # message trompeur, alors que la cause réelle (exit 124) est déjà rouge.
    if any(
        command.id in {"unit", "e2e", "symfony-e2e"} and command.exit_code == 124
        for command in commands
    ):
        _purge_unmanifested_evidence(staging)

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
    _harden_tree(staging)
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
    shutil.rmtree(previous, ignore_errors=True)
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
