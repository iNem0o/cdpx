# Glossary — French → English migration

Single source of truth for translating this repository into English. Every
translation pass (human or agent) MUST use these equivalences; any new
ambiguous term gets decided here first, in its own commit, before mass
translation applies it. One term, one translation — no synonyms.

## Doctrine terms

| Français | English | Notes |
|---|---|---|
| portail (qualité) | gate | `make check` = THE gate; "portail bloquant" → "blocking gate" |
| preuve | proof | pipeline de preuve → proof pipeline; rapport de preuve → proof report |
| cockpit (de preuve) | proof cockpit | keep "cockpit" as-is |
| site/app témoin | reference site / reference app | the deterministic fixture site in `tests/fixtures/` |
| harnais | harness | HARNESS.md keeps its name |
| boucle (de travail) | loop | boucle courte → short loop; boucle Symfony → Symfony loop |
| chantier | workstream | soldé → settled |
| fiche (feature, Usage) | sheet | fiche feature → feature sheet; fiche Usage → usage sheet |
| annotation d'intention | intent annotation | docstring + `#:` standard in tests |
| contrat scellé | sealed contract | the proof façade doctrine |
| staging manifesté | manifested staging | fail-closed allowlist copy into `.proof.new` |
| durcissement | hardening | durci → hardened |
| rejeu / rejouer | replay | |
| ratchet | ratchet | already English; "ratchet à 0" → "ratchet at 0" |
| jauge | gauge | |
| garde (mécanique) | (mechanical) guard | |
| ancre de session | session anchor | AGENTS.md role |

## Session & security terms

| Français | English | Notes |
|---|---|---|
| session supervisée | supervised session | |
| superviseur | supervisor | the private supervisor process |
| profil jetable | disposable profile | never "temporary profile" |
| target attribué | assigned target | |
| triple identité | identity triple | CDPX_SESSION / CDPX_RUN_ID / CDPX_TARGET |
| autorité | authority | observation / interaction / privileged (values stay as-is) |
| bail (SessionLease) | lease | |
| valeurs masquées | redacted values | "masqué par défaut" → "redacted by default" |
| origines autorisées | allowed origins | |
| propriétaire (run) | owner (run) | run propriétaire → owning run |
| jamais le Chrome personnel | never the user's personal Chrome | doctrine phrase, keep it strong |

## Technical terms

| Français | English | Notes |
|---|---|---|
| onglet | tab | |
| évènement | event | |
| sous-commande | subcommand | |
| charge utile | payload | |
| déterministe | deterministic | |
| borné / non borné | bounded / unbounded | "aucun sleep non borné" → "no unbounded sleep" |
| repli | fallback | |
| sortie (stdout) | output | |
| dépôt | repository | |
| exigence | requirement | |
| échec / échoue | failure / fails | |
| indisponible | unavailable | maps to the `unavailable` status literal |

## What is NEVER translated

- Identifiers: CLI commands and flags, JSON keys, make targets, file paths,
  status literals (`unavailable`, `started`…), env vars, scenario/feature ids.
- Proper nouns: cdpx, CDP, Chrome, Symfony, Docker, asciicast, xterm.js.
- Git history (commit messages stay French); future commits are English.

## Contract warning

stderr diagnostics and error message strings are part of observable CLI
behavior: translating one is a contract change — update the tests asserting
it in the same commit, per invariant 3 (AGENTS.md).
