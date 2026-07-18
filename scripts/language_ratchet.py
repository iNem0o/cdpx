#!/usr/bin/env python3
"""Count French-language lines per repository area and enforce a ratchet.

The migration to English (docs/GLOSSARY.md) needs an exact gauge: this
script counts lines that carry French markers (accented characters or
high-precision French words) grouped by area. The committed baseline is a
ratchet: any drift — up or down — must be acknowledged by regenerating the
baseline in the same commit, so progress is locked in and regressions are
loud.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).with_name("language_ratchet_baseline.json")

ACCENTED = re.compile("[àâäéèêëîïôöùûüÿçœÀÂÄÉÈÊËÎÏÔÖÙÛÜŸÇŒ]")
# Unaccented words kept deliberately short and high-precision: each one is
# common in this repository's French prose and absent from English text and
# from identifiers.
FRENCH_WORDS = re.compile(
    r"\b(?:aucun|aucune|jamais|toujours|chaque|pourquoi|fichier|fichiers"
    r"|navigateur|sinon|ainsi|donc|depuis|pendant|plusieurs|doit|doivent)\b"
)

# Scanned areas: repo-relative glob patterns per group. Generated artifacts
# (casts, .proof) are excluded: they are re-recorded, never translated.
AREAS: dict[str, tuple[str, ...]] = {
    "root-docs": ("*.md",),
    "docs": ("docs/**/*.md",),
    "src": ("src/**/*.py",),
    "tests": ("tests/**/*.py",),
    "fixtures": ("tests/fixtures/**/*.html", "tests/symfony-app/**/*.php"),
    "scripts": ("scripts/**/*.py",),
    "meta": (
        "Makefile",
        "Dockerfile",
        "docker-compose*.yml",
        "pyproject.toml",
        ".github/**/*.yml",
        ".github/**/*.md",
    ),
    "site": ("site/index.html", "site/**/*.md"),
}

# The migration tooling itself legitimately contains French words (the word
# list above, the FR->EN glossary): scanning it would leave a permanent
# false-positive floor above zero.
EXCLUDED = {
    "docs/GLOSSARY.md",
    "scripts/language_ratchet.py",
    "tests/test_language_ratchet.py",
}


def french_line_count(path: Path) -> int:
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(
        1 for line in text.splitlines() if ACCENTED.search(line) or FRENCH_WORDS.search(line)
    )


def area_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            relative = path.relative_to(root).as_posix()
            if path.is_file() and relative not in EXCLUDED:
                seen.add(path)
    return sorted(seen)


def measure(root: Path) -> dict[str, dict[str, int]]:
    report: dict[str, dict[str, int]] = {}
    for area, patterns in AREAS.items():
        files_with_french = 0
        lines = 0
        for path in area_files(root, patterns):
            count = french_line_count(path)
            if count:
                files_with_french += 1
                lines += count
        report[area] = {"files": files_with_french, "lines": lines}
    return report


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="persist the current measurement as the new ratchet baseline",
    )
    args = parser.parse_args(argv)
    report = measure(REPO_ROOT)
    total = sum(entry["lines"] for entry in report.values())
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.write_baseline:
        BASELINE_PATH.write_text(payload + "\n", encoding="utf-8")
        print(f"baseline written: {BASELINE_PATH}", file=sys.stderr)
    print(json.dumps({"areas": report, "total_lines": total}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
