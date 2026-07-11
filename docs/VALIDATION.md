# VALIDATION.md

Preuve reproductible des milestones cdpx. Les sorties restent compactes pour
les agents: stdout JSON quand c'est utile, logs bruts dans `.proof/`, et les
checks lourds explicitement séparés. `.proof/` est généré localement ou publié
comme artefact GitHub Actions; il n'est pas versionné.

## Portails

- `make check-local`: sous-portail de développement sans navigateur: lint,
  format, mypy et tests unitaires déterministes — y compris les garde-fous
  documentation (`tests/test_docs.py`:
  chaque commande documentée dans README et PRIMITIVES, chaque fiche routée,
  chaque exemple `cdpx` parsé contre le vrai parseur).
- `make check`: portail qualité standard et bloquant: `check-local`, puis le
  même contrôle dans l'image Docker, Chrome réel dans Docker et Symfony réel.
- `make test-e2e`: scénarios Chrome réel contre les fixtures locales.
- `make docker-check`: `make check-local` dans l'image portable `cdpx-ci`.
- `make docker-e2e`: Chrome réel dans l'image `cdpx-ci`.
- `make docker-symfony-e2e`: e2e profiler contre une vraie app Symfony Docker.
- `make proof`: collecte lint, format, tests unitaires/intégration, e2e Chrome,
  e2e Symfony (Docker), aide CLI, JUnit XML, logs, scénarios pytest et
  screenshots e2e, puis écrit `.proof/proof-report.html` et
  `.proof/validation-summary.json`.
- `make release`: portail agrégé bloquant. Il exige `check`, les contrôles
  Docker, Chrome réel, Symfony réel sans skip, la preuve complète et les
  artefacts wheel/sdist. `check-local` seul ne constitue jamais un verdict de
  release.
- `make dist`: construit wheel et sdist, applique `twine check --strict`,
  contrôle les contenus requis/interdits, puis installe le wheel dans un venv
  temporaire pour vérifier la licence, l'aide et les 30 commandes.

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

Politique Symfony: Docker, Compose et la suite Symfony réelle sont obligatoires
pour toute preuve de release. Une preuve `unavailable` ou un test Symfony
skippé rend le verdict rouge. Il n'existe pas de succès release dégradé sans
Docker. `make check-local` sert seulement à raccourcir la boucle de
développement; le portail standard `make check` reste complet.

Les workflows GitHub Actions appellent ces cibles Make plutôt que de réécrire
leur logique. Un résultat de runner GitHub reste requis avant tag, même lorsque
les mêmes commandes ont réussi localement.

## Matrice

| Milestone | Preuve |
| --- | --- |
| M0 socle | `make check-local`, mock CDP qui valide sorties, méthodes, params et ordre |
| M1 Chrome réel | `make test-e2e`, suite Blink/V8 complète sur les mêmes fixtures |
| M2 Symfony | `make docker-symfony-e2e`, extraction profiler via header réel |
| M3 interception | unit + e2e Fetch continue/fulfill/block, timing settle |
| M4 SEO/perf | vitals avec interaction, a11y AXTree, coverage JS/CSS, SEO edge |
| M5 orchestration | record/replay avec divergence, frame, allowlist, max-actions |
| M6 distribution | `make docker-check`, `make docker-e2e`, image `cdpx-ci` |
| Release | `make release`, tous les portails précédents + proof + wheel/sdist |

## Cas limites couverts

- Absence de Chrome: échec e2e explicite, sans faux succès par skip.
- Absence de Docker/Compose ou skip Symfony: échec explicite de la preuve et
  du portail release.
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
- `key` et le cycle de vie CLI `tabs` new/activate/close disposent de scénarios
  Chrome dédiés, en complément du protocole figé par le mock.
