# VALIDATION.md

Preuve reproductible des milestones cdpx. Les sorties restent compactes pour
les agents: stdout JSON quand c'est utile, logs bruts dans `.proof/`, et les
checks lourds explicitement séparés.

## Portails

- `make check`: lint ruff, format ruff, tests unitaires déterministes sans
  navigateur.
- `make test-e2e`: scénarios Chrome réel contre les fixtures locales.
- `make docker-check`: `make check` dans l'image portable `cdpx-ci`.
- `make docker-e2e`: Chrome réel dans l'image `cdpx-ci`.
- `make docker-symfony-e2e`: e2e profiler contre une vraie app Symfony Docker.
- `make proof`: collecte lint, format, tests unitaires/intégration, e2e Chrome,
  aide CLI, JUnit XML, logs, scénarios pytest et screenshots e2e, puis écrit
  `.proof/proof-report.html` et `.proof/validation-summary.json`.

## Matrice

| Milestone | Preuve |
| --- | --- |
| M0 socle | `make check`, mock CDP qui valide sorties, méthodes, params et ordre |
| M1 Chrome réel | `make test-e2e`, 18 scénarios Blink/V8 sur les mêmes fixtures |
| M2 Symfony | `make docker-symfony-e2e`, extraction profiler via header réel |
| M3 interception | unit + e2e Fetch continue/fulfill/block, timing settle |
| M4 SEO/perf | vitals avec interaction, a11y AXTree, coverage JS/CSS, SEO edge |
| M5 orchestration | record/replay avec divergence, frame, allowlist, max-actions |
| M6 distribution | `make docker-check`, `make docker-e2e`, image `cdpx-ci` |

## Cas limites couverts

- Absence de Chrome: échec e2e explicite, sans faux succès par skip.
- Preuve e2e: chaque scénario Chrome non skippé doit exposer au moins un
  screenshot dans `.proof/evidence/`.
- Cookies: `Storage.clearCookies` avec fallback CDP historique.
- Interception: réponse fulfill encodée, block réseau, continue, règle invalide.
- Replay: NDJSON invalide, action manquante, divergence `ok:false`, budget
  `--max-actions`.
- SEO: JSON-LD invalide, Product incomplet, H1 dupliqués, longueurs estimées.
- Origines: mutations refusées hors `CDPX_ORIGINS`, lectures permises.
- Sorties agentiques: JSON compact par défaut, limites `--limit`/`--max-actions`,
  NDJSON pour les flux, secrets cookies masqués.

## Dette non bloquante

- `KEY_MAP` reste volontairement minimal; il s'étend sur besoin réel et testé.
- `eval` reste une échappatoire surveillée; un usage répété se promeut en
  primitive nommée.
