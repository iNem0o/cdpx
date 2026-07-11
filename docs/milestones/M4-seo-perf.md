# M4 — Mesure SEO/perf avancée

## Pourquoi
Passer de « extraire le contrat SEO » (M0, fait) à « mesurer l'expérience
réelle » — CWV, sémantique et poids mort.

## Primitives
### cdpx vitals
- Comment: injecter un PerformanceObserver (LCP, CLS, INP) via
  Page.addScriptToEvaluateOnNewDocument AVANT navigation, naviguer, collecter.
- Piège: INP nécessite une interaction -> combiner avec click scripté.
- Fixture: page avec image large (LCP identifiable) + layout shift provoqué
  par un setTimeout fixe -> CLS déterministe attendu > 0.

### cdpx a11y
- Comment: Accessibility.getFullAXTree -> arbre {role, name, children}
  compacté. Usecase double: audit a11y ET "vision" texte-structurée pour
  l'agent, plus fiable qu'un innerText plat, moins cher qu'un screenshot.
- Fixture: a11y.html (landmarks, boutons avec/sans label, img sans alt).

### cdpx coverage
- Comment: Profiler.startPreciseCoverage + CSS.startRuleUsageTracking,
  naviguer, stop, agréger % utilisé par fichier.
- Usecase: dossier de préco perf client (poids mort chiffré par URL).

## Definition of Done
- [ ] vitals: valeurs stables sur fixture dédiée (2 runs, delta < seuil)
- [ ] a11y: sortie compacte documentée comme "vision agent" dans PRIMITIVES.md
- [ ] coverage: agrégat par fichier testé mock + e2e
