+++
id = "orchestration-control"
title = "Interception, émulation et orchestration"
status = "validated"
summary = "Contrôler le comportement réseau, émuler des contraintes d'appareil, lire des iframes, exécuter des scénarios métier et enregistrer/rejouer des actions navigateur bornées."
entrypoints = ["cdpx intercept", "cdpx emulate", "cdpx frame", "cdpx record", "cdpx replay", "cdpx scenario"]
path_globs = ["src/cdpx/primitives/advanced.py", "src/cdpx/primitives/actions.py", "src/cdpx/journal.py", "src/cdpx/scenarios.py", "tests/fixtures/intercept.html", "tests/fixtures/iframe.html", "tests/fixtures/scenarios/*.yml", "tests/test_journal.py", "tests/test_scenarios.py"]
test_globs = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_scenarios.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect", "tests/e2e/test_e2e_chrome.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*", "tests/e2e/test_e2e_chrome.py::test_cli_slow_3g*", "tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M3-interception-emulation.md", "docs/milestones/M5-orchestration.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "intercept-network"
title = "Forcer, bloquer ou laisser passer les requêtes réseau correspondantes"
entrypoint = "cdpx intercept"

[[journeys]]
id = "replay-flow"
title = "Enregistrer et rejouer des actions navigateur bornées"
entrypoint = "cdpx replay"

[[journeys]]
id = "scenario-run"
title = "Exécuter un scénario métier déclaratif avec preuves"
entrypoint = "cdpx scenario"

[[scenarios]]
id = "intercept-network-request"
journey = "intercept-network"
title = "Intercepter une requête réseau de façon déterministe"
ui_text = "Le run navigateur peut forcer, bloquer ou laisser passer les issues réseau."
report_text = "Ce scénario prouve que le comportement réseau peut être contrôlé pendant la validation navigateur et relié à une preuve lisible par un humain."
given = "Une page fixture émet des requêtes que les règles d'interception peuvent matcher."
when = "cdpx intercept applique un comportement fulfill, block ou continue pendant la navigation composée."
then = "Le résultat navigateur et la capture d'écran prouvent le chemin réseau demandé."
tests = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_intercept*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "orchestrate-replay-and-emulation"
journey = "replay-flow"
title = "Rejouer une orchestration navigateur bornée"
ui_text = "Le rapport relie les primitives d'orchestration aux tests de rejeu, d'iframe, d'émulation et de garde d'origine."
report_text = "Ce scénario prouve que des actions navigateur bornées et des contraintes d'appareil peuvent être réellement rejouées ou inspectées sans devenir un langage de macros illimité."
given = "Un journal NDJSON d'actions enregistrées, des fixtures iframe ou des contraintes d'émulation sont disponibles."
when = "cdpx valide le journal entier (syntaxe, actions, budget) puis rejoue réellement chaque action contre le navigateur, émule, lit des iframes ou applique la garde d'origine."
then = "Chaque action est rejouée dans la limite du budget, le rejeu s'arrête à la première divergence, et le résultat reste borné, vérifiable et rattaché à la feature d'orchestration."
tests = ["tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "run-declarative-business-scenario"
journey = "scenario-run"
title = "Exécuter un scénario métier YAML avec preuves"
ui_text = "Un fichier YAML décrit un parcours métier, ses assertions et ses preuves à collecter pendant et après le run."
report_text = "Ce scénario prouve que les primitives cdpx peuvent être composées en parcours métier déclaratifs avec verdict unique, findings et dossier de preuves."
given = "Un Chrome jetable cible une application locale ou Symfony et un fichier YAML décrit les steps, assertions et captures."
when = "cdpx scenario run exécute les steps, collecte console/réseau en continu, capture les preuves aux checkpoints et évalue les assertions."
then = "La sortie contient un verdict pass/fail unique, les findings, les steps exécutés et les artefacts produits."
tests = ["tests/test_scenarios.py::*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*", "tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intention

Permettre des expériences navigateur contrôlées où le réseau, les conditions
d'appareil ou un journal d'actions multi-étapes font partie de la validation.
Pendant la construction d'une app Symfony ou e-commerce, on a besoin de forcer
un backend en erreur sans le casser (`intercept`), de vérifier un rendu sous
contraintes mobiles ou réseau lent (`emulate`), de lire du contenu embarqué
dans une iframe (`frame`), et de constituer puis rejouer un parcours
reproductible (`record` / `replay`), ou d'élever ces primitives en scénario
métier déclaratif (`scenario run`). Le langage d'actions reste volontairement
compact (goto, wait, click, type, key, eval) : une action = une primitive
nommée, jamais d'échappatoire shell.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

L'allowlist de la session est obligatoire et toutes les actions sont
préflightées avec l'autorité du manifest. Pour les commandes composées, le
niveau suit le verbe (`goto`/`wait`: observation;
`click`/`type`/`key`: interaction; `eval`: privileged). `replay` et `scenario`
prennent le niveau maximal de tout le fichier avant effet CDP. Destinations et
origine réelle sont vérifiées ; le contenu page reste une entrée non fiable.
`frame` est une observation.

### `cdpx intercept`

Synopsis : `cdpx intercept --rule "PATTERN => ACTION" [--rule ...] [--settle S] -- goto <url>`

Intercepte les requêtes réseau pendant une navigation et leur applique un
comportement déterministe : répondre à la place du serveur avec un code HTTP
(ex. `503`), bloquer (`block`, échec `BlockedByClient`), ou laisser passer
(`continue`). Usecase : prouver qu'une page dégrade proprement quand son API
répond 503, sans toucher au backend. La commande est composée parce que
`Fetch.enable` meurt avec la connexion CDP : l'interception ne peut exister
que le temps d'une invocation, l'action à intercepter doit donc être exécutée
dans la même commande (`-- goto <url>`).

Options propres à la commande :

- `--rule` (requis, répétable) : règle `PATTERN => ACTION`. `PATTERN` est un
  motif fnmatch (`*api*`) ou une sous-chaîne de l'URL ; `ACTION` vaut un code
  HTTP numérique **de 200 à 599** (ex. `503`, réponse JSON
  `{"cdpx":"intercept","status":N}`),
  `block` ou `continue`. Première règle qui matche gagne ; une requête sans
  règle continue normalement.
- `--settle` : période de calme (secondes, défaut 0.5) après l'évènement
  `load` avant de conclure que le réseau est stable.
- `action` (après `--`) : uniquement `goto <url>`.

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx intercept --rule "*tracker* => block" --rule "*api* => continue" -- goto http://demo.test/produit-42
```

```json
{"url":"http://demo.test/","rules":["*api* => 503"],"hits":[{"url":"http://demo.test/","action":"continue"},{"url":"http://demo.test/api/health","action":"503"}],"count":2,"settle":1.0}
```

Erreurs et pièges : toute autre action que `goto <url>` après `--` est
refusée. Une règle sans `=>`, une faute de frappe (`typo`) ou un statut hors
`200..599` échoue au parsing **avant** `Fetch.enable`/navigation ; aucune
branche par défaut ne continue silencieusement. Si `load` n'arrive jamais, la
commande timeout. `intercept` exige `privileged` et une destination autorisée.
Le document principal est lui aussi intercepté : une
règle trop large (`* => 503`) casse la page porteuse.

### `cdpx emulate`

Synopsis : `cdpx emulate [mobile|slow-3g|cpu-4x] [--reset] [-- <action ...>]`

Applique un preset d'émulation — `mobile` (viewport 390x844, deviceScaleFactor
3, UA `cdpx-mobile/1.0`), `slow-3g` (latence 400 ms, débit 50 Kio/s montant et
descendant) ou `cpu-4x` (CPU ralenti 4x) — puis, sous forme composée, exécute
une action dans la même connexion CDP. Usecase : vérifier qu'une page reste
utilisable sur mobile ou en réseau dégradé. La forme composée est essentielle :
les overrides d'émulation MEURENT avec la connexion CDP (prouvé e2e sur
Chrome 150), donc agir sous émulation exige que l'action soit passée dans la
même invocation (`cdpx emulate mobile -- goto http://demo.test/`).

Options propres à la commande :

- `preset` (positionnel, optionnel) : `mobile`, `slow-3g` ou `cpu-4x`.
- `--reset` : restaure l'état par défaut — device metrics, user-agent (bug
  historique corrigé : l'UA du preset mobile survivait au reset), conditions
  réseau et taux CPU. S'utilise sans preset.
- `action` (après `--`) : action composée exécutée sous émulation —
  `goto <url>`, `wait <sélecteur>`, `click <sélecteur>`,
  `type <sélecteur> <texte> [--clear]`, `key <touche>`, `eval <js>`.

```bash
cdpx emulate mobile -- goto http://demo.test/
cdpx emulate slow-3g -- goto http://demo.test/panier
cdpx emulate mobile -- eval "navigator.userAgent"
cdpx emulate --reset
```

Sortie avec action composée :

```json
{"preset":"mobile","applied":true,"action":{"argv":["goto","http://demo.test/"],"result":{"url":"http://demo.test/","frameId":"7C93","loaderId":"A1F0","errorText":null,"waited":"load","ok":true,"elapsed_ms":52.7}}}
```

Sortie de `--reset` :

```json
{"reset":true}
```

Erreurs et pièges : sans preset ni `--reset`, la commande échoue
(`preset inconnu: None`, exit 1). PIÈGE PRINCIPAL : `cdpx emulate mobile` sans
action applique bien les overrides mais ils disparaissent dès la fin de la
commande — un `cdpx goto` lancé ensuite tourne SANS émulation (voir Limites
connues). La commande est classée par le verbe de son action :
`emulate mobile -- goto ...` relève d'observation, `emulate mobile -- click
...` exige interaction et toute destination reste bornée par l'allowlist.

### `cdpx frame`

Synopsis : `cdpx frame <sélecteur>`

Lit l'`innerText` d'un élément situé DANS une iframe same-origin de la page
courante : toutes les iframes sont parcourues, la première qui contient le
sélecteur fournit le texte. Usecase : vérifier le contenu d'un widget embarqué
(paiement sandbox, preview CMS) sans changer de target CDP.

Options propres à la commande :

- `selector` (positionnel, requis) : sélecteur CSS cherché dans le document de
  chaque iframe.

```bash
cdpx frame "#status"
```

```json
{"selector":"#status","text":"Paiement accepté"}
```

Erreurs et pièges : si aucun élément ne matche, ou si l'iframe est
cross-origin (son `contentDocument` est inaccessible), la sortie porte
`"text":null` avec exit 0 — vérifier la valeur, pas le code de sortie.
`frame` relève d'observation mais exige tout de même que l'origine courante
appartienne à l'allowlist obligatoire.

### `cdpx record`

Synopsis : `cdpx record [-o journal.ndjson] -- <action ...>`

Exécute RÉELLEMENT une action (via l'interpréteur d'actions partagé :
`goto <url>`, `wait <sélecteur>`, `click <sélecteur>`,
`type <sélecteur> <texte> [--clear]`, `key <touche>`, `eval <js>`) puis la
journalise dans le schéma NDJSON `cdpx.record/v2`. Le journal est ouvert en
append : plusieurs invocations construisent un parcours. Chaque ligne contient
schéma, `run_id`, action structurée ou argv, `replayable`, verdict, résultat
nettoyé et timestamp. Un échec est écrit avant l'exit 1.

`record type` exige `@env:NOM` : seule la référence est persistée, la valeur est
résolue en mémoire et enregistrée dans le contexte de redaction. `eval` est
toujours masqué, hashé et non rejouable. Toute autre forme de saisie est
refusée avant connexion.

Options propres à la commande :

- `-o`, `--output` : nom du journal NDJSON (défaut `cdpx-record.ndjson`).
  Seul son basename est retenu.
- `action` (après `--`) : l'action à exécuter et journaliser.

Le journal est confiné sous `artifacts/journals/` de la session, en `0600`,
avec métadonnées
`classification:"internal"`, `upload_allowed:false`, `retention:"session"`.
`replay` ne peut relire qu'un fichier régulier privé de ce même dossier.

```bash
cdpx record -o parcours.ndjson -- goto http://demo.test/
cdpx record -o parcours.ndjson -- click "#acheter"
cdpx record -o parcours.ndjson -- type "#password" @env:CHECKOUT_PASSWORD --clear
cdpx record -o parcours.ndjson -- wait "#confirmation"
```

```json
{"schema":"cdpx.record/v2","path":"parcours.ndjson","recorded":1,"replayable":true,"ok":true}
```

Ligne NDJSON écrite dans le journal :

```json
{"schema":"cdpx.record/v2","run_id":"checkout-17","action":{"verb":"type","selector":"#password","input":{"secret_ref":"CHECKOUT_PASSWORD","source":"env"},"clear":true},"replayable":true,"ok":true,"result":{"typed":true,"value_masked":true,"selector":"#password","cleared":true},"ts":1783814400.123}
```

Erreurs et pièges : une référence env absente est refusée avant effet CDP. Une
action qui échoue est journalisée `ok:false` avant l'exit 1. Le fichier et son
dossier sont forcés respectivement en `0600` et `0700`. L'autorité requise suit
l'action et l'origine réelle est revalidée après exécution.

### `cdpx replay`

Synopsis : `cdpx replay <journal.ndjson>` (budget : option globale `--max-actions`)

Rejoue un journal NDJSON produit par `cdpx record` contre le navigateur,
action par action, et s'arrête à la première divergence. Toute la validation
se fait AVANT la première exécution : syntaxe JSON de chaque ligne, présence
d'une action, schéma/rejouabilité, résolution de toutes les références de
secret, autorité maximale et budget `--max-actions`. Une seule référence
absente garantit `played:0` et aucune commande CDP. Ensuite chaque action est
réellement exécutée et son résultat non volatil est comparé au résultat
enregistré.

Après chaque `goto`, replay relit `window.location.href` au lieu de conserver
l'URL demandée. Cette URL finale est contrôlée immédiatement et à nouveau juste
avant la mutation suivante : une redirection autorisée → origine interdite ne
peut pas recevoir le clic suivant.

Options propres à la commande :

- `path` (positionnel, requis) : chemin du journal NDJSON à rejouer.
- Le budget d'actions vient de l'option globale `--max-actions` : un journal
  qui le dépasse est refusé avant tout rejeu.

```bash
cdpx replay parcours.ndjson
cdpx --max-actions 20 replay parcours.ndjson
```

Rejeu complet réussi :

```json
{"path":"parcours.ndjson","events":3,"played":3,"ok":true}
```

Divergence (exit 1, le JSON reste structuré sur stdout) :

```json
{"path":"parcours.ndjson","events":3,"played":1,"ok":false,"divergence":"event 1: sélecteur introuvable après 10.0s: #acheter"}
```

Erreurs et pièges : une ligne non-JSON ou sans `action` produit
`"ok":false` avec `"divergence":"line N: ..."` et `"played":0` (exit 1). Un
journal plus long que `--max-actions` provoque
`budget --max-actions dépassé` (exit 1, rien n'est rejoué). `played` compte
les actions effectivement rejouées avec succès ; l'index de `divergence` est
celui de l'évènement fautif (base 0). Les clés volatiles (`elapsed_ms`, IDs de
loader/frame, coordonnées) sont ignorées dans la comparaison. Les journaux v1
contenant `type` ou `eval` sont refusés ; les actions v1 non
sensibles restent compatibles.

### `cdpx scenario`

Synopsis : `cdpx scenario run <fichier.yml> [--settle S]`

Exécute un scénario métier déclaratif YAML contre l'onglet ciblé. Le scénario
décrit un contexte (`base_url`, émulation optionnelle), une suite de steps, des
assertions, des preuves finales et, si besoin, des preuves à collecter aux
moments clés du run (`capture` sur un step). La sortie est toujours un objet
JSON unique avec `verdict` (`pass` ou `fail`), `findings`, `steps`,
`assertions`, `artifacts` et `evidence_dir`.

Format P0 supporté :

- `context.base_url` : origine ou URL de base pour résoudre les `goto`
  relatifs.
- `context.emulation` : optionnel, `mobile`, `slow-3g` ou `cpu-4x`, appliqué
  dans la même connexion CDP que les steps.
- Steps : `goto`, `wait_visible`, `click`, `type`, `key`, `eval`,
  `wait_text`. `wait_visible` exige un élément attaché, rendu, visible et doté
  d'une boîte non nulle. `type` accepte uniquement
  `{selector, secret_ref, clear}` et prévalide la référence d'environnement.
- `capture` sur step : liste parmi `screenshot`, `console`, `network`,
  `profiler`. Ces preuves sont collectées immédiatement après le step, même si
  le step échoue.
- Assertions : `no_console_errors`, `network_errors_max`, `text_contains`.
- `artifacts` : mêmes types que `capture`, collectés en fin de scénario.

```yaml
name: checkout_guest_add_to_cart
context:
  base_url: http://shop.localhost
  emulation: mobile
steps:
  - label: product_page
    goto: /produit/42
    capture: [screenshot, console, network]
  - label: add_to_cart
    wait_visible: '[data-testid="add-to-cart"]'
  - click: '[data-testid="add-to-cart"]'
    capture: [screenshot, console]
  - type:
      selector: '[name="password"]'
      secret_ref: CHECKOUT_PASSWORD
      clear: true
  - wait_text: ['[data-testid="cart-count"]', '1']
assertions:
  - no_console_errors: true
  - network_errors_max: 0
  - text_contains: ['[data-testid="cart-count"]', '1']
artifacts:
  - screenshot
  - console
  - network
  - profiler
```

```bash
cdpx scenario run checkout_guest_add_to_cart.yml
```

Sortie réussie :

```json
{"name":"checkout_guest_add_to_cart","verdict":"pass","findings":[],"evidence_dir":"/runtime/session/artifacts/scenarios/checkout_guest_add_to_cart-20260706T120000Z","steps":[{"index":0,"label":"product_page","verb":"goto","ok":true}],"assertions":[{"name":"no_console_errors","expected":true,"ok":true,"actual":0}],"artifacts":[{"type":"screenshot","label":"product_page","path":"/runtime/session/artifacts/scenarios/.../000-product_page-screenshot.png","bytes":1234,"mime":"image/png","classification":"opaque-restricted","upload_allowed":false}],"_cdpx":{"content_trust":"untrusted"}}
```

Erreurs et pièges : un YAML invalide ou un champ inconnu sort en exit 2. Un
scénario exécuté mais non conforme sort en exit 1 avec `verdict:"fail"` et des
`findings` structurés. Les assertions ne s'arrêtent pas au premier échec :
elles accumulent les findings puis les preuves finales sont collectées. Une
capture `profiler` utilise d'abord les headers Symfony observés pendant le run
(`X-Debug-Token-Link` ou `X-Debug-Token`) ; si aucun header n'a été vu, cdpx
tente la dernière URL naviguée, puis ajoute un finding warning
`profiler_unavailable` si aucun profiler n'est disponible. Le collector effectue
un dernier drainage console/réseau **avant** les assertions, afin qu'une erreur
tardive participe au verdict. Chaque origine est contrôlée avant le step et
après stabilisation ; une redirection hors allowlist bloque mutation, capture
et assertions suivantes.

Le dossier de run est `0700`, ses fichiers et son manifest sont `0600`. Les
JSON console/réseau/profiler sont `internal`; screenshots et autres binaires
sont `opaque-restricted`, avec `upload_allowed:false`. Le résultat et les
erreurs sont redacted avant persistance. Le dossier de scénario est forcé sous
les artefacts de session et son TTL ne dépasse pas le temps restant du manifest;
le teardown supprime l'ensemble.

## Parcours utilisateur

- Intercepter une navigation et forcer des issues réseau déterministes
  (fulfill, block, continue).
- Émuler mobile, réseau lent ou ralentissement CPU et agir dans la même
  connexion.
- Exécuter un scénario métier YAML et récupérer un verdict, des findings et
  des preuves collectées aux checkpoints et en fin de run.
- Lire le texte d'une iframe same-origin.
- Enregistrer des actions réellement exécutées puis rejouer le journal avec un
  budget, arrêt à la première divergence.

## Validation

Les tests unitaires sur mock CDP valident les règles d'interception, les
presets et le reset d'émulation, l'exécution et la journalisation de `record`,
la validation préalable et le rejeu réel de `replay` (y compris la divergence
et le budget), les scénarios métier YAML, l'interpréteur d'actions partagé et
la garde d'origine. Les tests e2e valident l'interception Fetch réelle, la
non-persistance des overrides d'émulation entre connexions, le cycle
record/replay complet sur Chrome réel, et des scénarios déclaratifs pass/fail
avec preuves. Les e2e Symfony exécutent aussi des scénarios YAML contre les
routes déterministes `/scenario/front/*`, `/scenario/vitals/*` et
`/scenario/profiler/*`.

## Preuves

Preuves attendues : rapports JUnit, captures d'écran pour les scénarios e2e
d'orchestration (page interceptée, rendu sous émulation), JSON de runs
déclaratifs, console, réseau et profiler collectés par `cdpx scenario run`.

## Limites connues

- Les overrides d'émulation ne survivent PAS à la commande : ils meurent avec
  la connexion CDP (comportement de Chrome, vérifié e2e sur Chrome 150).
  `cdpx emulate mobile` seul est donc sans effet durable — toujours utiliser
  la forme composée `cdpx emulate mobile -- goto http://demo.test/`.
- `intercept` ne compose qu'avec `goto <url>` ; l'interception ne peut pas
  encore entourer un `click` ou un parcours complet.
- `frame` ne lit que les iframes same-origin (le `contentDocument` d'une
  iframe cross-origin est inaccessible) et retourne le premier match.
- Record/replay exécute des actions réelles mais le langage d'actions reste
  volontairement compact (goto, wait, click, type, key, eval) — ce n'est pas
  un langage de macros navigateur complet.
- Replay compare les résultats enregistrés hors champs volatils ; un résultat
  identique ne garantit pas à lui seul l'effet métier attendu. Ajouter une
  assertion observable dans un scénario pour le prouver.
