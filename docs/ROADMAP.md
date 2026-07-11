# ROADMAP.md

Chaque milestone a sa fiche détaillée dans `docs/milestones/`. Règle générale:
un milestone n'ouvre pas tant que le précédent n'a pas son `make check` (et,
à partir de M1, son `make test-e2e`) vert.

## M0 — Socle validé ✅ (fait, cet environnement)

- Client CDP sync + découverte /json (compat PUT/GET).
- 15 primitives: goto, wait, eval, text, html, count, click, type, key,
  screenshot, pdf, console, network, cookies/storage, seo, metrics, tabs.
- CLI `cdpx` (JSON stdout, exit codes stables), binaire installé et fumé.
- Mock CDP scriptable qui enregistre le protocole émis.
- Serveur de fixtures + pages HTML témoins, eux-mêmes sous test.
- Suite unitaire déterministe verte, comptage capturé par le JUnit de
  `make proof`; ruff clean, `make check-local` = sous-portail local et
  `make check` = portail Docker/Chrome/Symfony complet.
- Harness: CLAUDE.md, HARNESS.md, docs, Makefile.

## M1 — e2e Chrome réel ✅

Objectif validé: primitives M0-M5 prouvées contre un vrai Blink/V8, avec les
MÊMES fixtures. `tests/e2e/test_e2e_chrome.py` couvre la suite Chrome réelle,
dont full-page screenshot, interception Fetch, vitals interaction,
SEO edge, allowlist CLI, a11y/frame/coverage.
Fiche: `milestones/M1-e2e-chrome.md`.

## M2 — Boucle de dev Symfony/Shopware ✅

Livré: `cdpx profiler` (`X-Debug-Token-Link` + fallback `X-Debug-Token`),
`console --follow` NDJSON, `dom-diff`, fixture profiler simulée, et e2e
Symfony réel via `docker-compose.symfony-e2e.yml`.
Fiche: `milestones/M2-boucle-symfony.md`.

## M3 — Interception & émulation ✅

Livré et validé e2e: `cdpx intercept --rule ... -- goto <url>` en commande
composée persistante (continue/fulfill/block), et `cdpx emulate
mobile|slow-3g|cpu-4x|--reset`.
Fiche: `milestones/M3-interception-emulation.md`.

## M4 — Mesure SEO/perf avancée ✅

Livré: `vitals` avec interaction optionnelle, `a11y` compact, `coverage`
JS/CSS, SEO enrichi (px estimés, doublons, JSON-LD), sorties bornées pour
l'agent.
Fiche: `milestones/M4-seo-perf.md`.

## M5 — Orchestration & sessions ✅

Livré: `record`/`replay` NDJSON compact avec divergence, `frame`,
`CDPX_ORIGINS` pour mutations, `--max-actions` sur replay.
Fiche: `milestones/M5-orchestration.md`.

## M6 — Distribution ✅

Livré: `cdpx --version`, image `cdpx-ci`, GitLab CI réutilisable, Compose
Symfony e2e, snippet CLAUDE navigateur.
Fiche: `milestones/M6-distribution.md`.
