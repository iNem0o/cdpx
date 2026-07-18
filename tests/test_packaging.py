"""Garde-fous release mécaniques (HARNESS §6).

Version, licence MIT, métadonnées publiques et GitHub Actions restent alignés
avec le portail local. Ces tests tournent dans ``make check``.
"""

import hashlib
import json
import re
import tomllib
from pathlib import Path

import yaml

from cdpx import __version__

PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
REPOSITORY_URL = "https://github.com/inem0o/cdpx"


def test_action_journal_layer_does_not_depend_on_browser_primitives():
    journal_source = Path("src/cdpx/journal.py").read_text(encoding="utf-8")
    action_source = Path("src/cdpx/action_model.py").read_text(encoding="utf-8")

    assert "cdpx.primitives" not in journal_source
    assert "cdpx.primitives" not in action_source


def test_version_has_single_source():
    """La version n'a qu'une seule source de vérité (cdpx.__version__),
    déléguée dynamiquement par pyproject, et respecte le format x.y.z."""
    # La version vit dans cdpx.__version__; pyproject la déclare dynamic.
    #: pyproject délègue la version à l'attribut du paquet: aucune valeur
    #: statique à désynchroniser au moment de tagguer une release
    assert "version" not in PYPROJECT["project"], "version statique réintroduite dans pyproject"
    assert "version" in PYPROJECT["project"]["dynamic"]
    assert PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"] == "cdpx.__version__"
    #: exactement deux points = semver x.y.z, le format attendu par les
    #: tags v* et le CHANGELOG
    assert __version__.count(".") == 2  # x.y.z


def test_license_is_declared_and_present():
    """La licence MIT est déclarée en expression SPDX, le fichier LICENSE
    raconte la même chose, et aucun classifieur privé ou déprécié ne bloque
    la publication."""
    project = PYPROJECT["project"]
    #: la déclaration SPDX et le texte du fichier LICENSE doivent porter la
    #: même licence, avec le bon détenteur du copyright
    assert project["license"] == "MIT"
    license_text = Path("LICENSE").read_text(encoding="utf-8")
    assert license_text.startswith("MIT License\n")
    assert "Copyright (c) 2026 inem0o" in license_text
    #: ni garde anti-upload ni classifieur License:: (déprécié par PEP 639):
    #: le paquet est publiable tel quel et la licence vit dans license-files
    assert "Private :: Do Not Upload" not in project["classifiers"]
    assert not any(item.startswith("License ::") for item in project["classifiers"])
    assert project["license-files"] == ["LICENSE"]


def test_markdown_dependency_and_vendored_mermaid_notice_are_pinned():
    """La dépendance markdown est bornée en majeur et le bundle mermaid
    vendorisé est épinglé par hash avec licence et notice tierce: aucun code
    tiers anonyme ne voyage dans le paquet."""
    #: la borne <5 empêche un saut de majeur de markdown-it-py de changer
    #: silencieusement le rendu des rapports de preuve
    assert "markdown-it-py>=4.2,<5" in PYPROJECT["project"]["dependencies"]
    bundle = Path("src/cdpx/proofing/vendor/mermaid-11.16.0.min.js")
    license_path = Path("src/cdpx/proofing/vendor/LICENSE.mermaid")
    notice = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    #: le bundle et sa licence sont livrés, et le hash fige l'artefact:
    #: toute substitution casse le build au lieu de passer inaperçue
    assert bundle.is_file() and license_path.is_file()
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == (
        "74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b"
    )
    #: la notice tierce cite la version exacte et la licence MIT accompagne
    #: le vendoring, condition d'une redistribution propre
    assert "mermaid@11.16.0" in notice and "LICENSE.mermaid" in notice
    assert "MIT License" in license_path.read_text(encoding="utf-8")


def test_vendored_xterm_bundle_and_notice_are_pinned():
    """Le player xterm vendorisé (js et css) est épinglé par hash et
    accompagné de sa licence et de la notice tierce: même exigence que pour
    mermaid."""
    bundle = Path("src/cdpx/proofing/vendor/xterm-5.5.0.min.js")
    stylesheet = Path("src/cdpx/proofing/vendor/xterm-5.5.0.min.css")
    license_path = Path("src/cdpx/proofing/vendor/LICENSE.xterm")
    notice = Path("THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert bundle.is_file() and stylesheet.is_file() and license_path.is_file()
    #: le bundle du player est épinglé: toute substitution casse le build
    assert hashlib.sha256(bundle.read_bytes()).hexdigest() == (
        "4196e242ef1cf4c2adead8d97f4a772a69576076f70b095e004b4abbb049e7bf"
    )
    assert hashlib.sha256(stylesheet.read_bytes()).hexdigest() == (
        "f7f724aea2bb620a6482bfb8e4bdecfae1152b0c7facef55fbda61f3b6cfedb2"
    )
    #: la licence MIT et la notice tierce accompagnent le vendoring
    assert "@xterm/xterm@5.5.0" in notice and "LICENSE.xterm" in notice
    assert "Copyright" in license_path.read_text(encoding="utf-8")


def test_public_project_metadata_points_to_github():
    """Les métadonnées publiques du paquet (auteur, dépôt, issues, changelog)
    pointent toutes vers le dépôt GitHub canonique."""
    project = PYPROJECT["project"]
    #: toutes les URL publiées sur PyPI dérivent du même dépôt: aucun lien
    #: mort ni reliquat d'un ancien hébergement
    assert project["authors"] == [{"name": "inem0o"}]
    assert project["urls"]["Repository"] == REPOSITORY_URL
    assert project["urls"]["Issues"] == f"{REPOSITORY_URL}/issues"
    assert project["urls"]["Changelog"].startswith(REPOSITORY_URL)


def test_changelog_covers_current_version():
    """Le CHANGELOG contient une section pour la version courante du paquet:
    impossible de tagguer une release sans notes de version."""
    changelog = Path("CHANGELOG.md").read_text(encoding="utf-8")
    #: la section est cherchée pour la version importée du paquet, pas pour
    #: une valeur recopiée à la main: le lien version/notes est mécanique
    assert f"## [{__version__}]" in changelog, f"CHANGELOG sans entrée pour {__version__}"


def test_python_floor_matches_ruff_target():
    """Le plancher Python déclaré et les cibles ruff/mypy désignent la même
    version: l'outillage ne peut pas valider une syntaxe que le paquet ne
    promet pas de supporter."""
    floor = PYPROJECT["project"]["requires-python"].removeprefix(">=").strip()
    ruff_target = PYPROJECT["tool"]["ruff"]["target-version"]
    #: ruff et mypy sont dérivés du même plancher requires-python: monter
    #: l'un sans les autres serait un désalignement silencieux
    assert ruff_target == "py" + floor.replace(".", ""), (
        f"requires-python {floor} et ruff target-version {ruff_target} désalignés"
    )
    mypy_target = PYPROJECT["tool"]["mypy"]["python_version"]
    assert mypy_target == floor


def test_dev_extra_pins_the_toolchain():
    """L'extra dev embarque toute la chaîne d'outils du portail: installer
    `.[dev]` suffit pour rejouer make check-local sans dépendance implicite
    à l'environnement."""
    dev = PYPROJECT["project"]["optional-dependencies"]["dev"]
    for tool in ("pytest", "pytest-cov", "ruff", "build", "twine", "mypy"):
        #: chaque outil du portail doit être installable via l'extra, sinon
        #: le check dépendrait d'un poste de travail particulier
        assert any(dep.startswith(tool) for dep in dev), f"outil dev manquant: {tool}"


def test_release_image_contains_metadata_and_full_dev_toolchain():
    """L'image Docker de release transporte les fichiers légaux et la chaîne
    dev complète, et toutes les images de base sont figées par digest."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    #: les fichiers légaux voyagent dans l'image et l'installation inclut
    #: l'extra dev: le portail est rejouable dans le conteneur
    assert "COPY LICENSE THIRD_PARTY_NOTICES.md CHANGELOG.md" in dockerfile
    assert 'pip install -e ".[dev]"' in dockerfile
    #: aucun résidu de l'ancienne CI GitLab dans le contexte de build
    assert ".gitlab-ci.yml" not in dockerfile
    for path in (Path("Dockerfile"), Path("tests/symfony-app/Dockerfile")):
        from_line = path.read_text(encoding="utf-8").splitlines()[0]
        #: un FROM par tag flottant rend le build non reproductible; seul un
        #: digest sha256 fige réellement l'image de base
        assert re.fullmatch(r"FROM [^\s]+@sha256:[0-9a-f]{64}", from_line), (
            f"image Docker non épinglée par digest: {path}"
        )


def test_release_portal_and_ci_require_all_runtime_proofs():
    """Le portail make check agrège toutes les preuves runtime, make release
    enchaîne check + proof + distribution vérifiée, et la CI GitHub rejoue
    ces portails en publiant les preuves partageables."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    check_prerequisites = makefile.split("check:", 1)[1].split("\n", 1)[0]
    #: retirer un portail runtime des prérequis de check affaiblirait la
    #: Definition of Done sans bruit: la liste est figée ici
    for portal in ("check-local", "docker-check", "docker-e2e", "docker-symfony-e2e"):
        assert portal in check_prerequisites
    assert "docker run --rm cdpx-ci make check-local" in makefile

    #: release ne peut pas court-circuiter le check, la preuve ni la
    #: construction de la distribution
    assert "release:" in makefile
    for portal in ("check", "proof", "dist"):
        assert portal in makefile.split("release:", 1)[1].split("\n", 1)[0]

    #: la distribution est fumée et vérifiée avant toute publication
    assert "smoke-dist" in makefile.split("dist:", 1)[1]
    assert "scripts/verify_dist.py" in makefile.split("dist:", 1)[1]

    #: la migration GitLab -> GitHub est terminée: plus de fichier CI GitLab
    assert not Path(".gitlab-ci.yml").exists()
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    #: la CI rejoue les portails make (dont release), publie le résumé et
    #: les preuves partageables, sans logs redondants ni filtrage de chemins
    #: qui laisserait passer un commit non vérifié
    assert "make check-local" in ci
    assert "make cov" in ci
    assert "make release" in ci
    assert "include-hidden-files: true" in ci
    assert "name: PR Gate / Required" in ci
    assert "if: ${{ always() }}" in ci
    assert "GITHUB_STEP_SUMMARY" in ci
    assert "scripts/github_summary.py" in ci
    assert ".ci-artifacts/make-release.log" not in ci
    assert "tee .ci-artifacts" not in ci
    assert 'CDPX_PROOF_RETENTION_DAYS: "14"' in ci
    assert "path: .proof/shareable/" in ci
    assert "paths:" not in ci and "paths-ignore:" not in ci

    release = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    compose = Path("docker-compose.symfony-e2e.yml").read_text(encoding="utf-8")
    #: la release conserve ses preuves plus longtemps que la CI et la boucle
    #: Symfony reçoit la même politique de rétention
    assert 'CDPX_PROOF_RETENTION_DAYS: "30"' in release
    assert "path: .proof/shareable/" in release
    assert "CDPX_PROOF_RETENTION_DAYS" in compose


def test_github_workflows_are_parseable_and_actions_are_sha_pinned():
    """Les workflows GitHub sont du YAML valide et chaque action tierce est
    figée par SHA complet: rien de mutable dans la chaîne CI."""
    workflows = sorted(Path(".github/workflows").glob("*.yml"))
    #: l'inventaire des workflows est exhaustif: un workflow ajouté doit
    #: passer par ce garde-fou ou être explicitement listé
    assert {path.name for path in workflows} == {"ci.yml", "pages.yml", "release.yml"}
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        parsed = yaml.load(text, Loader=yaml.BaseLoader)
        #: BaseLoader prouve que le fichier est du YAML parseable avec les
        #: clés minimales d'un workflow, sans interprétation des valeurs
        assert isinstance(parsed, dict) and "on" in parsed and "jobs" in parsed
        for action in re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
            #: un tag ou une branche peut être réécrit a posteriori; seul un
            #: SHA de 40 caractères fige le code exécuté par la CI
            assert re.fullmatch(r"[^/@]+/[^/@]+@[0-9a-f]{40}", action), (
                f"action non épinglée par SHA dans {path}: {action}"
            )


def test_ci_and_release_workflows_keep_permissions_narrow():
    """La CI reste en lecture seule (ni OIDC ni pull_request_target) et la
    release accorde à chaque job exactement la permission dont il a besoin."""
    ci_text = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    #: la CI ne peut rien écrire, n'émet pas de jeton OIDC et n'expose pas
    #: les secrets au code des forks
    assert re.search(r"^permissions:\n  contents: read$", ci_text, re.MULTILINE)
    assert "id-token: write" not in ci_text
    assert "pull_request_target" not in ci_text

    release_text = Path(".github/workflows/release.yml").read_text(encoding="utf-8")
    #: la release ne part que d'un tag v*, publie via OIDC dans un
    #: environnement protégé et vérifie que le tag correspond à la version
    #: et descend bien de master
    assert "tags:" in release_text and '- "v*"' in release_text
    assert "environment:" in release_text and "name: pypi" in release_text
    assert "id-token: write" in release_text
    assert "pypa/gh-action-pypi-publish@" in release_text
    assert "gh release create" in release_text
    assert '"v${PACKAGE_VERSION}" = "${GITHUB_REF_NAME}"' in release_text
    assert "GH_REPO: ${{ github.repository }}" in release_text
    assert "(cd dist && sha256sum *) > SHA256SUMS" in release_text
    assert 'git merge-base --is-ancestor "${GITHUB_SHA}" origin/master' in release_text

    release = yaml.load(release_text, Loader=yaml.BaseLoader)
    jobs = release["jobs"]
    #: chaque job déclare exactement sa permission (moindre privilège):
    #: aucun job n'hérite d'un droit global du workflow
    assert jobs["publish-pypi"]["permissions"] == {"id-token": "write"}
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert jobs["build-and-verify"]["permissions"] == {"contents": "read"}


def test_public_community_files_and_generated_artifact_policy():
    """Les fichiers communautaires et la configuration GitHub existent, et
    les artefacts générés restent hors du dépôt comme du contexte Docker."""
    #: les fichiers de santé communautaire conditionnent l'accueil des
    #: contributions sur un dépôt public
    for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md"):
        assert Path(name).is_file(), f"fichier communautaire manquant: {name}"
    #: templates d'issues/PR et dependabot font partie du contrat de
    #: contribution outillé côté GitHub
    for name in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/dependabot.yml",
    ):
        assert Path(name).is_file(), f"configuration GitHub manquante: {name}"

    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
    #: les sorties générées (preuves, artefacts CI) et les contenus
    #: éditoriaux ne fuient ni dans git ni dans l'image Docker
    assert ".proof/" in gitignore
    assert ".ci-artifacts/" in gitignore
    assert ".proof" in dockerignore
    assert ".ci-artifacts" in dockerignore
    assert "article/" in dockerignore and "presentation/" in dockerignore
    assert Path("MANIFEST.in").is_file()


def test_github_templates_enforce_the_project_contract():
    """Les templates GitHub (PR, bug, feature) rappellent mécaniquement le
    contrat du projet: portail requis, invariants du harness et Definition
    of Done — un contributeur ne peut pas les ignorer par accident."""
    pr_template = Path(".github/PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")
    #: le template de PR nomme le check bloquant, refuse la checkbox comme
    #: preuve et couvre chaque invariant du harness
    assert "PR Gate / Required" in pr_template
    assert "checkbox déclarative ne remplace jamais" in pr_template
    for requirement in ("Contrat CLI", "protocole CDP", "Fixture", "Sécurité", "make release"):
        assert requirement.lower() in pr_template.lower()

    bug = yaml.safe_load(Path(".github/ISSUE_TEMPLATE/bug_report.yml").read_text())
    #: le rapport de bug force les champs nécessaires à une reproduction
    #: conforme au contrat CLI (commande, sorties, version, environnement)
    assert bug["labels"] == ["bug"]
    bug_ids = {item.get("id") for item in bug["body"]}
    assert {"command", "stdout-stderr", "version", "environment"} <= bug_ids

    feature = yaml.safe_load(Path(".github/ISSUE_TEMPLATE/feature_request.yml").read_text())
    #: la demande de feature embarque la Definition of Done du harness sous
    #: forme de cases: primitive, CLI, protocole, fixture, e2e
    assert feature["labels"] == ["enhancement"]
    dod = next(item for item in feature["body"] if item.get("id") == "definition-of-done")
    labels = " ".join(option["label"] for option in dod["attributes"]["options"])
    for requirement in ("primitive", "sous-commande", "JSON", "protocole", "fixture", "E2E"):
        assert requirement.lower() in labels.lower()

    contributing = Path("CONTRIBUTING.md").read_text(encoding="utf-8")
    #: CONTRIBUTING route vers la doc GitHub et nomme le même check requis
    #: que le template de PR: un seul vocabulaire pour le portail
    assert "docs/GITHUB.md" in contributing and "PR Gate / Required" in contributing


def test_derived_symfony_fixtures_retain_upstream_notice():
    """Le matériel dérivé de Symfony garde sa notice de licence amont, et
    l'app témoin ne verrouille que des dépendances MIT/BSD-3-Clause."""
    notice = Path("tests/fixtures/profiler/LICENSE.SYMFONY").read_text(encoding="utf-8")
    fixture_readme = Path("tests/fixtures/profiler/README.md").read_text(encoding="utf-8")
    #: la fixture dérivée conserve le copyright amont et son README pointe
    #: vers la notice: la provenance reste traçable
    assert "Copyright (c) 2004-present Fabien Potencier" in notice
    assert "Permission is hereby granted" in notice
    assert "LICENSE.SYMFONY" in fixture_readme

    composer = json.loads(Path("tests/symfony-app/composer.lock").read_text(encoding="utf-8"))
    licenses = {
        license_name
        for package in composer["packages"]
        for license_name in package.get("license", [])
    }
    #: le lock composer ne peut introduire aucune licence plus restrictive
    #: dans le dépôt via les dépendances de l'app témoin
    assert licenses <= {"MIT", "BSD-3-Clause"}
