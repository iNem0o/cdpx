+++
id = "browser-navigation"
title = "Navigation et synchronisation"
status = "validated"
summary = "Ouvrir des pages, sélectionner des onglets et attendre des états navigateur déterministes avant de lire ou d'agir."
entrypoints = ["cdpx tabs", "cdpx version", "cdpx goto", "cdpx wait"]
path_globs = ["src/cdpx/discovery.py", "src/cdpx/client.py", "src/cdpx/primitives/nav.py", "tests/test_discovery_and_client.py", "tests/fixtures/index.html", "tests/fixtures/spa.html"]
test_globs = ["tests/test_discovery_and_client.py::*", "tests/test_primitives.py::test_navigate*", "tests/test_primitives.py::test_wait*", "tests/test_cli.py::test_tabs*", "tests/test_cli.py::test_goto*", "tests/e2e/test_e2e_chrome.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_cli_browser_lifecycle*"]
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
report_text = "Ce scénario prouve la synchronisation entre la découverte des targets, la sélection d'onglet et le contenu DOM rendu tardivement."
given = "Un onglet cible existe et une fixture peut injecter du contenu après le chargement initial."
when = "cdpx attend un sélecteur ou liste les targets du navigateur qui peuvent être sélectionnés."
then = "Le target est attribué et le sélecteur attendu est attaché au DOM pour les primitives suivantes."
tests = ["tests/test_discovery_and_client.py::*", "tests/test_cli.py::test_tabs*", "tests/test_primitives.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_wait*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intention

Donner à l'agent (ou au dev qui le pilote) un moyen déterministe de choisir un
target Chrome, de naviguer, et d'attendre qu'un état réellement utile existe
avant toute lecture ou action. Pendant la construction d'une app Symfony ou
e-commerce, une page « en cours de chargement » est un piège : l'agent qui lit
trop tôt observe un état intermédiaire et en tire de fausses conclusions.
`goto` attend le cycle de vie de la page ; `wait` couvre le rendu côté client
(SPA, contenu injecté en JS) ; `tabs` et `version` ancrent la session sur le
bon navigateur et le bon onglet.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `cdpx tabs`

Synopsis : `cdpx tabs {list,new,activate,close} [--url URL] [--id ID]`

Gestion des onglets via l'API HTTP `/json` de Chrome : lister les targets
disponibles, ouvrir un nouvel onglet, mettre un onglet au premier plan, ou le
fermer. C'est la première commande d'une session : elle donne les `id` que
`--target` (option globale) accepte ensuite.

En mode local historique, l'absence de `--target` conserve la première page
implicite. En mode équipe, le manifest, le `run-id` et le `target` attribué sont
obligatoires : `tabs list` ne retourne que ce target et
`new`/`activate`/`close` sont refusés, car son lifecycle appartient au
supervisor de `cdpx session`.

Options propres à la commande :

- `action` (positionnel, requis) : `list`, `new`, `activate` ou `close`.
- `--url` : URL d'ouverture pour `new` (défaut : page vierge).
- `--id` : identifiant du target pour `activate` et `close`.

```bash
cdpx tabs list
cdpx tabs new --url http://demo.test/produit-42
cdpx tabs activate --id 4FA1B2C3D4E5F6
cdpx tabs close --id 4FA1B2C3D4E5F6
```

Sortie de `list` (collection bornable par `--limit`, avec le nombre total) :

```json
{"tabs":[{"id":"4FA1B2C3D4E5F6","type":"page","title":"Produit 42","url":"http://demo.test/produit-42"}],"count":1}
```

Sortie de `new` (le target créé, tel que retourné par Chrome), puis
`activate`/`close` (accusé compact) :

```json
{"activated":"4FA1B2C3D4E5F6"}
```

Erreurs : exit 1 si Chrome est injoignable sur `host:port` ou si l'`id` est
inconnu ; exit 2 si `--id` manque pour `activate`/`close` ou si une option ne
convient pas à l'action. Pièges : depuis Chrome 111, `/json/new` exige un
PUT — cdpx tente PUT puis retombe sur GET pour les vieux Chromium ; ne jamais
pointer le Chrome personnel, toujours un profil jetable
(`--user-data-dir=/tmp/cdpx-profile`).

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

Erreurs (exit 1) : aucun Chrome à l'écoute sur le port de debug (connexion
refusée sur `/json/version`).

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
En mode équipe, la destination est contrôlée avant connexion puis
`window.location.href` est relu après navigation : une redirection hors
allowlist transforme la commande en échec avant toute action suivante.

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
- Lister, créer, activer et fermer des targets Chrome.

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
