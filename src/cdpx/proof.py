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
CLI_HELP = PROOF_DIR / "cdpx-help.txt"
GIT_STATUS = PROOF_DIR / "git-status.txt"
GIT_DIFF_STAT = PROOF_DIR / "git-diff-stat.txt"
EVIDENCE_DIR = PROOF_DIR / "evidence"

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


def _run_text(argv: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=Path.cwd(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
    except FileNotFoundError as exc:
        return 127, f"{exc}\n"
    return proc.returncode, proc.stdout


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


def build_evidence_catalog(summary: dict, unit: dict, e2e: dict) -> list[dict]:
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
                f"{e2e.get('tests', 0)} scénarios navigateur, "
                f"{e2e.get('skipped', 0)} skip non-Chrome déclaré."
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

    version = "unknown"
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        if match:
            version = match.group(1)

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


def group_cases_by_module(unit: dict, e2e: dict) -> list[dict]:
    groups: dict[str, dict] = {}
    for suite_name, suite in (("unit", unit), ("e2e", e2e)):
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
    suites = {"unit": [], "integration": [], "e2e": []}
    files = []
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
        "screenshots": screenshots,
        "missing_e2e_screenshots": missing_e2e,
    }


def proof_failures_from_scenarios(scenario_evidence: dict) -> list[str]:
    failures = []
    for nodeid in scenario_evidence["totals"]["missing_e2e_screenshots"]:
        failures.append(f"missing e2e screenshot: {nodeid}")
    return failures


def enrich_scenario_evidence(scenario_evidence: dict, feature_inventory: dict) -> dict:
    by_nodeid = {}
    for feature in feature_inventory.get("features", []):
        for scenario in feature.get("matched_scenarios", []):
            by_nodeid[scenario.get("nodeid", "")] = scenario

    suites = {}
    for suite, scenarios in scenario_evidence.get("suites", {}).items():
        suites[suite] = [
            by_nodeid.get(scenario.get("nodeid", ""), scenario) for scenario in scenarios
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
            "risk": "Les preuves Symfony Docker ne sont pas lancées par `make proof`.",
            "mitigation": "`make docker-symfony-e2e` reste un portail séparé et documenté.",
            "rollback": "Exécuter le portail Docker dédié avant release Symfony-sensitive.",
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
                message = (
                    child.attrib.get("message", "") or (child.text or "").strip().splitlines()[0:1]
                )
                if isinstance(message, list):
                    message = message[0] if message else ""
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


def _status_class(status: str) -> str:
    return "ok" if status == "ok" else "failed"


def _case_focus(cases: list[dict]) -> list[dict]:
    non_passed = [case for case in cases if case["status"] != "passed"]
    if non_passed:
        return non_passed[:20]
    return sorted(cases, key=lambda case: case["time_s"], reverse=True)[:20]


def _suite_for_summary(suite: dict) -> dict:
    return {
        "path": suite.get("path", ""),
        "exists": suite.get("exists", True),
        "tests": suite.get("tests", 0),
        "passed": suite.get("passed", 0),
        "failures": suite.get("failures", 0),
        "errors": suite.get("errors", 0),
        "skipped": suite.get("skipped", 0),
        "time_s": suite.get("time_s", 0.0),
    }


def _list_items(items: list[str]) -> str:
    if not items:
        return "<li>Aucun item.</li>"
    return "".join(f"<li>{html.escape(item)}</li>" for item in items)


def _file_list(items: list[dict]) -> str:
    if not items:
        return "<li>Aucun fichier source détecté hors artefacts générés.</li>"
    return "".join(
        f"<li><span class='muted'>{html.escape(item['status'])}</span> "
        f"<code>{html.escape(item['path'])}</code></li>"
        for item in items
    )


def _category_cards(categories: dict[str, list[str]]) -> str:
    if not categories:
        return "<div class='panel'><strong>Aucun changement source détecté</strong></div>"
    cards = []
    for category, paths in sorted(categories.items()):
        files = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in sorted(paths))
        cards.append(
            f"<div class='panel'><strong>{html.escape(category)}</strong><ul>{files}</ul></div>"
        )
    return "".join(cards)


def _entrypoint_rows(entrypoints: list[dict]) -> str:
    if not entrypoints:
        return "<tr><td colspan='4'>Aucun entrypoint spécifique détecté.</td></tr>"
    rows = []
    for item in entrypoints:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(item['name'])}</code></td>"
            f"<td>{html.escape(item['type'])}</td>"
            f"<td><code>{html.escape(item['evidence'])}</code></td>"
            f"<td>{html.escape(item['review_focus'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _risk_rows(items: list[dict]) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['risk'])}</td>"
            f"<td>{html.escape(item['mitigation'])}</td>"
            f"<td>{html.escape(item['rollback'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _unknown_rows(items: list[dict]) -> str:
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(item['item'])}</td>"
            f"<td>{html.escape(item['why'])}</td>"
            f"<td>{html.escape(item['how_to_verify'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _milestone_rows(items: list[dict]) -> str:
    if not items:
        return "<tr><td colspan='2'>Aucune matrice de validation détectée.</td></tr>"
    return "".join(
        f"<tr><td>{html.escape(item['milestone'])}</td><td>{html.escape(item['proof'])}</td></tr>"
        for item in items
    )


def _coverage_rows(items: list[dict]) -> str:
    if not items:
        return "<tr><td colspan='5'>Aucun testcase JUnit détecté.</td></tr>"
    return "".join(
        "<tr>"
        f"<td><code>{html.escape(item['module'])}</code></td>"
        f"<td>{html.escape(item['suite'])}</td>"
        f"<td>{item['tests']}</td>"
        f"<td>{item['failed']}</td>"
        f"<td>{item['skipped']}</td>"
        "</tr>"
        for item in items
    )


def _simple_kv_rows(items: dict[str, int]) -> str:
    if not items:
        return "<tr><td colspan='2'>Aucune donnée.</td></tr>"
    return "".join(
        f"<tr><td>{html.escape(key)}</td><td>{value}</td></tr>"
        for key, value in sorted(items.items())
    )


def _path_items(paths: list[str]) -> str:
    if not paths:
        return "<li>Aucun fichier détecté.</li>"
    return "".join(f"<li><code>{html.escape(path)}</code></li>" for path in paths)


def _report_href(path: str) -> str:
    if not path:
        return ""
    try:
        return html.escape(Path(path).relative_to(PROOF_DIR).as_posix())
    except ValueError:
        return html.escape(path)


def _artifact_links(scenario: dict) -> str:
    artifacts = scenario.get("artifacts", [])
    if not artifacts:
        return "<span class='muted'>Aucun artefact</span>"
    links = []
    for artifact in artifacts:
        href = _report_href(artifact.get("path", ""))
        label = html.escape(artifact.get("label") or artifact.get("type", "artefact"))
        links.append(f"<a href='{href}'>{label}</a>")
    return "<br>".join(links)


def _screenshot_thumbs(scenario: dict) -> str:
    thumbs = []
    for artifact in scenario.get("artifacts", []):
        if artifact.get("type") != "screenshot":
            continue
        href = _report_href(artifact.get("path", ""))
        label = html.escape(artifact.get("label", "screenshot"))
        thumbs.append(
            f"<a class='shot' href='{href}'>"
            f"<img src='{href}' alt='{label}'><span>{label}</span></a>"
        )
    if not thumbs:
        return "<span class='muted'>-</span>"
    return "".join(thumbs)


def _scenario_rows(scenarios: list[dict]) -> str:
    if not scenarios:
        return "<tr><td colspan='6'>Aucun scénario collecté.</td></tr>"
    rows = []
    for scenario in sorted(scenarios, key=lambda item: item["nodeid"]):
        nodeid = scenario["nodeid"]
        file_part, _, test_part = nodeid.partition("::")
        scenario_cell = (
            f"<code>{html.escape(test_part or nodeid)}</code>"
            f"<div class='muted'>{html.escape(file_part)}</div>"
        )
        proves = scenario.get("proves") or []
        title = scenario.get("title", "")
        if proves:
            proves_text = "<br>".join(html.escape(item) for item in proves)
        elif title and title != (test_part or nodeid):
            proves_text = html.escape(title)
        else:
            proves_text = "<span class='muted'>-</span>"
        status = html.escape(scenario.get("status", "unknown"))
        message = html.escape(scenario.get("message", ""))
        rows.append(
            "<tr>"
            f"<td><span class='pill {status}'>{status}</span></td>"
            f"<td>{scenario_cell}</td>"
            f"<td>{proves_text}</td>"
            f"<td>{scenario.get('duration_s', 0):.3f}s</td>"
            f"<td>{_screenshot_thumbs(scenario)}</td>"
            f"<td>{_artifact_links(scenario)}<div class='muted'>{message}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def _compact_list(items: list[str], limit: int = 6) -> str:
    if not items:
        return "<span class='muted'>-</span>"
    visible = items[:limit]
    out = "".join(f"<li><code>{html.escape(item)}</code></li>" for item in visible)
    if len(items) > limit:
        out += f"<li class='muted'>+{len(items) - limit} autres</li>"
    return f"<ul class='compact'>{out}</ul>"


def _feature_status(feature: dict) -> str:
    scenarios = feature.get("matched_scenarios", [])
    if any(scenario.get("status") in {"failed", "error"} for scenario in scenarios):
        return "failed"
    if feature.get("gaps"):
        return "warning"
    return "ok"


def _feature_status_counts(feature_inventory: dict) -> dict[str, int]:
    counts = {"ok": 0, "warning": 0, "failed": 0}
    for feature in feature_inventory.get("features", []):
        counts[_feature_status(feature)] += 1
    return counts


def _scenario_status_counts(scenarios: list[dict]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for scenario in scenarios:
        status = scenario.get("status", "unknown")
        if status in {"failed", "error"}:
            counts["failed"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts


def _feature_cards(feature_inventory: dict) -> str:
    cards = []
    for feature in feature_inventory.get("features", []):
        status = _feature_status(feature)
        scenario_count = len(feature.get("matched_scenarios", []))
        proof_count = len(feature.get("proofs", []))
        gap_count = len(feature.get("gaps", []))
        search_text = " ".join(
            [feature["id"], feature["title"], feature["summary"]]
            + [item["id"] for item in feature.get("matched_entrypoints", [])]
        ).lower()
        gap_class = " has-gaps" if gap_count else ""
        cards.append(
            f"<article class='feature-card status-{html.escape(status)}' "
            f"data-status='{html.escape(status)}' "
            f"data-text='{html.escape(search_text)}'>"
            "<div class='feature-head'>"
            f"<span class='pill {status}'>{html.escape(status)}</span>"
            f"<code>{html.escape(feature['id'])}</code>"
            "</div>"
            f"<h3><a href='#feature-{html.escape(feature['id'])}'>"
            f"{html.escape(feature['title'])}</a></h3>"
            f"<p>{html.escape(feature['summary'])}</p>"
            "<div class='feature-metrics'>"
            f"<span>{len(feature.get('matched_entrypoints', []))}<small>entrypoints</small></span>"
            f"<span>{len(feature.get('matched_paths', []))}<small>paths</small></span>"
            f"<span>{scenario_count}<small>scénarios</small></span>"
            f"<span>{proof_count}<small>preuves</small></span>"
            f"<span class='{gap_class.strip()}'>{gap_count}<small>gaps</small></span>"
            "</div>"
            f"<a class='feature-open' href='#feature-{html.escape(feature['id'])}'>Détails →</a>"
            "</article>"
        )
    return "".join(cards) or "<div class='panel'>Aucune feature chargée.</div>"


def _proof_links(proofs: list[dict], limit: int = 8) -> str:
    if not proofs:
        return "<span class='muted'>Aucune preuve collectée.</span>"
    items = []
    for proof in proofs[:limit]:
        href = _report_href(proof.get("path", ""))
        scenario = proof.get("scenario", "").rsplit("::", 1)[-1]
        label = proof.get("label") or proof.get("path", "") or "preuve"
        if scenario:
            label = f"{scenario} · {label}"
        kind = html.escape(proof.get("type", "file"))
        items.append(
            f"<li><a href='{href}'>{html.escape(label)}</a> "
            f"<span class='muted'>({kind})</span></li>"
        )
    if len(proofs) > limit:
        items.append(f"<li class='muted'>+{len(proofs) - limit} autres</li>")
    return f"<ul class='compact'>{''.join(items)}</ul>"


def _feature_gap_callout(gaps: list[str]) -> str:
    if not gaps:
        return ""
    items = "".join(f"<li>{html.escape(gap)}</li>" for gap in gaps)
    return f"<div class='gap-callout'><strong>Gaps à traiter</strong><ul>{items}</ul></div>"


def _feature_scenario_line(scenarios: list[dict]) -> str:
    if not scenarios:
        return "<p class='muted'>Aucun scénario pytest relié.</p>"
    counts = _scenario_status_counts(scenarios)
    parts = [f"{len(scenarios)} scénarios reliés"]
    parts.append(f"{counts['passed']} passed")
    if counts["failed"]:
        parts.append(f"<strong class='bad-text'>{counts['failed']} failed</strong>")
    if counts["skipped"]:
        parts.append(f"{counts['skipped']} skipped")
    failed_items = "".join(
        f"<li><code>{html.escape(scenario.get('nodeid', ''))}</code> "
        f"{html.escape(scenario.get('message', ''))}</li>"
        for scenario in scenarios
        if scenario.get("status") in {"failed", "error"}
    )
    failed_html = f"<ul class='compact'>{failed_items}</ul>" if failed_items else ""
    return f"<p>{' · '.join(parts)}</p>{failed_html}"


def _feature_detail_sections(feature_inventory: dict) -> str:
    sections = []
    for feature in feature_inventory.get("features", []):
        status = _feature_status(feature)
        entrypoints = [item["id"] for item in feature.get("matched_entrypoints", [])]
        tests = feature.get("matched_tests", [])
        docs = feature.get("docs", [])
        paths = feature.get("matched_paths", [])
        gaps = feature.get("gaps", [])
        scenarios = feature.get("matched_scenarios", [])
        journeys = [
            f"{journey.get('title', '')} — {journey.get('entrypoint', journey.get('id', ''))}"
            for journey in feature.get("journeys", [])
        ]
        open_attr = "" if status == "ok" else " open"
        meta = (
            f"{len(scenarios)} scénarios · {len(feature.get('proofs', []))} preuves · "
            f"{len(gaps)} gaps"
        )
        sections.append(
            f"<details class='feature-detail status-{status}' "
            f"id='feature-{html.escape(feature['id'])}'{open_attr}>"
            "<summary>"
            f"<span class='pill {status}'>{status}</span>"
            f"<strong>{html.escape(feature['title'])}</strong>"
            f"<code>{html.escape(feature['id'])}</code>"
            f"<span class='muted'>{meta}</span>"
            "</summary>"
            "<div class='block-body'>"
            f"<p>{html.escape(feature['summary'])} "
            f"<span class='muted'>Statut déclaré: {html.escape(feature['status'])} · "
            f"source: <code>{html.escape(feature['source'])}</code></span></p>"
            f"{_feature_gap_callout(gaps)}"
            f"{_feature_scenario_line(scenarios)}"
            "<div class='feature-grid'>"
            f"<div><strong>Journeys</strong>{_compact_list(journeys)}</div>"
            f"<div><strong>Entrypoints rattachés</strong>{_compact_list(entrypoints, 8)}</div>"
            f"<div><strong>Docs</strong>{_compact_list(docs)}</div>"
            f"<div><strong>Code / specs</strong>{_compact_list(paths, 10)}</div>"
            f"<div><strong>Tests</strong>{_compact_list(tests, 10)}</div>"
            f"<div><strong>Preuves</strong>{_proof_links(feature.get('proofs', []))}</div>"
            "</div>"
            "</div>"
            "</details>"
        )
    return "".join(sections)


_GAP_ACTIONS = (
    ("entrypoint unmapped", "Déclarer l'entrypoint dans `entrypoints` d'un doc docs/features/."),
    ("entrypoint mapped multiple times", "Garder un seul doc feature propriétaire."),
    ("scenario references unknown feature", "Corriger l'id feature référencé par le test."),
    ("scenario unmapped", "Couvrir le nodeid via `test_globs` d'une feature."),
    ("source path unmapped", "Ajouter le chemin aux `path_globs` de la feature concernée."),
    ("feature id duplicated", "Renommer l'un des docs feature en conflit."),
    ("missing e2e screenshot", "Capturer un screenshot dans le scénario e2e concerné."),
    ("missing", "Compléter le doc feature (front matter ou sections requises)."),
)


def _gap_action(signal: str) -> str:
    for prefix, action in _GAP_ACTIONS:
        if prefix in signal:
            return action
    return "Voir docs/features/ et la feature concernée."


def _gap_rows(feature_inventory: dict, scenario_failures: list[str]) -> str:
    rows = []
    entries = [("failed", "violation", item) for item in feature_inventory.get("violations", [])]
    entries += [("failed", "preuve", item) for item in scenario_failures]
    entries += [("warning", "warning", item) for item in feature_inventory.get("warnings", [])]
    for status, kind, item in entries:
        rows.append(
            "<tr>"
            f"<td><span class='pill {status}'>{html.escape(kind)}</span></td>"
            f"<td>{html.escape(item)}</td>"
            f"<td>{html.escape(_gap_action(item))}</td>"
            "</tr>"
        )
    if not rows:
        return (
            "<tr><td><span class='pill ok'>ok</span></td>"
            "<td colspan='2'>Aucun gap bloquant détecté: entrypoints, scénarios et chemins "
            "sources sont tous rattachés à une feature.</td></tr>"
        )
    return "".join(rows)


REPORT_CSS = """\
:root {
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #18202a;
  --muted: #5d6673;
  --line: #d9dee7;
  --ok: #167044;
  --bad: #b42318;
  --info: #1d4ed8;
  --warn: #9a6700;
  --warn-soft: #f6c85f;
  --warn-bg: #fdf3d7;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; scroll-padding-top: 70px; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  background: var(--panel);
  border-bottom: 1px solid var(--line);
}
.topbar-inner {
  max-width: 1160px;
  margin: 0 auto;
  padding: 10px 24px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.topbar nav { display: flex; gap: 14px; flex-wrap: wrap; margin-left: auto; }
.topbar nav a { color: var(--muted); text-decoration: none; font-size: 13px; font-weight: 600; }
.topbar nav a:hover { color: var(--ink); }
header, main { max-width: 1160px; margin: 0 auto; padding: 20px 24px; }
h1, h2 { margin: 0 0 10px; line-height: 1.15; }
h1 { font-size: 26px; }
h2 { font-size: 20px; }
h3 { font-size: 16px; margin: 16px 0 8px; }
p { margin: 0 0 12px; color: var(--muted); }
.report-section { margin-top: 34px; }
.hero { display: grid; grid-template-columns: repeat(auto-fit, minmax(148px, 1fr)); gap: 12px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
}
.metric { display: block; font-size: 25px; font-weight: 700; color: var(--ink); }
.ok-text { color: var(--ok); }
.bad-text { color: var(--bad); }
.warn-text { color: var(--warn); }
.muted { color: var(--muted); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.feature-board { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.callout {
  border-left: 4px solid var(--info);
  background: #eef5ff;
  padding: 12px 16px;
  margin: 12px 0;
  border-radius: 0 6px 6px 0;
}
.gap-callout {
  border-left: 4px solid var(--warn);
  background: var(--warn-bg);
  padding: 10px 14px;
  margin: 10px 0;
  border-radius: 0 6px 6px 0;
}
.gap-callout ul { margin: 6px 0 0; padding-left: 18px; }
table {
  width: 100%;
  border-collapse: collapse;
  background: var(--panel);
  border: 1px solid var(--line);
}
th, td {
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
th { font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: .04em; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { font-size: 13px; }
td code, ul code, summary code { overflow-wrap: anywhere; word-break: break-word; }
pre {
  overflow: auto;
  white-space: pre-wrap;
  background: #10151f;
  color: #edf2f7;
  padding: 14px;
  border-radius: 6px;
}
.pill {
  display: inline-block;
  min-width: 58px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  text-align: center;
}
.ok, .passed { color: #fff; background: var(--ok); }
.failed, .error { color: #fff; background: var(--bad); }
.warning { color: #1f1600; background: var(--warn-soft); }
.skipped { color: #1c1600; background: #f6d365; }
.generated, .optional, .not-needed { color: #17324d; background: #dbeafe; }
.type {
  display: inline-block;
  padding: 2px 7px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f8fafc;
  font-size: 12px;
  font-weight: 700;
}
.shot {
  display: inline-flex;
  flex-direction: column;
  gap: 4px;
  width: 170px;
  margin: 0 8px 8px 0;
  color: var(--ink);
  text-decoration: none;
}
.shot img {
  width: 170px;
  height: 96px;
  object-fit: cover;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fff;
}
.shot span { font-size: 12px; color: var(--muted); }
.toolbar { display: flex; gap: 10px; align-items: center; margin: 12px 0; flex-wrap: wrap; }
.toolbar input, .toolbar select {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  background: #fff;
  color: var(--ink);
  font: inherit;
}
.toolbar input { min-width: 260px; }
.toolbar button {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 12px;
  background: #fff;
  color: var(--ink);
  font: inherit;
  cursor: pointer;
}
.toolbar button:hover { background: var(--bg); }
.feature-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  display: flex;
  flex-direction: column;
}
.feature-card.status-ok, details.feature-detail.status-ok { border-left: 4px solid var(--ok); }
.feature-card.status-warning, details.feature-detail.status-warning {
  border-left: 4px solid var(--warn-soft);
}
.feature-card.status-failed, details.feature-detail.status-failed {
  border-left: 4px solid var(--bad);
}
.feature-card h3 { margin: 10px 0 6px; }
.feature-card h3 a { color: var(--ink); text-decoration: none; }
.feature-card h3 a:hover { text-decoration: underline; }
.feature-card p { flex: 1; }
.feature-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.feature-metrics {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  margin: 10px 0;
}
.feature-metrics span {
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 6px 4px;
  font-weight: 700;
  text-align: center;
}
.feature-metrics span.has-gaps { border-color: var(--warn-soft); background: var(--warn-bg); }
.feature-metrics small { display: block; color: var(--muted); font-size: 11px; font-weight: 500; }
.feature-open { font-size: 13px; font-weight: 600; }
details { margin: 10px 0; }
summary { cursor: pointer; font-weight: 700; }
details.block, details.feature-detail {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
details.block > summary, details.feature-detail > summary { padding: 12px 16px; }
details.feature-detail > summary { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
details.feature-detail > summary > .muted {
  margin-left: auto;
  font-weight: 500;
  font-size: 13px;
}
.block-body { padding: 2px 16px 16px; }
.table-wrap { overflow-x: auto; }
.block-body > .table-wrap, .block-body > details { margin-top: 8px; }
.feature-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
ul.compact { margin: 8px 0 0; padding-left: 18px; }
ul.commands { columns: 2; padding-left: 20px; }
li { break-inside: avoid; margin-bottom: 6px; }
@media (max-width: 860px) {
  .two { grid-template-columns: 1fr; }
  .feature-board, .feature-grid { grid-template-columns: 1fr; }
  table { display: block; overflow-x: auto; }
  ul.commands { columns: 1; }
  .topbar nav { margin-left: 0; }
}
"""

REPORT_JS = """\
(function () {
  const search = document.getElementById('featureSearch');
  const status = document.getElementById('featureStatus');
  const counter = document.getElementById('featureCount');
  const cards = Array.from(document.querySelectorAll('.feature-card'));
  function filterFeatures() {
    const q = (search.value || '').trim().toLowerCase();
    const s = status.value || '';
    let shown = 0;
    for (const card of cards) {
      const visible = (!q || card.dataset.text.includes(q))
        && (!s || card.dataset.status === s);
      card.style.display = visible ? '' : 'none';
      if (visible) shown += 1;
    }
    counter.textContent = shown + '/' + cards.length + ' features affichées';
  }
  search.addEventListener('input', filterFeatures);
  status.addEventListener('change', filterFeatures);
  filterFeatures();
  function openHashTarget() {
    const id = decodeURIComponent(location.hash.slice(1));
    if (!id) return;
    const el = document.getElementById(id);
    if (!el) return;
    if (el.tagName === 'DETAILS') el.open = true;
    const parent = el.closest('details');
    if (parent) parent.open = true;
  }
  window.addEventListener('hashchange', openHashTarget);
  openHashTarget();
  const featureDetails = Array.from(document.querySelectorAll('details.feature-detail'));
  const expandAll = document.getElementById('expandFeatures');
  const collapseAll = document.getElementById('collapseFeatures');
  if (expandAll) {
    expandAll.addEventListener('click', () => featureDetails.forEach((d) => { d.open = true; }));
  }
  if (collapseAll) {
    collapseAll.addEventListener('click', () => featureDetails.forEach((d) => { d.open = false; }));
  }
})();
"""

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
.warning, .skipped { color: #241800; background: #f6d365; }
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
    return (feature.gaps || []).length ? 'warning' : 'ok';
  };
  const counts = (items) => {
    const out = {passed: 0, failed: 0, skipped: 0};
    for (const item of items || []) {
      if (['failed', 'error'].includes(item.status)) out.failed += 1;
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
    app.innerHTML = `${crumbs([{label: 'Run'}])}<h1>Preuves du run</h1>${renderMetrics()}
      <h2>Commandes</h2><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Preuve</th><th>Commande</th><th>Durée</th><th>Log</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h2>Catalogue</h2>${renderEvidenceCatalog()}`;
  }

  function renderEvidenceCatalog() {
    const rows = (data.evidence_catalog || []).map((item) => `<tr><td>${esc(item.type)}</td><td>${esc(item.name)}</td><td>${statusPill(item.status)}</td><td><code>${esc(item.path || '-')}</code></td><td>${esc(item.roi)}</td></tr>`).join('');
    return `<div class="table-wrap"><table><thead><tr><th>Type</th><th>Nom</th><th>Statut</th><th>Artefact</th><th>ROI</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderProject() {
    const project = data.project || {};
    app.innerHTML = `${crumbs([{label: 'Projet'}])}<h1>Contexte projet</h1>
      <section class="panel"><h2>Mission</h2><p>${esc(project.mission || '')}</p><p>Version <code>${esc(project.version || 'unknown')}</code>, branche <code>${esc(data.git?.branch || 'unknown')}</code> @ <code>${esc(data.git?.sha || 'unknown')}</code>.</p></section>
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


def _metric(value: object, label: str, tone: str = "") -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        f"<div class='panel'><span class='metric{tone_class}'>{html.escape(str(value))}</span>"
        f"<p>{html.escape(label)}</p></div>"
    )


def _section(section_id: str, title: str, body: str, intro: str = "") -> str:
    intro_html = f"<p>{html.escape(intro)}</p>" if intro else ""
    return (
        f"<section id='{section_id}' class='report-section'>"
        f"<h2>{html.escape(title)}</h2>{intro_html}{body}</section>"
    )


def _details_block(label_html: str, body: str, *, open_: bool = False) -> str:
    open_attr = " open" if open_ else ""
    return (
        f"<details class='block'{open_attr}><summary>{label_html}</summary>"
        f"<div class='block-body'>{body}</div></details>"
    )


def _table(header_cells: list[str], body_rows: str) -> str:
    head = "".join(f"<th>{html.escape(cell)}</th>" for cell in header_cells)
    return (
        "<div class='table-wrap'>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>"
        "</div>"
    )


def _command_rows(commands: list[dict]) -> str:
    rows = []
    for command in commands:
        status = html.escape(command["status"])
        rows.append(
            "<tr>"
            f"<td><span class='pill {_status_class(command['status'])}'>{status}</span></td>"
            f"<td>{html.escape(command['label'])}</td>"
            f"<td><code>{html.escape(' '.join(command['argv']))}</code></td>"
            f"<td>{command['duration_s']:.3f}s</td>"
            f"<td><code>{html.escape(command['log'])}</code></td>"
            "</tr>"
        )
    return "".join(rows)


def _suite_rows(unit: dict, e2e: dict) -> str:
    rows = []
    for name, suite in (("Unitaires", unit), ("E2E Chrome", e2e)):
        suite_status = (
            "ok"
            if suite["exists"] and suite["failures"] == 0 and suite["errors"] == 0
            else "failed"
        )
        rows.append(
            "<tr>"
            f"<td><span class='pill {_status_class(suite_status)}'>{suite_status}</span></td>"
            f"<td>{name}</td>"
            f"<td>{suite['tests']}</td><td>{suite['passed']}</td><td>{suite['failures']}</td>"
            f"<td>{suite['errors']}</td><td>{suite['skipped']}</td><td>{suite['time_s']:.3f}s</td>"
            f"<td><code>{html.escape(suite['path'])}</code></td>"
            "</tr>"
        )
    return "".join(rows)


def _focus_rows(unit: dict, e2e: dict) -> str:
    rows = []
    for suite_name, suite in (("Unitaires", unit), ("E2E Chrome", e2e)):
        for case in _case_focus(suite["cases"]):
            status = html.escape(case["status"])
            test_id = f"{html.escape(case['classname'])}::{html.escape(case['name'])}"
            rows.append(
                "<tr>"
                f"<td>{suite_name}</td>"
                f"<td><span class='pill {status}'>{status}</span></td>"
                f"<td><code>{test_id}</code></td>"
                f"<td>{case['time_s']:.3f}s</td>"
                f"<td>{html.escape(case['message'])}</td>"
                "</tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='5'>Aucun test détaillé disponible.</td></tr>")
    return "".join(rows)


def _evidence_rows(catalog: list[dict]) -> str:
    rows = []
    for item in catalog:
        path = (
            f"<code>{html.escape(item['path'])}</code>"
            if item["path"]
            else "<span class='muted'>-</span>"
        )
        status = html.escape(item["status"])
        rows.append(
            "<tr>"
            f"<td><span class='type'>{html.escape(item['type'])}</span></td>"
            f"<td>{html.escape(item['name'])}</td>"
            f"<td><span class='pill {status}'>{status}</span></td>"
            f"<td>{path}</td>"
            f"<td>{html.escape(item['roi'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _help_items(help_commands: list[dict[str, str]]) -> str:
    items = "".join(
        f"<li><code>{html.escape(command['name'])}</code> {html.escape(command['help'])}</li>"
        for command in help_commands
    )
    return items or "<li>Aucune commande extraite de l'aide CLI.</li>"


def _log_tail_sections() -> str:
    sections = []
    for label, path in (
        ("Ruff check", Path(".proof/ruff-check.log")),
        ("Ruff format", Path(".proof/ruff-format.log")),
        ("Pytest unitaires", UNIT_LOG),
        ("Pytest E2E Chrome", E2E_LOG),
    ):
        label_html = (
            f"{html.escape(label)} <span class='muted'>extrait final · "
            f"<code>{html.escape(str(path))}</code></span>"
        )
        sections.append(_details_block(label_html, f"<pre>{html.escape(_tail(path))}</pre>"))
    return "".join(sections)


def _entrypoint_inventory_rows(feature_inventory: dict) -> str:
    mapping = feature_inventory.get("feature_by_entrypoint", {})
    rows = []
    for entrypoint in feature_inventory.get("entrypoints", []):
        owner = mapping.get(entrypoint["id"], "")
        if owner:
            owner_html = (
                f"<a href='#feature-{html.escape(owner)}'><code>{html.escape(owner)}</code></a>"
            )
            pill = "<span class='pill ok'>rattaché</span>"
        else:
            owner_html = "<span class='muted'>-</span>"
            pill = "<span class='pill failed'>non rattaché</span>"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(entrypoint['id'])}</code></td>"
            f"<td>{html.escape(entrypoint['type'])}</td>"
            f"<td>{pill}</td>"
            f"<td>{owner_html}</td>"
            f"<td>{html.escape(entrypoint.get('label', ''))}</td>"
            "</tr>"
        )
    if not rows:
        return "<tr><td colspan='5'>Aucun entrypoint découvert.</td></tr>"
    return "".join(rows)


def _render_topbar(summary: dict) -> str:
    verdict = "OK" if summary["ok"] else "ECHEC"
    pill = "ok" if summary["ok"] else "failed"
    git_context = summary["git"]
    context = (
        f"{html.escape(git_context['branch'])} @ {html.escape(git_context['sha'])} · "
        f"{html.escape(summary['generated_at'])}"
    )
    return (
        "<div class='topbar'><div class='topbar-inner'>"
        "<strong>cdpx · cockpit de preuve</strong>"
        f"<span class='pill {pill}'>{verdict}</span>"
        f"<span class='muted'>{context}</span>"
        "<nav>"
        "<a href='#gaps'>Gaps</a>"
        "<a href='#features'>Features</a>"
        "<a href='#feature-details'>Dossiers</a>"
        "<a href='#scenarios'>Scénarios</a>"
        "<a href='#run'>Run</a>"
        "<a href='#project'>Projet</a>"
        "</nav>"
        "</div></div>"
    )


def _render_hero(summary: dict, scenario_failures: list[str]) -> str:
    totals = summary["totals"]
    feature_inventory = summary["feature_inventory"]
    feature_totals = feature_inventory["totals"]
    status_counts = _feature_status_counts(feature_inventory)
    scenario_totals = summary["scenario_totals"]
    gap_signals = feature_totals["violations"] + feature_totals["warnings"] + len(scenario_failures)
    entrypoint_ratio = f"{feature_totals['mapped_entrypoints']}/{feature_totals['entrypoints']}"
    all_mapped = feature_totals["mapped_entrypoints"] == feature_totals["entrypoints"]
    metrics = [
        _metric(
            "OK" if summary["ok"] else "ECHEC",
            "Verdict global",
            "ok-text" if summary["ok"] else "bad-text",
        ),
        _metric(
            f"{totals['passed']}/{totals['tests']}",
            "Tests passés",
            "ok-text" if not totals["failed"] else "bad-text",
        ),
        _metric(
            f"{status_counts['ok']}/{feature_totals['features']}",
            "Features sans gap",
            "ok-text" if status_counts["ok"] == feature_totals["features"] else "warn-text",
        ),
        _metric(
            entrypoint_ratio,
            "Entrypoints rattachés",
            "ok-text" if all_mapped else "bad-text",
        ),
        _metric(feature_totals["scenarios"], "Scénarios reliés"),
        _metric(scenario_totals["screenshots"], "Screenshots"),
        _metric(
            gap_signals,
            "Gaps signalés",
            "ok-text" if not gap_signals else "warn-text",
        ),
    ]
    return "".join(metrics)


def _render_gaps_section(feature_inventory: dict, scenario_failures: list[str]) -> str:
    body = _table(
        ["Type", "Signal", "Action suggérée"],
        _gap_rows(feature_inventory, scenario_failures),
    )
    intro = (
        "Toute surface non rattachée à une feature est une violation bloquante pour "
        "`make proof`; les warnings ne bloquent pas le verdict mais restent à traiter."
    )
    return _section("gaps", "Gaps et violations", body, intro)


def _render_features_section(feature_inventory: dict) -> str:
    body = (
        "<div class='toolbar'>"
        "<input id='featureSearch' type='search' "
        "placeholder='Filtrer: feature, commande, domaine'>"
        "<select id='featureStatus'>"
        "<option value=''>Tous statuts</option>"
        "<option value='ok'>OK</option>"
        "<option value='warning'>Warning</option>"
        "<option value='failed'>Failed</option>"
        "</select>"
        "<span id='featureCount' class='muted'></span>"
        "</div>"
        f"<div class='feature-board' id='featureBoard'>{_feature_cards(feature_inventory)}</div>"
    )
    intro = (
        "Statut dérivé des preuves du run: failed si un scénario relié échoue, "
        "warning si la feature a des gaps, ok sinon."
    )
    return _section("features", "Features", body, intro)


def _render_feature_details_section(feature_inventory: dict) -> str:
    toolbar = (
        "<div class='toolbar'>"
        "<button id='expandFeatures' type='button'>Tout déplier</button>"
        "<button id='collapseFeatures' type='button'>Tout replier</button>"
        "</div>"
    )
    body = toolbar + _feature_detail_sections(feature_inventory)
    intro = (
        "Dossier de preuve par feature: journeys, entrypoints, docs, code, tests et "
        "preuves collectées. Les features avec gaps ou échecs sont dépliées d'office."
    )
    return _section(
        "feature-details", "Dossiers feature — docs > code > tests > preuves", body, intro
    )


def _render_scenarios_section(scenario_evidence: dict) -> str:
    headers = [
        "Statut",
        "Scénario",
        "Preuve",
        "Durée",
        "Screenshots",
        "Artefacts",
    ]
    blocks = []
    for suite_id, label, open_ in (
        ("e2e", "E2E Chrome", True),
        ("integration", "Intégration", False),
        ("unit", "Unitaires", False),
    ):
        scenarios = scenario_evidence["suites"].get(suite_id, [])
        counts = _scenario_status_counts(scenarios)
        meta = (
            f"{len(scenarios)} scénarios · {counts['failed']} failed · {counts['skipped']} skipped"
        )
        label_html = f"{html.escape(label)} <span class='muted'>({meta})</span>"
        blocks.append(
            _details_block(label_html, _table(headers, _scenario_rows(scenarios)), open_=open_)
        )
    intro = (
        "Chaque scénario e2e Chrome non skippé doit fournir au moins un screenshot. "
        "Les suites unitaires et intégration partagent le même format de preuve sans "
        "obligation de capture visuelle."
    )
    return _section("scenarios", "Scénarios prouvés", body="".join(blocks), intro=intro)


def _render_run_section(summary: dict, unit: dict, e2e: dict) -> str:
    commands_table = _table(
        ["Statut", "Preuve", "Commande", "Durée", "Log"],
        _command_rows(summary["commands"]),
    )
    suites_table = _table(
        [
            "Statut",
            "Suite",
            "Total",
            "Passés",
            "Failures",
            "Errors",
            "Skips",
            "Durée",
            "Source",
        ],
        _suite_rows(unit, e2e),
    )
    catalog = summary["evidence_catalog"]
    evidence_block = _details_block(
        f"Catalogue des preuves <span class='muted'>({len(catalog)} artefacts)</span>",
        _table(["Type", "Nom", "Statut", "Artefact", "ROI review"], _evidence_rows(catalog)),
    )
    body = (
        "<h3>Commandes exécutées</h3>"
        + commands_table
        + "<h3>Suites de tests</h3>"
        + suites_table
        + evidence_block
        + _log_tail_sections()
    )
    intro = (
        "Preuves brutes du run: chaque commande du portail, ses logs, les JUnit et le "
        "catalogue complet des artefacts générés sous .proof/."
    )
    return _section("run", "Preuves du run", body, intro)


def _render_project_section(
    summary: dict, unit: dict, e2e: dict, help_commands: list[dict[str, str]]
) -> str:
    project = summary["project"]
    git_context = summary["git"]
    feature_inventory = summary["feature_inventory"]
    feature_totals = feature_inventory["totals"]
    mission = (
        "<div class='callout'>"
        f"<p><strong>Mission.</strong> {html.escape(project['mission'])}</p>"
        "<p><strong>Version prouvée.</strong> "
        f"<code>{html.escape(project['version'])}</code>, branche "
        f"<code>{html.escape(git_context['branch'])}</code> @ "
        f"<code>{html.escape(git_context['sha'])}</code>.</p>"
        "<p><strong>Portée du run.</strong> Lint, format, unitaires, e2e Chrome, aide CLI, "
        "JUnit XML, logs, screenshots e2e et inventaire feature.</p>"
        "<p><strong>Hors run automatique.</strong> Docker Symfony e2e et preuves visuelles "
        "persistantes restent des portails ou artefacts optionnels.</p>"
        "</div>"
    )
    entrypoint_ratio = f"{feature_totals['mapped_entrypoints']}/{feature_totals['entrypoints']}"
    fixtures_body = (
        _table(["Type", "Nombre"], _simple_kv_rows(project["fixture_kinds"]))
        + f"<ul class='compact'>{_path_items(project['fixtures'])}</ul>"
    )
    docs_body = (
        "<div class='two'>"
        "<div><strong>Documentation de référence</strong>"
        f"<ul class='compact'>{_path_items(project['docs'])}</ul></div>"
        "<div><strong>Docs milestones</strong>"
        f"<ul class='compact'>{_path_items(project['milestone_docs'])}</ul></div>"
        "</div>"
    )
    failed_tests = summary["totals"]["failed"]
    blocks = [
        _details_block(
            f"Surface CLI <span class='muted'>({len(help_commands)} commandes)</span>",
            f"<ul class='commands'>{_help_items(help_commands)}</ul>",
        ),
        _details_block(
            f"Entrypoints découverts <span class='muted'>({entrypoint_ratio} rattachés)</span>",
            _table(
                ["Entrypoint", "Type", "Rattachement", "Feature", "Description"],
                _entrypoint_inventory_rows(feature_inventory),
            ),
        ),
        _details_block(
            f"Fixtures locales <span class='muted'>({project['fixture_count']} fichiers)</span>",
            fixtures_body,
        ),
        _details_block(
            "Matrice de validation <span class='muted'>(milestones)</span>",
            _table(["Milestone", "Preuve attendue"], _milestone_rows(summary["validation_matrix"])),
        ),
        _details_block("Documentation et milestones", docs_body),
        _details_block(
            "Couverture par module de test",
            _table(
                ["Module", "Suite", "Tests", "Échecs", "Skips"],
                _coverage_rows(summary["coverage_groups"]),
            ),
        ),
        _details_block(
            "Tests à inspecter <span class='muted'>(échecs ou plus lents)</span>",
            _table(["Suite", "Statut", "Test", "Durée", "Message"], _focus_rows(unit, e2e)),
            open_=failed_tests > 0,
        ),
        _details_block(
            "Risques projet",
            _table(["Risque", "Mitigation", "Rollback"], _risk_rows(summary["risks"])),
        ),
        _details_block(
            "Limites connues",
            _table(
                ["Zone non vérifiée", "Pourquoi", "Comment vérifier"],
                _unknown_rows(summary["unknowns"]),
            ),
        ),
    ]
    return _section("project", "Contexte projet", mission + "".join(blocks))


def _json_for_html_script(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_html(summary: dict, unit: dict, e2e: dict, help_commands: list[dict[str, str]]) -> str:
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
    *,
    git_context: dict | None = None,
    help_commands: list[dict[str, str]] | None = None,
    scenario_evidence: dict | None = None,
) -> dict:
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
    coverage_groups = group_cases_by_module(unit, e2e)
    scenario_evidence = scenario_evidence or load_scenario_evidence()
    feature_inventory = build_feature_inventory(help_commands, scenario_evidence, git_context)
    scenario_evidence = enrich_scenario_evidence(scenario_evidence, feature_inventory)
    scenario_failures = proof_failures_from_scenarios(scenario_evidence)
    feature_inventory_failures = feature_failures(feature_inventory)
    risk_packet = build_project_risks_and_unknowns()
    failed_tests = unit["failures"] + unit["errors"] + e2e["failures"] + e2e["errors"]
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
        and not feature_inventory_failures
    )
    summary = {
        "ok": ok,
        "generated_at": _now(),
        "artifact_dir": str(PROOF_DIR),
        "report_html": str(REPORT_HTML),
        "unit_log": str(UNIT_LOG),
        "e2e_log": str(E2E_LOG),
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
        "commands": [asdict(command) for command in commands],
        "junit": {"unit": _suite_for_summary(unit), "e2e": _suite_for_summary(e2e)},
        "totals": {
            "tests": unit["tests"] + e2e["tests"],
            "passed": unit["passed"] + e2e["passed"],
            "skipped": unit["skipped"] + e2e["skipped"],
            "failed": failed_tests,
        },
        "git": git_context,
        "project": project,
        "validation_matrix": validation_matrix,
        "coverage_groups": coverage_groups,
        "scenario_evidence": scenario_evidence,
        "scenario_totals": scenario_evidence["totals"],
        "feature_inventory": feature_inventory,
        "proof_failures": scenario_failures + feature_inventory_failures,
        "risks": risk_packet["risks"],
        "unknowns": risk_packet["unknowns"],
    }
    summary["evidence_catalog"] = build_evidence_catalog(summary, unit, e2e)
    return summary


def generate() -> dict:
    if PROOF_DIR.exists():
        shutil.rmtree(PROOF_DIR)
    PROOF_DIR.mkdir(parents=True)
    env = _repo_env()
    unit_xml = PROOF_DIR / "unit-junit.xml"
    e2e_xml = PROOF_DIR / "e2e-junit.xml"

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
                "tests/e2e",
                "-v",
                f"--cdpx-evidence-dir={EVIDENCE_DIR}",
                f"--junitxml={e2e_xml}",
            ],
            E2E_LOG,
            env=env,
        ),
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
    help_commands = parse_help_commands(CLI_HELP.read_text(encoding="utf-8", errors="replace"))
    git_context = collect_git_context()
    summary = build_summary(
        commands, unit, e2e, git_context=git_context, help_commands=help_commands
    )
    summary["cli_commands"] = [command["name"] for command in help_commands]
    summary["cli_command_count"] = len(help_commands)
    write_scenario_evidence(EVIDENCE_DIR, summary["scenario_evidence"])
    SUMMARY_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_HTML.write_text(render_html(summary, unit, e2e, help_commands), encoding="utf-8")
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
