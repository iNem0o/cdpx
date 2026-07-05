# RELEASE-PLAN.md — release initiale cdpx

Fichier de suivi du refactor de release. Chaque étape terminée est cochée dans
le même commit que son contenu. Reprise de session: lire ce fichier, prendre la
première case non cochée, suivre la boucle CLAUDE.md (test mock d'abord,
`make check` vert, commit atomique).

## Contexte (constats de l'analyse, 2026-07-05)

- **Fonctionnel**: `record`/`replay` n'exécutaient aucune action navigateur
  (journal NDJSON seul) alors que la doc promet « journaliser et relire un
  parcours »; bug `emulate --reset` (viewport persiste entre connexions, UA
  jamais réinitialisé); `profiler_status` codé en dur à 200; param `fmt` de
  `screenshot` jamais exposé au CLI.
- **Sécurité**: `CDPX_ORIGINS` ne protégeait pas `dom-diff` (mutations réelles);
  entrée `replay` morte dans `MUTATING_COMMANDS`.
- **Preuve**: la SPA n'affichait pas des sections calculées (validation_matrix,
  coverage_groups, risks, unknowns, log tails, focus, surface CLI, JUnit par
  suite); ~700 lignes de rendu statique mort dans proof.py; `render_html` sans
  test; 21 scénarios narrés vs 110 tests rattachés.
- **Docs**: `pdf` absent de PRIMITIVES.md; README sans routage features ni
  contrat CLI; fiches features en EN; pas de doc utilisateur exhaustive.
- **QA/packaging**: pas de LICENSE/CHANGELOG/classifiers; version dupliquée;
  `make dist` = tar; pas de mypy/coverage/checks release; CI monostage;
  `.idea/` versionné; 21 sous-commandes sans test de dispatch CLI.

## Décisions actées

1. Langue: **français partout** (fiches features traduites).
2. Licence: **propriétaire / interne** (inem0o, tous droits réservés).
3. Distribution: **wheel+sdist en artefacts CI, pas de publication**.
4. QA complet: mypy (cible dédiée non bloquante), pytest-cov avec seuil,
   checks mécaniques release. Pas de pre-commit (portail unique `make check`).
5. `record`/`replay`: **rejeu réel** (exécution + arrêt à la divergence).

## Règles transverses

- Test mock d'abord: sortie JSON ET séquence CDP émise.
- `make check` vert à chaque étape; commits atomiques.
- Tout garde-fou doc/release est mécanique (un test), pas déclaratif.
- Changement de contrat CLI ⇒ tests + PRIMITIVES.md + fiche feature.

---

## Phase 0 — Suivi

- [x] `docs/RELEASE-PLAN.md` créé (ce fichier) + lien dans `docs/TODO.md`.

## Phase 1 — Corrections code

- [x] 1.1 Garde d'origine: `MUTATING_COMMANDS = {click, type, key, eval,
      intercept, dom-diff}` (advanced.py). Tests: dom-diff refusé hors
      CDPX_ORIGINS (exit 1, aucune commande CDP émise), autorisé sinon;
      assertion du contenu exact du set. Ajustement HARNESS §6: record/replay
      n'entrent dans le set qu'en Phase 2, quand leur passage par `_client()`
      rend le garde-fou effectivement appliqué (pas de règle sans enforcement).
- [x] 1.2 Fix `emulate --reset`. Constat e2e (Chrome 150): intra-connexion,
      `clearDeviceMetricsOverride` fonctionne mais l'UA du preset mobile
      survivait au reset → ajout `setUserAgentOverride ""`. DÉCOUVERTE: les
      overrides d'émulation meurent avec la connexion CDP — la persistance
      inter-connexions notée précédemment ne se reproduit pas; conséquence:
      `emulate` standalone est inopérant pour l'invocation suivante → forme
      composée `emulate <preset> -- <action>` ajoutée au périmètre Phase 2
      (réutilise l'interpréteur d'actions). Séquence de reset figée en mock,
      sémantique prouvée en e2e.
- [x] 1.3 `screenshot --format png|jpeg` exposé au CLI (param `fmt` existant).
      Test mock: format transmis à `Page.captureScreenshot`, défaut png.
- [x] 1.4 `profiler_status` réel (`res.status` urllib, plus de littéral 200).
- [x] 1.5 state.py: `except CDPError` ciblé, `import json` module-level; mock
      `fail_on(method)` + test du fallback `clear_cookies` (séquence
      Storage.clearCookies → Network.clearBrowserCookies).

## Phase 2 — record/replay: rejeu réel

- [x] 2.1 Interpréteur d'actions partagé `src/cdpx/primitives/actions.py`
      (extraction de `dev._run_action` + actions goto/wait), utilisé par
      dom-diff/record/replay/emulate. Non-régression protocole dom-diff OK.
- [x] 2.2 `record` exécute ET journalise (NDJSON `{action, ok, result, ts}`;
      échec → ok:false journalisé puis exception → exit 1). Guard actif.
- [x] 2.3 `replay` rejoue: validation complète AVANT exécution, arrêt à la
      première divergence (exit 1, JSON `{"ok": false, "divergence": ...}`,
      champ `played`). NOTE: le guard est passé d'un set de noms à
      `command_mutates(command, action)` — les commandes composées
      (dom-diff/record/emulate) sont classées par le VERBE de leur action
      (click/type/key/eval mutent; goto/wait lisent); replay mutant en bloc.
- [x] 2.4 e2e `test_record_replay_real` (record agit sur form.html, rejeu vert
      sur onglet vierge, journal altéré → divergence event 2) + fiche
      `orchestration-control.md` (globs + gap réécrit). PRIMITIVES.md: Phase 7.
- [x] 2.5 `emulate <preset> -- <action...>` (forme composée, même connexion).
      Tests mock (preset avant action) + e2e (`screen.width` 390 + UA mobile
      pendant l'action).

## Phase 3 — Filet tests CLI + e2e

- [x] 3.1 Test paramétré de dispatch CLI (16 cas + pdf/tabs-activate dédiés)
      couvrant wait, text, html, count, click, type, key, pdf, network,
      storage, metrics, dom-diff, emulate, vitals, a11y, coverage, frame;
      record/replay/intercept/profiler en tests dédiés.
- [x] 3.2 Tests argparse fragiles: dom-diff avec/sans `--`; record strip `--`
      du journal; intercept multi-règles (Fetch.fulfillRequest 503) + action
      non-goto → exit 1 sans Fetch; emulate sans preset ni --reset → exit 1.
- [x] 3.3 e2e nouveaux: `test_emulate_mobile_and_reset_real` (1.2),
      `test_metrics_real`, `test_pdf_real`, `test_record_replay_real` (2.4),
      `test_emulate_composed_action_real` (2.5). Globs fiches: remappage
      complet en Phase 4 (nouveaux tests = warnings non bloquants d'ici là).
      Différés assumés à noter dans VALIDATION.md (Phase 7): key isolé, tabs
      lifecycle (déjà exercés indirectement par les fixtures e2e).

## Phase 4 — Doc utilisateur par feature

- [x] 4.1 Parseur: section `## Usage` obligatoire; garde-fou: chaque entrypoint
      `cdpx <cmd>` / `make <target>` du TOML doit avoir son heading `###` dans
      Usage sinon violation (`ok:false`); `FeatureSpec.body` + `doc_html`.
- [x] 4.2 `src/cdpx/proofing/markdown.py`: convertisseur minimal (h2-h4,
      paragraphes, listes, tableaux, fences, inline code, gras, liens),
      escape-first, zéro dépendance; `tests/test_markdown.py`.
- [x] 4.3 Réécrire les 8 fiches en FRANÇAIS avec Usage exhaustif par commande:
      synopsis, options, exemple bash, exemple de sortie JSON, exit codes,
      pièges. Surface FINALE (--format, rejeu réel). Statut
      harness-proof-cockpit → validated.

## Phase 5 — Refonte rapport de preuve

- [x] 5.1 `build_summary`: `log_tail` par commande, `junit[suite].focus` et
      `.cases`, métrique `unavailable` dans totals.
- [x] 5.2 `SPA_JS`: panneau « Documentation utilisateur » (doc_html) par
      feature; vue Run enrichie (JUnit par suite, focus, log tails); routes
      `#/cli` (surface CLI + rattachement entrypoints) et `#/validation`
      (matrice, coverage_groups, risks, unknowns); hero avec `unavailable`.
- [x] 5.3 Suppression du code mort (~700 lignes: REPORT_CSS/REPORT_JS,
      `_render_*`, `_feature_cards`, `_metric`, `_table`…); conserver `_tail`,
      `_case_focus`, `_suite_for_summary`, `_empty_suite`,
      `_json_for_html_script`, `parse_help_commands`; `render_html(summary)`.
- [x] 5.4 `CDPX_PROOF_REQUIRE_SYMFONY=1` → `unavailable` devient proof_failure.
- [x] 5.5 Tests: `test_spa_renders_every_summary_key` (calculé ⇒ rendu), smoke
      render_html, log tails/focus, doc_html par feature, flag Symfony;
      adaptation des tests existants.
- [x] 5.6 Absorption legacy (globs scénarios élargis) + ratchet — RÉSULTAT:
      0 warning legacy, LEGACY_WARNING_BUDGET = 0 (le catalogue est 100%
      documenté; le budget ne peut que rester nul)
      `LEGACY_WARNING_BUDGET` dans features.py.

## Phase 6 — README + garde-fous docs

- [ ] 6.1 README.md FR complet: pitch, installation (profil Chrome jetable),
      démarrage rapide, **Contrat CLI** (sorties, exit codes, connexion,
      CDPX_ORIGINS, budgets), **tableau des 8 features** (lien par fiche),
      **index des 29 commandes**, qualité & preuve (lecture du cockpit),
      docs annexes, licence.
- [ ] 6.2 `tests/test_docs.py`: chaque sous-commande dans README ET
      PRIMITIVES.md; chaque fiche feature liée dans README; options globales +
      CDPX_ORIGINS + exit codes mentionnés; tout fence `cdpx ...` parsé par
      `build_parser()` (exemples toujours valides).

## Phase 7 — PRIMITIVES.md + VALIDATION.md

- [ ] PRIMITIVES.md: catalogue unique groupé par les 8 features (liens fiches),
      + `pdf`, + `--format`, + rejeu réel, mention « planifié » supprimée.
- [ ] VALIDATION.md: vues réelles de la SPA, politique Symfony; tableau
      `| Milestone | Preuve |` conservé tel quel (parse_validation_matrix).

## Phase 8 — Packaging, QA, CI

- [ ] 8.1 pyproject: version dynamique (`attr = cdpx.__version__`), LICENSE
      propriétaire + `license-files`, readme, keywords, classifiers, urls,
      extras `dev`, ruff `target-version = py311`; CHANGELOG.md (0.1.0);
      `make setup` → `pip install -e .[dev]`.
- [ ] 8.2 `tests/test_packaging.py`: pas de version statique; LICENSE présent;
      CHANGELOG contient la version; floor requires-python == ruff target.
- [ ] 8.3 Makefile: `dist` → `python -m build` + `twine check` (tar supprimé);
      cible `cov` (--cov-fail-under calé sur le réel −2); cible `typecheck`
      (mypy lenient, hors check).
- [ ] 8.4 `git rm -r --cached .idea` + .gitignore (.idea/, .mypy_cache/,
      .coverage, htmlcov/). `.proof/` reste versionné.
- [ ] 8.5 .gitlab-ci.yml: stages test+build; check en matrice 3.11/3.12 avec
      cov; job proof (artefacts .proof/ + report junit); typecheck
      allow_failure; e2e:chrome avec artefacts; job build (dist + smoke
      install); e2e:symfony inchangé.

## Phase 9 — Validation finale

- [ ] `make check` vert (ruff + tests dont packaging/docs/markdown).
- [ ] `make test-e2e` vert (scénarios dont emulate/metrics/pdf/replay).
- [ ] `make docker-symfony-e2e` si Docker disponible.
- [ ] `make proof` → ok:true, 0 violation, 0 gap; inspection manuelle du
      rapport (doc FR par feature, vues Run/CLI/Validation peuplées).
- [ ] `make dist` → wheel+sdist + smoke install.
- [ ] RELEASE-PLAN coché, TODO.md à jour, commit final.
