# M9 — Preuves secondaires généralisées

## Pourquoi

L'annotation d'intention (430/430 docstrings, déroulés `#:`) avait rendu le
cockpit lisible, mais beaucoup de tests prouvaient sans montrer : la pièce à
conviction (journal redacté, manifest d'allowlist, transcript CLI, capture)
restait dans le sandbox du run. Ce jalon déroule le backlog des 61
opportunités relevées pendant l'annotation (`attach-backlog.json`) pour que
chaque affirmation forte du harness soit visible dans le modal du cockpit.

## État livré

### Attachs sur toutes les suites

69 appels de preuve secondaire ajoutés (12 lots, un commit par lot) :
`attach_text`/`attach_json` pour les sorties redactées et manifests,
`attach_file` pour journaux ndjson et binaires, `attach_command_output` pour
les exécutions CLI in-process, `attach_cli_run` pour les sous-processus e2e,
`attach_log_excerpt` et `attach_cast` côté pipeline de preuve. Tous les
attachs sont gardés par `if evidence_case is not None:` — les suites restent
déterministes sans `--cdpx-evidence-dir`.

### `.ndjson` inlinable

Les journaux record/eval attachés tombaient en type `file` opaque, donc
invisibles dans le cockpit. `.ndjson` est désormais typé `logs`, textuel,
copié redacté et classé `internal` : le modal montre le journal qui prouve
que seule la référence `@env:` est persistée.

### Rattachement aux features

27 marqueurs `@pytest.mark.scenario` ajoutés (23 → 50), en privilégiant les
scénarios existants des fiches ; 4 scénarios nouveaux documentés dans
`state-session.md` (contenu de page untrusted, cycle superviseur sans Chrome,
diagnostics de démarrage redactés, manifest public sans leviers de contrôle).
Inventaire features et ratchet legacy restés à zéro violation.

### Sécurité des preuves

Chaque lot a vérifié au grep l'absence des canaris/secrets dans l'arbre
d'évidence produit. Les sorties `--show-values` ne sont jamais attachées
brutes : la preuve dérivée démontre le contraste masqué/révélé sans exposer
de valeur. Les binaires (captures, PDF) restent `opaque-restricted`, doublés
d'un JSON lisible (droits, signatures, tailles) pour la trace cockpit.

## Preuves

Backlog `attach-backlog.json` vidé (61/61, suppression au fil des lots).
`make check-local` vert après chaque lot ; `make test-e2e` vert sur les lots
Chrome ; `make docker-symfony-e2e` vert sur le lot Symfony (7/7) ;
`make check` et `make proof` verts en clôture, nouvelles preuves visibles
dans les pages features (contenu inliné dans le modal, pas de repli
« Contenu non embarqué » pour le textuel).

## Definition of Done

- [x] 61 entrées du backlog traitées ou reclassées, backlog à `[]` ;
- [x] tests verts avec et sans dossier d'évidence, intent 430/430 préservé ;
- [x] scenario_ids tous résolus par l'inventaire features (ratchet 0) ;
- [x] aucun canari/secret dans les artefacts d'évidence attachés ;
- [x] `make check` + `make proof` verts en fin de parcours.
