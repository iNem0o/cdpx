# M1 — E2E Chrome réel

## Pourquoi

Le mock valide le protocole CDP émis, pas le comportement de Blink/V8 : rendu,
timing d'évènements, saisie trusted, téléchargement ou dimensions de capture.
La suite réelle complète donc le mock sans le remplacer.

## État validé

`tests/e2e/test_e2e_chrome.py` exerce les familles de commandes sur les mêmes
fixtures déterministes que les tests unitaires; `test_e2e_sessions.py` couvre
le lifecycle multi-session. Elles couvrent notamment :

- navigation, attente SPA et cycle de vie des onglets ;
- clic, saisie, clavier, iframe et garde d'origine ;
- capture PNG/JPEG/PDF, console, réseau et métriques ;
- cookies, stockage, SEO, vitals, accessibilité et couverture ;
- interception, émulation, record/replay et scénarios déclaratifs ;
- contrat du binaire installé : stdout, stderr et codes de sortie.
- trois profils/targets simultanés, isolation cookies/storage, grants, lease,
  stop et teardown sur signal normal du supervisor.

Chaque scénario qui exige une preuve visuelle attache un screenshot local
`opaque-restricted` au dossier du cas. Le manifest le relie à la preuve, mais
ses octets ne quittent pas automatiquement `.proof/` via le staging CI.

## Exécution

```bash
make test-e2e
make docker-e2e
```

Le lancement local utilise un profil Chrome jetable et le port de debug sur
loopback. L'absence de Chrome ou un skip rend le portail rouge. En CI GitHub,
la cible Docker reproduit l'environnement navigateur.

## Invariants

- une divergence Chrome/mock entraîne un test et, si nécessaire, une mise à
  jour du mock ;
- aucun accès réseau externe depuis les fixtures ;
- aucun sleep non borné ;
- aucune connexion au Chrome personnel ;
- les artefacts générés restent dans `.proof/` ou dans les artefacts CI.

## Definition of Done

- [x] suite Chrome réelle verte localement et dans l'image Docker ;
- [x] absence de Chrome traitée comme une erreur ;
- [x] screenshots et JUnit rattachés au cockpit de preuve ;
- [x] scénarios boîte noire du binaire installable.
