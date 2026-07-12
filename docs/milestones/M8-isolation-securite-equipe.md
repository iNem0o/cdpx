# M8 — Isolation et sécurité du mode équipe

## Pourquoi

Une première page implicite et un Chrome partagé peuvent fonctionner localement
tout en devenant non déterministes avec plusieurs agents. Ce jalon attribue une
capacité navigateur complète à chaque run et ferme les frontières de confiance,
de secrets et d'artefacts.

## Contrats implémentés

### Session attribuée

`cdpx session start` crée un profil jetable, un port loopback dynamique et un
target page unique. Le manifest privé lie `session_id`, `run_id`, `target_id`,
grant, origines, TTL et supervisor. Chaque commande équipe exige
`--session`, `--run-id`, `--target`; host/port ne sont pas surchargeables et un
lease exclusif refuse les commandes concurrentes.

`stop`, expiration, disparition de l'owner et terminaison supervisée passent
par le teardown : target fermé, Chrome terminé, profil et dossier supprimés.

### Autorités et origines

- `observation` : navigations/lectures/captures, jamais `eval` ;
- `interaction` : observation + clic/saisie/clavier ;
- `privileged` : eval, cookies, storage, profiler, interception, émulation et
  opérations sensibles.

Les commandes composées sont préflightées au niveau maximal. Une allowlist
HTTP(S) non vide est obligatoire; destination et origine réelle sont contrôlées
avant/après navigation et avant mutation. Toute sortie équipe indique
`content_trust: "untrusted"`.

### Secrets et preuves

Saisies/cookies/scénarios/journaux utilisent des références d'environnement.
La redaction couvre secrets connus, credentials, URL/query, headers, console,
réseau, profiler et erreurs. Les preuves privées sont classifiées; seul le
staging textuel manifesté peut être envoyé, après scan de canaris.

### Interactions et orchestration

`wait_visible` vérifie la visibilité réelle. `click` exige actionability et
hit-test; `type --clear` sélectionne puis émet Backspace. Replay bloque les
redirections hors origine et l'interception refuse les actions inconnues. Les
assertions de scénario arrivent après le drainage final.

## Preuves ciblées présentes

- unitaires : policy, session, journal, redaction, artefacts, CLI équipe,
  scénarios et interactions ;
- intégration sécurité : canaris dans stdout/stderr simulés, URL, headers,
  console, storage, profiler, journal et artefacts, plus modes `0600`/`0700` ;
- Chrome réel : trois sessions simultanées prouvent profils/targets/états
  isolés, grants, lease et `stop`; un second scénario envoie SIGTERM au
  supervisor et prouve suppression du profil et fermeture du port. Chaque
  scénario attache un screenshot local classé `opaque-restricted` et un JSON.

La cible locale `make test-e2e` est verte avec ces scénarios intégrés. La suite
Symfony possède son portail Docker séparé et bloquant pour le verdict complet.

## Validation intégrée

Le jalon est validé par `make check`, par les deux scénarios session collectés
dans `make proof`, par `make cov` au-dessus du seuil de 85 %, et par
l'installation isolée du wheel qui expose les 31 commandes attendues. Les
cases correspondantes sont closes dans `docs/TODO.md`.

## Limites assumées

- Le mode legacy conserve première page implicite, host configurable et
  allowlist opt-in pour compatibilité pré-1.0.
- Un arrêt machine brutal peut laisser un dossier runtime privé à nettoyer.
- Le TTL des artefacts autonomes est manifesté mais sans daemon global de
  purge hors session supervisée.
- La redaction ne devine pas toute PII ni tout secret inconnu; les contenus
  opaques restent non partageables par défaut.
