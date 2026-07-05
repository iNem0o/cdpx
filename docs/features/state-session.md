+++
id = "state-session"
title = "État et contrôles de session"
status = "validated"
summary = "Inspecter et préparer cookies, localStorage et sessionStorage sans fuite de secrets par défaut."
entrypoints = ["cdpx cookies", "cdpx storage"]
path_globs = ["src/cdpx/primitives/state.py", "tests/fixtures/storage.html"]
test_globs = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*", "tests/test_primitives.py::test_get_storage", "tests/e2e/test_e2e_chrome.py::test_cookies*"]
docs = ["docs/PRIMITIVES.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-session"
title = "Inspecter l'état de session du navigateur"
entrypoint = "cdpx cookies"

[[journeys]]
id = "prepare-session"
title = "Poser ou purger des cookies pour un scénario répétable"
entrypoint = "cdpx cookies"

[[scenarios]]
id = "read-session-state"
journey = "read-session"
title = "Lire l'état de session du navigateur en sécurité"
ui_text = "L'utilisateur peut inspecter cookies et storage sans exposer les valeurs secrètes par défaut."
report_text = "Ce scénario prouve que l'état de session du navigateur est observable tout en gardant les valeurs de cookies sensibles masquées, sauf demande explicite."
given = "Une fixture de storage locale pose des cookies et des valeurs de storage navigateur."
when = "cdpx lit les cookies, le localStorage ou le sessionStorage."
then = "La sortie est structurée et sûre à relire dans le rapport de preuve."
tests = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_get_storage", "tests/e2e/test_e2e_chrome.py::test_cookies*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "prepare-repeatable-session"
journey = "prepare-session"
title = "Préparer un état de session navigateur répétable"
ui_text = "L'agent peut poser ou purger l'état de session avant d'exécuter un scénario."
report_text = "Ce scénario prouve que les workflows navigateur répétables peuvent préparer les cookies avant l'action, en conservant la même traçabilité de revue."
given = "Une cible navigateur accepte la mutation de cookies via CDP."
when = "cdpx pose ou purge les cookies pour l'origine cible."
then = "Les étapes suivantes s'exécutent sur un état de session contrôlé."
tests = ["tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*"]
expected_proofs = ["junit"]
+++

## Intention

Permettre des scénarios navigateur répétables (poser un cookie de session,
repartir d'un état propre, vérifier ce que la page stocke) tout en rendant la
fuite de secrets improbable par accident : les valeurs de cookies sont
masquées par défaut dans toutes les sorties, et les afficher est un acte
volontaire de l'humain (HARNESS.md §2).

## Usage

Options globales et codes de sortie : voir la section Contrat CLI du README.

### `cdpx cookies`

```
usage: cdpx cookies {get,set,clear} [--show-values] [--name NAME] [--value VALUE] [--url URL]
```

Lit, pose ou purge les cookies de la cible. Usecase : vérifier qu'une session
Symfony est bien posée, injecter un cookie de feature flag avant un scénario,
repartir d'un navigateur vierge entre deux runs.

Options propres :

- `action` (positionnel, requis) : `get`, `set` ou `clear`.
- `--show-values` : avec `get`, affiche les valeurs en clair au lieu de `***`.
- `--name` : avec `set`, nom du cookie (requis pour `set`).
- `--value` : avec `set`, valeur du cookie (requise pour `set`).
- `--url` : avec `set`, URL qui détermine domaine/chemin du cookie (requise
  pour `set`).

Sécurité — le point central de cette primitive :

- `get` masque TOUTES les valeurs par défaut (`"value": "***"`,
  `"values_masked": true`). Un agent qui recopie sa sortie dans un ticket, un
  commit ou un log ne peut pas exfiltrer une session par accident.
- `--show-values` est un acte volontaire : sa sortie ne va NI dans un commit
  NI dans un ticket (HARNESS.md §2). À réserver au debug local.
- `clear` utilise `Storage.clearCookies` avec repli automatique sur
  `Network.clearBrowserCookies` (méthode dépréciée) pour les Chrome
  historiques ; le champ `method` de la sortie indique la voie empruntée.

```bash
cdpx cookies get
cdpx cookies get --show-values
cdpx cookies set --name PHPSESSID --value abc123 --url http://demo.test/
cdpx cookies clear
```

Sortie JSON de `get` (valeurs masquées par défaut) :

```json
{"cookies": [{"name": "PHPSESSID", "value": "***", "domain": "demo.test", "path": "/", "httpOnly": true, "secure": false}], "count": 1, "values_masked": true}
```

Sortie JSON de `set` :

```json
{"name": "PHPSESSID", "url": "http://demo.test/", "success": true}
```

Sortie JSON de `clear` (le `method` peut valoir `Network.clearBrowserCookies`
sur un Chrome historique) :

```json
{"cleared": true, "method": "Storage.clearCookies"}
```

Pièges :

- `set` sans `--name`, `--value` et `--url` échoue côté CDP (exit 1) : les
  trois sont nécessaires en pratique même si le parseur ne les impose pas.
- `clear` purge les cookies de TOUT le navigateur piloté, pas seulement de
  l'origine courante : toujours travailler sur un profil jetable
  (`--user-data-dir`), jamais sur le Chrome personnel.
- `--show-values` sur une page authentifiée expose des jetons de session :
  relire la sortie avant tout partage.

### `cdpx storage`

```
usage: cdpx storage [--kind {local,session}]
```

Lit le contenu du `localStorage` ou du `sessionStorage` de la page courante.
Usecase : vérifier ce qu'une app front persiste réellement (panier, consent,
tokens applicatifs) et le comparer entre deux états.

Options propres :

- `--kind` : magasin à lire, `local` ou `session` (défaut : `local`).

```bash
cdpx storage
cdpx storage --kind session
```

Sortie JSON :

```json
{"kind": "session", "entries": {"cart": "{\"items\":2}", "consent": "granted"}, "count": 2}
```

Pièges :

- Contrairement aux cookies, les valeurs de storage ne sont PAS masquées :
  si la page y range des jetons, la sortie les contient. Relire avant
  partage sur une page authentifiée.
- Les valeurs sont les chaînes brutes du storage : un objet y apparaît comme
  du JSON sérialisé, à re-parser si besoin.

## Parcours utilisateur

- Lire les cookies avec valeurs masquées par défaut, ou en clair sur demande
  explicite et assumée.
- Poser un cookie nommé pour une origine avant d'exécuter un scénario.
- Purger tous les cookies pour repartir d'un état contrôlé, y compris sur un
  Chrome historique via le repli déprécié.
- Lire le localStorage ou le sessionStorage de la page courante.

## Validation

Les tests unitaires mock imposent le masquage par défaut, la forme des
mutations et le repli `Storage.clearCookies` → `Network.clearBrowserCookies`
(`tests/test_primitives.py`, `tests/test_cli.py`) ; les tests e2e vérifient
l'état réel posé par la fixture `storage.html`
(`tests/e2e/test_e2e_chrome.py`).

## Preuves

Preuves attendues : résultats JUnit, plus screenshots e2e pour la lecture de
session.

## Limites connues

- La sortie de `storage` doit être relue avant partage quand la page est
  authentifiée : aucun masquage n'y est appliqué.
- `cookies clear` est global au navigateur piloté, sans purge ciblée par
  origine.
