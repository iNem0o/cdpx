"""Language ratchet: the migration to English never regresses.

The committed baseline (scripts/language_ratchet_baseline.json) is exact:
any drift — increase as well as decrease — requires regenerating it in the
same commit (`python3 scripts/language_ratchet.py --write-baseline`), so
that every translation is locked in and every regression is noisy.
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
    """The counter sees accented as well as unaccented French, and ignores
    English as well as purely technical lines."""

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

    #: three French lines: one by accents, two by high-precision
    #: unaccented words ("chaque", "aucun")
    assert french_line_count(sample) == 3


@pytest.mark.skipif(
    not Path(REPO_ROOT, ".git").exists(),
    reason="arbre partiel (image Docker sans site/ ni AGENTS.md): le ratchet "
    "ne se mesure que sur un checkout complet",
)
def test_language_baseline_matches_current_measurement():
    """The committed baseline equals the current measurement, area by
    area — on a complete checkout only: the Docker image copies a
    deliberately partial tree (.dockerignore) where the measurement would
    be wrong."""

    current = measure(REPO_ROOT)
    baseline = load_baseline()

    #: any difference produces an actionable message: regenerate the
    #: baseline in the commit that changes the amount of French
    assert current == baseline, (
        "la mesure de français a dérivé du baseline; si c'est volontaire: "
        "python3 scripts/language_ratchet.py --write-baseline"
    )


def test_migration_tooling_is_excluded_from_the_scan():
    """The migration tooling files (glossary, ratchet) do not pollute the
    measurement: without this exclusion, zero would be unreachable."""

    glossary = Path(REPO_ROOT, "docs", "GLOSSARY.md")

    #: the FR→EN glossary contains French by construction…
    assert french_line_count(glossary) > 0
    #: …but is scanned in no area, no more than the ratchet itself
    assert glossary not in area_files(REPO_ROOT, AREAS["docs"])
    ratchet_test = Path(REPO_ROOT, "tests", "test_language_ratchet.py")
    assert ratchet_test not in area_files(REPO_ROOT, AREAS["tests"])
