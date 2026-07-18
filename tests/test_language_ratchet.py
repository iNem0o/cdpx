"""Ratchet de langue: la migration vers l'anglais ne recule jamais.

Le baseline commité (scripts/language_ratchet_baseline.json) est exact:
toute dérive — hausse comme baisse — exige de le régénérer dans le même
commit (`python3 scripts/language_ratchet.py --write-baseline`), pour que
chaque traduction soit verrouillée et chaque régression bruyante.
"""

from pathlib import Path

import pytest
from scripts.language_ratchet import (
    AREAS,
    REPO_ROOT,
    area_files,
    french_line_count,
    load_baseline,
    measure,
)


def test_french_line_count_detects_accents_and_plain_french_words(tmp_path):
    """Le compteur voit le français accentué comme non accentué, et ignore
    l'anglais ainsi que les lignes purement techniques."""

    sample = tmp_path / "sample.md"
    sample.write_text(
        "\n".join(
            [
                "The proof pipeline is deterministic.",
                "évènement réseau surveillé",
                "chaque portail reste vert",
                "the loop never sleeps unbounded",
                "aucun secret ne sort en clair",
                "make check && cdpx tabs list",
            ]
        ),
        encoding="utf-8",
    )

    #: trois lignes françaises: une par accents, deux par mots non
    #: accentués à haute précision («chaque», «aucun»)
    assert french_line_count(sample) == 3


@pytest.mark.skipif(
    not Path(REPO_ROOT, ".git").exists(),
    reason="arbre partiel (image Docker sans site/ ni AGENTS.md): le ratchet "
    "ne se mesure que sur un checkout complet",
)
def test_language_baseline_matches_current_measurement():
    """Le baseline commité égale la mesure courante, zone par zone — sur un
    checkout complet uniquement: l'image Docker copie un arbre volontairement
    partiel (.dockerignore) où la mesure serait fausse."""

    current = measure(REPO_ROOT)
    baseline = load_baseline()

    #: toute différence produit un message actionnable: régénérer le
    #: baseline dans le commit qui change la quantité de français
    assert current == baseline, (
        "la mesure de français a dérivé du baseline; si c'est volontaire: "
        "python3 scripts/language_ratchet.py --write-baseline"
    )


def test_migration_tooling_is_excluded_from_the_scan():
    """Les fichiers outils de la migration (glossaire, ratchet) ne polluent
    pas la mesure: sans cette exclusion, le zéro serait inatteignable."""

    glossary = Path(REPO_ROOT, "docs", "GLOSSARY.md")

    #: le glossaire FR→EN contient du français par construction…
    assert french_line_count(glossary) > 0
    #: …mais n'est balayé dans aucune zone, pas plus que le ratchet lui-même
    assert glossary not in area_files(REPO_ROOT, AREAS["docs"])
    ratchet_test = Path(REPO_ROOT, "tests", "test_language_ratchet.py")
    assert ratchet_test not in area_files(REPO_ROOT, AREAS["tests"])
