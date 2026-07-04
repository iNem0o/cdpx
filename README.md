# cdpx

Primitives Chrome DevTools Protocol exposées en CLI, pour agents de dev et
les humains qui les pilotent — contexte: apps Symfony, e-commerce
(Shopware/PrestaShop), opérations SEO.

Un binaire, une commande = une action navigateur, une sortie = un objet JSON.

```
pip install -e .            # ou: make setup
chromium --headless=new --remote-debugging-port=9222 \
  --user-data-dir=$(mktemp -d) &

cdpx tabs list
cdpx goto http://shop.localhost/checkout
cdpx wait "#payment-form"
cdpx type "#email" "test@example.test" --clear
cdpx click "#submit"
cdpx console --duration 2
cdpx network http://shop.localhost/checkout
cdpx seo https://www.exemple.fr/produit-42
cdpx screenshot -o /tmp/etat.png
```

Sans Chrome sous la main:

```
make mock            # faux Chrome scriptable
cdpx --port <PORT> goto http://demo.test/
```

## Docs

- `CLAUDE.md` — ancre agent: mission, invariants, boucle de travail
- `HARNESS.md` — sécurité, déterminisme, supervision
- `docs/CONTEXT.md` — pourquoi ce projet existe, décisions
- `docs/PRIMITIVES.md` — catalogue (implémenté + planifié, usecases, exemples)
- `docs/ROADMAP.md` + `docs/milestones/` — M1..M6
- `docs/TODO.md` — liste de travail quoi/comment/pourquoi
- `docs/VALIDATION.md` — matrice de preuve, portails, cas limites

## Qualité

```
make check                 # lint + format + tests unitaires déterministes
make test-e2e              # e2e Chrome réel — skip propre si absent
make docker-check          # check dans l'image portable cdpx-ci
make docker-e2e            # e2e Chrome réel dans Docker
make docker-symfony-e2e    # profiler contre une vraie app Symfony Docker
make proof                 # logs courts dans .proof/ + résumé JSON
```

Les tests unitaires tournent contre un **mock CDP** qui enregistre chaque
commande émise: on valide la sortie ET le protocole. Le e2e réutilise les
mêmes fixtures HTML (`tests/fixtures/`) servies par un serveur déterministe
(`cdpx.testing.fixture_server`).
