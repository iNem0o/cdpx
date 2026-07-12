+++
id = "dev-profiler-diff"
title = "Diagnostics développeur"
status = "validated"
summary = "Parser les panels du Web Profiler Symfony (Doctrine, Twig, cache, exceptions, HTTP client, Messenger, routing, temps, logs) depuis une navigation navigateur, puis comparer le DOM avant/après une action."
entrypoints = ["cdpx profiler", "cdpx dom-diff", "make docker-symfony-e2e"]
path_globs = ["src/cdpx/primitives/dev.py", "src/cdpx/primitives/profiler_panels.py", "tests/fixtures/profiler/**", "tests/fixtures/form.html", "docker-compose.symfony-e2e.yml", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**", "tests/test_profiler_panels.py"]
test_globs = ["tests/test_profiler_panels.py::*", "tests/test_primitives.py::test_profiler*", "tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_profiler*", "tests/test_cli.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*", "tests/e2e/test_e2e_symfony.py::*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M2-boucle-symfony.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-profiler"
title = "Lire le profiler Symfony depuis une navigation navigateur"
entrypoint = "cdpx profiler"

[[journeys]]
id = "compare-profiler-variants"
title = "Comparer des variantes déterministes du profiler Symfony"
entrypoint = "make docker-symfony-e2e"

[[journeys]]
id = "diff-dom-action"
title = "Comparer le DOM avant et après une action"
entrypoint = "cdpx dom-diff"

[[scenarios]]
id = "read-symfony-profiler"
journey = "read-profiler"
title = "Lire les données du profiler Symfony depuis une navigation"
ui_text = "L'agent peut ouvrir une page Symfony et suivre les preuves du profiler."
report_text = "Ce scénario prouve que les diagnostics framework sont accessibles depuis une navigation navigateur. `make proof` tente automatiquement le vrai portail Docker Symfony, enregistre un Docker indisponible comme statut explicite non bloquant, et bloque le verdict quand Docker est disponible mais que le scénario Symfony échoue."
given = "Une fixture ou l'app de test Symfony expose des en-têtes et pages de type profiler."
when = "cdpx lit les données du profiler après navigation pendant l'e2e Chrome et, quand Docker est disponible, via le portail e2e Symfony."
then = "Le rapport lie les tests profiler, le statut Docker, JUnit, logs, la sortie profiler JSON et les captures d'écran à la feature diagnostics développeur."
tests = ["tests/test_profiler_panels.py::*", "tests/test_primitives.py::test_profiler*", "tests/test_cli.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_symfony.py::*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compare-symfony-profiler-variants"
journey = "compare-profiler-variants"
title = "Comparer les variantes du profiler Symfony"
ui_text = "Le rapport compare des variantes déterministes du profiler Symfony."
report_text = "Ce scénario prouve que baseline/dégradé, N+1 façon Doctrine, rafales de requêtes dupliquées, cache hit/miss/expiré, coût de rendu Twig, sections Stopwatch, issues du client HTTP, messages Messenger, issues de routing et en-têtes de cache de réponse sont lus dans les vrais panels du WebProfiler et disponibles comme preuves Symfony structurées."
given = "L'app de test Symfony exerce de vrais collecteurs (Doctrine, cache, HTTP client, Messenger...) sous `/scenario/profiler/{case}`."
when = "cdpx navigue chaque cas, suit le vrai token WebProfiler et parse le HTML des panels (db, twig, cache, exception, http_client, messenger, router, time, logger)."
then = "Le rapport lie les preuves JSON nettoyées, les logs Docker, JUnit et les captures d'écran privées à la feature diagnostics développeur sans publier le token profiler."
tests = ["tests/e2e/test_e2e_symfony.py::test_profiler_compares_deterministic_symfony_variants"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "diff-dom-after-action"
journey = "diff-dom-action"
title = "Comparer le DOM avant et après une action navigateur"
ui_text = "Le rapport explique ce qui a changé dans le DOM après une action."
report_text = "Ce scénario prouve que les changements du DOM peuvent être comparés autour d'une action navigateur contrôlée et passés en revue comme preuve développeur."
given = "Une page fixture a un état avant stable et une action utilisateur qui mute le DOM."
when = "cdpx enregistre le DOM avant et après l'action."
then = "Le diff est disponible comme preuve de test structurée avec des captures d'écran navigateur pour la couverture e2e."
tests = ["tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "symfony-front-state-regression"
journey = "diff-dom-action"
title = "Comparer l'état front Symfony avant et après action"
ui_text = "Le rapport montre une transition d'état front Symfony déterministe."
report_text = "Ce scénario prouve qu'une route Symfony peut exposer un état front contrôlé et que cdpx peut capturer le diff DOM après une action navigateur."
given = "Le moteur de scénarios Symfony expose `/scenario/front/states`."
when = "cdpx capture le DOM, clique le bouton de transition d'état et capture le DOM à nouveau."
then = "Le diff DOM et la capture d'écran sont attachés comme preuves Symfony."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_front_state_dom_diff"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intention

Donner un retour diagnostique conscient du framework sans obliger l'agent à
dépouiller manuellement une session navigateur complète. `cdpx profiler`
remonte les données du WebProfiler Symfony depuis une simple navigation;
`cdpx dom-diff` transforme "qu'est-ce qui a changé à l'écran ?" en un diff
DOM stable et relisible; `make docker-symfony-e2e` prouve le tout contre une
vraie application Symfony sous Docker.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `cdpx profiler`

Synopsis: `cdpx profiler url [--settle S] [--panels LISTE|all|none]`

Navigue vers `url`, repère l'en-tête `X-Debug-Token-Link` dans les réponses
réseau (repli sur `X-Debug-Token` en reconstruisant l'URL
`/_profiler/<token>`), puis va chercher les pages de panels du Web Profiler
**depuis la page elle-même** (`fetch()` même origine: cookies et résolution
d'hôte du navigateur, indispensable derrière Docker ou un port-forward) et
parse leur HTML. Le WebProfilerBundle n'exposant aucune API JSON, cdpx
extrait un contrat structuré par panel: `db` (requêtes, statements
distincts, duplicats, liste SQL), `twig` (appels de templates, blocks,
macros), `cache` (hits/misses/writes, par pool), `exception`
(classe/message), `http_client` (requêtes sortantes, statuts), `messenger`
(messages dispatchés par bus), `router` (route, contrôleur, statut,
redirection), `time` (temps total/init, timeline best-effort) et `logger`
(erreurs, warnings, dépréciations).

Options propres:

- `url` (positionnel, requis) — route de l'app Symfony à profiler.
- `--settle S` — fenêtre en secondes de collecte des événements réseau après
  le chargement, le temps que la réponse portant le token arrive
  (défaut: 0.2).
- `--panels` — `all` (défaut, les 9 panels), `none` (sonde token seule,
  aucun fetch de panel) ou une liste CSV
  (`router,time,db,twig,cache,exception,http_client,messenger,logger`);
  un nom inconnu est une erreur d'usage (exit 2).

```bash
# Parser tous les panels d'une route locale
cdpx profiler http://127.0.0.1:8000/produit/42

# Cibler la boucle Doctrine + cache uniquement
cdpx profiler http://127.0.0.1:8000/produit/42 --panels db,cache
```

Sortie (extrait réaliste, tronqué aux panels demandés):

```json
{
  "token_present": true,
  "url": "http://127.0.0.1:8000/produit/42",
  "status": 200,
  "profiler_url": "http://127.0.0.1:8000/_profiler/***",
  "profiler_status": 200,
  "response_headers": {"x-debug-token-link": "http://127.0.0.1:8000/_profiler/***"},
  "panels": {
    "db": {
      "available": true,
      "queries": 6,
      "statements": 2,
      "duplicates": 4,
      "time_ms": 1.76,
      "list": [{"sql": "SELECT ... FROM book b0_", "duration_ms": 0.42}]
    },
    "cache": {
      "available": true,
      "calls": 4,
      "hits": 3,
      "misses": 1,
      "writes": 1,
      "deletes": 0,
      "pools": {"app.scenario_pool": {"calls": 4, "hits": 3, "misses": 1, "writes": 1, "deletes": 0, "reads": 4}}
    }
  }
}
```

Pièges et cas d'erreur:

- **Breaking change** (post-0.1.0): les champs `signals` (en-têtes
  `X-CDPX-*`) et `profiler_bytes` ont disparu; `panels` est désormais un
  objet structuré par panel, plus jamais une enveloppe `raw`.
- Le token brut n'est jamais retourné : la sortie expose seulement
  `token_present`, masque le segment dans `profiler_url` et nettoie headers,
  URL/query, SQL/messages et résultats une seconde fois à la frontière stdout.
- Si aucune réponse ne porte `X-Debug-Token-Link` ni `X-Debug-Token`
  (profiler désactivé, environnement `prod`), la commande échoue avec
  `header X-Debug-Token-Link/X-Debug-Token introuvable` (exit 1).
- Un panel dont le collector n'est pas installé (pas de doctrine-bundle,
  pas de messenger...) sort en `{"available": false}` — ce n'est pas une
  erreur. Un panel présent mais au markup imprévu sort en
  `{"available": true, "parse_error": ...}`: le parsing ne lève jamais.
- Le parsing est couplé au markup HTML du WebProfilerBundle 7.x (blocs
  metric label/valeur, tables). Une version majeure de Symfony peut le
  déplacer: les fixtures committées (`tests/fixtures/profiler/`) figent le
  contrat et leur README documente la re-capture.
- Les durées (`*_ms`) sont indicatives; n'asserter que comptes, classes,
  routes et statuts.
- `--settle` trop court = token raté si la réponse arrive tard; augmenter la
  fenêtre plutôt que de relancer en boucle.

### `cdpx dom-diff`

Synopsis: `cdpx dom-diff -- <action>`

Prend un instantané normalisé du DOM (balises, id, classes triées, attributs
`data-*`, textes), exécute **une** action, reprend un instantané, puis rend
un diff unifié stable. Usecase: vérifier qu'un clic ouvre bien l'off-canvas
panier, qu'un submit affiche l'erreur attendue, qu'une route SPA remplace le
bon fragment — sans relire deux pages HTML complètes.

Les actions acceptées viennent de l'interpréteur partagé
(`src/cdpx/primitives/actions.py`), le même que `record`, `replay` et
`emulate`:

- `goto <url>` — naviguer.
- `wait <selecteur>` — attendre un sélecteur CSS.
- `click <selecteur>` — cliquer un élément.
- `type <selecteur> <texte> [--clear]` — taper un texte non sensible (option
  `--clear` pour vider le champ avant). Les secrets appartiennent aux surfaces
  dédiées qui acceptent une référence d'environnement.
- `key <touche>` — presser une touche (Enter, Tab, Escape, ArrowUp/Down).
- `eval <js>` — évaluer du JavaScript.

Options propres:

- `action` (positionnel, reste de la ligne) — l'action à encadrer; le
  séparateur `--` est supporté et recommandé pour isoler l'action des
  options de cdpx.

```bash
# Le clic ouvre-t-il l'off-canvas panier ?
cdpx dom-diff -- click "#offcanvas-cart"

# Diff entre la page courante et une autre route (lecture pure)
cdpx dom-diff -- goto http://127.0.0.1:8000/panier

# La saisie déclenche-t-elle l'autocomplétion ?
cdpx dom-diff -- type "#recherche" "chaussures trail" --clear
```

Sortie:

```json
{
  "action": ["click", "#offcanvas-cart"],
  "changed": true,
  "diff": [
    "--- before",
    "+++ after",
    "@@ -12,6 +12,9 @@",
    "   <div#offcanvas-cart.cart>",
    "+    <div.cart-panel.open>",
    "+      \"1 article - 89,00 EUR\""
  ],
  "lines": 6
}
```

Pièges et cas d'erreur:

- **Sécurité** : l'allowlist est obligatoire et l'autorité suit l'action
  (`eval` exige `privileged`), y compris pour les lectures. Ne jamais passer de
  secret à l'action composée `type`; utiliser `cdpx type --secret-env` ou un
  scénario avec `secret_ref`.
- Une action absente ou inconnue échoue avec le rappel d'usage de
  l'interpréteur (exit 2 pour une erreur d'usage).
- `changed: false` avec `diff: []` est un résultat valide: l'action n'a rien
  mué — précieux pour détecter un bouton mort.
- Le diff est borné par `--limit` (50 lignes par défaut); passer `--full`
  pour un diff complet sur les grosses mutations.

### `make docker-symfony-e2e`

Synopsis: `make docker-symfony-e2e`

Lance la suite e2e profiler contre une **vraie** application Symfony servie
par Docker (`docker-compose.symfony-e2e.yml` + `tests/symfony-app/`): les
contrôleurs de scénarios exercent de **vrais collecteurs** (requêtes
Doctrine réelles sur SQLite — N+1 et duplicats compris —, pool de cache,
client HTTP vers des endpoints locaux, messages Messenger, exceptions et
redirections) sous `/scenario/profiler/{case}`, et `/scenario/front/states`
pour le diff DOM. C'est la preuve que `cdpx profiler` parse les vrais panels
du WebProfiler, pas seulement les fixtures HTML committées.

Options propres: aucune (cible Make sans paramètre; Docker et Docker Compose
doivent être installés et démarrables).

```bash
make docker-symfony-e2e
```

Les preuves produites atterrissent dans `.proof/` (`symfony-e2e.log`,
`symfony-e2e-junit.xml`, JSON de comparaison profiler, diff DOM JSON,
captures d'écran).

Pièges et cas d'erreur:

- Docker absent : la cible et la preuve de release échouent avec un statut
  `unavailable` explicite ; il n'existe pas de succès release dégradé.
- Docker présent mais scénario Symfony en échec: le verdict global de
  `make proof` est bloqué — un vrai échec ne se déguise pas en absence.
- Le premier lancement construit l'image Symfony: prévoir un temps de build
  initial avant que les scénarios ne s'exécutent.

## Parcours utilisateur

- Naviguer vers une route Symfony, suivre le token du profiler et lire les
  panels parsés (Doctrine, Twig, cache, exceptions, HTTP client, Messenger,
  routing, temps, logs).
- Comparer baseline/dégradé, N+1 façon Doctrine, rafales de requêtes
  dupliquées, cache hit/miss/expiré, coût de rendu Twig, sections Stopwatch,
  issues du client HTTP, messages Messenger, issues de routing et en-têtes
  de cache de réponse — à partir des vrais panels.
- Prendre un diff DOM stable autour d'une action navigateur.

## Validation

Les parseurs de panels sont validés unitairement sur du HTML committé
(`tests/fixtures/profiler/`, markup WebProfilerBundle réel élagué), servi
aussi par le serveur de fixtures pour l'e2e Chrome. `make proof` exécute le
portail Docker Symfony : indisponibilité, skip ou échec bloque le verdict.

## Preuves

Les preuves locales attendues sont JUnit et captures d'écran privées pour les
scénarios Chrome. Le portail Symfony ajoute `.proof/symfony-e2e.log`,
`.proof/symfony-e2e-junit.xml`, le JSON de comparaison des diagnostics
profiler, le JSON de diff DOM et des captures d'écran navigateur. Les captures
opaques restent hors `.proof/shareable/`.

## Limites connues

La disponibilité de Docker dépend de l'environnement; son absence bloque la
preuve et se résout en installant Docker puis en relançant `make proof` ou
`make docker-symfony-e2e`. Le parsing des panels est couplé
au markup HTML du WebProfilerBundle 7.x (aucune API JSON n'existe côté
Symfony): une évolution majeure du bundle peut demander une re-capture des
fixtures et un ajustement des parseurs — le contrat de tolérance
(`available`/`parse_error`, jamais d'exception) garantit qu'entre-temps la
commande dégrade proprement au lieu de casser.
