# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Ce projet suit un versionnage sémantique.

## [Non publié]

### Modifié

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

- 29 sous-commandes CLI sur le Chrome DevTools Protocol, organisées en 8
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
