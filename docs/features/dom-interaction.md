+++
id = "dom-interaction"
title = "Inspection du DOM et actions utilisateur"
status = "validated"
summary = "Lire le texte/HTML rendu, évaluer du JavaScript, compter des éléments et produire des entrées utilisateur trusted."
entrypoints = ["cdpx eval", "cdpx text", "cdpx html", "cdpx count", "cdpx click", "cdpx type", "cdpx key"]
path_globs = ["src/cdpx/primitives/js.py", "src/cdpx/primitives/inputs.py", "tests/fixtures/form.html"]
test_globs = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*"]
docs = ["docs/PRIMITIVES.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "inspect-dom"
title = "Lire l'état du DOM rendu à faible coût en tokens"
entrypoint = "cdpx text"

[[journeys]]
id = "submit-form"
title = "Taper et cliquer comme un utilisateur"
entrypoint = "cdpx type"

[[scenarios]]
id = "inspect-rendered-dom"
journey = "inspect-dom"
title = "Inspecter l'état du DOM rendu"
ui_text = "L'utilisateur peut lire le texte rendu, le HTML, des comptages ou des résultats JavaScript sans capture d'écran."
report_text = "Ce scénario prouve que l'agent peut inspecter l'état rendu par le navigateur avec des primitives sobres en tokens avant de décider de l'action suivante."
given = "Une page fixture expose un état DOM et JavaScript déterministe."
when = "cdpx évalue du JavaScript, lit du texte ou compte des éléments dans la page rendue."
then = "La sortie de la commande donne une représentation compacte et vérifiable de l'état du navigateur."
tests = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "submit-form-like-user"
journey = "submit-form"
title = "Soumettre un formulaire comme un utilisateur"
ui_text = "Le navigateur reçoit des évènements trusted de clic, de saisie et de clavier."
report_text = "Ce scénario prouve que le CLI peut réaliser des interactions DOM proches de l'utilisateur réel et que l'état résultant est visible dans le rapport de preuve."
given = "Une fixture de formulaire locale est chargée dans Chrome."
when = "cdpx clique, tape du texte ou presse des touches via les domaines Input de Chrome."
then = "L'état de la fixture change et la preuve e2e conserve une capture d'écran de l'état final du navigateur."
tests = ["tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intention

Exposer l'état rendu du navigateur et des primitives d'entrée trusted dans un
contrat CLI compact et répétable. Le point clé : `click`, `type` et `key`
passent par le domaine Input de Chrome (pipeline navigateur réel — hover,
focus, évènements `isTrusted`), et non par du `el.click()` en JS. C'est ce qui
fait la différence sur les frameworks front qui filtrent les évènements non
trusted, et c'est ce que verrait un vrai utilisateur. Les primitives de
lecture (`text`, `html`, `count`) donnent une vision sémantique de la page
bien moins coûteuse qu'une capture d'écran ; `eval` reste la primitive racine
pour tout le reste.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

Piège sécurité commun : `eval`, `click`, `type` et `key` sont des MUTATIONS.
Quand la variable d'environnement `CDPX_ORIGINS` est posée, elles sont
refusées (exit 1) sur toute page dont l'origine n'est pas dans la liste —
garde-fou contre une action sur le mauvais site.

### `cdpx eval`

Synopsis : `cdpx eval <expression> [--await]`

Évalue une expression JavaScript dans la page et retourne sa valeur. C'est
l'échappatoire universelle : tout ce qu'aucune primitive nommée ne couvre
encore (lire une variable globale, sonder un endpoint depuis la page) — à
n'utiliser qu'en dernier recours, les primitives nommées ayant un contrat de
sortie stable.

Options propres à la commande :

- `expression` (positionnel, requis) : expression JavaScript à évaluer.
- `--await` : attendre la résolution si l'expression retourne une Promise
  (`awaitPromise`).

```bash
cdpx eval "document.title"
cdpx eval "fetch('/api/panier').then(r => r.status)" --await
```

```json
{"value":"Produit 42 — Demo"}
```

Erreurs et pièges : une exception JS dans la page → exit 1 avec la description
de l'exception sur stderr. Sans `--await`, une Promise retourne `{"value":{}}`
(objet non sérialisé), pas sa valeur résolue. `eval` est une mutation au sens
de la garde `CDPX_ORIGINS` : refusée hors des origines autorisées.

### `cdpx text`

Synopsis : `cdpx text [selector]`

Retourne l'`innerText` d'un élément, ou du `body` sans sélecteur. C'est la
lecture « sémantique » à bas coût : ce que voit l'utilisateur, sans le bruit
du HTML ni le poids d'une capture d'écran.

Options propres à la commande :

- `selector` (positionnel, optionnel) : sélecteur CSS ; défaut : le `body`
  entier.

```bash
cdpx text ".product-price"
```

```json
{"selector":".product-price","text":"42,00 €"}
```

Erreurs et pièges : un sélecteur sans correspondance retourne `"text":null`
avec exit 0 — ce n'est PAS une erreur, tester la valeur. Sans sélecteur, le
texte du `body` peut être volumineux : la sortie est bornée par défaut
(cf. options globales).

### `cdpx html`

Synopsis : `cdpx html [selector]`

Retourne l'`outerHTML` d'un élément, ou du document entier sans sélecteur.
Pour l'inspection structurelle fine : vérifier des attributs, des classes, la
structure exacte d'un fragment généré (Twig, Stimulus, etc.).

Options propres à la commande :

- `selector` (positionnel, optionnel) : sélecteur CSS ; défaut : le document
  entier (`document.documentElement`).

```bash
cdpx html "#cart-summary"
```

```json
{"selector":"#cart-summary","html":"<div id=\"cart-summary\" class=\"cart\"><span>1 article</span></div>"}
```

Erreurs et pièges : sélecteur sans correspondance → `"html":null`, exit 0.
Le HTML est l'état rendu (après JS), pas la source serveur : pour comparer au
HTML initial, utiliser une requête HTTP directe.

### `cdpx count`

Synopsis : `cdpx count <selector>`

Compte les éléments correspondant à un sélecteur CSS. Assertion à coût minimal
pour l'agent : « la liste produit contient 12 cartes », « aucune erreur de
validation affichée ».

Options propres à la commande :

- `selector` (positionnel, requis) : sélecteur CSS.

```bash
cdpx count ".product-card"
```

```json
{"selector":".product-card","count":12}
```

Erreurs et pièges : un sélecteur sans correspondance retourne `"count":0`
avec exit 0 — ce qui est souvent l'assertion voulue. Un sélecteur CSS
syntaxiquement invalide lève une exception JS → exit 1.

### `cdpx click`

Synopsis : `cdpx click <selector>`

Clique au centre d'un élément via `Input.dispatchMouseEvent` (mouseMoved,
mousePressed, mouseReleased). L'élément est d'abord scrollé dans le viewport.
Les évènements passent le pipeline réel du navigateur : ils sont `isTrusted`
et déclenchent hover/focus comme un clic humain.

Options propres à la commande :

- `selector` (positionnel, requis) : sélecteur CSS de l'élément à cliquer.

```bash
cdpx click "button[type=submit]"
```

```json
{"clicked":"button[type=submit]","x":412.5,"y":318.0}
```

Erreurs et pièges : sélecteur introuvable → exit 1. Le clic vise le centre
géométrique : un élément recouvert par un overlay reçoit le clic à sa place —
vérifier l'état résultant (`cdpx text` ou `cdpx wait`) plutôt que de supposer.
Mutation soumise à la garde `CDPX_ORIGINS`.

### `cdpx type`

Synopsis : `cdpx type <selector> <text> [--clear]`

Donne le focus à un champ puis insère le texte via `Input.insertText`
(composition sûre vis-à-vis des IME). Les frameworks de formulaire voient une
saisie réaliste, pas une affectation directe de `value`.

Options propres à la commande :

- `selector` (positionnel, requis) : sélecteur CSS du champ.
- `text` (positionnel, requis) : texte à insérer.
- `--clear` : vider le champ avant la saisie (déclenche un évènement `input`).

```bash
cdpx type "input[name=email]" "client@demo.test" --clear
```

```json
{"typed":"client@demo.test","selector":"input[name=email]","cleared":true}
```

Erreurs et pièges : sélecteur introuvable → exit 1. Sans `--clear`, le texte
s'ajoute au contenu existant du champ. La saisie ne presse pas Entrée :
enchaîner avec `cdpx key Enter` pour soumettre. Mutation soumise à la garde
`CDPX_ORIGINS`.

### `cdpx key`

Synopsis : `cdpx key <key>`

Presse une touche via `Input.dispatchKeyEvent` (rawKeyDown, char si la touche
produit du texte, keyUp). Complète `type` pour la soumission de formulaire, la
navigation clavier et la fermeture de modales.

Options propres à la commande :

- `key` (positionnel, requis) : `Enter`, `Tab`, `Escape`, `ArrowUp` ou
  `ArrowDown`.

```bash
cdpx key Enter
```

```json
{"pressed":"Enter"}
```

Erreurs et pièges : toute autre touche → exit 1 avec la liste des touches
supportées (KEY_MAP volontairement minimal, voir Limites connues). La touche
part vers l'élément qui a le focus : la faire précéder d'un `cdpx click` ou
`cdpx type` qui pose le focus. Mutation soumise à la garde `CDPX_ORIGINS`.

## Parcours utilisateur

- Lire le texte du body ou d'un sélecteur sans prendre de capture d'écran.
- Inspecter le HTML ou compter des éléments pour des assertions à bas coût.
- Cliquer, taper et presser des touches via les domaines Input de Chrome.

## Validation

Les tests mock vérifient le protocole CDP émis (séquences Input.dispatch*,
Runtime.evaluate) en plus de la sortie JSON ; les e2e Chrome valident
l'interaction réelle avec la fixture de formulaire `form.html`.

## Preuves

Preuves attendues : rapports JUnit, plus captures d'écran de l'état final du
navigateur pour les interactions de formulaire réelles.

## Limites connues

- `eval` reste une échappatoire : tout usage qui se répète doit être promu en
  primitive nommée avec contrat de sortie stable (et tests protocolaires).
- `KEY_MAP` est volontairement minimal (`Enter`, `Tab`, `Escape`, `ArrowUp`,
  `ArrowDown`) : les combinaisons avec modificateurs (Ctrl, Shift) et les
  autres touches ne sont pas couvertes.
- La garde d'origines ne s'applique que si `CDPX_ORIGINS` est posée ; sans
  elle, les mutations sont acceptées sur toute page du Chrome de debug —
  d'où l'exigence d'un profil jetable, jamais le Chrome personnel.
