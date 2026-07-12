+++
id = "state-session"
title = "État et contrôles de session"
status = "validated"
summary = "Attribuer une session Chrome isolée et inspecter cookies/localStorage/sessionStorage sans fuite de secrets par défaut."
entrypoints = ["cdpx cookies", "cdpx storage", "cdpx session"]
path_globs = ["src/cdpx/session.py", "src/cdpx/policy.py", "src/cdpx/artifacts.py", "src/cdpx/security/*.py", "src/cdpx/primitives/state.py", "tests/test_session.py", "tests/test_policy.py", "tests/test_team_cli.py", "tests/test_artifacts.py", "tests/test_redaction.py", "tests/test_security_integration.py", "tests/e2e/test_e2e_sessions.py", "tests/fixtures/storage.html"]
test_globs = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*", "tests/test_primitives.py::test_get_storage*", "tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_team_cli.py::*", "tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*", "tests/e2e/test_e2e_chrome.py::test_cookies*", "tests/e2e/test_e2e_chrome.py::test_cli_cookie_masking*", "tests/e2e/test_e2e_sessions.py::*"]
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

[[journeys]]
id = "isolate-team-runs"
title = "Attribuer un Chrome jetable et exclusif à chaque run d'équipe"
entrypoint = "cdpx session"

[[journeys]]
id = "teardown-supervisor-signal"
title = "Détruire le Chrome et le profil lors de l'arrêt du supervisor"
entrypoint = "cdpx session"

[[scenarios]]
id = "read-session-state"
journey = "read-session"
title = "Lire l'état de session du navigateur en sécurité"
ui_text = "L'utilisateur peut inspecter cookies et storage sans exposer les valeurs secrètes par défaut."
report_text = "Ce scénario prouve que l'état de session du navigateur est observable tout en gardant les valeurs de cookies et de storage masquées, sauf demande explicite."
given = "Une fixture de storage locale pose des cookies et des valeurs de storage navigateur."
when = "cdpx lit les cookies, le localStorage ou le sessionStorage."
then = "La sortie est structurée et sûre à relire dans le rapport de preuve."
tests = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_get_storage*", "tests/e2e/test_e2e_chrome.py::test_cookies*"]
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

[[scenarios]]
id = "isolate-managed-team-runs"
journey = "isolate-team-runs"
title = "Isoler et détruire les sessions Chrome d'équipe"
ui_text = "Chaque run reçoit un profil, un target, un grant et un endpoint loopback distincts, utilisables par une seule commande à la fois."
report_text = "Ce scénario prouve sur Chrome réel l'isolation de trois runs, l'absence de partage cookies/storage, la matrice d'autorités, le lease exclusif et le teardown des profils/endpoints."
given = "Trois runs démarrent des sessions gérées avec les grants observation, interaction et privileged."
when = "Le CLI exécute lectures, interactions et opérations privilégiées, tente un lease concurrent puis arrête chaque session."
then = "Les états navigateur restent isolés, les grants sont appliqués, le second lease échoue et chaque profil/endpoint disparaît au teardown."
tests = ["tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_team_cli.py::*", "tests/e2e/test_e2e_sessions.py::test_managed_team_sessions_are_isolated_authorized_and_torn_down"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "redact-sensitive-session-data"
journey = "read-session"
title = "Empêcher un canari de sortir du run sécurisé"
ui_text = "Cookies, storage, URL, headers, console, profiler, journal et artefacts sont nettoyés avant partage."
report_text = "Ce scénario prouve que les canaris connus sont absents des sorties et artefacts, que le texte ordinaire reste lisible et que les permissions privées sont imposées."
given = "Le mock CDP expose un secret canari dans plusieurs surfaces navigateur."
when = "cdpx observe, journalise et construit un staging partageable."
then = "Le protocole peut recevoir la valeur en mémoire mais stdout, stderr, journal et artefacts partageables n'en contiennent pas."
tests = ["tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*"]
expected_proofs = ["junit", "json"]

[[scenarios]]
id = "teardown-on-supervisor-signal"
journey = "teardown-supervisor-signal"
title = "Nettoyer une session après SIGTERM du supervisor"
ui_text = "Un arrêt supervisé ferme Chrome, le port CDP et supprime le profil privé sans exiger une seconde commande."
report_text = "Ce scénario prouve sur Chrome réel que le bloc de teardown du supervisor s'exécute aussi lors d'un SIGTERM normal."
given = "Une session équipe gérée est active avec un target et un profil jetable."
when = "Le processus supervisor reçoit SIGTERM."
then = "Manifest, profil et dossier disparaissent, et le port loopback n'accepte plus de connexion."
tests = ["tests/e2e/test_e2e_sessions.py::test_supervisor_signal_still_tears_down_chrome_and_private_files"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intention

Attribuer à chaque run d'équipe un Chrome jetable et exclusif, puis permettre
des scénarios répétables (poser un cookie, repartir d'un état propre, vérifier
ce que la page stocke) sans fuite accidentelle. Cookies et storage sont
masqués par défaut ; les afficher est un acte volontaire et privilégié.

## Usage

Options globales et codes de sortie : voir la section Contrat CLI du README.

### `cdpx session`

```text
usage: cdpx session start --run-id RUN --authority observation|interaction|privileged --origins ORIGINES [--ttl S] [--owner-pid PID] [--chrome BIN]
usage: cdpx session status --manifest PATH --run-id RUN [--target ID]
usage: cdpx session stop --manifest PATH --run-id RUN [--target ID]
```

`start` lance un Chrome headless sur loopback avec port dynamique, profil
jetable, target unique et supervisor. Le manifest privé associe ces ressources
au run, au grant et à l'allowlist. La sortie publique omet chemins physiques,
PID et URL WebSocket, mais fournit `manifest`, `session_id`, `target_id`,
`authority`, `origins`, `created_at` et `expires_at`.

```bash
cdpx session start --run-id checkout-17 --authority interaction --origins "http://*.test,http://127.0.0.1:*" --ttl 1800
cdpx session status --manifest /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
cdpx session stop --manifest /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
```

Une commande métier utilise ensuite les options globales explicites :

```bash
cdpx --session /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123 text "#cart"
```

Le manifest est `0600`, son dossier/profil/artefacts sont `0700`, et un lease
non bloquant empêche deux commandes de piloter le target simultanément. Le
supervisor détruit la session sur `stop`, TTL ou disparition de
`--owner-pid`. En mode équipe, host/port ne sont pas surchargeables,
`CDPX_ORIGINS` doit être non vide et la page reste marquée
`content_trust: "untrusted"`. Le mode local historique reste disponible sans
`--session`, avec target implicite possible et teardown manuel.

### `cdpx cookies`

```
usage: cdpx cookies {get,set,clear} [--show-values] [--name NAME] [--value VALUE | --value-env NOM] [--url URL]
```

Lit, pose ou purge les cookies de la cible. Usecase : vérifier qu'une session
Symfony est bien posée, injecter un cookie de feature flag avant un scénario,
repartir d'un navigateur vierge entre deux runs.

Options propres :

- `action` (positionnel, requis) : `get`, `set` ou `clear`.
- `--show-values` : avec `get`, affiche les valeurs en clair au lieu de `***`.
- `--name` : avec `set`, nom du cookie (requis pour `set`).
- `--value` : avec `set`, valeur du cookie (requise pour `set`).
- `--value-env` : lit la valeur depuis une variable d'environnement. En mode
  équipe, cette référence est obligatoire et `--value` littéral est refusé.
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
cdpx cookies set --name PHPSESSID --value-env CHECKOUT_SESSION --url http://demo.test/
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

- `set` sans `--name`, valeur (`--value` ou `--value-env`) ou `--url` est
  refusé par le CLI avant toute commande CDP.
- `clear` purge les cookies de TOUT le navigateur piloté, pas seulement de
  l'origine courante : toujours travailler sur un profil jetable
  (`--user-data-dir`), jamais sur le Chrome personnel.
- `--show-values` sur une page authentifiée expose des jetons de session :
  relire la sortie avant tout partage.

### `cdpx storage`

```
usage: cdpx storage [--kind {local,session}] [--show-values]
```

Lit le contenu du `localStorage` ou du `sessionStorage` de la page courante.
Usecase : vérifier ce qu'une app front persiste réellement (panier, consent,
tokens applicatifs) et le comparer entre deux états.

Options propres :

- `--kind` : magasin à lire, `local` ou `session` (défaut : `local`).
- `--show-values` : affiche les chaînes brutes au lieu de `***`; opération
  privilégiée à réserver au diagnostic local.

```bash
cdpx storage
cdpx storage --kind session
```

Sortie JSON :

```json
{"kind": "session", "entries": {"cart": "***", "consent": "***"}, "count": 2, "values_masked": true}
```

Pièges :

- Les valeurs sont toutes masquées par défaut, sans tenter de distinguer panier
  et token. `--show-values` les expose toutes et ne doit jamais alimenter une
  preuve ou un ticket.
- Les valeurs sont les chaînes brutes du storage : un objet y apparaît comme
  du JSON sérialisé en mode `--show-values`, à re-parser si besoin.

## Parcours utilisateur

- Lire les cookies avec valeurs masquées par défaut, ou en clair sur demande
  explicite et assumée.
- Poser un cookie nommé pour une origine avant d'exécuter un scénario.
- Purger tous les cookies pour repartir d'un état contrôlé, y compris sur un
  Chrome historique via le repli déprécié.
- Démarrer, inspecter et arrêter une session Chrome attribuée à un run.
- Lire le localStorage ou le sessionStorage avec valeurs masquées par défaut.

## Validation

Les tests unitaires mock imposent le masquage cookies/storage, la forme des
mutations, les références de secrets, le repli cookies, la politique d'autorité,
les manifests privés et le lease. L'E2E multi-session lance trois Chrome et
vérifie profils/targets distincts, isolation d'état, grants et teardown.

## Preuves

Preuves attendues : JUnit, JSON d'isolation/teardown et screenshots locaux
`opaque-restricted` pour les deux scénarios de sessions gérées.

## Limites connues

- `--show-values` contourne volontairement le masquage et ne doit pas être
  persisté.
- `cookies clear` est global au navigateur piloté, sans purge ciblée par
  origine.
- Le supervisor couvre les terminaisons gérées ; après un arrêt machine brutal,
  le répertoire runtime privé peut nécessiter un nettoyage au redémarrage.
