# Leverage Log

- Session-Key: master@87bf815
  - Symptom: l'interception Fetch passait hors conteneur mais `Page.navigate` expirait dans Chrome Docker lorsque la requête document était mise en pause avant sa réponse CDP.
  - Root cause (missing capability): le client synchrone ne conservait pas les réponses de commandes croisées consommées pendant le traitement d'évènements bloquants.
  - Fix encoded (doc/script/lint): `CDPClient.wait_response()` et le buffer de réponses permettent à `intercept_goto` d'envoyer la navigation sans attendre, de traiter `Fetch.requestPaused`, puis de récupérer sa réponse.
  - Verification (commande/CI): `rtk make check`, `rtk make proof` et `rtk make release` verts; le test d'interception Chrome Docker passe.

- Session-Key: master@9dd4bfd
  - Symptom: RGAA focus-visible check passed by CSS intent but failed under Chrome headless via `getComputedStyle(...).outlineStyle`.
  - Root cause (missing capability): The deterministic Symfony scenario needed a machine-readable focus-visible contract instead of relying only on browser-resolved focus CSS.
  - Fix encoded (doc/script/lint): `tests/symfony-app/src/Controller/ScenarioController.php` now exposes `data-focus-visible`; `tests/e2e/test_e2e_symfony.py` asserts that deterministic signal while the page still renders focus CSS.
  - Verification (commande/CI): `rtk proxy make docker-symfony-e2e` and `rtk proxy python3 -m cdpx.proof`.
