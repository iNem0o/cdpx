# M1 — e2e Chrome réel

## Pourquoi
Le mock valide le protocole émis par cdpx, pas le comportement de Blink/V8
(rendu, timing réel des évènements, insertText dans un vrai input, screenshot
non vide). Tant que M1 n'est pas vert, chaque primitive est "protocole prouvé,
navigateur présumé".

## Prérequis
Un poste/CI avec chromium ou google-chrome. En CI GitLab: image
`zenika/alpine-chrome` ou build maison (voir M6).

## Contenu livré d'avance (non validé)
`tests/e2e/test_e2e_chrome.py` — 9 scénarios couvrant: goto+title, wait sur
`spa.html` (élément à +300ms), form click+type (`form.html` -> `#result` ==
"OK:Léo"), console (`console.html`: log/warn/error/uncaught), réseau
(`network.html`: 1x200, 1x500, 1x lent), seo (conforme vs cassé), cookies JS +
localStorage (`storage.html`), screenshot PNG > 1ko, fetch --await vers
`/api/json`.

## Étapes
1. `make setup && make check` (doit être vert avant de toucher au e2e).
2. `make test-e2e` — dérouler, corriger les écarts mock/réalité.
3. Chaque écart découvert = un ajustement DU MOCK aussi (le mock suit le réel,
   invariant CLAUDE.md n°6) + note ici.
4. Ajouter le job e2e en CI (nightly d'abord, puis sur MR si stable < 60s).

## Points de vigilance connus
- `--headless=new` requis (l'ancien headless a un Input domain incomplet).
- Timing `wait`: vérifier que elapsed_ms >= 250 tient en machine chargée,
  sinon assouplir l'assertion (déterminisme > précision).
- `Network.clearBrowserCookies` est déprécié: si Chrome le retire, basculer
  sur Storage.clearCookies (target browser) — test mock à mettre à jour.

## Definition of Done
- [ ] 9/9 e2e verts en local ET en CI
- [ ] divergences mock/réel documentées ici et corrigées dans le mock
- [ ] durée e2e < 60s
