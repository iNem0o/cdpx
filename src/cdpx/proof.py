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
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import cache, lru_cache
from importlib import resources
from pathlib import Path
from string import Template

from cdpx.artifacts import (
    ArtifactClassification,
    ArtifactError,
    SecureArtifactWriter,
    scan_canaries,
)
from cdpx.proofing.documentation import (
    build_documentation_catalog,
    documentation_failures,
)
from cdpx.proofing.features import build_feature_inventory, feature_failures
from cdpx.security.redaction import RedactionContext, redact_text, redact_tree
from cdpx.testing.evidence import (
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

GENERATED_PREFIXES = (".proof/", ".idea/")
PRIVATE_WORKTREE_PREFIXES = ("AGENTS.md", "article/", "presentation/")
VALIDATION_DOC = Path("docs/VALIDATION.md")
MERMAID_VERSION = "11.16.0"
MERMAID_RESOURCE = f"vendor/mermaid-{MERMAID_VERSION}.min.js"
MERMAID_SHA256 = "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"

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


def run_evidence(
    id: str,
    label: str,
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
    redaction_context: RedactionContext | None = None,
) -> CommandEvidence:
    context = redaction_context or redaction_context_from_environment()
    started = _now()
    start = time.monotonic()
    _secure_dir(log_path.parent)
    safe_argv = _sanitize_argv(argv, context)
    header = [
        f"$ {' '.join(safe_argv)}",
        f"started_at: {started}",
        "",
        "--- output ---",
    ]
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        exit_code = proc.returncode
        output = redact_text(proc.stdout, context=context, path=f"$.commands.{id}.stdout")
    except FileNotFoundError as exc:
        exit_code = 127
        output = redact_text(str(exc), context=context, path=f"$.commands.{id}.error") + "\n"
    duration = time.monotonic() - start
    footer = ["", "--- result ---", f"exit_code: {exit_code}", f"duration_s: {duration:.3f}", ""]
    _write_private_text(log_path, "\n".join(header) + "\n" + output + "\n".join(footer))
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
) -> None:
    context = redaction_context or redaction_context_from_environment()
    _secure_dir(EVIDENCE_DIR)
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
                        "path": str(SYMFONY_LOG),
                        "bytes": SYMFONY_LOG.stat().st_size if SYMFONY_LOG.exists() else 0,
                        "mime": "text/plain",
                        "created_at": _now(),
                    }
                ],
            }
        ],
    }
    _write_private_text(
        EVIDENCE_DIR / "symfony-scenarios.json",
        json.dumps(redact_tree(payload, context=context), ensure_ascii=False, indent=2) + "\n",
    )


def run_symfony_evidence(
    *,
    redaction_context: RedactionContext | None = None,
) -> CommandEvidence:
    context = redaction_context or redaction_context_from_environment()
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
    _secure_dir(EVIDENCE_DIR)
    compose_env = _repo_env()
    compose_env["CDPX_E2E_UID"] = str(os.getuid())
    compose_env["CDPX_E2E_GID"] = str(os.getgid())

    checks: list[str] = []
    if shutil.which("docker") is None:
        reason = "Docker CLI not found; Symfony e2e is required for release proof."
        _write_command_log(
            SYMFONY_LOG,
            argv,
            started,
            reason,
            "status: unavailable\nexit_code: 1",
            redaction_context=context,
        )
        write_symfony_unavailable_evidence(reason, redaction_context=context)
        return CommandEvidence(
            id="symfony-e2e",
            label="Symfony E2E Docker",
            argv=argv,
            log=str(SYMFONY_LOG),
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
                SYMFONY_LOG,
                argv,
                started,
                body,
                "status: unavailable\nexit_code: 1",
                redaction_context=context,
            )
            write_symfony_unavailable_evidence(reason, redaction_context=context)
            return CommandEvidence(
                id="symfony-e2e",
                label="Symfony E2E Docker",
                argv=argv,
                log=str(SYMFONY_LOG),
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
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            env=compose_env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
    finally:
        # Même une interruption/exception pendant `up` doit rendre la main avec
        # les conteneurs et réseaux Compose supprimés.
        post_code, post_output = _run_text(down_argv, timeout=60, env=compose_env)
    duration = time.monotonic() - start
    body = "\n\n".join(
        checks
        + [
            f"$ {' '.join(down_argv)}\n{pre_output.rstrip()}\nexit_code: {pre_code}",
            f"$ {' '.join(argv)}\n{proc.stdout.rstrip()}\nexit_code: {proc.returncode}",
            f"$ {' '.join(down_argv)}\n{post_output.rstrip()}\nexit_code: {post_code}",
        ]
    )
    result_code = proc.returncode if proc.returncode != 0 else post_code
    _write_command_log(
        SYMFONY_LOG,
        argv,
        started,
        body,
        f"exit_code: {result_code}\nduration_s: {duration:.3f}",
        redaction_context=context,
    )
    return CommandEvidence(
        id="symfony-e2e",
        label="Symfony E2E Docker",
        argv=argv,
        log=str(SYMFONY_LOG),
        exit_code=result_code,
        duration_s=round(duration, 3),
        status="ok" if result_code == 0 else "failed",
    )


def collect_git_context(*, redaction_context: RedactionContext | None = None) -> dict:
    context = redaction_context or redaction_context_from_environment()
    branch_code, branch = _run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    sha_code, sha = _run_text(["git", "rev-parse", "--short", "HEAD"])
    status_code, status = _run_text(["git", "status", "--short"])
    stat_code, stat = _run_text(
        ["git", "diff", "--stat", "--", ".", ":(exclude).proof/*", ":(exclude).idea/*"]
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
    _write_private_text(GIT_STATUS, status)
    _write_private_text(GIT_DIFF_STAT, stat)

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
        "status_path": str(GIT_STATUS),
        "diff_stat_path": str(GIT_DIFF_STAT),
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
            "item": "Asciinema terminal record",
            "why": "`asciinema` est optionnel et ne doit pas bloquer `make proof`.",
            "how_to_verify": "Option: `asciinema rec .proof/make-proof.cast -c 'make proof'`.",
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


def build_evidence_catalog(summary: dict, unit: dict, e2e: dict, symfony: dict) -> list[dict]:
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
        for path in sorted(PROOF_DIR.rglob(pattern)):
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
    if not any(item["type"] == "asciinema" for item in catalog):
        catalog.append(
            {
                "type": "asciinema",
                "name": "Terminal record",
                "path": "",
                "status": "optional",
                "roi": "Optionnel; les logs texte couvrent déjà la reproduction du run.",
            }
        )
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
INLINE_TOTAL_BUDGET = 2 * 1024 * 1024
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
    if size > INLINE_MAX_BYTES or size > remaining:
        entry["inline_skipped"] = "taille" if size > INLINE_MAX_BYTES else "budget"
        entry["truncated"] = True
        if not entry.get("excerpt"):
            entry["excerpt"] = _artifact_excerpt(path.read_text(encoding="utf-8", errors="replace"))
        return remaining
    entry["inline_content"] = path.read_text(encoding="utf-8", errors="replace")
    entry["truncated"] = False
    return remaining - size


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
            "item": "Asciinema du run complet",
            "why": "`asciinema` est optionnel et ne doit pas bloquer le portail.",
            "how_to_verify": "Option: `asciinema rec .proof/make-proof.cast -c 'make proof'`.",
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


@lru_cache(maxsize=1)
def _mermaid_bundle() -> str:
    bundle = resources.files("cdpx.proofing").joinpath(MERMAID_RESOURCE).read_bytes()
    digest = hashlib.sha256(bundle).hexdigest()
    if digest != MERMAID_SHA256:
        raise ValueError(
            f"bundle Mermaid {MERMAID_VERSION} invalide: attendu={MERMAID_SHA256}, reçu={digest}"
        )
    source = bundle.decode("utf-8")
    if "</script" in source.lower():
        raise ValueError(f"bundle Mermaid {MERMAID_VERSION} impropre à une inclusion inline")
    return source


def render_html(summary: dict) -> str:
    verdict = "OK" if summary["ok"] else "ECHEC"
    generated = html.escape(summary["generated_at"])
    payload = _json_for_html_script(summary)
    mermaid_bundle = _mermaid_bundle()
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
        payload=payload,
        mermaid_bundle=mermaid_bundle,
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
    scenario_evidence: dict | None = None,
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
    scenario_evidence = scenario_evidence or load_scenario_evidence()
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
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
        and not feature_inventory_failures
        and not documentation_catalog_failures
        and not symfony_failures
        and not suite_failures
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
        "proof_failures": scenario_failures
        + feature_inventory_failures
        + documentation_catalog_failures
        + command_failures
        + symfony_failures
        + suite_failures,
        "risks": risk_packet["risks"],
        "unknowns": risk_packet["unknowns"],
    }
    summary["evidence_catalog"] = build_evidence_catalog(summary, unit, e2e, symfony)
    return summary


def _sanitize_text_file(path: Path, context: RedactionContext) -> None:
    if not path.exists() or path.is_symlink():
        return
    value = path.read_text(encoding="utf-8", errors="replace")
    cleaned = redact_text(value, context=context, path=f"$.files.{path.name}")
    _write_private_text(path, cleaned)


def _proof_artifact_policy(path: Path) -> tuple[ArtifactClassification, bool]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime.startswith("text/") or path.suffix.lower() in _TEXTUAL_PROOF_SUFFIXES:
        return ArtifactClassification.INTERNAL, True
    return ArtifactClassification.OPAQUE_RESTRICTED, False


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
    copied to staging. ``pre_redacted_paths`` is reserved for text assembled
    exclusively from redacted structures plus trusted static code. A final
    exact-value canary scan fails closed, including for these preserved files.
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

    if store_root.exists():
        shutil.rmtree(store_root)
    writer = SecureArtifactWriter(store_root, "proof", ttl=selected_ttl)
    for source in source_paths:
        relative = source.relative_to(proof_dir).as_posix()
        classification, upload_allowed = _proof_artifact_policy(source)
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
    retention_seconds = proof_retention_seconds()
    if PROOF_DIR.exists():
        shutil.rmtree(PROOF_DIR)
    _secure_dir(PROOF_DIR)
    context = redaction_context_from_environment()
    env = _repo_env()
    unit_xml = PROOF_DIR / "unit-junit.xml"
    e2e_xml = PROOF_DIR / "e2e-junit.xml"
    symfony_xml = SYMFONY_JUNIT

    commands = [
        run_evidence(
            "ruff-check",
            "Ruff lint",
            [sys.executable, "-m", "ruff", "check", "src", "tests"],
            PROOF_DIR / "ruff-check.log",
            env=env,
            redaction_context=context,
        ),
        run_evidence(
            "ruff-format",
            "Ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "src", "tests"],
            PROOF_DIR / "ruff-format.log",
            env=env,
            redaction_context=context,
        ),
        run_evidence(
            "mypy",
            "Mypy typage",
            [sys.executable, "-m", "mypy", "src/cdpx"],
            PROOF_DIR / "mypy.log",
            env=env,
            redaction_context=context,
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
                f"--cdpx-evidence-dir={EVIDENCE_DIR}",
                f"--junitxml={unit_xml}",
            ],
            UNIT_LOG,
            env=env,
            redaction_context=context,
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
                f"--cdpx-evidence-dir={EVIDENCE_DIR}",
                f"--junitxml={e2e_xml}",
            ],
            E2E_LOG,
            env=env,
            redaction_context=context,
        ),
        run_symfony_evidence(redaction_context=context),
        run_evidence(
            "cli-help",
            "Aide CLI",
            [sys.executable, "-m", "cdpx.cli", "--help"],
            CLI_HELP,
            env=env,
            redaction_context=context,
        ),
    ]

    for path in (unit_xml, e2e_xml, symfony_xml):
        _sanitize_text_file(path, context)
    unit = parse_junit(unit_xml)
    e2e = parse_junit(e2e_xml)
    symfony = parse_junit(symfony_xml)
    help_commands = parse_help_commands(CLI_HELP.read_text(encoding="utf-8", errors="replace"))
    git_context = collect_git_context(redaction_context=context)
    summary = build_summary(
        commands,
        unit,
        e2e,
        symfony,
        git_context=git_context,
        help_commands=help_commands,
    )
    summary["cli_commands"] = [command["name"] for command in help_commands]
    summary["cli_command_count"] = len(help_commands)
    summary = redact_tree(summary, context=context, path="$.summary")
    # Les contenus inlinés n'existent que dans le payload HTML: les JSON disque
    # pointent vers les fichiers d'artefacts, sans duplication.
    lean_evidence = _strip_inline_content(summary["scenario_evidence"])
    write_scenario_evidence(
        EVIDENCE_DIR,
        lean_evidence,
        redaction_context=context,
    )
    _write_private_text(
        SUMMARY_JSON,
        json.dumps({**summary, "scenario_evidence": lean_evidence}, ensure_ascii=False, indent=2)
        + "\n",
    )
    _write_private_text(
        REPORT_HTML,
        render_html(summary),
    )
    _harden_tree(PROOF_DIR)
    build_shareable_proof(
        PROOF_DIR,
        canaries=environment_secret_values(),
        ttl=retention_seconds,
        pre_redacted_paths={REPORT_HTML.name},
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
