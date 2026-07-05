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
        return "<tr><td colspan='7'>Aucun scénario collecté.</td></tr>"
    rows = []
    for scenario in sorted(scenarios, key=lambda item: item["nodeid"]):
        proves = scenario.get("proves") or []
        proves_text = "<br>".join(html.escape(item) for item in proves) or html.escape(
            scenario.get("title", "")
        )
        status = html.escape(scenario.get("status", "unknown"))
        message = html.escape(scenario.get("message", ""))
        rows.append(
            "<tr>"
            f"<td><span class='pill {status}'>{status}</span></td>"
            f"<td><code>{html.escape(scenario['nodeid'])}</code></td>"
            f"<td>{html.escape(scenario.get('title', ''))}</td>"
            f"<td>{proves_text}</td>"
            f"<td>{scenario.get('duration_s', 0):.3f}s</td>"
            f"<td>{_screenshot_thumbs(scenario)}</td>"
            f"<td>{_artifact_links(scenario)}<div class='muted'>{message}</div></td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(summary: dict, unit: dict, e2e: dict, help_commands: list[dict[str, str]]) -> str:
    command_rows = []
    for command in summary["commands"]:
        status = html.escape(command["status"])
        status_class = _status_class(command["status"])
        command_rows.append(
            "<tr>"
            f"<td><span class='pill {status_class}'>{status}</span></td>"
            f"<td>{html.escape(command['label'])}</td>"
            f"<td><code>{html.escape(' '.join(command['argv']))}</code></td>"
            f"<td>{command['duration_s']:.3f}s</td>"
            f"<td><code>{html.escape(command['log'])}</code></td>"
            "</tr>"
        )

    suite_rows = []
    for name, suite in (("Unitaires", unit), ("E2E Chrome", e2e)):
        suite_status = (
            "ok"
            if suite["exists"] and suite["failures"] == 0 and suite["errors"] == 0
            else "failed"
        )
        suite_rows.append(
            "<tr>"
            f"<td><span class='pill {_status_class(suite_status)}'>{suite_status}</span></td>"
            f"<td>{name}</td>"
            f"<td>{suite['tests']}</td><td>{suite['passed']}</td><td>{suite['failures']}</td>"
            f"<td>{suite['errors']}</td><td>{suite['skipped']}</td><td>{suite['time_s']:.3f}s</td>"
            f"<td><code>{html.escape(suite['path'])}</code></td>"
            "</tr>"
        )

    focus_rows = []
    for suite_name, suite in (("Unitaires", unit), ("E2E Chrome", e2e)):
        for case in _case_focus(suite["cases"]):
            status = html.escape(case["status"])
            test_id = f"{html.escape(case['classname'])}::{html.escape(case['name'])}"
            focus_rows.append(
                "<tr>"
                f"<td>{suite_name}</td>"
                f"<td><span class='pill {status}'>{status}</span></td>"
                f"<td><code>{test_id}</code></td>"
                f"<td>{case['time_s']:.3f}s</td>"
                f"<td>{html.escape(case['message'])}</td>"
                "</tr>"
            )
    if not focus_rows:
        focus_rows.append("<tr><td colspan='5'>Aucun test detaille disponible.</td></tr>")

    evidence_rows = []
    for item in summary["evidence_catalog"]:
        path = (
            f"<code>{html.escape(item['path'])}</code>"
            if item["path"]
            else "<span class='muted'>-</span>"
        )
        status = html.escape(item["status"])
        evidence_rows.append(
            "<tr>"
            f"<td><span class='type'>{html.escape(item['type'])}</span></td>"
            f"<td>{html.escape(item['name'])}</td>"
            f"<td><span class='pill {status}'>{status}</span></td>"
            f"<td>{path}</td>"
            f"<td>{html.escape(item['roi'])}</td>"
            "</tr>"
        )

    help_items = "\n".join(
        f"<li><code>{html.escape(command['name'])}</code> {html.escape(command['help'])}</li>"
        for command in help_commands
    )
    if not help_items:
        help_items = "<li>Aucune commande extraite de l'aide CLI.</li>"

    log_sections = []
    for label, path in (
        ("Ruff check", Path(".proof/ruff-check.log")),
        ("Ruff format", Path(".proof/ruff-format.log")),
        ("Pytest unitaires", UNIT_LOG),
        ("Pytest E2E Chrome", E2E_LOG),
    ):
        log_sections.append(
            f"<details><summary>{html.escape(label)} - extrait final</summary>"
            f"<pre>{html.escape(_tail(path))}</pre></details>"
        )

    verdict = "OK" if summary["ok"] else "ECHEC"
    generated = html.escape(summary["generated_at"])
    total_tests = summary["totals"]["tests"]
    cli_count = len(help_commands)
    git_context = summary["git"]
    branch = html.escape(git_context["branch"])
    sha = html.escape(git_context["sha"])
    project = summary["project"]
    validation_matrix = summary["validation_matrix"]
    coverage_groups = summary["coverage_groups"]
    scenario_evidence = summary["scenario_evidence"]
    scenario_totals = summary["scenario_totals"]
    risks = summary["risks"]
    unknowns = summary["unknowns"]
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rapport de preuve cdpx - {verdict}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #18202a;
      --muted: #5d6673;
      --line: #d9dee7;
      --ok: #167044;
      --bad: #b42318;
      --info: #1d4ed8;
      --skip: #7a5d00;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header, main {{ max-width: 1160px; margin: 0 auto; padding: 24px; }}
    header {{ padding-top: 34px; }}
    h1, h2 {{ margin: 0 0 12px; line-height: 1.15; }}
    h1 {{ font-size: 34px; }}
    h2 {{ font-size: 22px; margin-top: 28px; }}
    h3 {{ font-size: 16px; margin: 18px 0 8px; }}
    p {{ margin: 0 0 12px; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
    .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .metric {{ display: block; font-size: 30px; font-weight: 700; color: var(--ink); }}
    .muted {{ color: var(--muted); }}
    .callout {{
      border-left: 4px solid var(--info);
      background: #eef5ff;
      padding: 14px 16px;
      margin: 12px 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{ font-size: 12px; text-transform: uppercase; color: var(--muted); letter-spacing: .04em; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    code {{ font-size: 13px; }}
    pre {{
      overflow: auto;
      white-space: pre-wrap;
      background: #10151f;
      color: #edf2f7;
      padding: 14px;
      border-radius: 6px;
    }}
    .pill {{
      display: inline-block;
      min-width: 64px;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      text-align: center;
    }}
    .ok, .passed {{ color: #fff; background: var(--ok); }}
    .failed, .error {{ color: #fff; background: var(--bad); }}
    .skipped {{ color: #1c1600; background: #f6d365; }}
    .generated, .optional, .not-needed {{ color: #17324d; background: #dbeafe; }}
    .type {{
      display: inline-block;
      padding: 2px 7px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #f8fafc;
      font-size: 12px;
      font-weight: 700;
    }}
    .shot {{
      display: inline-flex;
      flex-direction: column;
      gap: 4px;
      width: 170px;
      margin: 0 8px 8px 0;
      color: var(--ink);
      text-decoration: none;
    }}
    .shot img {{
      width: 170px;
      height: 96px;
      object-fit: cover;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
    }}
    .shot span {{ font-size: 12px; color: var(--muted); }}
    details {{ margin: 10px 0; }}
    summary {{ cursor: pointer; font-weight: 700; }}
    ul.commands {{ columns: 2; padding-left: 20px; }}
    li {{ break-inside: avoid; margin-bottom: 6px; }}
    @media (max-width: 820px) {{
      .grid, .two {{ grid-template-columns: 1fr; }}
      table {{ display: block; overflow-x: auto; }}
      ul.commands {{ columns: 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Rapport de preuve projet cdpx</h1>
    <p>
      Genere le {generated}. Ce rapport est reconstruit a chaque `make proof`
      pour prouver l'etat global du projet: surface CLI, fixtures, milestones,
      tests, logs, artefacts et limites connues.
    </p>
    <div class="grid">
      <div class="panel"><span class="metric">{verdict}</span><p>Verdict global</p></div>
      <div class="panel"><span class="metric">{total_tests}</span><p>Tests collectes</p></div>
      <div class="panel">
        <span class="metric">{scenario_totals["e2e"]}</span><p>Scenarios e2e</p>
      </div>
      <div class="panel">
        <span class="metric">{scenario_totals["screenshots"]}</span><p>Screenshots</p>
      </div>
    </div>
  </header>
  <main>
    <h2>Vue d'ensemble projet</h2>
    <div class="callout">
      <p>
        <strong>Mission.</strong> {html.escape(project["mission"])}
      </p>
      <p>
        <strong>Version prouvee.</strong> <code>{html.escape(project["version"])}</code>,
        branche <code>{branch}</code> @ <code>{sha}</code>.
      </p>
      <p>
        <strong>Portee du run.</strong> Lint, format, unitaires, e2e Chrome,
        aide CLI, JUnit XML, logs, screenshots e2e et inventaire projet.
      </p>
      <p>
        <strong>Hors run automatique.</strong> Docker Symfony e2e et preuves
        visuelles persistantes restent des portails ou artefacts optionnels.
      </p>
    </div>

    <h2>Surface prouvee</h2>
    <div class="two">
      <div class="panel">
        <strong>Primitives CLI</strong>
        <p><span class="metric">{cli_count}</span></p>
        <ul class="commands">{help_items}</ul>
      </div>
      <div class="panel">
        <strong>Fixtures locales</strong>
        <p><span class="metric">{project["fixture_count"]}</span></p>
        <table>
          <thead><tr><th>Type</th><th>Nombre</th></tr></thead>
          <tbody>{_simple_kv_rows(project["fixture_kinds"])}</tbody>
        </table>
      </div>
    </div>

    <h2>Matrice de validation</h2>
    <table>
      <thead><tr><th>Milestone</th><th>Preuve attendue</th></tr></thead>
      <tbody>{_milestone_rows(validation_matrix)}</tbody>
    </table>

    <h2>Inventaire projet</h2>
    <div class="two">
      <div class="panel">
        <strong>Documentation de référence</strong>
        <ul>{_path_items(project["docs"])}</ul>
      </div>
      <div class="panel">
        <strong>Docs milestones</strong>
        <ul>{_path_items(project["milestone_docs"])}</ul>
      </div>
    </div>
    <details>
      <summary>Fixtures détaillées</summary>
      <ul>{_path_items(project["fixtures"])}</ul>
    </details>

    <h2>Catalogue des preuves</h2>
    <table>
      <thead>
        <tr><th>Type</th><th>Nom</th><th>Statut</th><th>Artefact</th><th>ROI review</th></tr>
      </thead>
      <tbody>{"".join(evidence_rows)}</tbody>
    </table>

    <h2>Commandes executees</h2>
    <table>
      <thead><tr><th>Statut</th><th>Preuve</th><th>Commande</th><th>Duree</th><th>Log</th></tr></thead>
      <tbody>{"".join(command_rows)}</tbody>
    </table>

    <h2>Suites de tests</h2>
    <table>
      <thead><tr><th>Statut</th><th>Suite</th><th>Total</th><th>Passes</th><th>Failures</th><th>Errors</th><th>Skips</th><th>Duree</th><th>Source</th></tr></thead>
      <tbody>{"".join(suite_rows)}</tbody>
    </table>

    <h2>Scenarios prouves</h2>
    <p>
      Chaque scenario e2e Chrome non skippe doit avoir au moins un screenshot.
      Les tests unitaires et integration reutilisent le meme format de preuve,
      sans obligation de capture visuelle.
    </p>
    <details open>
      <summary>E2E Chrome</summary>
      <table>
        <thead><tr><th>Statut</th><th>Scenario</th><th>Titre</th><th>Preuve</th><th>Duree</th><th>Screenshots</th><th>Artefacts</th></tr></thead>
        <tbody>{_scenario_rows(scenario_evidence["suites"].get("e2e", []))}</tbody>
      </table>
    </details>
    <details>
      <summary>Integration</summary>
      <table>
        <thead><tr><th>Statut</th><th>Scenario</th><th>Titre</th><th>Preuve</th><th>Duree</th><th>Screenshots</th><th>Artefacts</th></tr></thead>
        <tbody>{_scenario_rows(scenario_evidence["suites"].get("integration", []))}</tbody>
      </table>
    </details>
    <details>
      <summary>Unitaires</summary>
      <table>
        <thead><tr><th>Statut</th><th>Scenario</th><th>Titre</th><th>Preuve</th><th>Duree</th><th>Screenshots</th><th>Artefacts</th></tr></thead>
        <tbody>{_scenario_rows(scenario_evidence["suites"].get("unit", []))}</tbody>
      </table>
    </details>

    <h2>Couverture par module de test</h2>
    <table>
      <thead><tr><th>Module</th><th>Suite</th><th>Tests</th><th>Echecs</th><th>Skips</th></tr></thead>
      <tbody>{_coverage_rows(coverage_groups)}</tbody>
    </table>

    <h2>Tests a inspecter</h2>
    <table>
      <thead><tr><th>Suite</th><th>Statut</th><th>Test</th><th>Duree</th><th>Message</th></tr></thead>
      <tbody>{"".join(focus_rows)}</tbody>
    </table>

    <h2>Risques projet</h2>
    <table>
      <thead><tr><th>Risque</th><th>Mitigation</th><th>Rollback</th></tr></thead>
      <tbody>{_risk_rows(risks)}</tbody>
    </table>

    <h2>Limites connues</h2>
    <table>
      <thead><tr><th>Zone non verifiee</th><th>Pourquoi</th><th>Comment verifier</th></tr></thead>
      <tbody>{_unknown_rows(unknowns)}</tbody>
    </table>

    <h2>Extraits de logs</h2>
    {"".join(log_sections)}
  </main>
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
    scenario_failures = proof_failures_from_scenarios(scenario_evidence)
    risk_packet = build_project_risks_and_unknowns()
    failed_tests = unit["failures"] + unit["errors"] + e2e["failures"] + e2e["errors"]
    ok = (
        all(command.exit_code == 0 for command in commands)
        and failed_tests == 0
        and not scenario_failures
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
        "proof_failures": scenario_failures,
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
