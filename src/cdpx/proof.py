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
from functools import lru_cache
from importlib import resources
from pathlib import Path

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


SPA_CSS = """\
:root {
  color-scheme: light;
  --bg: #f4f6f8;
  --panel: #ffffff;
  --ink: #17202a;
  --muted: #5e6875;
  --line: #d9dee7;
  --soft: #eef2f6;
  --ok: #167044;
  --bad: #b42318;
  --warn: #9a6700;
  --info: #2457a7;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: var(--info); text-decoration: none; }
a:hover { text-decoration: underline; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 18px;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}
.brand { font-weight: 800; white-space: nowrap; }
.topbar nav { display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }
.topbar nav a, .side a, .button {
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 6px 9px;
  color: var(--muted);
  font-weight: 650;
}
.topbar nav a.active, .side a.active, .button:hover {
  color: var(--ink);
  background: var(--soft);
  text-decoration: none;
}
.shell {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  min-height: calc(100vh - 48px);
}
.side {
  position: sticky;
  top: 49px;
  height: calc(100vh - 49px);
  overflow: auto;
  padding: 16px;
  background: #fbfcfe;
  border-right: 1px solid var(--line);
}
.side h2 { margin: 0 0 10px; font-size: 13px; text-transform: uppercase; color: var(--muted); }
.side input {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 9px;
  margin-bottom: 12px;
  font: inherit;
}
.side a { display: block; margin-bottom: 5px; overflow-wrap: anywhere; }
.side small { display: block; color: var(--muted); font-weight: 500; }
.side [hidden] { display: none; }
.doc-tree { margin: 0 0 10px; }
.doc-tree summary { color: var(--muted); font-weight: 750; padding: 5px 0; }
.doc-tree .doc-tree { margin-left: 10px; }
main { padding: 22px 28px 40px; max-width: 1180px; width: 100%; }
.crumbs { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 14px; color: var(--muted); }
h1 { margin: 0 0 8px; font-size: 27px; line-height: 1.15; }
h2 { margin: 24px 0 10px; font-size: 18px; }
h3 { margin: 16px 0 8px; font-size: 15px; }
p { margin: 0 0 12px; color: var(--muted); }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { font-size: 12px; overflow-wrap: anywhere; }
pre {
  overflow: auto;
  white-space: pre-wrap;
  background: #10151f;
  color: #edf2f7;
  padding: 12px;
  border-radius: 6px;
}
.grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.two { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.card, .panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
}
.card { display: flex; flex-direction: column; min-height: 150px; }
.card h2, .card h3 { margin-top: 0; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 14px 0 20px; }
.metric { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.metric strong { display: block; font-size: 24px; line-height: 1.1; }
.metric.warning { border-color: var(--warn, #b45309); }
.panel.doc h2 { margin-top: 18px; }
.panel.doc h3 { margin: 16px 0 6px; }
.panel.doc h4 { margin: 12px 0 4px; }
.panel.doc h1 { margin-top: 0; }
.panel.doc ol, .panel.doc ul { padding-left: 24px; }
.panel.doc blockquote {
  margin: 12px 0;
  padding: 8px 14px;
  border-left: 4px solid var(--info);
  background: var(--soft);
}
.panel.doc blockquote p { margin: 0; }
.panel.doc table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.panel.doc th, .panel.doc td { border: 1px solid var(--line); padding: 5px 8px; text-align: left; }
.panel.doc pre { overflow-x: auto; }
.panel.doc .mermaid {
  min-height: 80px;
  padding: 14px;
  color: var(--ink);
  background: #fff;
  border: 1px solid var(--line);
  text-align: center;
  white-space: pre;
}
.panel.doc .mermaid svg { display: block; width: 100%; max-width: 100%; height: auto; margin: auto; }
.mermaid-error { margin: 6px 0 16px; color: var(--bad); font-weight: 700; }
.doc-link-unavailable { color: var(--muted); text-decoration: line-through; cursor: not-allowed; }
details { margin: 6px 0; }
details summary { cursor: pointer; }
details pre { max-height: 320px; overflow: auto; }
.muted { color: var(--muted); }
.pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 58px;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 800;
}
.ok, .passed { color: #fff; background: var(--ok); }
.failed, .error { color: #fff; background: var(--bad); }
.warning, .skipped, .unavailable { color: #241800; background: #f6d365; }
.meta { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 8px 0 12px; }
.list { margin: 8px 0 0; padding-left: 18px; }
.list li { margin-bottom: 5px; }
.scenario-list { display: grid; gap: 10px; }
.scenario-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: start;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}
.bdd { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.bdd div { background: #fbfcfe; border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
.shot {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
  width: 178px;
  margin: 0 8px 10px 0;
  color: var(--ink);
}
.shot img {
  width: 178px;
  height: 100px;
  object-fit: cover;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
table { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }
th, td { padding: 9px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
.table-wrap { overflow-x: auto; }
.empty { padding: 24px; border: 1px dashed var(--line); border-radius: 8px; color: var(--muted); }
@media (max-width: 900px) {
  .shell { grid-template-columns: 1fr; }
  .side { position: static; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
  .grid, .two, .metrics, .bdd { grid-template-columns: 1fr; }
  main { padding: 18px; }
}
"""

SPA_JS = """\
(function () {
  const data = JSON.parse(document.getElementById('report-data').textContent);
  const app = document.getElementById('app');
  const featureNav = document.getElementById('featureNav');
  const featureSearch = document.getElementById('featureSearch');
  const featureSide = document.getElementById('featureSide');
  const docsNav = document.getElementById('docsNav');
  const docsSearch = document.getElementById('docsSearch');
  const docsSide = document.getElementById('docsSide');
  const topLinks = Array.from(document.querySelectorAll('[data-route]'));

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const routeInfo = () => {
    const raw = location.hash.slice(1) || '/features';
    const [path, query = ''] = raw.split('?', 2);
    return {path, params: new URLSearchParams(query)};
  };
  const route = () => routeInfo().path;
  const features = () => data.feature_inventory.features || [];
  const documents = () => data.documentation?.documents || [];
  const findDocument = (path) => documents().find((document) => document.path === path);
  const docHref = (path, section = '') => {
    const base = '#/docs/view/' + String(path).split('/').map(encodeURIComponent).join('/');
    return section ? base + '?section=' + encodeURIComponent(section) : base;
  };
  const featureStatus = (feature) => {
    if ((feature.matched_scenarios || []).some((s) => ['failed', 'error'].includes(s.status))) {
      return 'failed';
    }
    if ((feature.matched_scenarios || []).some((s) => s.status === 'unavailable')) {
      return 'warning';
    }
    return (feature.gaps || []).length ? 'warning' : 'ok';
  };
  const counts = (items) => {
    const out = {passed: 0, failed: 0, skipped: 0, unavailable: 0};
    for (const item of items || []) {
      if (['failed', 'error'].includes(item.status)) out.failed += 1;
      else if (item.status === 'unavailable') out.unavailable += 1;
      else if (item.status === 'skipped') out.skipped += 1;
      else out.passed += 1;
    }
    return out;
  };
  const hrefFor = (path) => {
    if (!path) return '';
    return String(path).startsWith('.proof/') ? String(path).slice(7) : String(path);
  };
  const findFeature = (id) => features().find((feature) => feature.id === id);
  const findJourney = (feature, id) => (feature?.journeys || []).find((journey) => journey.id === id);
  const findScenario = (feature, id) => {
    for (const journey of feature?.journeys || []) {
      const scenario = (journey.scenarios || []).find((item) => item.id === id);
      if (scenario) return {journey, scenario};
    }
    return {};
  };
  const list = (items, formatter) => {
    if (!items || !items.length) return '<div class="empty">Aucune donnée.</div>';
    return '<ul class="list">' + items.map(formatter).join('') + '</ul>';
  };
  const crumbs = (items) => '<div class="crumbs">' + items.map((item, index) => {
    const sep = index ? '<span>/</span>' : '';
    return sep + (item.href ? `<a href="${item.href}">${esc(item.label)}</a>` : `<span>${esc(item.label)}</span>`);
  }).join('') + '</div>';
  const statusPill = (status) => `<span class="pill ${esc(status)}">${esc(status)}</span>`;

  if (window.mermaid) {
    window.mermaid.parseError = () => {};
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      htmlLabels: false,
      flowchart: {htmlLabels: false},
      theme: 'default'
    });
  }

  async function renderMermaid() {
    if (!window.mermaid) return;
    const nodes = Array.from(app.querySelectorAll('pre.mermaid'));
    for (const node of nodes) {
      const source = node.textContent;
      await window.mermaid.run({nodes: [node], suppressErrors: true});
      if (!node.querySelector('svg')) {
        node.textContent = source;
        node.removeAttribute('data-processed');
        const error = document.createElement('p');
        error.className = 'mermaid-error';
        error.textContent = 'Diagramme Mermaid invalide — source conservée.';
        node.insertAdjacentElement('afterend', error);
      }
    }
  }

  function renderFeatureNav() {
    const q = (featureSearch.value || '').trim().toLowerCase();
    featureNav.innerHTML = features()
      .filter((feature) => !q || [feature.id, feature.title, feature.summary].join(' ').toLowerCase().includes(q))
      .map((feature) => {
        const status = featureStatus(feature);
        return `<a href="#/features/${esc(feature.id)}" data-feature-id="${esc(feature.id)}">
          ${esc(feature.title)}<small>${esc(status)} · ${(feature.journeys || []).length} journeys</small>
        </a>`;
      }).join('');
  }

  function renderDocTree(node, query, root = false) {
    const ownDocuments = (node.documents || []).map(findDocument).filter(Boolean)
      .filter((document) => !query || [document.title, document.path, document.summary].join(' ').toLowerCase().includes(query));
    const children = (node.children || []).map((child) => renderDocTree(child, query)).filter(Boolean);
    if (!ownDocuments.length && !children.length) return '';
    const links = ownDocuments.map((document) => `<a href="${docHref(document.path)}" data-doc-path="${esc(document.path)}">${esc(document.title)}<small>${esc(document.path)}</small></a>`).join('');
    const body = links + children.join('');
    if (root) return body;
    return `<details class="doc-tree" open><summary>${esc(node.label || node.name)}</summary>${body}</details>`;
  }

  function renderDocsNav() {
    const q = (docsSearch.value || '').trim().toLowerCase();
    docsNav.innerHTML = renderDocTree(data.documentation?.tree || {}, q, true);
  }

  function setActiveNav() {
    const current = route();
    topLinks.forEach((link) => {
      link.classList.toggle('active', current === link.dataset.route || current.startsWith(link.dataset.route + '/'));
    });
    Array.from(featureNav.querySelectorAll('a')).forEach((link) => {
      link.classList.toggle('active', current.includes('/features/' + link.dataset.featureId));
    });
    Array.from(docsNav.querySelectorAll('a')).forEach((link) => {
      link.classList.toggle('active', current.startsWith('/docs/view/') && decodeURIComponent(current.slice(11)) === link.dataset.docPath);
    });
    const inDocs = current === '/docs' || current.startsWith('/docs/');
    featureSide.hidden = inDocs;
    docsSide.hidden = !inDocs;
  }

  function renderMetrics() {
    const totals = data.totals || {};
    const featureTotals = data.feature_inventory.totals || {};
    const scenarioTotals = data.scenario_totals || {};
    const okFeatures = features().filter((feature) => featureStatus(feature) === 'ok').length;
    return `<div class="metrics">
      <div class="metric"><strong>${data.ok ? 'OK' : 'ECHEC'}</strong><span>Verdict global</span></div>
      <div class="metric"><strong>${esc(totals.passed || 0)}/${esc(totals.tests || 0)}</strong><span>Tests passés</span></div>
      <div class="metric"><strong>${okFeatures}/${features().length}</strong><span>Features sans gap</span></div>
      <div class="metric"><strong>${esc(featureTotals.documented_scenarios || 0)}</strong><span>Scénarios documentés</span></div>
      <div class="metric"><strong>${esc(featureTotals.scenarios || 0)}</strong><span>Tests scénarisés</span></div>
      <div class="metric"><strong>${esc(scenarioTotals.screenshots || 0)}</strong><span>Screenshots</span></div>
      <div class="metric"><strong>${esc((featureTotals.violations || 0) + (featureTotals.warnings || 0))}</strong><span>Gaps catalogue</span></div>
      <div class="metric"><strong>${esc(featureTotals.mapped_entrypoints || 0)}/${esc(featureTotals.entrypoints || 0)}</strong><span>Entrypoints rattachés</span></div>
      <div class="metric${(totals.unavailable || 0) ? ' warning' : ''}"><strong>${esc(totals.unavailable || 0)}</strong><span>Preuves indisponibles</span></div>
    </div>`;
  }

  function renderFeatures() {
    const cards = features().map((feature) => {
      const status = featureStatus(feature);
      return `<article class="card">
        <div class="meta">${statusPill(status)} <code>${esc(feature.id)}</code></div>
        <h2><a href="#/features/${esc(feature.id)}">${esc(feature.title)}</a></h2>
        <p>${esc(feature.summary)}</p>
        <div class="meta">
          <span>${(feature.journeys || []).length} journeys</span>
          <span>${(feature.scenarios || []).length} scénarios docs</span>
          <span>${(feature.matched_tests || []).length} tests</span>
          <span>${(feature.proofs || []).length} preuves</span>
        </div>
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features'}])}
      <h1>Features</h1>
      <p>Navigation produit par feature, journey et scénario. Les textes affichés viennent des docs feature.</p>
      ${renderMetrics()}<div class="grid">${cards}</div>`;
  }

  function renderDocs() {
    const cards = documents().map((document) => `<article class="card">
      <div class="meta"><span class="pill ${document.kind === 'feature' ? 'ok' : 'warning'}">${esc(document.kind)}</span><code>${esc(document.path)}</code></div>
      <h2><a href="${docHref(document.path)}">${esc(document.title)}</a></h2>
      <p>${esc(document.summary || (document.kind === 'feature' ? 'Spécification fonctionnelle liée au harness.' : 'Référence produit rendue depuis le dépôt.'))}</p>
    </article>`).join('');
    app.innerHTML = `${crumbs([{label: 'Documentation'}])}
      <h1>Documentation produit</h1>
      <p>Guides, références et spécifications fonctionnelles rendus depuis les sources Markdown du dépôt. Les fiches features restent également reliées aux tests et preuves.</p>
      <div class="grid">${cards || '<div class="empty">Aucun document publié.</div>'}</div>`;
  }

  function renderDocument(document) {
    const featureLink = document.feature_id
      ? `<a class="button" href="#/features/${esc(document.feature_id)}">Voir le harness et les preuves</a>`
      : '';
    app.innerHTML = `${crumbs([{label: 'Documentation', href: '#/docs'}, {label: document.title}])}
      <div class="meta"><code>${esc(document.path)}</code>${featureLink}</div>
      <section class="panel doc">${document.html || '<div class="empty">Document vide.</div>'}</section>`;
  }

  function renderFeature(feature) {
    const journeys = (feature.journeys || []).map((journey) => {
      const c = counts(journey.matched_scenarios || []);
      return `<article class="card">
        <h3><a href="#/features/${esc(feature.id)}/journeys/${esc(journey.id)}">${esc(journey.title || journey.id)}</a></h3>
        <p><code>${esc(journey.entrypoint || '')}</code></p>
        <div class="meta"><span>${(journey.scenarios || []).length} scénarios</span><span>${c.passed} passed</span><span>${c.failed} failed</span></div>
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features', href: '#/features'}, {label: feature.title}])}
      <h1>${esc(feature.title)}</h1>
      <p>${esc(feature.summary)}</p>
      <div class="meta">${statusPill(featureStatus(feature))}<code>${esc(feature.source)}</code></div>
      <div class="two">
        <section class="panel"><h2>Documentation</h2>${list(feature.docs || [], (doc) => {
          const published = findDocument(doc);
          return published ? `<li><a href="${docHref(doc)}"><code>${esc(doc)}</code></a></li>` : `<li><code>${esc(doc)}</code></li>`;
        })}</section>
        <section class="panel"><h2>Gaps</h2>${list(feature.gaps || [], (gap) => `<li>${esc(gap)}</li>`)}</section>
      </div>
      <section class="panel doc"><h2>Documentation utilisateur</h2>${feature.doc_html || '<div class="empty">Aucune documentation.</div>'}</section>
      <h2>User journeys</h2><div class="grid">${journeys}</div>
      <h2>Tests et preuves</h2>
      <div class="two">
        <section class="panel"><h3>Tests</h3>${list(feature.matched_tests || [], (test) => `<li><code>${esc(test)}</code></li>`)}</section>
        <section class="panel"><h3>Preuves</h3>${renderProofLinks(feature.proofs || [])}</section>
      </div>`;
  }

  function renderJourney(feature, journey) {
    const scenarios = (journey.scenarios || []).map((scenario) => renderScenarioRow(feature, journey, scenario)).join('');
    app.innerHTML = `${crumbs([
      {label: 'Features', href: '#/features'},
      {label: feature.title, href: '#/features/' + feature.id},
      {label: journey.title || journey.id}
    ])}
      <h1>${esc(journey.title || journey.id)}</h1>
      <p><code>${esc(journey.entrypoint || '')}</code></p>
      <div class="meta"><span>${(journey.matched_tests || []).length} tests</span><span>${(journey.proofs || []).length} preuves</span></div>
      <h2>Scénarios</h2><div class="scenario-list">${scenarios || '<div class="empty">Aucun scénario documenté.</div>'}</div>
      <h2>Preuves du journey</h2>${renderProofLinks(journey.proofs || [])}`;
  }

  function renderScenarioRow(feature, journey, scenario) {
    const c = counts(scenario.matched_scenarios || []);
    const status = c.failed ? 'failed' : ((scenario.gaps || []).length ? 'warning' : 'ok');
    return `<article class="scenario-row">
      ${statusPill(status)}
      <div>
        <strong><a href="#/features/${esc(feature.id)}/scenarios/${esc(scenario.id)}">${esc(scenario.title)}</a></strong>
        <p>${esc(scenario.ui_text)}</p>
        <code>${esc(scenario.scenario_id)}</code>
      </div>
      <div class="muted">${(scenario.matched_tests || []).length} tests<br>${(scenario.proofs || []).length} preuves</div>
    </article>`;
  }

  function renderScenario(feature, journey, scenario) {
    app.innerHTML = `${crumbs([
      {label: 'Features', href: '#/features'},
      {label: feature.title, href: '#/features/' + feature.id},
      {label: journey.title || journey.id, href: '#/features/' + feature.id + '/journeys/' + journey.id},
      {label: scenario.title}
    ])}
      <h1>${esc(scenario.title)}</h1>
      <p>${esc(scenario.report_text || scenario.ui_text)}</p>
      <div class="meta"><code>${esc(scenario.scenario_id)}</code>${statusPill((scenario.gaps || []).length ? 'warning' : 'ok')}</div>
      <section class="bdd">
        <div><h3>Given</h3><p>${esc(scenario.given)}</p></div>
        <div><h3>When</h3><p>${esc(scenario.when)}</p></div>
        <div><h3>Then</h3><p>${esc(scenario.then)}</p></div>
      </section>
      <h2>Tests liés</h2>${renderScenarioRuns(scenario.matched_scenarios || [], scenario.tests || [])}
      <h2>Preuves</h2>${renderProofLinks(scenario.proofs || [])}`;
  }

  function renderScenarioRuns(runs, declaredTests) {
    const declared = list(declaredTests, (test) => `<li><code>${esc(test)}</code></li>`);
    if (!runs.length) return `<div class="two"><section class="panel"><h3>Déclarés</h3>${declared}</section><section class="panel"><h3>Exécutés</h3><div class="empty">Aucun test exécuté.</div></section></div>`;
    const rows = runs.map((run) => `<tr>
      <td>${statusPill(run.status || 'unknown')}</td>
      <td><code>${esc(run.nodeid)}</code><p>${esc(run.message || '')}</p></td>
      <td>${esc(run.duration_s || 0)}s</td>
      <td>${renderArtifacts(run.artifacts || [])}</td>
    </tr>`).join('');
    return `<div class="two"><section class="panel"><h3>Déclarés</h3>${declared}</section><section class="panel"><h3>Exécutés</h3><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Test</th><th>Durée</th><th>Artefacts</th></tr></thead><tbody>${rows}</tbody></table></div></section></div>`;
  }

  function renderArtifacts(artifacts) {
    if (!artifacts.length) return '<span class="muted">Aucun artefact</span>';
    return artifacts.map((artifact) => {
      const href = hrefFor(artifact.path);
      const label = esc(artifact.label || artifact.type || 'artefact');
      if (artifact.type === 'screenshot') {
        return `<a class="shot" href="${esc(href)}"><img src="${esc(href)}" alt="${label}"><span>${label}</span></a>`;
      }
      return `<a href="${esc(href)}">${label}</a>`;
    }).join('');
  }

  function renderProofLinks(proofs) {
    if (!proofs.length) return '<div class="empty">Aucune preuve collectée.</div>';
    return list(proofs, (proof) => `<li><a href="${esc(hrefFor(proof.path))}">${esc(proof.label || proof.type || 'preuve')}</a> <code>${esc(proof.scenario_id || proof.scenario || '')}</code></li>`);
  }

  function renderGaps() {
    const inv = data.feature_inventory || {};
    const proofFailures = data.proof_failures || [];
    app.innerHTML = `${crumbs([{label: 'Gaps'}])}<h1>Gaps et violations</h1>
      <div class="two">
        <section class="panel"><h2>Violations</h2>${list(inv.violations || [], (item) => `<li>${esc(item)}</li>`)}</section>
        <section class="panel"><h2>Warnings</h2>${list(inv.warnings || [], (item) => `<li>${esc(item)}</li>`)}</section>
      </div>
      <section class="panel"><h2>Proof failures</h2>${list(proofFailures, (item) => `<li>${esc(item)}</li>`)}</section>`;
  }

  function renderRun() {
    const commands = data.commands || [];
    const rows = commands.map((command) => `<tr><td>${statusPill(command.status)}</td><td>${esc(command.label)}</td><td><code>${esc((command.argv || []).join(' '))}</code></td><td>${esc(command.duration_s)}s</td><td><code>${esc(command.log)}</code></td></tr>`).join('');
    const junit = data.junit || {};
    const suiteRows = Object.entries(junit).map(([name, suite]) => `<tr>
      <td>${esc(name)}</td><td>${esc(suite.tests)}</td><td>${esc(suite.passed)}</td>
      <td>${esc(suite.failures + suite.errors)}</td><td>${esc(suite.skipped)}</td>
      <td>${esc(suite.time_s)}s</td><td><code>${esc(suite.path)}</code></td>
    </tr>`).join('');
    const focusRows = Object.entries(junit).flatMap(([name, suite]) =>
      (suite.focus || []).map((tc) => `<tr><td>${esc(name)}</td><td>${statusPill(tc.status)}</td><td><code>${esc(tc.classname)}.${esc(tc.name)}</code></td><td>${esc(tc.time_s)}s</td></tr>`)
    ).join('');
    const tails = commands.map((command) => `<details><summary>${esc(command.label)} — <code>${esc(command.log)}</code></summary><pre>${esc(command.log_tail || '(log vide)')}</pre></details>`).join('');
    app.innerHTML = `${crumbs([{label: 'Run'}])}<h1>Preuves du run</h1>${renderMetrics()}
      <h2>Commandes</h2><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Preuve</th><th>Commande</th><th>Durée</th><th>Log</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h2>Suites JUnit</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Tests</th><th>Passés</th><th>Échecs</th><th>Skips</th><th>Durée</th><th>XML</th></tr></thead><tbody>${suiteRows}</tbody></table></div>
      <h2>Focus (échecs ou plus lents)</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Statut</th><th>Test</th><th>Durée</th></tr></thead><tbody>${focusRows}</tbody></table></div>
      <h2>Fins de logs</h2><section class="panel">${tails}</section>
      <h2>Catalogue</h2>${renderEvidenceCatalog()}`;
  }

  function renderCli() {
    const inv = data.feature_inventory || {};
    const byEp = inv.feature_by_entrypoint || {};
    const rows = (inv.entrypoints || []).map((ep) => {
      const featureId = byEp[ep.id] || '';
      const link = featureId ? `<a href="#/features/${esc(featureId)}">${esc(featureId)}</a>` : '<span class="muted">non rattaché</span>';
      return `<tr><td><code>${esc(ep.id)}</code></td><td>${esc(ep.type)}</td><td>${esc(ep.label || '')}</td><td>${link}</td></tr>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'CLI'}])}<h1>Surface CLI et entrypoints</h1>
      <p>${esc((data.project || {}).cli_command_count || 0)} sous-commandes cdpx. Chaque entrypoint public est rattaché à exactement une feature (sinon la preuve échoue). Aide complète capturée: <code>${esc(data.cli_help || '')}</code></p>
      <div class="table-wrap"><table><thead><tr><th>Entrypoint</th><th>Type</th><th>Description</th><th>Feature</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderValidation() {
    const matrixRows = (data.validation_matrix || []).map((row) => `<tr><td>${esc(row.milestone)}</td><td>${esc(row.proof)}</td></tr>`).join('');
    const coverageRows = (data.coverage_groups || []).map((group) => `<tr><td>${esc(group.suite)}</td><td><code>${esc(group.module)}</code></td><td>${esc(group.tests)}</td><td>${esc(group.failed)}</td><td>${esc(group.skipped)}</td></tr>`).join('');
    const riskRows = (data.risks || []).map((risk) => `<tr><td>${esc(risk.risk)}</td><td>${esc(risk.mitigation)}</td><td>${esc(risk.rollback)}</td></tr>`).join('');
    const unknownRows = (data.unknowns || []).map((item) => `<tr><td>${esc(item.item)}</td><td>${esc(item.why)}</td><td>${esc(item.how_to_verify)}</td></tr>`).join('');
    app.innerHTML = `${crumbs([{label: 'Validation'}])}<h1>Matrice de validation</h1>
      <h2>Preuve par milestone</h2><div class="table-wrap"><table><thead><tr><th>Milestone</th><th>Preuve</th></tr></thead><tbody>${matrixRows}</tbody></table></div>
      <h2>Tests par module</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Module</th><th>Tests</th><th>Échecs</th><th>Skips</th></tr></thead><tbody>${coverageRows}</tbody></table></div>
      <h2>Risques et mitigations</h2><div class="table-wrap"><table><thead><tr><th>Risque</th><th>Mitigation</th><th>Rollback</th></tr></thead><tbody>${riskRows}</tbody></table></div>
      <h2>Inconnues assumées</h2><div class="table-wrap"><table><thead><tr><th>Sujet</th><th>Pourquoi</th><th>Comment vérifier</th></tr></thead><tbody>${unknownRows}</tbody></table></div>`;
  }

  function renderEvidenceCatalog() {
    const rows = (data.evidence_catalog || []).map((item) => `<tr><td>${esc(item.type)}</td><td>${esc(item.name)}</td><td>${statusPill(item.status)}</td><td><code>${esc(item.path || '-')}</code></td><td>${esc(item.roi)}</td></tr>`).join('');
    return `<div class="table-wrap"><table><thead><tr><th>Type</th><th>Nom</th><th>Statut</th><th>Artefact</th><th>ROI</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderProject() {
    const project = data.project || {};
    const env = data.environment || {};
    app.innerHTML = `${crumbs([{label: 'Projet'}])}<h1>Contexte projet</h1>
      <section class="panel"><h2>Mission</h2><p>${esc(project.mission || '')}</p><p>Version <code>${esc(project.version || 'unknown')}</code>, branche <code>${esc(data.git?.branch || 'unknown')}</code> @ <code>${esc(data.git?.sha || 'unknown')}</code>.</p>
      <p>Environnement du run: Python <code>${esc(env.python || '?')}</code>, <code>${esc(env.platform || '?')}</code>, Chrome/Chromium ${env.chrome_or_chromium ? 'présent' : 'absent'}.</p></section>
      <div class="two">
        <section class="panel"><h2>Docs</h2>${list(project.docs || [], (doc) => `<li><code>${esc(doc)}</code></li>`)}</section>
        <section class="panel"><h2>Fixtures</h2>${list(project.fixtures || [], (fixture) => `<li><code>${esc(fixture)}</code></li>`)}</section>
      </div>`;
  }

  function renderNotFound() {
    app.innerHTML = `${crumbs([{label: 'Introuvable'}])}<h1>Vue introuvable</h1><p>La route <code>${esc(route())}</code> ne correspond à aucune vue.</p>`;
  }

  function render() {
    renderFeatureNav();
    renderDocsNav();
    const parts = route().split('/').filter(Boolean);
    if (parts.length === 0 || parts[0] === 'features' && parts.length === 1) renderFeatures();
    else if (parts[0] === 'features' && parts.length === 2) {
      const feature = findFeature(parts[1]);
      feature ? renderFeature(feature) : renderNotFound();
    } else if (parts[0] === 'features' && parts[2] === 'journeys') {
      const feature = findFeature(parts[1]);
      const journey = findJourney(feature, parts[3]);
      feature && journey ? renderJourney(feature, journey) : renderNotFound();
    } else if (parts[0] === 'features' && parts[2] === 'scenarios') {
      const feature = findFeature(parts[1]);
      const found = findScenario(feature, parts[3]);
      feature && found.scenario ? renderScenario(feature, found.journey, found.scenario) : renderNotFound();
    } else if (parts[0] === 'docs' && parts.length === 1) renderDocs();
    else if (parts[0] === 'docs' && parts[1] === 'view' && parts.length >= 3) {
      const path = decodeURIComponent(parts.slice(2).join('/'));
      const document = findDocument(path);
      document ? renderDocument(document) : renderNotFound();
    } else if (parts[0] === 'gaps') renderGaps();
    else if (parts[0] === 'run') renderRun();
    else if (parts[0] === 'cli') renderCli();
    else if (parts[0] === 'validation') renderValidation();
    else if (parts[0] === 'project') renderProject();
    else renderNotFound();
    setActiveNav();
    renderMermaid().then(() => {
      const section = routeInfo().params.get('section');
      if (section) document.getElementById(section)?.scrollIntoView();
    }).catch((error) => {
      const message = document.createElement('p');
      message.className = 'mermaid-error';
      message.textContent = 'Rendu Mermaid indisponible: ' + String(error);
      app.prepend(message);
    });
  }

  featureSearch.addEventListener('input', renderFeatureNav);
  docsSearch.addEventListener('input', renderDocsNav);
  window.addEventListener('hashchange', render);
  if (!location.hash) location.hash = '#/features';
  render();
})();
"""


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
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-src 'none'">
  <title>Rapport de preuve cdpx - {verdict}</title>
  <style>
{SPA_CSS}
  </style>
</head>
<body>
  <div class="topbar">
    <div class="brand">cdpx · cockpit de preuve</div>
    <span class="pill {pill}">{verdict}</span>
    <span class="muted">{context}</span>
    <nav>
      <a href="#/features" data-route="/features">Features</a>
      <a href="#/docs" data-route="/docs">Docs</a>
      <a href="#/cli" data-route="/cli">CLI</a>
      <a href="#/validation" data-route="/validation">Validation</a>
      <a href="#/gaps" data-route="/gaps">Gaps</a>
      <a href="#/run" data-route="/run">Run</a>
      <a href="#/project" data-route="/project">Projet</a>
    </nav>
  </div>
  <div class="shell">
    <aside class="side">
      <section id="featureSide">
        <h2>Features</h2>
        <input id="featureSearch" type="search" placeholder="Filtrer les features">
        <nav id="featureNav"></nav>
      </section>
      <section id="docsSide" hidden>
        <h2>Documentation</h2>
        <input id="docsSearch" type="search" placeholder="Filtrer les documents">
        <nav id="docsNav"></nav>
      </section>
    </aside>
    <main id="app" aria-live="polite"></main>
  </div>
  <script id="report-data" type="application/json">{payload}</script>
  <script>
{mermaid_bundle}
  </script>
  <script>
{SPA_JS}
  </script>
</body>
</html>
"""


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
) -> Path:
    """Build the only CI-uploadable proof tree from an explicit manifest.

    Textual proof material is already redacted when it reaches this function.
    Opaque/binary attachments remain in the private local proof and are never
    copied to staging. A final exact-value canary scan fails closed.
    """

    selected_ttl = proof_retention_seconds() if ttl is None else ttl
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
        writer.register_file(
            source,
            name=f".proof/{relative}",
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
    write_scenario_evidence(
        EVIDENCE_DIR,
        summary["scenario_evidence"],
        redaction_context=context,
    )
    _write_private_text(
        SUMMARY_JSON,
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    _write_private_text(
        REPORT_HTML,
        redact_text(render_html(summary), context=context, path="$.report_html"),
    )
    _harden_tree(PROOF_DIR)
    build_shareable_proof(
        PROOF_DIR,
        canaries=environment_secret_values(),
        ttl=retention_seconds,
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
