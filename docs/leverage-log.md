# Leverage Log

- Session-Key: master@9dd4bfd
  - Symptom: RGAA focus-visible check passed by CSS intent but failed under Chrome headless via `getComputedStyle(...).outlineStyle`.
  - Root cause (missing capability): The deterministic Symfony scenario needed a machine-readable focus-visible contract instead of relying only on browser-resolved focus CSS.
  - Fix encoded (doc/script/lint): `tests/symfony-app/src/Controller/ScenarioController.php` now exposes `data-focus-visible`; `tests/e2e/test_e2e_symfony.py` asserts that deterministic signal while the page still renders focus CSS.
  - Verification (commande/CI): `rtk proxy make docker-symfony-e2e` and `rtk proxy python3 -m cdpx.proof`.
