# M3 — Interception & émulation

## Pourquoi
Tester les conditions que la prod rencontre: API paiement down, 3rd-party à
3s, mobile bas de gamme. Aujourd'hui l'agent ne peut qu'observer le cas
nominal.

## Primitives
### cdpx intercept
- Comment: Fetch.enable avec patterns; sur Fetch.requestPaused ->
  fulfillRequest (mock JSON), failRequest (blocage) ou continueRequest.
- CLI: `cdpx intercept --rule 'POST /api/payment => 503' --rule
  '*googletagmanager* => block' -- goto http://shop.test/checkout`
- ATTENTION architecture: nécessite une connexion QUI RESTE OUVERTE pendant
  la navigation -> introduire un mode session (`cdpx session start/exec/stop`
  ou intercept prenant la commande à exécuter en argument, comme ci-dessus).
  Décision à documenter dans CONTEXT.md au moment de l'implémentation.
- Fixture: network.html couvre déjà les 3 profils de requêtes à intercepter.

### cdpx emulate
- Comment: Emulation.setDeviceMetricsOverride, setUserAgentOverride,
  Network.emulateNetworkConditions (latence/débit), Emulation.setCPUThrottlingRate.
- Presets: mobile, slow-3g, cpu-4x. `--reset` pour tout annuler.

## Definition of Done
- [ ] intercept: règles fulfill/fail/continue testées mock + e2e
- [ ] emulate: presets documentés, screenshot mobile != desktop en e2e
- [ ] le mode session (si introduit) documenté dans HARNESS.md (état = risque)
