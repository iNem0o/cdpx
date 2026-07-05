"""Garde-fous documentation: le README et PRIMITIVES.md suivent la surface CLI
réelle, chaque fiche feature est routée, et tout exemple `cdpx ...` documenté
est syntaxiquement valide contre le vrai parseur. Une doc qui dérive casse
`make check` (esprit HARNESS §6: une règle sans garde-fou mécanique est un vœu).
"""

import re
import shlex
from pathlib import Path

import pytest

from cdpx.cli import build_parser
from cdpx.proof import parse_help_commands
from cdpx.proofing.features import load_feature_specs

README = Path("README.md").read_text(encoding="utf-8")
PRIMITIVES = Path("docs/PRIMITIVES.md").read_text(encoding="utf-8")

GLOBAL_CONTRACT_TOKENS = [
    "--pretty",
    "--full",
    "--limit",
    "--max-actions",
    "--target",
    "--host",
    "--port",
    "--timeout",
    "CDPX_HOST",
    "CDPX_PORT",
    "CDPX_ORIGINS",
]


def cli_command_names() -> list[str]:
    return [command["name"] for command in parse_help_commands(build_parser().format_help())]


def test_every_cli_command_appears_in_readme_and_primitives():
    # Aurait attrapé l'oubli historique de `cdpx pdf` dans PRIMITIVES.md.
    for name in cli_command_names():
        assert f"cdpx {name}" in README, f"commande absente du README: cdpx {name}"
        assert f"cdpx {name}" in PRIMITIVES, f"commande absente de PRIMITIVES.md: cdpx {name}"


def test_readme_routes_to_every_feature_doc():
    specs, errors = load_feature_specs()
    assert errors == []
    for spec in specs:
        link = f"docs/features/{spec.id}.md"
        assert link in README, f"fiche feature non routée depuis le README: {link}"


def test_primitives_references_every_feature_doc():
    specs, _ = load_feature_specs()
    for spec in specs:
        assert f"features/{spec.id}.md" in PRIMITIVES, f"fiche non liée dans PRIMITIVES: {spec.id}"


def test_readme_documents_cli_contract():
    for token in GLOBAL_CONTRACT_TOKENS:
        assert token in README, f"contrat CLI incomplet dans le README: {token}"
    for exit_code in ("exit 0", "exit 1", "exit 2"):
        assert re.search(exit_code.replace(" ", r"\s*"), README, re.I), (
            f"code de sortie non documenté: {exit_code}"
        )


def _fenced_cdpx_lines(text: str) -> list[str]:
    lines = []
    for fence in re.findall(r"```[a-z]*\n(.*?)```", text, re.S):
        for line in fence.splitlines():
            stripped = line.strip()
            if stripped.startswith("cdpx "):
                lines.append(stripped.rstrip("\\").strip())
    return lines


def _all_documented_examples() -> list[tuple[str, str]]:
    examples = [("README.md", line) for line in _fenced_cdpx_lines(README)]
    examples += [("docs/PRIMITIVES.md", line) for line in _fenced_cdpx_lines(PRIMITIVES)]
    specs, _ = load_feature_specs()
    for spec in specs:
        examples += [(spec.source, line) for line in _fenced_cdpx_lines(spec.body)]
    return examples


@pytest.mark.parametrize(
    "source,line",
    _all_documented_examples(),
    ids=[f"{src}:{line[:40]}" for src, line in _all_documented_examples()],
)
def test_documented_cdpx_examples_parse(source, line):
    argv = shlex.split(line)[1:]
    try:
        build_parser().parse_args(argv)
    except SystemExit as exc:
        # --version / -h sortent en 0: exemples valides. Un exit 2 = exemple faux.
        assert exc.code == 0, f"{source}: exemple invalide: {line}"
