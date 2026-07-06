# ruff: noqa: E501
"""Generate the human proof report consumed by `make proof`.

The report is intentionally evidence-first: every human-facing conclusion is
derived from command exits, pytest JUnit XML, captured logs, or the CLI help
captured during the same run.
"""

from __future__ import annotations

import html
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cdpx.proofing.features import build_feature_inventory, feature_failures

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
VALIDATION_DOC = Path("docs/VALIDATION.md")


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


def _repo_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(Path("src").resolve())
    current = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not current else f"{src}{os.pathsep}{current}"
    return env


def run_evidence(
    id: str,
    label: str,
    argv: list[str],
    log_path: Path,
    *,
    env: dict[str, str],
) -> CommandEvidence:
    started = _now()
    start = time.monotonic()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        f"$ {' '.join(argv)}",
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
        output = proc.stdout
    except FileNotFoundError as exc:
        exit_code = 127
        output = f"{exc}\n"
    duration = time.monotonic() - start
    footer = ["", "--- result ---", f"exit_code: {exit_code}", f"duration_s: {duration:.3f}", ""]
    log_path.write_text("\n".join(header) + "\n" + output + "\n".join(footer), encoding="utf-8")
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
    log_path: Path, argv: list[str], started: str, body: str, result: str
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                f"$ {' '.join(argv)}",
                f"started_at: {started}",
                "",
                "--- output ---",
                body.rstrip(),
                "",
                "--- result ---",
                result.rstrip(),
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_symfony_unavailable_evidence(reason: str) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
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
                "message": reason,
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
    (EVIDENCE_DIR / "symfony-scenarios.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_symfony_evidence() -> CommandEvidence:
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
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    compose_env = os.environ.copy()
    compose_env["CDPX_E2E_UID"] = str(os.getuid())
    compose_env["CDPX_E2E_GID"] = str(os.getgid())

    checks: list[str] = []
    if shutil.which("docker") is None:
        reason = "Docker CLI not found; Symfony e2e marked unavailable and non-blocking."
        _write_command_log(SYMFONY_LOG, argv, started, reason, "status: unavailable\nexit_code: 0")
        write_symfony_unavailable_evidence(reason)
        return CommandEvidence(
            id="symfony-e2e",
            label="Symfony E2E Docker",
            argv=argv,
            log=str(SYMFONY_LOG),
            exit_code=0,
            duration_s=round(time.monotonic() - start, 3),
            status="unavailable",
        )

    for check_argv in (["docker", "compose", "version"], ["docker", "info"]):
        code, output = _run_text(check_argv, timeout=15, env=compose_env)
        checks.append(f"$ {' '.join(check_argv)}\n{output.rstrip()}\nexit_code: {code}")
        if code != 0:
            reason = (
                "Docker is installed but unavailable; Symfony e2e marked unavailable and "
                "non-blocking."
            )
            body = "\n\n".join(checks + [reason])
            _write_command_log(
                SYMFONY_LOG, argv, started, body, "status: unavailable\nexit_code: 0"
            )
            write_symfony_unavailable_evidence(reason)
            return CommandEvidence(
                id="symfony-e2e",
                label="Symfony E2E Docker",
                argv=argv,
                log=str(SYMFONY_LOG),
                exit_code=0,
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


def collect_git_context() -> dict:
    branch_code, branch = _run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    sha_code, sha = _run_text(["git", "rev-parse", "--short", "HEAD"])
    status_code, status = _run_text(["git", "status", "--short"])
    stat_code, stat = _run_text(
        ["git", "diff", "--stat", "--", ".", ":(exclude).proof/*", ":(exclude).idea/*"]
    )

    GIT_STATUS.write_text(status, encoding="utf-8")
    GIT_DIFF_STAT.write_text(stat, encoding="utf-8")

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
        "branch": branch.strip() if branch_code == 0 else "unknown",
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
    if path.startswith("docs/") or path in {"README.md", "HARNESS.md", "CLAUDE.md"}:
        return "Documentation"
    if path in {"Makefile", "pyproject.toml", "Dockerfile"} or path.startswith(".gitlab/"):
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
                "review_focus": "Parsing JUnit, aide CLI et résumé legacy.",
            }
        )

    change_types = []
    if any(path.startswith("src/") for path in paths):
        change_types.append("code")
    if any(path.startswith("tests/") for path in paths):
        change_types.append("tests")
    if "Makefile" in paths or any(path.startswith(".gitlab/") for path in paths):
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
        "Les artefacts lourds doivent rester repliables et traçables pour éviter le bruit en MR.",
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
            "risk": "Rapport trop verbeux pour une MR.",
            "mitigation": "Résumé court; logs et détails secondaires en sections repliables.",
            "rollback": "Réduire les sections dans `render_html` sans toucher à la collecte.",
        },
    ]
    unknowns = [
        {
            "item": "Rendu GitLab exact du HTML",
            "why": "Le rapport est un fichier HTML local, pas une description Markdown GitLab.",
            "how_to_verify": (
                "Ouvrir `.proof/proof-report.html`; pour GitLab, publier un résumé Markdown."
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
            "how_to_verify": "Pour une MR UI, ajouter une capture dans `.proof/`.",
        },
    ]
    if git_context["generated_count"]:
        unknowns.append(
            {
                "item": "Artefacts générés versionnés",
                "why": "Le dépôt suit déjà certains fichiers `.proof`.",
                "how_to_verify": (
                    "Vérifier `git status --short` et choisir entre commit ou pièce jointe MR."
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


def write_scenario_evidence(root: Path, scenario_evidence: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
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
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
            "risk": "Docker peut être absent sur un poste local.",
            "mitigation": (
                "`make proof` lance Symfony si Docker répond; sinon le scénario est marqué "
                "unavailable dans le rapport sans bloquer les preuves Python/Chrome."
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
            "item": "Captures visuelles persistées",
            "why": "Les e2e valident le screenshot PNG sans le conserver dans `.proof`.",
            "how_to_verify": (
                "Ajouter une capture dédiée dans `.proof/` si une preuve visuelle est requise."
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
        }

    root = ET.fromstring(path.read_text(encoding="utf-8"))
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
.panel.doc table { width: 100%; border-collapse: collapse; margin: 8px 0; }
.panel.doc th, .panel.doc td { border: 1px solid var(--line); padding: 5px 8px; text-align: left; }
.panel.doc pre { overflow-x: auto; }
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
  const topLinks = Array.from(document.querySelectorAll('[data-route]'));

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const route = () => location.hash.slice(1) || '/features';
  const features = () => data.feature_inventory.features || [];
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

  function setActiveNav() {
    const current = route();
    topLinks.forEach((link) => {
      link.classList.toggle('active', current === link.dataset.route || current.startsWith(link.dataset.route + '/'));
    });
    Array.from(featureNav.querySelectorAll('a')).forEach((link) => {
      link.classList.toggle('active', current.includes('/features/' + link.dataset.featureId));
    });
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
        <section class="panel"><h2>Documentation</h2>${list(feature.docs || [], (doc) => `<li><code>${esc(doc)}</code></li>`)}</section>
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
    } else if (parts[0] === 'gaps') renderGaps();
    else if (parts[0] === 'run') renderRun();
    else if (parts[0] === 'cli') renderCli();
    else if (parts[0] === 'validation') renderValidation();
    else if (parts[0] === 'project') renderProject();
    else renderNotFound();
    setActiveNav();
  }

  featureSearch.addEventListener('input', renderFeatureNav);
  window.addEventListener('hashchange', render);
  if (!location.hash) location.hash = '#/features';
  render();
})();
"""


def _json_for_html_script(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_html(summary: dict) -> str:
    verdict = "OK" if summary["ok"] else "ECHEC"
    generated = html.escape(summary["generated_at"])
    payload = _json_for_html_script(summary)
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
      <a href="#/cli" data-route="/cli">CLI</a>
      <a href="#/validation" data-route="/validation">Validation</a>
      <a href="#/gaps" data-route="/gaps">Gaps</a>
      <a href="#/run" data-route="/run">Run</a>
      <a href="#/project" data-route="/project">Projet</a>
    </nav>
  </div>
  <div class="shell">
    <aside class="side">
      <h2>Features</h2>
      <input id="featureSearch" type="search" placeholder="Filtrer les features">
      <nav id="featureNav"></nav>
    </aside>
    <main id="app" aria-live="polite"></main>
  </div>
  <script id="report-data" type="application/json">{payload}</script>
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
    scenario_evidence = enrich_scenario_evidence(scenario_evidence, feature_inventory)
    scenario_failures = proof_failures_from_scenarios(scenario_evidence)
    feature_inventory_failures = feature_failures(feature_inventory)
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
    unavailable = sum(
        1
        for suite in scenario_evidence.get("suites", {}).values()
        for scenario in suite
        if scenario.get("status") == "unavailable"
    )
    # Symfony sans Docker reste non bloquant par défaut (visible, pas vert
    # silencieux); CDPX_PROOF_REQUIRE_SYMFONY=1 en fait un échec de preuve.
    symfony_failures = []
    if unavailable and os.environ.get("CDPX_PROOF_REQUIRE_SYMFONY") == "1":
        symfony_failures.append(
            f"symfony evidence unavailable ({unavailable} scenarios) "
            "with CDPX_PROOF_REQUIRE_SYMFONY=1"
        )
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
        and not feature_inventory_failures
        and not symfony_failures
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
        "proof_failures": scenario_failures
        + feature_inventory_failures
        + command_failures
        + symfony_failures,
        "risks": risk_packet["risks"],
        "unknowns": risk_packet["unknowns"],
    }
    summary["evidence_catalog"] = build_evidence_catalog(summary, unit, e2e, symfony)
    return summary


def generate() -> dict:
    if PROOF_DIR.exists():
        shutil.rmtree(PROOF_DIR)
    PROOF_DIR.mkdir(parents=True)
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
        ),
        run_evidence(
            "ruff-format",
            "Ruff format",
            [sys.executable, "-m", "ruff", "format", "--check", "src", "tests"],
            PROOF_DIR / "ruff-format.log",
            env=env,
        ),
        run_evidence(
            "mypy",
            "Mypy typage",
            [sys.executable, "-m", "mypy", "src/cdpx"],
            PROOF_DIR / "mypy.log",
            env=env,
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
        ),
        run_evidence(
            "e2e",
            "Pytest E2E Chrome",
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/e2e/test_e2e_chrome.py",
                "-v",
                f"--cdpx-evidence-dir={EVIDENCE_DIR}",
                f"--junitxml={e2e_xml}",
            ],
            E2E_LOG,
            env=env,
        ),
        run_symfony_evidence(),
        run_evidence(
            "cli-help",
            "Aide CLI",
            [sys.executable, "-m", "cdpx.cli", "--help"],
            CLI_HELP,
            env=env,
        ),
    ]

    unit = parse_junit(unit_xml)
    e2e = parse_junit(e2e_xml)
    symfony = parse_junit(symfony_xml)
    help_commands = parse_help_commands(CLI_HELP.read_text(encoding="utf-8", errors="replace"))
    git_context = collect_git_context()
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
    write_scenario_evidence(EVIDENCE_DIR, summary["scenario_evidence"])
    SUMMARY_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_HTML.write_text(render_html(summary), encoding="utf-8")
    return summary


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
