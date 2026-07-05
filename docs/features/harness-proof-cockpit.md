+++
id = "harness-proof-cockpit"
title = "Harness et cockpit de preuve"
status = "validated"
summary = "Exécuter des portails qualité déterministes et publier un cockpit de validation central, orienté features."
entrypoints = ["make help", "make setup", "make check", "make lint", "make fmt", "make test", "make test-e2e", "make fixtures", "make mock", "make docker-build", "make docker-check", "make docker-e2e", "make proof", "make clean", "make dist", "python -m cdpx.proof"]
path_globs = ["Makefile", "pyproject.toml", "Dockerfile", ".gitlab-ci.yml", "src/cdpx/__init__.py", "src/cdpx/cli.py", "src/cdpx/output.py", "src/cdpx/primitives/__init__.py", "src/cdpx/proof.py", "src/cdpx/proofing/*.py", "src/cdpx/proofing/markdown.py", "src/cdpx/testing/*.py", "tests/conftest.py", "tests/e2e/test_e2e_chrome.py", "tests/fixtures/pixel.png", "tests/test_cli.py", "tests/test_evidence.py", "tests/test_features.py", "tests/test_fixture_server.py", "tests/test_primitives.py", "tests/test_proof.py", "tests/test_markdown.py", "README.md", "HARNESS.md", "CLAUDE.md", "docs/*.md", "docs/features/*.md", "docs/milestones/*.md"]
test_globs = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_markdown.py::*", "tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cli_dispatch*", "tests/test_cli.py::test_cdpx_version"]
docs = ["README.md", "HARNESS.md", "CLAUDE.md", "docs/VALIDATION.md", "docs/ROADMAP.md", "docs/TODO.md"]
expected_proofs = ["junit"]

[[journeys]]
id = "run-quality-gate"
title = "Exécuter lint, format et tests déterministes"
entrypoint = "make check"

[[journeys]]
id = "publish-proof"
title = "Générer le rapport de validation humain et machine"
entrypoint = "make proof"

[[scenarios]]
id = "run-local-quality-gate"
journey = "run-quality-gate"
title = "Exécuter les portails qualité locaux"
ui_text = "Le développeur peut exécuter le portail déterministe lint + format + tests unitaires."
report_text = "Ce scénario prouve que le projet maintient un portail qualité local avant de produire des preuves navigateur plus lourdes."
given = "Les dépendances du dépôt sont installées localement."
when = "Le harness exécute lint, vérification de format et tests déterministes, y compris le filet de dispatch CLI (test de contrat du harness)."
then = "Les échecs remontent comme preuves de commande et résumés JUnit."
tests = ["tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cli_dispatch*", "tests/test_cli.py::test_cdpx_version"]
expected_proofs = ["junit"]

[[scenarios]]
id = "publish-feature-proof"
journey = "publish-proof"
title = "Publier un cockpit de preuve orienté features"
ui_text = "Le rapport généré permet à un humain de naviguer de la feature produit vers le parcours, le scénario, le test et la preuve."
report_text = "Ce scénario prouve que le rapport se lit comme un cockpit orienté produit, et non comme une liste plate d'artefacts CI."
given = "Les fiches features, les preuves pytest, le XML JUnit et les journaux de commandes existent pour le run."
when = "python -m cdpx.proof construit le résumé de validation et le rapport HTML, en rendant la doc Markdown des fiches features."
then = "Le rapport expose depuis un seul artefact les dossiers de features, les explications de scénarios, les tests, les captures et les manques."
tests = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_markdown.py::*"]
expected_proofs = ["junit"]
+++

## Intention

Rendre le harness du projet observable, reproductible et auditable à travers
un cockpit central. Les cibles make sont les portails : `make check` tranche
avant tout merge, les cibles Docker isolent les vérifications lourdes, et
`make proof` transforme les preuves collectées (JUnit, journaux, captures,
fiches features) en un rapport HTML feature-centrique — la documentation
humaine du produit, où chaque affirmation est reliée à sa preuve.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `make help`

Liste les cibles du Makefile avec leur description (extraite des commentaires
`##`). Point d'entrée pour découvrir le harness.

### `make setup`

Installe le paquet en editable plus les outils dev (pytest, ruff). À lancer
une fois après clonage, avant tout `make check`.

### `make check`

LE portail qualité : lint + vérification de format + tests unitaires
déterministes. Rien ne se merge s'il ne passe pas ; toute session de travail
se termine par un `make check` vert.

```bash
make check
```

### `make lint`

`ruff check` plus vérification de format (`ruff format --check`) sur `src` et
`tests`, sans rien modifier.

### `make fmt`

Reformate le code (`ruff format`) et applique les corrections automatiques
(`ruff check --fix`). La contrepartie corrective de `make lint`.

### `make test`

Tests unitaires déterministes seuls : mock CDP + serveur de fixtures, loopback
uniquement, aucun Chrome requis, aucun réseau externe.

### `make test-e2e`

Tests e2e sur Chrome réel (M1) — échoue si Chrome/Chromium est absent. C'est
la vérification lourde qui valide le comportement protocolaire réel.

### `make fixtures`

Lance le site témoin statique sur le port 8899, pour l'inspection manuelle ou
un e2e piloté à la main.

### `make mock`

Lance un faux Chrome scriptable (mock CDP) pour déboguer le CLI sans
navigateur ; le port de découverte est affiché au démarrage.

```bash
make mock &
cdpx --port 9333 tabs list
```

### `make docker-build`

Construit l'image portable `cdpx-ci`, socle des portails Docker.

### `make docker-check`

Exécute `make check` dans l'image `cdpx-ci` : reproduit le portail qualité
dans un environnement propre, indépendant du poste.

### `make docker-e2e`

Exécute les e2e Chrome réel dans l'image `cdpx-ci`, sans exiger de Chrome
installé localement.

### `make clean`

Supprime les artefacts de build et de cache (pytest, ruff, dist, egg-info,
`__pycache__`).

### `make dist`

Produit l'archive distribuable dans `dist/` — après un `make check` vert,
jamais sans.

### `make proof`

Génère le rapport HTML humain à partir des preuves collectées dans `.proof/`.
C'est l'alias make de `python -m cdpx.proof` (avec `PYTHONPATH=src`) : voir
l'entrée suivante pour le détail des artefacts produits.

```bash
make proof
```

### `python -m cdpx.proof`

Construit le cockpit de preuve : lit les fiches features de `docs/features/`
(front matter TOML strict + doc utilisateur Markdown), les preuves pytest
collectées, le XML JUnit, les journaux de commandes (`make-check-pytest.log`,
`e2e-chrome.log`, `symfony-e2e.log`), l'aide CLI et le contexte git, puis
publie deux artefacts dans `.proof/` :

- `.proof/proof-report.html` — le cockpit de preuve feature-centrique : la
  documentation humaine du produit, navigable de la feature au parcours, au
  scénario, au test et à la preuve (captures comprises), manques inclus ;
- `.proof/validation-summary.json` — le même contenu pour les machines
  (CI, agents), avec violations et avertissements d'inventaire.

Une fiche feature invalide (section manquante, entrypoint sans doc
utilisateur, scénario orphelin) est une violation qui fait échouer la
génération : la doc ne peut pas diverger silencieusement du produit.

```bash
PYTHONPATH=src python3 -m cdpx.proof
```

## Parcours utilisateur

- Exécuter les portails qualité locaux (`make check`) puis, au besoin, les
  portails lourds Docker et e2e.
- Générer `.proof/proof-report.html` et `.proof/validation-summary.json`.
- Inspecter la couverture features, scénarios, tests et preuves depuis une
  seule page.

## Validation

Les tests unitaires valident le parsing strict des fiches features (front
matter, sections, doc par entrypoint), le rendu Markdown du cockpit, la
compatibilité du résumé de validation, les règles d'échec de preuve, le
serveur de fixtures et le contrat CLI (sorties, erreurs d'usage, garde
d'origine, filet de dispatch des sous-commandes).

## Preuves

Preuves attendues : rapports JUnit, plus les artefacts de preuve générés
(`.proof/proof-report.html`, `.proof/validation-summary.json`, journaux de
commandes).

## Limites connues

- Les portails Docker (`docker-check`, `docker-e2e`, e2e Symfony) restent des
  vérifications lourdes explicites : ils ne sont pas lancés par défaut par
  `make check`.
- Politique Symfony : si Docker est absent sur le poste, la preuve Symfony est
  marquée « unavailable » dans le rapport — constat non bloquant, la
  génération du cockpit reste verte.
