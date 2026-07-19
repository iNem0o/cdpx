"""Public copy uses one language and describes only the current product."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(".")

ROOT_MARKDOWN = {
    Path("AGENTS.md"),
    Path("CHANGELOG.md"),
    Path("CLAUDE.md"),
    Path("CODE_OF_CONDUCT.md"),
    Path("CONTRIBUTING.md"),
    Path("HARNESS.md"),
    Path("README.md"),
    Path("SECURITY.md"),
    Path("SUPPORT.md"),
}

EXPLICIT_SURFACES = {
    Path("Makefile"),
    Path("pyproject.toml"),
    Path("site/index.html"),
    Path("tests/fixtures/profiler/README.md"),
}

FRENCH_PROSE = re.compile(
    r"[àâäéèêëîïôöùûüÿçœÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇŒ]"
    r"|\b(?:accueil|artefact|branche|brute|commande|contexte|famille|fermer|"
    r"fichier|filtrer|fonctionnalités|introuvable|nom|octets|panier|parcours|"
    r"preuve|produit|projet|recette|statut|taille|touche|visualiseur|vue)\b",
    re.IGNORECASE,
)


def public_surfaces() -> list[Path]:
    paths = set(ROOT_MARKDOWN) | set(EXPLICIT_SURFACES)
    paths.update(Path("docs").rglob("*.md"))
    paths.update(Path(".github").rglob("*.md"))
    paths.update(Path(".github").rglob("*.yml"))
    paths.update(Path("src/cdpx/proofing/cockpit").rglob("*"))
    paths.update(Path("scripts/site_casts").rglob("*.py"))
    paths.update(Path("site/assets/casts").glob("*.md"))
    paths.update(Path("site/assets/casts").glob("*.cast"))
    return sorted(path for path in paths if path.is_file())


def test_public_surfaces_are_english():
    violations: list[str] = []
    for path in public_surfaces():
        text = path.read_text(encoding="utf-8").replace(".prototype", "")
        for line_number, line in enumerate(text.splitlines(), 1):
            if FRENCH_PROSE.search(line):
                violations.append(f"{path}:{line_number}: {line.strip()}")

    assert violations == [], "public-copy violations:\n" + "\n".join(violations)
