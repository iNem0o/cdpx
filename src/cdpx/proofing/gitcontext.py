"""Contexte git du run de preuve et packs de revue dérivés.

``collect_git_context`` reçoit en keyword-only ce que la façade `cdpx.proof`
laisse monkeypatcher (``run_text``) ou dérive de ses constantes patchables
(chemins, excludes): aucun symbole de ce module ne lit `cdpx.proof` à
l'exécution.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from cdpx.proofing.private_io import _write_private_text
from cdpx.security.redaction import (
    RedactionContext,
    redact_text,
)
from cdpx.testing.evidence import redaction_context_from_environment

GENERATED_PREFIXES = (".proof/", ".idea/")
PRIVATE_WORKTREE_PREFIXES = ("AGENTS.md", "article/", "presentation/")

RunText = Callable[..., tuple[int, str]]


def collect_git_context(
    *,
    redaction_context: RedactionContext | None = None,
    status_path: Path,
    diff_stat_path: Path,
    run_text: RunText,
    timeout: float,
    diff_excludes: Sequence[str],
) -> dict:
    context = redaction_context or redaction_context_from_environment()
    branch_code, branch = run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], timeout)
    sha_code, sha = run_text(["git", "rev-parse", "--short", "HEAD"], timeout)
    status_code, status = run_text(["git", "status", "--short"], timeout)
    stat_code, stat = run_text(
        ["git", "diff", "--stat", "--", ".", *diff_excludes],
        timeout,
    )

    # Une sortie git en échec (timeout 124, dépôt cassé) n'est pas du
    # porcelain: sortie partielle et annotation de timeout produiraient des
    # entrées corrompues. On ne parse ni ne publie rien — le status_code et
    # le diff_stat_code déjà exposés au summary suffisent au diagnostic.
    if status_code != 0:
        status = ""
    if stat_code != 0:
        stat = ""
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
