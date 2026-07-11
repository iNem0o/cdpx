# cdpx

Primitives Chrome DevTools Protocol exposées en CLI, pour agents de dev et
les humains qui les pilotent — contexte: apps Symfony, e-commerce
(Shopware/PrestaShop), opérations SEO.

Un binaire, une commande = une action navigateur, une sortie = un objet JSON.

Version 0.1.0 — logiciel propriétaire inem0o (voir [Licence](#licence)).

## Installation

Prérequis: Python ≥ 3.11, Chrome ou Chromium pour agir sur un vrai navigateur
(les tests unitaires n'en ont pas besoin).

```
pip install -e .            # ou: make setup (installe aussi les outils dev)
```

Toujours piloter un Chrome **jetable** — jamais votre navigateur personnel
(sessions bancaires, mails, admin prod; règle n°1 de [HARNESS.md](HARNESS.md)):

```
chromium --headless=new --remote-debugging-port=9222 \
  --user-data-dir=$(mktemp -d /tmp/cdpx-XXXX) --no-first-run &
```

## Démarrage rapide

```bash
cdpx tabs list
cdpx goto http://shop.localhost/checkout
cdpx wait "#payment-form"
cdpx type "#email" "test@example.test" --clear
cdpx click "#submit"
cdpx console --duration 2
cdpx network http://shop.localhost/checkout
cdpx seo https://www.exemple.fr/produit-42
cdpx screenshot -o /tmp/etat.png --format jpeg
```

Sans Chrome sous la main:

```
make mock            # faux Chrome scriptable, affiche son port de découverte
cdpx --port 9222 tabs list
```

## Contrat CLI

Le contrat est identique pour les 30 commandes; c'est ce qui rend chaque
action d'agent reproductible par un humain en une ligne.

**Sorties.** stdout = un objet JSON compact (machine, sobre en tokens);
`--pretty` = JSON indenté pour lecture humaine; stderr = diagnostics. Les
sorties volumineuses sont bornées par `--limit` (défaut raisonnable, métadonnées
`*_truncated` / `*_total`); `--full` demande le détail complet. Les flux
(`cdpx console --follow`, journaux `record`) sont en NDJSON compact, une ligne
JSON par évènement.

**Codes de sortie.** exit 0 = succès; exit 1 = erreur d'exécution (élément
introuvable, timeout, erreur CDP, divergence de replay, mutation refusée);
exit 2 = mauvaise invocation (argparse). Un agent qui boucle sur des exit 1
doit remonter à l'humain, pas insister à l'aveugle.

**Connexion.** `--host` (défaut `127.0.0.1`, env `CDPX_HOST`), `--port`
(défaut `9222`, env `CDPX_PORT`), `--target ID` pour viser un onglet précis
(défaut: première page), `--timeout` secondes (défaut 15). Chaque invocation
ouvre et ferme sa connexion: aucun état caché côté CLI, l'état vit dans le
navigateur.

**Sécurité.** `CDPX_ORIGINS` (liste de motifs séparés par des virgules, ex.
`http://*.localhost,http://*.test`) borne les mutations: `click`, `type`,
`key`, `eval`, `intercept`, `replay`, et toute commande composée dont l'action
mute la page sont refusés (exit 1) si l'origine de l'onglet n'est pas dans la
liste; les lectures restent permises. `--max-actions` plafonne le budget d'un
`replay`. Les valeurs de cookies sont masquées par défaut dans toutes les
sorties.

## Features

Le produit est découpé en 8 features. Chaque fiche est la **documentation
utilisateur exhaustive** de ses commandes (options, exemples, sorties JSON,
pièges) et la spec de preuve que le rapport de validation vérifie
mécaniquement.

| Feature | Ce que ça couvre | Commandes | Doc |
|---|---|---|---|
| Navigation et synchronisation | ouvrir, attendre l'état utile, onglets | `tabs`, `version`, `goto`, `wait` | [fiche](docs/features/browser-navigation.md) |
| Inspection du DOM et actions utilisateur | lire le rendu, agir en évènements trusted | `eval`, `text`, `html`, `count`, `click`, `type`, `key` | [fiche](docs/features/dom-interaction.md) |
| Capture et observabilité | pixels, PDF, console, réseau, métriques | `screenshot`, `pdf`, `console`, `network`, `metrics` | [fiche](docs/features/browser-capture-observability.md) |
| État et session | cookies (masqués), localStorage | `cookies`, `storage` | [fiche](docs/features/state-session.md) |
| Audits SEO, performance, accessibilité | contrat SEO du DOM rendu, vitals, AXTree, coverage | `seo`, `vitals`, `a11y`, `coverage` | [fiche](docs/features/seo-performance-accessibility.md) |
| Diagnostics développeur | profiler Symfony, diff DOM autour d'une action | `profiler`, `dom-diff` | [fiche](docs/features/dev-profiler-diff.md) |
| Interception, émulation, orchestration | mocker le réseau, émuler, scénariser, enregistrer/rejouer | `intercept`, `emulate`, `frame`, `record`, `replay`, `scenario` | [fiche](docs/features/orchestration-control.md) |
| Harness et cockpit de preuve | portails qualité et rapport de validation | cibles `make`, `python -m cdpx.proof` | [fiche](docs/features/harness-proof-cockpit.md) |

### Index des commandes

| Commande | En une ligne | Fiche |
|---|---|---|
| `cdpx tabs` | lister/créer/activer/fermer les onglets | [navigation](docs/features/browser-navigation.md) |
| `cdpx version` | identité du Chrome ciblé avant d'agir | [navigation](docs/features/browser-navigation.md) |
| `cdpx goto` | naviguer et attendre le cycle de vie (`--wait`) | [navigation](docs/features/browser-navigation.md) |
| `cdpx wait` | attendre qu'un sélecteur existe (SPA, contenu injecté) | [navigation](docs/features/browser-navigation.md) |
| `cdpx eval` | exécuter du JS dans la page (`--await`) — dernier recours | [dom](docs/features/dom-interaction.md) |
| `cdpx text` | innerText d'un élément ou du body | [dom](docs/features/dom-interaction.md) |
| `cdpx html` | outerHTML d'un élément ou du document | [dom](docs/features/dom-interaction.md) |
| `cdpx count` | compter les éléments matchant un sélecteur | [dom](docs/features/dom-interaction.md) |
| `cdpx click` | clic trusted au centre de l'élément (Input domain) | [dom](docs/features/dom-interaction.md) |
| `cdpx type` | saisir du texte après focus réel (`--clear`) | [dom](docs/features/dom-interaction.md) |
| `cdpx key` | frappe clavier (Enter, Tab, Escape, flèches) | [dom](docs/features/dom-interaction.md) |
| `cdpx screenshot` | capture PNG/JPEG (`--full-page`, `--format`) | [capture](docs/features/browser-capture-observability.md) |
| `cdpx pdf` | imprimer la page en PDF | [capture](docs/features/browser-capture-observability.md) |
| `cdpx console` | logs et exceptions JS (`--duration` ou `--follow --max`) | [capture](docs/features/browser-capture-observability.md) |
| `cdpx network` | naviguer en capturant l'activité réseau (`--settle`) | [capture](docs/features/browser-capture-observability.md) |
| `cdpx metrics` | Performance.getMetrics (heap, nodes, layouts) | [capture](docs/features/browser-capture-observability.md) |
| `cdpx cookies` | get (masqué) / set / clear | [état](docs/features/state-session.md) |
| `cdpx storage` | localStorage / sessionStorage (`--kind`) | [état](docs/features/state-session.md) |
| `cdpx seo` | contrat SEO du DOM rendu + findings | [audits](docs/features/seo-performance-accessibility.md) |
| `cdpx vitals` | LCP/CLS/INP, interaction optionnelle (`--click`) | [audits](docs/features/seo-performance-accessibility.md) |
| `cdpx a11y` | arbre d'accessibilité compacté | [audits](docs/features/seo-performance-accessibility.md) |
| `cdpx coverage` | JS/CSS mort par fichier | [audits](docs/features/seo-performance-accessibility.md) |
| `cdpx profiler` | lire le profiler Symfony (X-Debug-Token-Link) | [diagnostics](docs/features/dev-profiler-diff.md) |
| `cdpx dom-diff` | diff DOM stable avant/après une action | [diagnostics](docs/features/dev-profiler-diff.md) |
| `cdpx intercept` | fulfill/block/continue les requêtes pendant un goto | [orchestration](docs/features/orchestration-control.md) |
| `cdpx emulate` | mobile / slow-3g / cpu-4x, action composée, `--reset` | [orchestration](docs/features/orchestration-control.md) |
| `cdpx frame` | lire du texte dans une iframe same-origin | [orchestration](docs/features/orchestration-control.md) |
| `cdpx record` | exécuter une action et la journaliser en NDJSON | [orchestration](docs/features/orchestration-control.md) |
| `cdpx replay` | rejouer un journal, stop à la première divergence | [orchestration](docs/features/orchestration-control.md) |
| `cdpx scenario` | exécuter un scénario métier YAML avec verdict et preuves | [orchestration](docs/features/orchestration-control.md) |

`cdpx --version` affiche la version du paquet.

## Qualité et preuve

```
make check-local           # boucle courte: lint + mypy + tests unitaires
make check                 # PORTAIL: local + Docker + Chrome + Symfony
make test-e2e              # e2e Chrome réel — Chrome/Chromium obligatoire
make docker-check          # check dans l'image portable cdpx-ci
make docker-e2e            # e2e Chrome réel dans Docker
make docker-symfony-e2e    # profiler contre une vraie app Symfony Docker
make proof                 # rapport HTML humain + preuves dans .proof/
make release               # portail final: tous les contrôles + wheel/sdist
```

Une release exige Docker/Compose, Chrome réel et la suite Symfony sans aucun
skip. `make proof` échoue si cette preuve runtime est indisponible;
`make check` est déjà le portail runtime complet et `make release` lui ajoute
le cockpit de preuve et les artefacts distribuables. `make check-local` est
une boucle de développement, pas un verdict de livraison.

Les tests unitaires tournent contre un **mock CDP** qui enregistre chaque
commande émise: on valide la sortie ET le protocole. Le e2e réutilise les
mêmes fixtures HTML (`tests/fixtures/`) servies par un serveur déterministe
(`cdpx.testing.fixture_server`).

**Le rapport de preuve est la documentation vivante du produit.** `make proof`
génère `.proof/proof-report.html`, un cockpit navigable par feature:

- **Features** — pour chaque feature: sa documentation utilisateur complète,
  ses parcours, ses scénarios given/when/then, les tests exécutés et leurs
  preuves (screenshots Chrome réels compris);
- **CLI** — la surface complète des commandes et le rattachement de chaque
  entrypoint à sa feature (une commande non rattachée fait échouer la preuve);
- **Validation** — la matrice milestone → preuve, les tests par module, les
  risques assumés et leurs mitigations;
- **Gaps** — violations et warnings du catalogue (zéro exigé pour release);
- **Run** — chaque commande du run, les suites JUnit, les tests en échec ou
  les plus lents, les fins de logs.

La cohérence doc ↔ code est **mécanique**: une commande sans doc utilisateur,
un exemple `cdpx` invalide, une fiche non routée depuis ce README ou un
entrypoint non rattaché cassent `make check` / `make proof`.

## Docs annexes

- [CLAUDE.md](CLAUDE.md) — ancre agent: mission, invariants, boucle de travail
- [HARNESS.md](HARNESS.md) — sécurité, déterminisme, supervision
- [docs/CONTEXT.md](docs/CONTEXT.md) — pourquoi ce projet existe, décisions
- [docs/PRIMITIVES.md](docs/PRIMITIVES.md) — catalogue usecases par feature
- [docs/VALIDATION.md](docs/VALIDATION.md) — portails et matrice de preuve
- [docs/ROADMAP.md](docs/ROADMAP.md) + [docs/milestones/](docs/milestones/) — M0..M6
- [docs/TODO.md](docs/TODO.md) — liste de travail
- [docs/RELEASE-PLAN.md](docs/RELEASE-PLAN.md) — plan et suivi de la release

## Licence

Logiciel propriétaire — © inem0o, tous droits réservés. Usage interne
uniquement; voir [LICENSE](LICENSE).
