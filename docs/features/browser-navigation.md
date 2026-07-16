+++
id = "browser-navigation"
title = "Navigation et synchronisation"
status = "validated"
summary = "Inspecter le target attribué, ouvrir des pages et attendre des états navigateur déterministes avant de lire ou d'agir."
entrypoints = ["cdpx tabs", "cdpx version", "cdpx goto", "cdpx wait"]
path_globs = ["src/cdpx/discovery.py", "src/cdpx/client.py", "src/cdpx/primitives/nav.py", "tests/test_discovery_and_client.py", "tests/fixtures/index.html", "tests/fixtures/spa.html", "src/cdpx/cdp_types.py"]
test_globs = ["tests/test_discovery_and_client.py::*", "tests/test_primitives.py::test_navigate*", "tests/test_primitives.py::test_wait*", "tests/test_cli.py::test_tabs*", "tests/test_cli.py::test_goto*", "tests/e2e/test_e2e_chrome.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_cli_browser_lifecycle*", "tests/test_primitives.py::test_event_primitives_reject_negative_budgets*", "tests/test_cli.py::test_connection_failure_exits_1*", "tests/test_cli.py::test_send_failure_exits_1*", "tests/test_cli.py::test_transport_failure_exits_1*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "open-page"
title = "Ouvrir une page cible et confirmer la fin du cycle de vie"
entrypoint = "cdpx goto"

[[journeys]]
id = "wait-spa-content"
title = "Attendre le contenu injecté après le chargement initial"
entrypoint = "cdpx wait"

[[scenarios]]
id = "open-page-success"
journey = "open-page"
title = "Ouvrir une page cible avec succès"
ui_text = "Le navigateur ouvre une URL locale et confirme que la page a atteint un état exploitable."
report_text = "Ce scénario prouve qu'un utilisateur peut demander une navigation et obtenir un état navigateur déterministe sans inspection manuelle."
given = "Une page fixture locale est disponible et Chrome expose un target débogable."
when = "cdpx goto ouvre l'URL et attend la fin du cycle de vie de la page."
then = "La commande retourne un payload de succès compact et la page peut être capturée par le run de preuve."
tests = ["tests/test_cli.py::test_goto", "tests/test_primitives.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_navigate*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "wait-for-rendered-state"
journey = "wait-spa-content"
title = "Attendre le contenu rendu avant de lire l'état"
ui_text = "L'agent attend que le contenu soit présent dans le DOM avant de le lire ou d'agir."
report_text = "Ce scénario prouve la synchronisation entre l'attestation du target attribué et le contenu DOM rendu tardivement."
given = "Un onglet cible existe et une fixture peut injecter du contenu après le chargement initial."
when = "cdpx attend un sélecteur ou inspecte le target attribué à la session."
then = "Le target est attribué et le sélecteur attendu est attaché au DOM pour les primitives suivantes."
tests = ["tests/test_discovery_and_client.py::*", "tests/test_cli.py::test_tabs*", "tests/test_primitives.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_wait*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "diagnose-transport-failures"
journey = "open-page"
title = "Diagnostiquer les échecs de transport et refuser les budgets invalides"
ui_text = "Une connexion ou un envoi CDP qui échoue devient un diagnostic exit 1, et un budget de temps négatif est refusé avant toute I/O."
report_text = "Ce scénario prouve que les échecs de transport CDP sortent en erreur diagnostiquée sur stderr (jamais un succès partiel trompeur) et que les budgets de temps invalides sont rejetés avant de toucher le navigateur."
given = "Un transport CDP scripté pour échouer à la connexion, à l'envoi ou pendant la collecte, et des budgets de temps négatifs."
when = "Le CLI exécute une commande navigateur et les primitives valident leur budget avant d'émettre."
then = "Chaque échec de transport rend exit 1 avec son motif sur stderr et aucun message CDP n'est émis pour un budget invalide."
tests = ["tests/test_cli.py::test_connection_failure_exits_1*", "tests/test_cli.py::test_send_failure_exits_1*", "tests/test_cli.py::test_transport_failure_exits_1*", "tests/test_primitives.py::test_event_primitives_reject_negative_budgets*"]
expected_proofs = ["junit"]

+++

## Intention

Donner à l'agent (ou au dev qui le pilote) un target Chrome attribué de façon
déterministe, puis lui permettre de naviguer et d'attendre un état utile
avant toute lecture ou action. Pendant la construction d'une app Symfony ou
e-commerce, une page « en cours de chargement » est un piège : l'agent qui lit
trop tôt observe un état intermédiaire et en tire de fausses conclusions.
`goto` attend le cycle de vie de la page ; `wait` couvre le rendu côté client
(SPA, contenu injecté en JS) ; `tabs` et `version` ancrent la session sur le
bon navigateur et le bon onglet.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `cdpx tabs`

Synopsis : `cdpx tabs list`

Inspecte via `/json` l'unique target `page` attribué à la session. Le manifest,
le run et le target sont obligatoires, explicitement ou par environnement ; la
commande filtre la découverte sur cette attestation exacte. Le lifecycle des
targets appartient au superviseur de `cdpx session` et n'est pas exposé comme
action publique.

```bash
cdpx tabs list
```

Sortie de `list` (collection bornable par `--limit`, avec le nombre total) :

```json
{"tabs":[{"id":"4FA1B2C3D4E5F6","type":"page","title":"Produit 42","url":"http://demo.test/produit-42"}],"count":1,"_cdpx":{"content_trust":"untrusted"}}
```

Erreurs : exit 1 si l'endpoint attesté devient injoignable ou si le target ne
correspond plus au manifest ; exit 2 si un identifiant de session manque ou si
une autre action est demandée. cdpx ne cible jamais un Chrome personnel : le
profil jetable est créé et détruit par le superviseur.

### `cdpx version`

Synopsis : `cdpx version`

Retourne les informations du navigateur (`/json/version`). Sert de « ping » de
session : vérifier que le port de debug répond et identifier la version de
Chrome avant d'attribuer un comportement au protocole.

Options propres à la commande : aucune.

```bash
cdpx version
```

```json
{"Browser":"Chrome/126.0.6478.61","Protocol-Version":"1.3","User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36","V8-Version":"12.6.228.13","WebKit-Version":"537.36"}
```

Erreurs (exit 1) : le navigateur supervisé ne répond plus sur son endpoint
attesté (`/json/version`).

### `cdpx goto`

Synopsis : `cdpx goto <url> [--wait {load,domcontentloaded,none}]`

Navigue vers une URL et attend l'évènement de cycle de vie demandé avant de
rendre la main. C'est ce qui évite à l'agent de lire des états intermédiaires :
la commande ne retourne que quand la page a réellement atteint l'état demandé.

Options propres à la commande :

- `url` (positionnel, requis) : URL cible.
- `--wait` : évènement attendu — `load` (défaut), `domcontentloaded`, ou
  `none` (retour immédiat après acceptation de la navigation).

```bash
cdpx goto http://demo.test/produit-42
cdpx goto http://demo.test/panier --wait domcontentloaded
```

```json
{"url":"http://demo.test/produit-42","frameId":"7C93","loaderId":"A1F0","errorText":null,"waited":"load","ok":true,"elapsed_ms":48.2}
```

Erreurs et pièges : si Chrome refuse la navigation (DNS, connexion refusée),
la sortie porte `"ok":false` avec `errorText` renseigné (ex.
`net::ERR_CONNECTION_REFUSED`) — vérifier `ok`, pas seulement le code de
sortie. Un cycle de vie qui n'arrive jamais dans le délai imparti (option
globale `--timeout`) provoque un exit 1. `--wait none` ne garantit rien sur
l'état du DOM : à réserver aux cas où l'on enchaîne avec `cdpx wait`.
La destination est contrôlée avant connexion puis `window.location.href` est
relu après navigation : une redirection hors allowlist transforme la commande
en échec avant toute action suivante.

### `cdpx wait`

Synopsis : `cdpx wait <selector>`

Attend qu'un sélecteur CSS existe dans le DOM, par polling léger
(`Runtime.evaluate`, sans état résiduel injecté dans la page). C'est la
synchronisation pour les SPA et le contenu rendu côté client : la fixture
`spa.html` du site témoin injecte `#late-content` 300 ms après le `load`, et
`wait` est ce qui permet de le lire de façon fiable.

Options propres à la commande :

- `selector` (positionnel, requis) : sélecteur CSS à attendre. Le délai
  maximal vient de l'option globale `--timeout` (défaut 15 s).

```bash
cdpx wait "#late-content"
```

```json
{"found":true,"selector":"#late-content","elapsed_ms":312.4}
```

Erreurs et pièges : sélecteur toujours absent à l'échéance → exit 1 avec
diagnostic sur stderr (`sélecteur introuvable après Ns`). `wait` teste
l'existence dans le DOM, pas la visibilité : un élément présent mais
`display:none` est considéré comme trouvé. Toujours citer le sélecteur
(`"#id"`) pour éviter que le shell n'interprète `#` comme un commentaire.
Le step YAML `wait_visible` utilise une primitive distincte : il exige en plus
un élément connecté, `display`/`visibility` visibles et une boîte non nulle.

## Parcours utilisateur

- Ouvrir une URL et recevoir un résultat de navigation JSON compact.
- Attendre un sélecteur qui apparaît après le rendu côté client.
- Inspecter l'unique target attribué sans pouvoir modifier son lifecycle.

## Validation

La validation combine des tests protocolaires sur mock CDP (la séquence de
commandes émises EST la spec) et des tests e2e sur Chrome réel contre les
fixtures locales, dont `spa.html` pour le contenu tardif.

## Preuves

Preuves attendues : rapports JUnit, plus captures d'écran e2e pour les
parcours visibles dans le navigateur.

## Limites connues

`wait` CLI ne teste que la présence DOM, pas la visibilité ni
l'interactivité ; `scenario wait_visible` couvre la visibilité, tandis que
l'actionability complète reste vérifiée au moment de `click`/`type`. Le contenu
retourné par la page est non fiable et ne peut pas choisir un autre target.
