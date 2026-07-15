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
SESSION_LIFECYCLE = Path("docs/SESSION-LIFECYCLE.md").read_text(encoding="utf-8")

GLOBAL_CONTRACT_TOKENS = [
    "--pretty",
    "--full",
    "--limit",
    "--max-actions",
    "--target",
    "--session",
    "--run-id",
    "--timeout",
    "CDPX_SESSION",
    "CDPX_RUN_ID",
    "CDPX_TARGET",
    "CDPX_ORIGINS",
]


def cli_command_names() -> list[str]:
    return [command["name"] for command in parse_help_commands(build_parser().format_help())]


def test_every_cli_command_appears_in_readme_and_primitives():
    """La surface CLI réelle, extraite du parseur, est intégralement couverte
    par le README et par PRIMITIVES.md: aucune sous-commande ne peut être
    livrée sans documentation."""
    # Aurait attrapé l'oubli historique de `cdpx pdf` dans PRIMITIVES.md.
    for name in cli_command_names():
        #: la liste des commandes vient du --help généré, pas d'une liste
        #: maintenue à la main: toute commande ajoutée sans doc nomme ici
        #: le document fautif
        assert f"cdpx {name}" in README, f"commande absente du README: cdpx {name}"
        assert f"cdpx {name}" in PRIMITIVES, f"commande absente de PRIMITIVES.md: cdpx {name}"


def test_readme_routes_to_every_feature_doc():
    """Chaque fiche feature déclarée est atteignable depuis le README: une
    fiche écrite mais non routée resterait invisible pour le lecteur."""
    specs, errors = load_feature_specs()
    #: des fiches illisibles fausseraient la couverture: on exige zéro
    #: erreur de chargement avant de juger le routage
    assert errors == []
    for spec in specs:
        link = f"docs/features/{spec.id}.md"
        #: le README est la porte d'entrée du dépôt: chaque fiche doit y
        #: être liée par son chemin exact
        assert link in README, f"fiche feature non routée depuis le README: {link}"


def test_primitives_references_every_feature_doc():
    """PRIMITIVES.md, le catalogue de référence, lie chaque fiche feature: le
    catalogue ne peut pas ignorer une capacité documentée ailleurs."""
    specs, _ = load_feature_specs()
    for spec in specs:
        #: qui lit le catalogue doit pouvoir rebondir vers chaque fiche;
        #: une capacité absente d'ici n'existe pas pour l'utilisateur
        assert f"features/{spec.id}.md" in PRIMITIVES, f"fiche non liée dans PRIMITIVES: {spec.id}"


def test_session_lifecycle_reference_is_routed_and_diagrammed():
    """La référence du cycle de vie de session est routée depuis les deux
    points d'entrée et conserve ses quatre diagrammes mermaid accessibles:
    en ajouter ou en retirer un doit être un choix explicite."""
    #: le document est atteignable depuis le README comme depuis le catalogue
    assert "docs/SESSION-LIFECYCLE.md" in README
    assert "SESSION-LIFECYCLE.md" in PRIMITIVES
    #: le compte exact fige le contrat visuel, et chaque diagramme porte
    #: titre et description d'accessibilité (accTitle/accDescr)
    assert SESSION_LIFECYCLE.count("```mermaid") == 4
    assert SESSION_LIFECYCLE.count("accTitle:") == 4
    assert SESSION_LIFECYCLE.count("accDescr:") == 4


def test_readme_documents_cli_contract():
    """Le README documente le contrat CLI global en entier: options
    transverses, variables d'environnement de session et les trois codes
    de sortie de l'invariant stdout/exit."""
    for token in GLOBAL_CONTRACT_TOKENS:
        #: chaque jeton du contrat global (options et variables CDPX_*) doit
        #: apparaître: un contrat partiellement documenté piège l'utilisateur
        assert token in README, f"contrat CLI incomplet dans le README: {token}"
    for exit_code in ("exit 0", "exit 1", "exit 2"):
        #: les trois codes de sortie font partie du contrat public; la
        #: recherche tolère l'espacement pour ne pas figer la mise en forme
        assert re.search(exit_code.replace(" ", r"\s*"), README, re.I), (
            f"code de sortie non documenté: {exit_code}"
        )


def test_active_user_docs_only_describe_the_supervised_session_contract():
    """Aucun document utilisateur actif ne mentionne un contrat supprimé
    (endpoints bruts, gestion d'onglets, mode équipe/legacy): la doc vivante
    ne peut pas ressusciter une API retirée."""
    specs, errors = load_feature_specs()
    #: le corpus jugé inclut toutes les fiches; elles doivent se charger
    #: sans erreur pour que la vérification soit exhaustive
    assert errors == []
    documents = {
        "README.md": README,
        "HARNESS.md": Path("HARNESS.md").read_text(encoding="utf-8"),
        "docs/PRIMITIVES.md": PRIMITIVES,
        **{spec.source: spec.body for spec in specs},
    }
    removed_contracts = (
        "--host",
        "--port",
        "CDPX_HOST",
        "CDPX_PORT",
        "--manifest",
        "--evidence-dir",
        "tabs new",
        "tabs activate",
        "tabs close",
        "mode équipe",
        "mode local historique",
        "legacy",
    )
    for source, content in documents.items():
        lowered = content.lower()
        for removed in removed_contracts:
            #: la traque est insensible à la casse et nomme le document
            #: fautif: une seule mention d'un contrat retiré casse le portail
            assert removed.lower() not in lowered, (
                f"{source}: contrat supprimé encore documenté: {removed}"
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
    """Toute ligne `cdpx ...` extraite des blocs de code de la doc est
    acceptée par le vrai parseur: un exemple copié-collé ne peut jamais
    échouer en erreur d'usage."""
    argv = shlex.split(line)[1:]
    try:
        build_parser().parse_args(argv)
    except SystemExit as exc:
        # --version / -h sortent en 0: exemples valides. Un exit 2 = exemple faux.
        #: seule une sortie volontaire (aide, version) est tolérée; un exit
        #: du parseur en erreur prouve que la doc ment sur la syntaxe
        assert exc.code == 0, f"{source}: exemple invalide: {line}"
