# M5 — Orchestration et garde-fous

## Pourquoi

Composer les primitives en parcours de recette bornés, rejouables et
observables, sans créer un langage de macros illimité.

## État livré

### `record` / `replay`

`record` exécute une action et écrit une ligne `cdpx.record/v2` privée. Actions,
résultats et erreurs sont redacted. Une saisie littérale est refusée,
`type ... @env:NOM` persiste seulement la référence et permet un rejeu, tandis
que `eval` reste non rejouable. Une référence absente est refusée avant effet
CDP.

`replay` valide tout le journal, sa rejouabilité, les secrets et
`--max-actions` avant la première action. Il compare les résultats enregistrés
hors champs volatils, relit l'URL réelle après navigation et bloque une
redirection hors origine avant la mutation suivante. Une comparaison verte ne
remplace pas une assertion métier explicite.

### Scénarios YAML

Le runner compose `goto`, `wait_visible`, `wait_text`, `click`, `type`, `key`
et `eval`, puis assertions et captures. `wait_visible` vérifie rendu/boîte non
nulle; une saisie exige `secret_ref`; le drainage console/réseau final
précède le verdict. Les artefacts sont privés et classifiés.

### `frame`

La lecture parcourt les `contentDocument` des iframes same-origin et retourne
le premier match. Elle n'utilise pas de contextId CDP et ne traverse pas la
frontière cross-origin.

### Garde-fous

- Manifest/run/target et allowlist sont obligatoires ; toutes les origines
  consultées sont fail-closed et l'autorité maximale du fichier est
  préflightée. L'isolation complète du navigateur appartient au M8.
- `--max-actions` borne un replay donné, pas un compteur cumulatif de session.

## Preuves

Mock CDP : parsing, protocole, journal v2, références de secrets, divergences,
origines et drainage. Chrome réel : record/replay, scénarios pass/fail,
interactions et preuves. Symfony Docker : scénarios contre l'application témoin.

## Definition of Done

- [x] parcours record/replay complet sur fixtures mock et Chrome réel ;
- [x] allowlist obligatoire et contrôles de redirection testés ;
- [x] scénarios YAML, assertions et preuves documentés ;
- [x] garde-fous exécutables décrits dans HARNESS.md.
