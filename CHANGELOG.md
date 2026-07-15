# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Ce projet suit un versionnage sémantique.

## [Non publié]

### Ajouté

- Portail **Docs** dans le cockpit de preuve : catalogue curaté et hiérarchique,
  rendu CommonMark sûr, huit spécifications features et diagrammes Mermaid
  hors ligne. Une référence dédiée décrit le cycle complet des sessions et des
  processus Chrome.
- Nouvelle commande `cdpx session start|status|stop` : profil navigateur
  jetable loopback, port dynamique, target unique, manifest privé, lease
  exclusif, TTL/owner et teardown supervisé. La surface publique passe de 30 à
  31 commandes.
- `make mock` démarre une session supervisée au premier plan avec le même
  contrat manifest/run/target que Chrome réel, affiche les exports nécessaires
  et nettoie ses ressources sur `Ctrl-C`.
- Politique d'autorités `observation`, `interaction`, `privileged`; les
  commandes composées, replay et scénarios sont préflightés au niveau maximal
  requis et toute capacité non classée est refusée.
- Références de secrets : `type --secret-env`, `cookies set --value-env`,
  `record type ... @env:NOM` et `scenario type.secret_ref`.
- Redaction transversale des secrets enregistrés, Bearer/JWT, URL/query,
  headers sensibles, console, réseau, profiler, erreurs, journaux et scénarios.
  `SecureArtifactWriter` réapplique ce nettoyage aux textes, JSON et fichiers
  textuels enregistrés. Les artefacts portent classification, SHA-256,
  politique de redaction, TTL et décision d'upload dans un manifest privé.

### Modifié

- `session start --startup-timeout` sépare désormais le budget de cold start
  Chrome (60 secondes par défaut, 300 maximum) des timeouts CDP. Le superviseur
  et son parent partagent ce budget sans course d'expiration; les runners CI
  contournent leur `/dev/shm` borné et les erreurs conservent des tails de logs
  bornés et redacted avant le teardown privé.
- **Breaking** : la session supervisée devient l'unique contrat d'exécution.
  `--session`, `--run-id` et `--target` (ou leurs variables d'environnement)
  sont requis avant discovery ; la connexion directe par `--host`/`--port`, le
  target implicite et l'allowlist optionnelle sont supprimés.
- **Breaking** : `tabs` expose uniquement `list`; création, activation et
  fermeture des targets appartiennent au superviseur de session.
- **Breaking** : `storage` masque désormais toutes les valeurs par défaut et
  expose `values_masked`; `--show-values` devient l'opt-in explicite, comme
  pour les cookies.
- **Breaking** : `type` ne retourne plus le texte saisi mais
  `typed:true,value_masked:true`. `record` écrit le schéma
  `cdpx.record/v2` : la saisie CLI exige `--secret-env`, `record type` exige
  `@env:NOM`, un scénario exige `secret_ref`, et `eval` reste redacted et non
  rejouable. Les évènements v1 sensibles sont refusés.
- **Breaking** : screenshots, PDF, journaux et preuves de scénarios sont
  toujours confinés sous les artefacts privés de la session ; l'option
  `scenario --evidence-dir` et les chemins de sortie arbitraires disparaissent.
- `click` exige désormais un élément attaché, visible, activé, stable, de
  taille non nulle et recevant le hit-test central. `type --clear` sélectionne
  le contenu puis émet Backspace avant `Input.insertText`; `wait_visible`
  teste réellement la visibilité. `key` couvre désormais Backspace/Delete,
  Home/End, PageUp/PageDown, Space et les quatre flèches en plus du jeu initial.
- Les assertions console/réseau des scénarios sont évaluées après un drainage
  final. Les preuves de scénario sont privées, manifestées et classifiées;
  screenshots/PDF/binaires sont `opaque-restricted`.
- La CI PR publie uniquement `.proof/shareable/` pendant 14 jours. La preuve
  release est conservée 30 jours et les distributions 90 jours.

### Sécurité

- Toute commande navigateur impose loopback, affectation exacte
  session/run/target, exclusivité par lease et métadonnée
  `_cdpx.content_trust:"untrusted"` sur les sorties. Les destinations et
  origines réelles sont contrôlées en fail-closed.
- `replay` relit `window.location.href` après chaque navigation et avant la
  mutation suivante : une redirection vers une origine interdite ne peut plus
  recevoir le clic suivant.
- Le parseur d'interception refuse toute action autre que `continue`, `block`
  ou un statut HTTP `200..599`; une faute de frappe ne continue plus
  silencieusement la requête.
- Les sorties publiques de découverte ne contiennent plus les URL WebSocket de
  débogage. Le staging de preuve exclut les fichiers opaques et échoue fermé si
  un canari connu subsiste.

## [0.2.0] — 2026-07-11

### Modifié

- Le projet est désormais publié sous licence MIT, avec inem0o comme
  détenteur du copyright établi pour 2026.
- GitHub devient la plateforme publique principale du projet à l'adresse
  `https://github.com/inem0o/cdpx`.
- GitHub Actions appelle les portails Make avec permissions minimales et actions
  épinglées ; la publication PyPI est préparée par Trusted Publishing OIDC.
- Les images Docker de validation sont épinglées par digest et suivies par
  Dependabot. Les preuves `.proof/` deviennent des artefacts CI non versionnés.
- Le wheel et le sdist sont inspectés avant une installation propre du wheel ;
  la notice MIT de Symfony accompagne les fixtures WebProfiler dérivées.
- Le portail standard `make check` exige désormais Docker, Chrome et Symfony;
  la boucle courte est explicitement `make check-local`. `make release` ajoute
  un cockpit de preuve vert sans skip Symfony et les artefacts wheel/sdist.
  Docker/Symfony indisponible n'est plus un succès dégradé de `make proof`.
- L'image de validation embarque les métadonnées de packaging et l'intégralité
  de l'outillage `.[dev]`; la CI exécute Chrome, Symfony et proof sur merge
  request, tag et pipeline planifié avant le job de build.
- L'outillage de distribution exige une version de `packaging` compatible avec
  les métadonnées PEP 639 (`License-Expression`/`License-File`) produites par
  setuptools récent.
- **Breaking**: `tabs list` retourne désormais `{tabs, count}` au lieu d'une
  liste racine, ce qui rend `--limit` effectif et maintient stdout sous forme
  d'objet JSON pour toutes les commandes.
- La garde `CDPX_ORIGINS` couvre aussi cookies, `vitals --click`, la destination
  d'interception et chaque mutation rejouée après navigation. `replay` valide
  tout le journal avant action et compare les résultats enregistrés.
- Les erreurs de navigation CDP deviennent des exit 1, le SEO accepte les
  racines JSON-LD tableaux/scalaires, les preuves masquent les headers sensibles
  et la couverture JS expose les octets utilisés/inutilisés par ressource.

- **Breaking**: `cdpx profiler` parse désormais les vrais panels HTML du
  WebProfilerBundle (db, twig, cache, exception, http_client, messenger,
  router, time, logger) récupérés par `fetch()` dans la page. `panels` est
  un objet structuré par panel (`available`/`parse_error`, jamais
  d'exception de parsing); nouvelle option `--panels all|none|liste`.
- **Breaking**: suppression des champs `signals` (en-têtes fabriqués
  `X-CDPX-Profiler-*`) et `profiler_bytes` de la sortie de `cdpx profiler`
  et de l'artefact `profiler` des scénarios: les métriques viennent des
  panels réels, plus de signaux de fixtures.

## [0.1.0] — 2026-07-05

Release initiale.

### Ajouté

- 30 sous-commandes CLI sur le Chrome DevTools Protocol, organisées en 8
  features documentées (navigation, DOM/actions, capture/observabilité,
  état/session, audits SEO/perf/a11y, diagnostics Symfony, orchestration,
  harness/preuve). Contrat stable: stdout = un objet JSON, stderr =
  diagnostics, exit 0/1/2.
- Rejeu réel des parcours: `record` exécute et journalise chaque action
  (NDJSON), `replay` valide le journal puis rejoue contre le navigateur et
  s'arrête à la première divergence.
- Forme composée `emulate <preset> -- <action>`: agir sous émulation dans la
  même connexion CDP (les overrides meurent avec la connexion).
- `screenshot --format png|jpeg`; `emulate --reset` restaure aussi
  l'user-agent; `profiler_status` reflète le statut HTTP réel du profiler.
- Garde d'origine `CDPX_ORIGINS` étendue aux commandes composées
  (classement par verbe d'action) et à `replay`.
- Cockpit de preuve `make proof`: documentation utilisateur par feature
  embarquée, vues Features / CLI / Validation / Gaps / Run, politique Symfony
  explicite (`CDPX_PROOF_REQUIRE_SYMFONY=1`), ratchet de dette narrative à 0.
- Garde-fous documentation mécaniques (`tests/test_docs.py`): commandes
  toutes documentées, fiches routées depuis le README, exemples `cdpx`
  validés contre le parseur réel.
- Packaging: version unique (`cdpx.__version__`), wheel+sdist via
  `make dist`, licence propriétaire, extras `dev`, couverture avec seuil,
  cible `typecheck` mypy, CI GitLab en matrice 3.11/3.12 avec artefacts.
