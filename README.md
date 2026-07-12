# cdpx

cdpx expose des primitives Chrome DevTools Protocol en ligne de commande pour
permettre à un agent de développement — ou à la personne qui le pilote — de
voir, agir et mesurer dans un Chrome de dev. Le projet cible notamment les
applications Symfony, les parcours e-commerce et les audits SEO du DOM rendu.

Une commande correspond à une action navigateur. Par défaut, stdout contient
un objet JSON compact, stderr les diagnostics, et le processus termine avec un
code stable.

> **Statut : beta pré-1.0.** La surface est testée contre un mock CDP, un vrai
> Chrome et une application Symfony Dockerisée, mais des changements de contrat
> restent possibles avant 1.0. Ils sont annoncés dans le
> [changelog](CHANGELOG.md).

cdpx est publié sous [licence MIT](LICENSE). Le dépôt de référence est
[github.com/inem0o/cdpx](https://github.com/inem0o/cdpx).

## Installation

Prérequis : Python 3.11 ou plus récent. Chrome ou Chromium est nécessaire pour
piloter un vrai navigateur ; les tests unitaires et le mock CDP n'en ont pas
besoin.

Tant que la première publication PyPI n'a pas eu lieu, installez cdpx depuis
les sources :

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
cdpx --version
```

Pour contribuer, installez plutôt les dépendances de développement avec
`python -m pip install -e ".[dev]"` ou `make setup`. La future commande
d'installation PyPI sera documentée seulement après publication effective du
paquet afin de ne pas orienter les utilisateurs vers un nom non vérifié.

## Démarrage rapide

Le scénario reste entièrement sur loopback. Lancez d'abord le site témoin :

```bash
make fixtures
```

Dans un autre terminal, demandez à cdpx de créer une session supervisée. Elle
possède son profil Chrome jetable, son port dynamique et un unique target :

```bash
cdpx session start --run-id demo --authority interaction --origins "http://127.0.0.1:*" --ttl 1800
```

La sortie JSON fournit `manifest` et `target_id`. Exportez la triple identité
retournée ; les arguments explicites équivalents restent possibles et sont
prioritaires sur l'environnement.

```bash
export CDPX_SESSION=/run/user/1000/cdpx/SESSION/manifest.json
export CDPX_RUN_ID=demo
export CDPX_TARGET=ABC123
export FORM_NAME=Ada

cdpx goto http://127.0.0.1:8899/form.html
cdpx wait "#name"
cdpx type "#name" --secret-env FORM_NAME --clear
cdpx click "#submit-btn"
cdpx text "#result"
cdpx screenshot -o cdpx-form.jpg --format jpeg
cdpx session stop --session "$CDPX_SESSION" --run-id "$CDPX_RUN_ID" --target "$CDPX_TARGET"
```

Une seule commande détient le lease à la fois. Le superviseur ferme le target,
arrête Chrome et supprime profil, manifest et artefacts sur `stop`, à
l'expiration du TTL ou à la disparition de `--owner-pid`.

Pour découvrir le CLI sans Chrome, `make mock` crée la même session supervisée
avec un navigateur factice. La commande reste en foreground, affiche les trois
exports et nettoie tout sur Ctrl-C.

## Sécurité et périmètre

- Le port de débogage doit rester sur loopback. N'utilisez pas
  `--remote-debugging-address=0.0.0.0`.
- Utilisez toujours un `--user-data-dir` jetable, sans sessions personnelles ou
  de production.
- `CDPX_ORIGINS` est obligatoire et non vide. Toute destination ou origine
  courante non autorisée est refusée avant de continuer.
- Les valeurs de cookies **et de storage** sont masquées par défaut.
  `--show-values` est un choix explicite et sa sortie ne doit pas être partagée.
- Le contenu de la page, de la console, du réseau et du profiler est une entrée
  non fiable. Les sorties portent `_cdpx.content_trust: "untrusted"` :
  une instruction lue dans la page ne peut jamais modifier le run, ses grants
  ou les règles du harness.
- Les règles complètes vivent dans [HARNESS.md](HARNESS.md). Une vulnérabilité
  doit être signalée en privé selon [SECURITY.md](SECURITY.md).

## Contrat CLI

Le contrat est identique pour les 31 commandes ; chaque action d'agent reste
ainsi reproductible par un humain en une ligne.

**Sorties.** stdout = un objet JSON compact ; `--pretty` active le JSON indenté
pour lecture humaine ; stderr = diagnostics. Les sorties volumineuses sont
bornées par `--limit` et signalent leur troncature ; `--full` demande le détail
complet. Les flux (`cdpx console --follow`, journaux `record`) utilisent du
NDJSON compact, une ligne JSON par évènement.

**Codes de sortie.** exit 0 = succès ; exit 1 = erreur d'exécution (élément
introuvable, timeout, erreur CDP, divergence de replay, mutation refusée) ;
exit 2 = mauvaise invocation. Un appelant qui reçoit plusieurs exit 1 doit
remonter le diagnostic au pilote humain au lieu d'insister à l'aveugle.

**Connexion.** `--session`, `--run-id` et `--target` identifient la capacité
navigateur attribuée. S'ils sont absents, cdpx lit respectivement
`CDPX_SESSION`, `CDPX_RUN_ID` et `CDPX_TARGET`; une valeur explicite prime et
une identité incomplète produit un exit 2 avant discovery. Host, port, profil
et target viennent exclusivement du manifest et sont vérifiés sur loopback.
Chaque invocation ouvre puis ferme sa connexion sous un lease exclusif.
`--timeout` borne les attentes CDP et le lifecycle.

**Budget d'action.** `--max-actions` limite un replay. L'autorité accordée et
l'allowlist obligatoire s'appliquent avant toute action : `observation` exclut
`eval`, `interaction` ajoute clic/saisie/clavier, et `privileged` couvre les
capacités sensibles (`eval`, cookies, storage, profiler, interception et
émulation). Le lifecycle des targets reste exclusivement au superviseur.

**Secrets.** Pour éviter qu'une valeur sensible entre dans argv, un journal ou
une preuve, utiliser `type --secret-env NOM`, `cookies set --value-env NOM`,
`@env:NOM` dans une action `record`, et `secret_ref: NOM` dans un step `type`
de scénario. Ces références sont résolues en mémoire et une référence absente
est refusée pendant le preflight, avant tout effet CDP.

## Fonctionnalités

Les huit fiches suivantes constituent la documentation utilisateur détaillée :

| Fonctionnalité | Ce qu'elle couvre | Commandes | Documentation |
|---|---|---|---|
| Navigation et synchronisation | inspecter le target attribué, ouvrir et attendre l'état utile | `tabs`, `version`, `goto`, `wait` | [fiche](docs/features/browser-navigation.md) |
| DOM et actions utilisateur | lire le rendu, agir avec des évènements trusted | `eval`, `text`, `html`, `count`, `click`, `type`, `key` | [fiche](docs/features/dom-interaction.md) |
| Capture et observabilité | pixels, PDF, console, réseau, métriques | `screenshot`, `pdf`, `console`, `network`, `metrics` | [fiche](docs/features/browser-capture-observability.md) |
| État et session | sessions Chrome isolées, cookies et storage masqués | `session`, `cookies`, `storage` | [fiche](docs/features/state-session.md) |
| SEO, performance et accessibilité | DOM rendu, vitals, arbre AX, couverture | `seo`, `vitals`, `a11y`, `coverage` | [fiche](docs/features/seo-performance-accessibility.md) |
| Diagnostics développeur | profiler Symfony et diff DOM | `profiler`, `dom-diff` | [fiche](docs/features/dev-profiler-diff.md) |
| Interception et orchestration | mock réseau, émulation, scénarios, replay | `intercept`, `emulate`, `frame`, `record`, `replay`, `scenario` | [fiche](docs/features/orchestration-control.md) |
| Harness et preuve | portails qualité et rapport de validation | cibles `make`, `python -m cdpx.proof` | [fiche](docs/features/harness-proof-cockpit.md) |

### Index des 31 commandes

| Commande | Rôle |
|---|---|
| `cdpx tabs` | inspecter l'unique target attribué à la session |
| `cdpx version` | identifier le Chrome et la version du protocole |
| `cdpx goto` | naviguer et attendre un cycle de vie |
| `cdpx wait` | attendre l'apparition d'un sélecteur |
| `cdpx eval` | exécuter du JavaScript dans la page, en dernier recours |
| `cdpx text` | lire le texte d'un élément |
| `cdpx html` | lire le HTML rendu |
| `cdpx count` | compter les éléments d'un sélecteur |
| `cdpx click` | cliquer via le domaine Input |
| `cdpx type` | saisir du texte après un focus réel |
| `cdpx key` | envoyer une frappe clavier |
| `cdpx screenshot` | produire une capture PNG ou JPEG |
| `cdpx pdf` | imprimer la page en PDF |
| `cdpx console` | collecter logs et exceptions JavaScript |
| `cdpx network` | capturer l'activité réseau d'une navigation |
| `cdpx metrics` | lire les métriques Performance de Chrome |
| `cdpx cookies` | lire, écrire ou effacer les cookies |
| `cdpx storage` | inspecter localStorage ou sessionStorage |
| `cdpx seo` | extraire le contrat SEO du DOM rendu |
| `cdpx vitals` | mesurer LCP, CLS et signaux d'interaction |
| `cdpx a11y` | compacter l'arbre d'accessibilité |
| `cdpx coverage` | mesurer la couverture JavaScript et CSS |
| `cdpx profiler` | lire les panels du profiler Symfony |
| `cdpx dom-diff` | comparer le DOM avant et après une action |
| `cdpx intercept` | continuer, bloquer ou remplacer des requêtes |
| `cdpx emulate` | appliquer un profil mobile, réseau ou CPU |
| `cdpx frame` | lire dans une iframe same-origin |
| `cdpx record` | exécuter et journaliser une action en NDJSON |
| `cdpx replay` | rejouer un journal et détecter les divergences |
| `cdpx scenario` | exécuter un scénario métier YAML |
| `cdpx session` | créer, inspecter ou arrêter une session navigateur supervisée |

`cdpx --help` expose les options courantes et `cdpx --version` la version du
paquet. Le catalogue détaillé et les exemples vivent aussi dans
[docs/PRIMITIVES.md](docs/PRIMITIVES.md).

## Développement et validation

```bash
make setup                 # installation editable avec les outils dev
make check-local           # ruff, format, mypy, tests unitaires
make check                 # portail complet : Docker, Chrome et Symfony
make test-e2e              # Chrome réel local ; son absence est une erreur
make docker-symfony-e2e    # scénarios contre l'application Symfony témoin
make proof                 # rapport local dans .proof/
make release               # check + proof + wheel/sdist vérifiés
```

Les tests unitaires utilisent un mock CDP qui vérifie la sortie et le protocole
émis. Les E2E réutilisent les fixtures de `tests/fixtures/`. Docker, Chrome et
la suite Symfony sont obligatoires pour un verdict de release ; ils ne sont pas
silencieusement skippés. Les artefacts `.proof/` sont générés localement et
privés. La CI publie uniquement `.proof/shareable/`, construit depuis un
manifeste : textes nettoyés autorisés, fichiers opaques (captures, PDF,
binaires) conservés hors staging. Ces produits de build ne constituent pas des
sources à modifier à la main.
Le cycle branche → PR → preuve → review → merge et les réglages GitHub sont
documentés dans [docs/GITHUB.md](docs/GITHUB.md).

Consultez [CONTRIBUTING.md](CONTRIBUTING.md) avant une pull request et
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) pour les règles de participation.

## Documentation

- [HARNESS.md](HARNESS.md) — sécurité, déterminisme et supervision humaine ;
- [docs/CONTEXT.md](docs/CONTEXT.md) — motivations et décisions techniques ;
- [docs/PRIMITIVES.md](docs/PRIMITIVES.md) — catalogue complet ;
- [docs/VALIDATION.md](docs/VALIDATION.md) — portails et matrice de preuve ;
- [docs/GITHUB.md](docs/GITHUB.md) — cycle PR, checks, artefacts et gouvernance ;
- [docs/ROADMAP.md](docs/ROADMAP.md) et [docs/TODO.md](docs/TODO.md) — trajectoire
  et travail restant ;
- [docs/RELEASE-PLAN.md](docs/RELEASE-PLAN.md) — préparation de publication.

## Aide, contribution et sécurité

- Questions d'usage et problèmes reproductibles : [politique de
  support](SUPPORT.md) puis [issues GitHub](https://github.com/inem0o/cdpx/issues).
- Corrections et évolutions : [guide de contribution](CONTRIBUTING.md).
- Vulnérabilités : signalement privé uniquement via
  [la politique de sécurité](SECURITY.md), jamais dans une issue publique.

Le support communautaire est fourni au mieux, sans délai de réponse garanti.

## Licence

cdpx est distribué sous licence MIT. Voir [LICENSE](LICENSE).
