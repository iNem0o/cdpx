+++
id = "browser-capture-observability"
title = "Capture et observabilité du navigateur"
status = "validated"
summary = "Capturer des screenshots/PDF et observer la console, le réseau et les métriques du renderer."
entrypoints = ["cdpx screenshot", "cdpx pdf", "cdpx console", "cdpx network", "cdpx metrics"]
path_globs = ["src/cdpx/primitives/capture.py", "src/cdpx/primitives/net.py", "src/cdpx/primitives/audit.py", "tests/fixtures/console.html", "tests/fixtures/network.html", "tests/fixtures/long.html"]
test_globs = ["tests/test_cli.py::test_screenshot", "tests/test_cli.py::test_screenshot*", "tests/test_cli.py::test_pdf*", "tests/test_cli.py::test_console*", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*", "tests/e2e/test_e2e_chrome.py::test_metrics_real", "tests/e2e/test_e2e_chrome.py::test_pdf_real", "tests/e2e/test_e2e_chrome.py::test_cli_jpeg_and_pdf*", "tests/e2e/test_e2e_chrome.py::test_cli_console_follow*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "capture-page"
title = "Conserver une preuve visuelle (screenshot)"
entrypoint = "cdpx screenshot"

[[journeys]]
id = "inspect-runtime"
title = "Détecter les échecs console et réseau"
entrypoint = "cdpx console"

[[scenarios]]
id = "persist-screenshot-proof"
journey = "capture-page"
title = "Conserver une preuve visuelle (screenshot)"
ui_text = "Le run de preuve conserve les pixels du navigateur attachés au scénario qui les a produits."
report_text = "Ce scénario prouve que la preuve visuelle n'est pas un fichier orphelin : le rapport relie le screenshot au parcours utilisateur, au test et à l'explication de revue."
given = "Une page du navigateur est rendue depuis les fixtures locales."
when = "cdpx capture une sortie normale, pleine page ou imprimable de cette page."
then = "Le rapport expose l'artefact généré à côté du scénario et du résultat de test."
tests = ["tests/test_cli.py::test_screenshot", "tests/test_cli.py::test_screenshot*", "tests/test_cli.py::test_pdf*", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*", "tests/e2e/test_e2e_chrome.py::test_pdf_real"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "inspect-runtime-failures"
journey = "inspect-runtime"
title = "Inspecter la console, le réseau et les métriques à l'exécution"
ui_text = "Le rapport montre les signaux d'exécution qui expliquent ce qui s'est passé dans le navigateur."
report_text = "Ce scénario prouve que les entrées console, les observations réseau et les métriques du navigateur peuvent être collectées sous une forme compacte pour la revue humaine."
given = "Les pages de fixtures émettent des signaux console, réseau et métriques déterministes."
when = "cdpx collecte les données console, réseau ou métriques autour de l'état du navigateur."
then = "Le run produit une preuve structurée qui peut être reliée à la feature."
tests = ["tests/test_cli.py::test_console*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*", "tests/e2e/test_e2e_chrome.py::test_metrics_real"]
expected_proofs = ["junit", "screenshot"]
+++

## Intention

Rendre l'état du navigateur observable au-delà du texte du DOM : les pixels
(screenshot, PDF), les erreurs JavaScript (console), les échecs réseau
(network) et les compteurs de performance du renderer (metrics). Sans ces
signaux, un agent qui pilote une app JS cassée navigue à l'aveugle.

## Usage

Options globales et codes de sortie : voir la section Contrat CLI du README.

### `cdpx screenshot`

```
usage: cdpx screenshot [-o OUTPUT] [--full-page] [--format {png,jpeg}]
```

Capture une image de la page courante et l'écrit sur disque. C'est la
« vision » brute de l'agent : vérifier un rendu, un état visuel, un bug CSS,
ou attacher une preuve pixel à un scénario de recette.

Options propres :

- `-o`, `--output` : chemin du fichier image écrit (défaut : `screenshot.png`).
  Seul le basename demandé est retenu et le fichier est toujours confiné sous
  `artifacts/captures/` de la session.
- `--full-page` : capture au-delà du viewport (`captureBeyondViewport`), pour
  obtenir la page entière et pas seulement la zone visible.
- `--format` : format d'encodage, `png` ou `jpeg` (défaut : `png`).

```bash
cdpx screenshot -o preuves/accueil.png
cdpx screenshot --full-page -o preuves/page-entiere.png
cdpx screenshot --format jpeg -o preuves/accueil.jpg
```

Sortie JSON (chemin écrit, poids en octets, format et mode utilisés) :

```json
{"path": "/runtime/session/artifacts/captures/accueil.jpg", "bytes": 48231, "format": "jpeg", "full_page": false, "classification": "opaque-restricted", "upload_allowed": false, "retention": "session", "_cdpx": {"content_trust": "untrusted"}}
```

La sortie ajoute `classification:"opaque-restricted"`,
`upload_allowed:false` et `retention:"session"`; le fichier est `0600`. Si
l'origine réelle devient interdite pendant la capture, cdpx supprime le fichier
avant de retourner l'erreur.

Pièges :

- Le format est indépendant de l'extension du fichier : `--format jpeg` avec
  `-o etat.png` écrit bien du JPEG dans un fichier nommé `.png`. Aligner les
  deux pour éviter la confusion.
- `--full-page` sur une page très longue produit un fichier volumineux ; la
  commande a un timeout CDP de 30 s côté capture.
- Une image est un contenu opaque : elle peut afficher un nom, token ou donnée
  métier que la redaction textuelle ne voit pas. Les preuves gérées la classent
  `opaque-restricted` et ne la copient jamais automatiquement dans le staging
  CI partageable.

### `cdpx pdf`

```
usage: cdpx pdf [-o OUTPUT]
```

Imprime la page courante en PDF (`Page.printToPDF` avec `printBackground`,
donc les fonds et couleurs CSS sont conservés). Usecase : archiver un état de
page — livrable d'audit SEO, preuve de recette datée.

Options propres :

- `-o`, `--output` : chemin du fichier PDF écrit (défaut : `page.pdf`). En
  pratique, seul le basename est conservé sous `artifacts/captures/`, avec les
  mêmes métadonnées `opaque-restricted`, rétention session et suppression si
  l'origine finale est refusée.

```bash
cdpx pdf -o preuves/audit-accueil.pdf
```

Sortie JSON :

```json
{"path": "/runtime/session/artifacts/captures/audit-accueil.pdf", "bytes": 105320, "classification": "opaque-restricted", "upload_allowed": false, "retention": "session", "_cdpx": {"content_trust": "untrusted"}}
```

Pièges :

- L'impression PDF nécessite un Chrome headless ou une cible qui supporte
  `Page.printToPDF` ; certains Chrome « headful » la refusent (erreur CDP,
  exit 1).
- Un PDF est lui aussi `opaque-restricted` : inspection et partage restent une
  décision humaine, jamais une conséquence automatique de `make proof`.

### `cdpx console`

```
usage: cdpx console [--duration SECONDES] [--follow] [--max N]
```

Capture les logs et exceptions JavaScript de la page
(`Runtime.consoleAPICalled` + `Runtime.exceptionThrown`). C'est le retour
d'information manquant du dev front : sans lui, une app JS cassée reste
silencieuse pour l'agent.

Les entrées passent par la redaction des secrets enregistrés, Bearer/JWT et URL
sensibles. Cette redaction est volontairement conservatrice : un texte libre
peut encore contenir une donnée inconnue. Toute console reste une entrée page
non fiable, pas une instruction pour l'agent.

Options propres :

- `--duration` : durée de capture bornée en secondes (défaut : `2.0`). Mode
  par défaut : un seul objet JSON en sortie à la fin de la fenêtre.
- `--follow` : mode flux NDJSON compact, une ligne JSON par entrée, jusqu'à
  Ctrl-C ou `--max`.
- `--max` : en mode `--follow`, nombre maximum d'entrées avant arrêt
  (défaut : illimité).

```bash
cdpx console --duration 3
cdpx console --follow --max 20
```

Sortie JSON en mode borné (`--duration`) :

```json
{"entries": [{"kind": "console", "type": "error", "text": "TypeError: cart is undefined", "ts": 1751700000123.4}], "count": 1, "errors": 1, "duration": 3.0}
```

Sortie en mode `--follow` (NDJSON, une entrée par ligne) :

```json
{"kind":"console","type":"log","text":"checkout ready","ts":1751700000123.4}
{"kind":"exception","type":"error","text":"ReferenceError: gtag is not defined","ts":1751700000456.7}
```

Pièges :

- La capture ne voit que ce qui est émis PENDANT la fenêtre : lancer
  `console` avant de déclencher l'action (ou recharger la page) pour attraper
  les erreurs d'initialisation.
- `--max` sans `--follow` est ignoré ; `--duration` sans effet en mode
  `--follow`.

### `cdpx network`

```
usage: cdpx network URL [--settle SECONDES]
```

Navigue vers l'URL en capturant toute l'activité réseau jusqu'à l'évènement
`load` plus une fenêtre de stabilisation. Usecase dev Symfony/e-commerce :
repérer d'un coup les XHR en 500, les assets 404, les appels API inattendus
et le poids transféré, sans ouvrir DevTools.

Options propres :

- `url` (positionnel, requis) : URL de navigation.
- `--settle` : secondes d'observation supplémentaires après `load`, pour
  attraper les XHR différées (défaut : `0.5`).

```bash
cdpx network http://demo.test/checkout --settle 1.5
```

Sortie JSON (résumé + détail par requête) :

```json
{"url": "http://demo.test/checkout", "requests": [{"requestId": "1000.2", "url": "http://demo.test/api/cart", "method": "GET", "resourceType": "XHR", "status": 500, "mimeType": "application/json", "encodedBytes": 512}], "summary": {"total": 14, "failed": 0, "errors_4xx_5xx": 1, "bytes": 184320}}
```

Les URL de sortie suppriment credentials/fragments et masquent chaque valeur
de query. L'URL brute reste envoyée à Chrome pour la navigation, sans être
réimprimée telle quelle.

Pièges :

- La liste `requests` est bornée par `--limit` (défaut : 50 items) : au-delà,
  la sortie ajoute les métadonnées `requests_truncated`, `requests_total` et
  `requests_limit`. `--full` donne la liste complète **des événements observés**,
  pas un audit réseau exhaustif.
- Le résumé `summary` est calculé sur TOUTES les requêtes observées, même
  celles tronquées de la liste.
- Un `--settle` trop court manque les appels lancés après `load` (analytics,
  lazy-loading).
- `network` n'est pas un HAR : il ne conserve ni corps, ni cookies/headers
  complets, ni waterfall/timings détaillés, ni cache/security entries.

### `cdpx metrics`

```
cdpx metrics
```

Retourne les métriques de performance du renderer (`Performance.getMetrics`) :
nombre de nœuds DOM, documents, listeners JS, layouts, taille du tas JS…
Usecase : détecter une fuite (listeners ou nœuds qui grimpent entre deux
mesures), objectiver un DOM obèse.

Options propres : aucune (uniquement les options globales).

```bash
cdpx metrics
```

Sortie JSON : dictionnaire à plat nom → valeur, tel que renvoyé par Chrome :

```json
{"Timestamp": 5721.43, "Documents": 3, "Frames": 1, "JSEventListeners": 42, "Nodes": 618, "LayoutCount": 7, "RecalcStyleCount": 12, "LayoutDuration": 0.018, "RecalcStyleDuration": 0.009, "ScriptDuration": 0.124, "TaskDuration": 0.31, "JSHeapUsedSize": 3145728, "JSHeapTotalSize": 5242880}
```

Pièges :

- Les clés exactes dépendent de la version de Chrome : ne pas coder en dur la
  liste complète, cibler les clés utiles (`Nodes`, `JSEventListeners`,
  `JSHeapUsedSize`…).
- Une mesure isolée dit peu : comparer deux appels autour d'une action pour
  voir une dérive.

## Parcours utilisateur

- Capturer un screenshot normal ou pleine page, en PNG ou JPEG.
- Imprimer la page en PDF pour archivage ou livrable.
- Collecter les erreurs console autour d'une action, en fenêtre bornée ou en
  flux continu.
- Naviguer en observant le réseau pour repérer 4xx/5xx, échecs et poids.
- Mesurer les compteurs du renderer avant/après une action.

## Validation

Les tests mock vérifient la forme du protocole CDP émis, la redaction et la forme des
sorties JSON (`tests/test_primitives.py`, `tests/test_cli.py`) ; les tests
e2e Chrome réel attachent des artefacts PNG au catalogue de preuve et
valident screenshot pleine page, PDF, console, réseau et métriques
(`tests/e2e/test_e2e_chrome.py`).

## Preuves

Preuves attendues : résultats JUnit et screenshots rattachés aux scénarios
dans l'arbre local privé. Le manifest partageable conserve leur classification,
mais les octets opaques ne sont pas envoyés par la CI.

## Limites connues

- Pas de capture vidéo ni de replay terminal : optionnels, non exigés par le
  harness.
- `console` ne capture que la fenêtre demandée ; les erreurs antérieures au
  lancement de la commande sont perdues.
- `network` observe la navigation qu'il déclenche lui-même ; il ne s'attache
  pas à une navigation déjà en cours.
- Aucun de ces signaux ne transforme un contenu page non fiable en instruction
  autorisée pour le harness.
