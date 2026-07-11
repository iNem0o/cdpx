# TODO.md — liste de travail

> **Release initiale en cours**: le plan de refactor complet et son suivi
> (cases à cocher) vivent dans [`docs/RELEASE-PLAN.md`](RELEASE-PLAN.md).

Format: chaque item porte son POURQUOI et son COMMENT. Un item se prend en
suivant la boucle CLAUDE.md (test mock d'abord). Cocher = `make check` vert +
doc à jour. Les items renvoient aux fiches milestones pour le détail.

## Fait (M0)

- [x] Client CDP sync (commandes, évènements bufferisés, timeouts propres)
- [x] Découverte /json avec compat PUT/GET pour /json/new
- [x] Primitives: goto, wait, eval, text, html, count, click, type, key,
      screenshot, pdf, console, network, cookies, storage, seo, metrics, tabs, version
- [x] CLI JSON stdout / stderr diag / exit 0-1-2, binaire `cdpx` installé
- [x] Mock CDP scriptable + enregistrement du protocole émis
- [x] Serveur de fixtures (statique + /api/json, /api/slow, /api/status,
      /api/echo, /api/set-cookie) lui-même sous test
- [x] Fixtures HTML: index, form, spa, console, network, seo, seo-broken,
      storage, iframe, child, long, intercept, vitals, coverage, seo-edge (+ assets)
- [x] Tests unitaires, ruff clean, `make check`
- [x] E2E Chrome réel avec Chrome/Chromium obligatoire
- [x] Harness: CLAUDE.md, HARNESS.md, Makefile, docs complètes

## M1 — e2e Chrome réel (PRIORITÉ à la reprise)

- [x] Dérouler `make test-e2e` sur un poste avec chromium.
      Pourquoi: le mock prouve le protocole, pas Blink. Comment: fiche M1.
- [x] Corriger les divergences mock/réel DANS LE MOCK (+ note fiche M1).
- [x] Job CI GitLab e2e nightly (image chromium, cf. M6 pour l'image finale).
- [x] Vérifier `--full-page` (captureBeyondViewport) sur page longue réelle:
      ajouter fixture long.html (contenu > 3 viewports) + marqueur bas de page.

## M2 — Boucle Symfony (le différenciateur)

- [x] `cdpx profiler`: header x-debug-token-link -> fetch profiler.
      Pourquoi: N+1 et exceptions visibles par l'agent sans ouvrir le browser.
      Comment: fixture /api/profiler-sim + test mock scriptant le header. Fiche M2.
- [x] `cdpx console --follow` (NDJSON, --max n).
- [x] `cdpx dom-diff` (snapshot normalisé avant/après, diff stable).
- [x] Fixture profiler-sim dans fixture_server + tests marqueurs.

## M3 — Interception & émulation

- [x] Décision d'architecture "session" (connexion persistante) — documenter
      dans CONTEXT.md. Pourquoi: Fetch.enable meurt avec la connexion.
- [x] `cdpx intercept` (fulfill/fail/continue par règles).
- [x] `cdpx emulate` (presets mobile / slow-3g / cpu-4x, --reset).

## M4 — SEO/perf avancé

- [x] `cdpx vitals` (LCP/CLS/INP, script pré-injecté) + fixture CLS/interaction.
- [x] `cdpx a11y` (AXTree compacté = vision sémantique agent) + fixture a11y.html.
- [x] `cdpx coverage` (JS/CSS mort par fichier).
- [x] Enrichir `cdpx seo`: taille title/description en px estimés, détection
      contenu dupliqué intra-page, validation JSON-LD contre schéma minimal.

## M5 — Orchestration & garde-fous

- [x] `cdpx record` / `cdpx replay` (NDJSON versionnable, stop à la divergence).
- [x] `cdpx frame` (contextId par iframe; fixtures déjà prêtes).
- [x] Allowlist CDPX_ORIGINS: mutations refusées hors liste (exit 1), lectures
      permises. Pourquoi: exécution agentique autonome = pouvoir borné. Fiche M5.
- [x] --max-actions (budget de session agentique).

## M6 — Distribution

- [x] `cdpx --version`.
- [x] Image Docker chromium+cdpx+fixtures, job GitLab réutilisable.
- [x] docker-compose.e2e.yml de référence Symfony/Shopware.
- [x] Snippet CLAUDE.md "outillage navigateur" pour les projets inem0o.

## Dette / vigilance (fil continu)

- [x] Portail release agrégé: Docker/Compose + Chrome + Symfony sans skip +
      proof + dist obligatoires; `check-local` sépare la boucle courte du
      `check` standard complet; CI MR/tags alignée et cleanup Compose garanti.
- [x] Network.clearBrowserCookies déprécié -> bascule Storage.clearCookies avec fallback.
- [ ] KEY_MAP minimal (5 touches): étendre à la demande, jamais en spéculatif.
- [ ] `eval` reste l'échappatoire: si un usage revient 3 fois dans les sessions
      agent, le promouvoir en primitive nommée (règle d'or du catalogue).
