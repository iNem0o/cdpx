# M5 — Orchestration & sessions

## Pourquoi
Passer de primitives unitaires à des parcours: recette automatisée, non-
régression, exécution agentique longue mais BORNÉE.

## Contenu
### cdpx record / replay
- record: chaque commande cdpx (avec succès/échec + extraits de sortie) est
  journalisée en NDJSON -> un parcours devient un artefact versionnable.
- replay: rejoue le journal, s'arrête à la première divergence (exit 1 +
  diff). Usecase: "le tunnel de commande marche encore après la MEP ?"

### cdpx frame
- Runtime.evaluate avec contextId de l'iframe (Page.getFrameTree +
  Runtime.executionContextCreated). Fixture iframe.html/child.html déjà prête.

### Garde-fous agentiques (HARNESS)
- Allowlist d'origines: variable CDPX_ORIGINS="http://*.test,http://localhost:*";
  hors liste, les primitives MUTANTES (click/type/eval/intercept) refusent
  avec exit 1, les lectures restent permises. Défaut: tout permis en usage
  humain, allowlist OBLIGATOIRE dès que l'agent tourne en autonome.
- Budget: --max-actions par session pour borner une boucle agentique.

## Definition of Done
- [ ] un parcours record/replay complet sur les fixtures en e2e
- [ ] allowlist testée: mutation refusée hors origine, lecture permise
- [ ] HARNESS.md mis à jour (le harness tranche, mécaniquement)
