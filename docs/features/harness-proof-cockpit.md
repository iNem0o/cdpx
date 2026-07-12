+++
id = "harness-proof-cockpit"
title = "Harness et cockpit de preuve"
status = "validated"
summary = "Exécuter des portails qualité déterministes et publier un cockpit de validation central, orienté features."
entrypoints = ["make help", "make setup", "make check-local", "make check", "make lint", "make fmt", "make test", "make test-e2e", "make cov", "make typecheck", "make fixtures", "make mock", "make docker-build", "make docker-check", "make docker-e2e", "make proof", "make release", "make clean", "make dist", "make smoke-dist", "python -m cdpx.proof"]
path_globs = ["Makefile", "pyproject.toml", "MANIFEST.in", "scripts/*.py", "Dockerfile", ".gitignore", ".dockerignore", ".github/workflows/*.yml", ".github/ISSUE_TEMPLATE/*.yml", ".github/*.md", ".github/dependabot.yml", "src/cdpx/__init__.py", "src/cdpx/cli.py", "src/cdpx/output.py", "src/cdpx/primitives/__init__.py", "src/cdpx/proof.py", "src/cdpx/proofing/*.py", "src/cdpx/proofing/markdown.py", "src/cdpx/testing/*.py", "tests/conftest.py", "tests/e2e/test_e2e_chrome.py", "tests/fixtures/pixel.png", "tests/test_cli.py", "tests/test_evidence.py", "tests/test_features.py", "tests/test_fixture_server.py", "tests/test_github_summary.py", "tests/test_primitives.py", "tests/test_proof.py", "tests/test_markdown.py", "tests/test_docs.py", "tests/test_packaging.py", "README.md", "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "SUPPORT.md", "HARNESS.md", "CLAUDE.md", "docs/*.md", "docs/features/*.md", "docs/milestones/*.md"]
test_globs = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_github_summary.py::*", "tests/test_markdown.py::*", "tests/test_docs.py::*", "tests/test_packaging.py::*", "tests/test_fixture_server.py::*", "tests/test_cli.py::test_pretty*", "tests/test_cli.py::test_agent_output*", "tests/test_cli.py::test_discovery_error*", "tests/test_cli.py::test_usage_error*", "tests/test_cli.py::test_origin_guard*", "tests/test_cli.py::test_cli_dispatch*", "tests/test_cli.py::test_cdpx_version", "tests/test_cli.py::test_conditional_cli_arguments*", "tests/test_cli.py::test_cookie_mutations_and_vitals*", "tests/e2e/test_e2e_chrome.py::test_cli_stdout_stderr*"]
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
then = "Le rapport local relie dossiers de features, scénarios, tests, captures privées et manques; le staging CI ne contient que les fichiers textuels manifestés et nettoyés."
tests = ["tests/test_proof.py::*", "tests/test_features.py::*", "tests/test_evidence.py::*", "tests/test_github_summary.py::*", "tests/test_markdown.py::*", "tests/test_docs.py::*", "tests/test_packaging.py::*"]
expected_proofs = ["junit"]
+++

## Intention

Rendre le harness du projet observable, reproductible et auditable à travers
un cockpit central. Les cibles make sont les portails : `make check` tranche
avant tout merge, les cibles Docker isolent les vérifications lourdes, et
`make proof` transforme les preuves collectées (JUnit, journaux, captures
locales privées, fiches features) en un rapport HTML feature-centrique — la documentation
humaine du produit, où chaque affirmation est reliée à sa preuve.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `make help`

Liste les cibles du Makefile avec leur description (extraite des commentaires
`##`). Point d'entrée pour découvrir le harness.

### `make setup`

Installe le paquet en editable plus les outils dev (pytest, ruff). À lancer
une fois après clonage, avant tout `make check`.

### `make check-local`

Boucle courte de développement: lint/format, mypy et tests unitaires
déterministes. Ce sous-portail n'est pas une décision de release.

### `make check`

Portail qualité standard complet: `check-local`, reproduction dans l'image
Docker, e2e Chrome réel dans Docker et suite Symfony réelle. Docker/Compose
est donc requis pour déclarer le dépôt vert.

LE portail qualité : contrôles locaux déterministes, reproduction en image,
Chrome réel et Symfony réel. Rien ne se merge s'il ne passe pas ; toute
session de travail se termine par un `make check` vert.

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

### `make cov`

Tests unitaires avec mesure de couverture et seuil bloquant
(`--cov-fail-under`, 85% par défaut via `COV_MIN`). Appliqué en CI sur la
matrice Python; en local, `make check` reste le portail rapide.

### `make typecheck`

Vérification mypy de `src/cdpx`. Bloquante depuis le passage au vert durable
(0 erreur, 2026-07): incluse dans `make check` et sans `allow_failure` en CI.

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

Exécute `make check-local` dans l'image `cdpx-ci` : reproduit lint, typage et
unitaires dans un environnement propre sans récursion vers Docker.

### `make docker-e2e`

Exécute les e2e Chrome réel dans l'image `cdpx-ci`, sans exiger de Chrome
installé localement.

### `make clean`

Supprime les artefacts de build, de preuve et de cache (pytest, ruff, `.proof`,
dist, egg-info, `__pycache__`).

### `make dist`

Construit et vérifie les artefacts distribuables (`python -m build`,
`twine check --strict`, contrôle du contenu public, puis `make smoke-dist`) :
wheel + sdist dans `dist/` — après un `make check` vert, jamais sans.

### `make smoke-dist`

Crée un environnement virtuel temporaire, y installe le wheel construit et
vérifie la licence MIT, `cdpx --version`, `cdpx --help` et les 31 commandes.
L'environnement est supprimé même en cas d'échec.

### `make proof`

Génère le rapport HTML humain à partir des preuves collectées dans `.proof/`.
C'est l'alias make de `python -m cdpx.proof` (avec `PYTHONPATH=src`) : voir
l'entrée suivante pour le détail des artefacts produits.

```bash
make proof
```

Docker/Compose et la suite Symfony réelle sont requis. Une indisponibilité ou
un skip Symfony produit un rapport rouge et un exit non nul.

### `make release`

Portail final agrégé : `check` complet, cockpit de preuve vert, puis
wheel/sdist vérifiés. C'est la commande de décision release; elle exige Docker
et Chrome.

```bash
make release
```

### `python -m cdpx.proof`

Construit le cockpit de preuve : lit les fiches features de `docs/features/`
(front matter TOML strict + doc utilisateur Markdown), les preuves pytest
collectées, le XML JUnit, les journaux de commandes (`make-check-pytest.log`,
`e2e-chrome.log`, `symfony-e2e.log`), l'aide CLI et le contexte git, puis
publie deux artefacts principaux dans l'arbre privé `.proof/` :

- `.proof/proof-report.html` — le cockpit de preuve feature-centrique : la
  documentation humaine du produit, navigable de la feature au parcours, au
  scénario, au test et à la preuve locale (captures comprises), manques inclus ;
- `.proof/validation-summary.json` — le même contenu pour les machines
  (CI, agents), avec violations et avertissements d'inventaire.

Les dossiers sont forcés en `0700` et les fichiers en `0600`. Un manifest
`cdpx.artifacts/v1` classe chaque fichier (`public`, `internal`, `secret`,
`opaque-restricted`), enregistre SHA-256, version de redaction, TTL et droit
d'upload. `make proof` construit ensuite `.proof/shareable/` uniquement avec
les fichiers textuels `internal` explicitement autorisés. Captures, PDF et
binaires restent opaques/restreints en local. Un scan de canaris échoue fermé
avant publication.

La CI PR conserve ce staging 14 jours. Sur tag, `release-proof` le conserve
30 jours et les distributions séparées 90 jours. Le manifest porte la même
rétention que l'upload : `CDPX_PROOF_RETENTION_DAYS`, entier strict de 1 à
90, vaut 14 par défaut et 30 dans le workflow de release. Une valeur invalide
fait échouer la preuve. Hors session supervisée, la purge locale n'est pas
déclenchée par un daemon global.

Une fiche feature invalide (section manquante, entrypoint sans doc
utilisateur, scénario orphelin) est une violation qui fait échouer la
génération : la doc ne peut pas diverger silencieusement du produit.

```bash
PYTHONPATH=src python3 -m cdpx.proof
```

## Parcours utilisateur

- Exécuter `make check-local` pour une rétroaction courte, `make check` pour
  le verdict qualité complet, puis `make release` avant toute livraison.
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

Preuves attendues : rapports JUnit, artefacts locaux privés
(`.proof/proof-report.html`, `.proof/validation-summary.json`, journaux et
captures), plus `.proof/shareable/` et son manifest pour la CI.

## Limites connues

- La boucle courte porte explicitement le nom `make check-local`; `make check`
  inclut toujours Docker, Chrome et Symfony.
- Docker/Compose absent ou test Symfony skippé : `make proof` et
  `make release` échouent. Le rapport conserve le statut `unavailable` comme
  diagnostic, jamais comme succès dégradé.
- `SecureArtifactWriter` redige automatiquement texte, JSON et fichiers
  textuels enregistrés, mais ne peut inspecter sûrement un binaire opaque ni
  deviner toute PII. Le scan de canaris reste le dernier verrou de staging.
