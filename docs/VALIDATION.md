# VALIDATION.md

Preuve reproductible des milestones cdpx. Les sorties restent compactes pour
les agents: stdout JSON quand c'est utile, logs bruts dans `.proof/`, et les
checks lourds explicitement séparés.

## Portails

- `make check`: lint ruff, format ruff, tests unitaires déterministes sans
  navigateur — y compris les garde-fous documentation (`tests/test_docs.py`:
  chaque commande documentée dans README et PRIMITIVES, chaque fiche routée,
  chaque exemple `cdpx` parsé contre le vrai parseur).
- `make test-e2e`: scénarios Chrome réel contre les fixtures locales.
- `make docker-check`: `make check` dans l'image portable `cdpx-ci`.
- `make docker-e2e`: Chrome réel dans l'image `cdpx-ci`.
- `make docker-symfony-e2e`: e2e profiler contre une vraie app Symfony Docker.
- `make proof`: collecte lint, format, tests unitaires/intégration, e2e Chrome,
  e2e Symfony (Docker), aide CLI, JUnit XML, logs, scénarios pytest et
  screenshots e2e, puis écrit `.proof/proof-report.html` et
  `.proof/validation-summary.json`.

## Le rapport de preuve

`.proof/proof-report.html` est une application monopage navigable, pensée
comme la documentation humaine du produit:

- **Features**: doc utilisateur complète de chaque feature (générée depuis
  `docs/features/*.md`), parcours, scénarios given/when/then, tests exécutés,
  preuves (screenshots Chrome réels).
- **CLI**: surface complète des commandes et rattachement entrypoint →
  feature. Un entrypoint public non rattaché est une violation bloquante.
- **Validation**: matrice milestone → preuve (tableau ci-dessous), tests par
  module, risques/mitigations, inconnues assumées.
- **Gaps**: violations (bloquantes) et warnings du catalogue. Le budget de
  tests « legacy » (rattachés sans scénario documenté) est un ratchet à 0.
- **Run**: commandes du run, suites JUnit, tests en échec ou les plus lents,
  fins de logs repliables.

Politique Symfony: si Docker est absent, les scénarios Symfony sont marqués
`unavailable` — visibles dans le hero et la feature, sans bloquer le verdict.
`CDPX_PROOF_REQUIRE_SYMFONY=1` (recommandé en CI planifiée) transforme cet
état en échec de preuve.

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
- e2e différés assumés: `key` isolé et le cycle de vie `tabs`
  new/activate/close ne sont pas des scénarios e2e dédiés — ils sont exercés
  indirectement par chaque test e2e (ouverture/fermeture d'onglet par la
  fixture, soumission clavier du formulaire) et couverts en mock au niveau
  CLI.
