+++
id = "state-session"
title = "État et contrôles de session"
status = "validated"
summary = "Attribuer une session navigateur supervisée et inspecter cookies/localStorage/sessionStorage sans fuite de secrets par défaut."
entrypoints = ["cdpx cookies", "cdpx storage", "cdpx session"]
path_globs = ["src/cdpx/session.py", "src/cdpx/policy.py", "src/cdpx/artifacts.py", "src/cdpx/security/*.py", "src/cdpx/primitives/state.py", "src/cdpx/testing/mock_session.py", "tests/test_session.py", "tests/test_policy.py", "tests/test_session_cli.py", "tests/test_artifacts.py", "tests/test_redaction.py", "tests/test_security_integration.py", "tests/e2e/test_e2e_sessions.py", "tests/fixtures/storage.html"]
test_globs = ["tests/test_cli.py::test_cookies*", "tests/test_cli.py::test_missing_session*", "tests/test_cli.py::test_direct_connection_options*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_clear_cookies*", "tests/test_primitives.py::test_get_storage*", "tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_session_cli.py::*", "tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*", "tests/test_scenarios.py::test_scenario_secret_ref_never_reaches_outputs_or_evidence", "tests/e2e/test_e2e_chrome.py::test_cookies*", "tests/e2e/test_e2e_chrome.py::test_cli_cookie_masking*", "tests/e2e/test_e2e_sessions.py::*"]
docs = ["docs/PRIMITIVES.md", "docs/SESSION-LIFECYCLE.md", "HARNESS.md"]
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
id = "isolate-session-runs"
title = "Attribuer un navigateur jetable et exclusif à chaque run"
entrypoint = "cdpx session"

[[journeys]]
id = "exercise-session-without-chrome"
title = "Exercer le contrat supervisé avec le backend mock"
entrypoint = "cdpx session"

[[journeys]]
id = "teardown-supervisor-signal"
title = "Détruire le navigateur et le profil lors de l'arrêt du superviseur"
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
id = "isolate-supervised-session-runs"
journey = "isolate-session-runs"
title = "Isoler et détruire les sessions navigateur supervisées"
ui_text = "Chaque run reçoit un profil, un target, une autorité et un endpoint loopback distincts, utilisables par une seule commande à la fois."
report_text = "Ce scénario prouve sur Chrome réel l'isolation de trois runs, l'absence de partage cookies/storage, la matrice d'autorités, le lease exclusif et le teardown des profils/endpoints."
given = "Trois runs démarrent des sessions supervisées avec les autorités observation, interaction et privileged."
when = "Le CLI exécute lectures, interactions et opérations privilégiées, tente un lease concurrent puis arrête chaque session."
then = "Les états navigateur restent isolés, les autorités sont appliquées, le second lease échoue et chaque profil/endpoint disparaît au teardown."
tests = ["tests/test_cli.py::test_missing_session*", "tests/test_cli.py::test_direct_connection_options*", "tests/test_session.py::*", "tests/test_policy.py::*", "tests/test_session_cli.py::*", "tests/e2e/test_e2e_sessions.py::test_supervised_sessions_are_isolated_authorized_and_torn_down"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "run-supervised-mock-session"
journey = "exercise-session-without-chrome"
title = "Utiliser le backend mock à travers une session supervisée"
ui_text = "Le développeur peut démarrer une session au premier plan sans Chrome et utiliser la même identité triple que partout ailleurs."
report_text = "Ce scénario prouve que le mock crée un manifest privé, atteste un target et applique le même contrat session/run/target avant de tout supprimer."
given = "Chrome réel n'est pas nécessaire et le backend mock CDP est disponible sur loopback."
when = "Une session mock supervisée démarre, exécute une commande puis s'arrête."
then = "La commande passe par le manifest attribué et le teardown supprime les ressources privées."
tests = ["tests/test_session.py::test_mock_backend_uses_supervised_session_contract"]
expected_proofs = ["junit"]

[[scenarios]]
id = "mark-page-content-untrusted"
journey = "read-session"
title = "Marquer le contenu de page comme non fiable"
ui_text = "Toute donnée lue dans une page revient étiquetée untrusted, jamais comme une instruction à suivre."
report_text = "Ce scénario prouve qu'une lecture sous autorité observation reste confinée à l'origine autorisée et que la sortie porte content_trust=untrusted, même quand la page tente d'injecter une consigne au harnais."
given = "Une page servie sur l'origine autorisée renvoie un texte qui imite une consigne d'injection."
when = "cdpx lit le texte de la page sous autorité observation en session supervisée."
then = "Le texte est restitué comme donnée accompagnée du bloc _cdpx content_trust=untrusted, sans jamais être exécuté."
tests = ["tests/test_session_cli.py::test_session_observation_is_scoped_and_emits_untrusted_metadata"]
expected_proofs = ["junit", "command"]

[[scenarios]]
id = "redact-sensitive-session-data"
journey = "read-session"
title = "Empêcher un canari de sortir du run sécurisé"
ui_text = "Cookies, storage, URL, headers, console, profiler, journal et artefacts sont nettoyés avant partage."
report_text = "Ce scénario prouve que les canaris connus sont absents des sorties et artefacts, que le texte ordinaire reste lisible et que les permissions privées sont imposées."
given = "Le mock CDP expose un secret canari dans plusieurs surfaces navigateur."
when = "cdpx observe, journalise et construit un staging partageable."
then = "Le protocole peut recevoir la valeur en mémoire mais stdout, stderr, journal et artefacts partageables n'en contiennent pas."
tests = ["tests/test_cli.py::test_cookies_masked_output", "tests/test_artifacts.py::*", "tests/test_redaction.py::*", "tests/test_security_integration.py::*", "tests/test_scenarios.py::test_scenario_secret_ref_never_reaches_outputs_or_evidence"]
expected_proofs = ["junit", "json"]

[[scenarios]]
id = "teardown-on-supervisor-signal"
journey = "teardown-supervisor-signal"
title = "Nettoyer une session après SIGTERM du superviseur"
ui_text = "Un arrêt supervisé ferme le navigateur, le port CDP et supprime le profil privé sans exiger une seconde commande."
report_text = "Ce scénario prouve sur Chrome réel que le bloc de teardown du superviseur s'exécute aussi lors d'un SIGTERM normal."
given = "Une session supervisée est active avec un target et un profil jetable."
when = "Le processus superviseur reçoit SIGTERM."
then = "Manifest, profil et dossier disparaissent, et le port loopback n'accepte plus de connexion."
tests = ["tests/e2e/test_e2e_sessions.py::test_supervisor_signal_still_tears_down_chrome_and_private_files"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intention

Attribuer à chaque run une session navigateur jetable, exclusive et
supervisée, puis permettre des scénarios répétables sans fuite accidentelle.
Ce contrat est l'unique porte d'entrée des commandes navigateur, avec Chrome
réel comme avec le backend mock. Cookies et storage sont masqués par défaut ;
les afficher est un acte volontaire et privilégié.

## Usage

Options globales et codes de sortie : voir la section Contrat CLI du README.

### `cdpx session`

```text
usage: cdpx session start --run-id RUN --authority observation|interaction|privileged --origins ORIGINES [--ttl S] [--owner-pid PID] [--chrome BIN] [--startup-timeout S]
usage: cdpx session status --session PATH --run-id RUN --target ID
usage: cdpx session stop --session PATH --run-id RUN --target ID
```

`start` lance un Chrome headless sur loopback avec port dynamique, profil
jetable, target unique et superviseur. Le manifest privé associe ces ressources
au run, à l'autorité et à l'allowlist. La sortie publique omet les PID, les
chemins profil/artefacts et l'URL WebSocket ; elle fournit le chemin du manifest
et l'identité nécessaire aux commandes.

La sélection du binaire, la ligne de commande exacte, l'arbre des processus,
les fichiers privés, les surfaces exposées et tous les chemins de teardown sont
documentés dans [Sessions supervisées et processus Chrome](../SESSION-LIFECYCLE.md).
Le cold start dispose par défaut de 60 secondes, dans une limite stricte de
300 secondes. Le parent attend ce budget puis une courte marge de transmission,
sans courir contre le timeout interne du superviseur. Sur un runner CI, Chrome
évite le `/dev/shm` souvent borné. Si le démarrage échoue, les tails
`supervisor.log` et `chrome-stderr.log` sont bornés, redacted puis remontés dans
le diagnostic avant le teardown; les fichiers privés bruts restent supprimés.

```bash
cdpx session start --run-id checkout-17 --authority interaction --origins "http://*.test,http://127.0.0.1:*" --ttl 1800
cdpx session status --session /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
cdpx session stop --session /tmp/cdpx-session/manifest.json --run-id checkout-17 --target ABC123
```

Les trois identifiants peuvent être exportés une fois :

```bash
export CDPX_SESSION=/tmp/cdpx-session/manifest.json
export CDPX_RUN_ID=checkout-17
export CDPX_TARGET=ABC123
cdpx text "#cart"
```

Le manifest est `0600`, son dossier/profil/artefacts sont privés, et un lease
non bloquant empêche deux commandes de piloter le target simultanément. Le
superviseur détruit la session sur `stop`, TTL ou disparition de
`--owner-pid`. L'endpoint vient uniquement du manifest, l'allowlist ne peut pas
être vide et chaque sortie porte `_cdpx.content_trust: "untrusted"`.

`make mock` lance le même cycle au premier plan avec un navigateur simulé,
affiche les trois exports et nettoie la session sur `Ctrl-C`.

### `cdpx cookies`

```text
usage: cdpx cookies {get,set,clear} [--show-values] [--name NAME] [--value-env NOM] [--url URL]
```

Lit, pose ou purge les cookies du profil jetable. `get` masque toutes les
valeurs par défaut. `set` exige `--name`, `--value-env` et `--url`; la valeur
n'est jamais acceptée littéralement sur la ligne de commande.

```bash
cdpx cookies get
cdpx cookies get --show-values
cdpx cookies set --name PHPSESSID --value-env CHECKOUT_SESSION --url http://demo.test/
cdpx cookies clear
```

Sortie de lecture :

```json
{"cookies": [{"name": "PHPSESSID", "value": "***", "domain": "demo.test", "path": "/"}], "count": 1, "values_masked": true, "_cdpx": {"content_trust": "untrusted"}}
```

`--show-values` est une élévation volontaire : sa sortie ne va ni dans un
commit, ni dans un ticket, ni dans une preuve. La redaction transversale reste
prioritaire et remasque donc un secret déjà enregistré, même avec cette option.
`clear` purge tout le profil attribué. Un repli vers
`Network.clearBrowserCookies` maintient la compatibilité avec les versions de
Chrome qui ne proposent pas encore `Storage.clearCookies`.

### `cdpx storage`

```text
usage: cdpx storage [--kind {local,session}] [--show-values]
```

Lit le `localStorage` ou le `sessionStorage` de la page courante. Les chaînes
sont masquées par défaut, sans tenter de distinguer panier et token.

```bash
cdpx storage
cdpx storage --kind session
```

```json
{"kind": "session", "entries": {"cart": "***", "consent": "***"}, "count": 2, "values_masked": true, "_cdpx": {"content_trust": "untrusted"}}
```

## Parcours utilisateur

- Démarrer une session, exporter son identité, l'inspecter puis l'arrêter.
- Exercer exactement ce cycle sans Chrome réel avec `make mock`.
- Lire cookies et storage avec valeurs masquées par défaut.
- Poser un cookie depuis une référence d'environnement ou purger le profil.

## Validation

Les tests unitaires mock imposent identité triple, métadonnées, masquage,
références de secrets, allowlist, matrice d'autorité, manifests privés, lease
et confinement des artefacts. L'E2E multi-session lance trois Chrome et vérifie
profils/targets distincts, isolation d'état, autorités et teardown. Un scénario
unitaire dédié prouve que le backend mock emprunte le même chemin supervisé.

## Preuves

Preuves attendues : JUnit, JSON d'isolation/teardown et screenshots locaux
`opaque-restricted` pour les scénarios Chrome réel ; JUnit pour le cycle mock.

## Limites connues

- `--show-values` contourne volontairement le masquage et ne doit pas être
  persisté.
- `cookies clear` est global au profil jetable attribué, sans purge ciblée par
  origine.
- Le superviseur couvre les terminaisons gérées ; après un arrêt machine brutal,
  le répertoire runtime privé peut nécessiter un nettoyage au redémarrage.
