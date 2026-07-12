# M8 — Sessions supervisées et frontière de confiance

## Pourquoi

Une première page implicite et un Chrome partagé deviennent non déterministes
dès que plusieurs agents travaillent en parallèle. Ce jalon attribue une
capacité navigateur complète à chaque run, en fait le seul contrat public et
ferme les frontières de confiance, de secrets et d'artefacts.

## Contrats implémentés

### Session attribuée

`cdpx session start` crée un profil jetable, un port loopback dynamique et un
target page unique. Le manifest privé lie `session_id`, `run_id`, `target_id`,
autorité, origines, TTL, backend et superviseur. Chaque commande navigateur
exige `--session`, `--run-id`, `--target`, explicitement ou par environnement ;
l'endpoint n'est pas surchargeable et un lease exclusif refuse les commandes
concurrentes.

La connexion directe, le choix implicite de la première page et les opérations
publiques de lifecycle des targets sont supprimés. `tabs list` inspecte
uniquement le target attesté. `make mock` crée le même manifest et le même
cycle supervisé avec un backend simulé.

`stop`, expiration, disparition de l'owner et terminaison supervisée passent
par le teardown : target fermé, Chrome terminé, profil et dossier supprimés.

### Autorités et origines

- `observation` : navigations/lectures/captures, jamais `eval` ;
- `interaction` : observation + clic/saisie/clavier ;
- `privileged` : eval, cookies, storage, profiler, interception, émulation et
  opérations sensibles.

Les commandes composées sont préflightées au niveau maximal. Une allowlist
HTTP(S) non vide est obligatoire; destination et origine réelle sont contrôlées
avant/après navigation et avant mutation. Toute sortie indique
`_cdpx.content_trust: "untrusted"`.

### Secrets et preuves

Saisies CLI, cookies, scénarios et journaux utilisent des références
d'environnement.
La redaction couvre secrets connus, credentials, URL/query, headers, console,
réseau, profiler et erreurs. Les preuves privées sont classifiées; seul le
staging textuel manifesté peut être envoyé, après scan de canaris.

### Interactions et orchestration

`wait_visible` vérifie la visibilité réelle. `click` exige actionability et
hit-test; `type --clear` sélectionne puis émet Backspace. Replay bloque les
redirections hors origine et l'interception refuse les actions inconnues. Les
assertions de scénario arrivent après le drainage final.

## Preuves ciblées présentes

- unitaires : policy, session, journal, redaction, artefacts, CLI supervisé,
  scénarios et interactions ;
- intégration sécurité : canaris dans stdout/stderr simulés, URL, headers,
  console, storage, profiler, journal et artefacts, plus modes `0600`/`0700` ;
- Chrome réel : trois sessions simultanées prouvent profils/targets/états
  isolés, autorités, lease et `stop`; un second scénario envoie SIGTERM au
  superviseur et prouve suppression du profil et fermeture du port. Chaque
  scénario attache un screenshot local classé `opaque-restricted` et un JSON.
- backend mock : un scénario dédié prouve manifest privé, attestation du
  target, commande sous identité triple et teardown sans Chrome réel.

La cible locale `make test-e2e` est verte avec ces scénarios intégrés. La suite
Symfony possède son portail Docker séparé et bloquant pour le verdict complet.

## Validation intégrée

Le jalon est validé par `make check`, par les scénarios session Chrome et mock
collectés dans `make proof`, par `make cov` au-dessus du seuil de 85 %, et par
l'installation isolée du wheel qui expose les 31 commandes attendues. Le
HARNESS, les fiches features, la matrice de validation et le cockpit décrivent
le même contrat. Les cases correspondantes sont closes dans `docs/TODO.md`.

## Limites assumées

- Un arrêt machine brutal peut laisser un dossier runtime privé à nettoyer.
- Le TTL des preuves locales est manifesté mais sans daemon global de purge.
- La redaction ne devine pas toute PII ni tout secret inconnu; les contenus
  opaques restent non partageables par défaut.
