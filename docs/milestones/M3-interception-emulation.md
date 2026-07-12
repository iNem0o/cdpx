# M3 — Interception et émulation

## Pourquoi

Tester les conditions rencontrées en production sans modifier le backend : API
indisponible, tiers bloqué, viewport mobile, réseau lent ou CPU contraint.

## État livré

### `cdpx intercept`

`Fetch.enable` suspend les requêtes de la navigation composée. La première
règle dont le motif URL matche applique exactement l'une des actions suivantes :

- `continue` → `Fetch.continueRequest` ;
- `block` → `Fetch.failRequest(BlockedByClient)` ;
- statut `200..599` → `Fetch.fulfillRequest` avec corps JSON mock.

Le matcher porte sur l'URL (fnmatch ou sous-chaîne), pas sur la méthode HTTP.
Une faute de frappe ou un statut hors plage est refusé au parsing avant effet
CDP. L'interception compose uniquement avec `goto` parce que l'état Fetch meurt
avec la connexion :

```bash
cdpx intercept --rule "*api/payment* => 503" --rule "*googletagmanager* => block" -- goto http://shop.test/checkout
```

La fixture dédiée `intercept.html`, le mock et Chrome réel couvrent continue,
fulfill et block. Les sessions gérées du M8 isolent les runs, mais ne rendent
pas l'interception persistante entre commandes.

### `cdpx emulate`

Les presets `mobile`, `slow-3g` et `cpu-4x` configurent métriques/UA, conditions
réseau ou throttling CPU. `--reset` restaure métriques, user-agent, réseau et
CPU. Comme les overrides meurent avec la connexion, l'action utile se passe
dans la même invocation :

```bash
cdpx emulate mobile -- goto http://shop.test/checkout
```

## Preuves et limites

- Le mock verrouille les méthodes/paramètres et le rejet des règles invalides.
- Chrome réel vérifie interception, viewport/UA, reset, latence et screenshot.
- La preuve ne revendique pas une comparaison pixel desktop/mobile exhaustive.
- `intercept` n'entoure pas encore un clic ou un scénario entier.

## Definition of Done

- [x] règles fulfill/fail/continue testées mock et Chrome réel ;
- [x] actions inconnues refusées avant navigation ;
- [x] presets/reset documentés et exercés sur Chrome réel ;
- [x] durée de vie connexion/interception explicitée.
