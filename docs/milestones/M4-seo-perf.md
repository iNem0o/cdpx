# M4 — Mesure SEO, performance et accessibilité

## Pourquoi

Compléter le contrat SEO du DOM rendu par des signaux locaux de performance,
de sémantique accessible et de poids mort, sans les présenter comme des audits
exhaustifs.

## État livré

### `cdpx vitals`

Des `PerformanceObserver` LCP/CLS/INP sont injectés avant navigation. Un clic
optionnel produit une interaction réelle pour tenter d'alimenter INP. Les
valeurs sont support-dépendantes et bornées par `--settle` : ce diagnostic
local n'est ni une méthodologie lab multi-run ni une donnée terrain CrUX/RUM.

### `cdpx a11y`

`Accessibility.getFullAXTree` produit une liste compacte des nœuds non ignorés
`{role, name, ignored}`. C'est une vue sémantique utile à l'agent, pas la
reproduction d'un lecteur d'écran ni un audit RGAA complet. Les contrôles RGAA
de la fixture Symfony constituent un sous-ensemble automatisé séparé.

### `cdpx coverage`

`Profiler.takePreciseCoverage` et `CSS.stopRuleUsageTracking` agrègent octets
JS utilisés/inutilisés par ressource et règles CSS utilisées/inutilisées. La
mesure reflète uniquement le chargement instrumenté : une fonctionnalité non
exercée peut apparaître comme morte.

### `cdpx seo`

Le diagnostic inspecte title, metas, canonical, robots, h1, hreflang, JSON-LD,
images et liens du DOM rendu. Il reste on-page : aucun crawl, signal
d'indexation, backlink, log serveur ou Search Console.

## Preuves

Le mock verrouille le protocole et les sorties; Chrome réel exerce les signaux
sur fixtures locales; Symfony Docker ajoute les variantes déterministes et le
sous-ensemble RGAA. Les assertions acceptent l'absence de signaux non supportés
au lieu d'inventer une stabilité inter-run.

## Definition of Done

- [x] vitals injectés avant navigation et interaction optionnelle exercée ;
- [x] AXTree compact documenté et testé mock/Chrome ;
- [x] coverage JS/CSS agrégée par ressource et testée ;
- [x] limites SEO/vitals/a11y explicites dans la documentation utilisateur.
