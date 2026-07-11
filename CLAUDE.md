# CLAUDE.md — cdpx

Ancre de session pour tout agent travaillant sur ce dépôt. Lire ce fichier en
premier, puis `HARNESS.md` (règles), puis `docs/CONTEXT.md` (pourquoi ce projet
existe). Le modèle agit, mais le harness tranche.

## Mission

cdpx = primitives Chrome DevTools Protocol exposées en CLI, pour qu'un agent
(ou le dev qui le pilote) puisse **voir, agir et mesurer** dans un Chrome de
dev pendant la construction d'apps Symfony / e-commerce, et pendant les audits
SEO. Voir `docs/PRIMITIVES.md` pour le catalogue implémenté.

## Commandes de travail

```
make setup               # installation editable + outils de développement
make check-local         # boucle courte: lint + format + mypy + unitaires
make check               # PORTAIL: local + Docker + Chrome + Symfony
make test                # unitaires déterministes, loopback uniquement
make test-e2e            # Chrome réel local — son absence est une erreur
make docker-symfony-e2e  # scénarios contre une vraie app Symfony Docker
make proof               # rapport de preuve généré dans .proof/
make release             # check + proof + wheel/sdist vérifiés
make fixtures            # site témoin sur :8899
make mock                # faux Chrome scriptable, sans navigateur
```

Essai rapide sans Chrome:

```
make mock &                    # affiche le port de découverte
cdpx --port <PORT> tabs list
cdpx --port <PORT> goto http://demo.test/
```

## Invariants (non négociables)

1. **`make check` vert avant toute fin de session.** Pas d'exception.
2. **Tests unitaires = déterministes.** Loopback uniquement, aucun réseau
   externe, aucun sleep non borné, aucun Chrome requis. Ce qui exige un vrai
   navigateur va dans `tests/e2e/`; l'indisponibilité de Chrome est bloquante
   pour les portails runtime et la release.
3. **Contrat CLI stable**: stdout = un objet JSON, stderr = diagnostics,
   exit 0/1/2. Tout changement de contrat = changement de tests + note dans
   `docs/PRIMITIVES.md`.
4. **Chaque primitive nouvelle arrive avec**: sa fonction dans
   `src/cdpx/primitives/`, sa sous-commande CLI, ses tests mock (sortie ET
   protocole émis), sa fixture HTML si un scénario e2e a du sens, son entrée
   dans `docs/PRIMITIVES.md` (usecase, pourquoi, exemple).
5. **Sécurité**: valeurs de cookies masquées par défaut dans toute sortie;
   jamais de connexion au Chrome personnel de l'utilisateur dans les docs ou
   exemples (toujours `--user-data-dir` jetable). Voir `HARNESS.md`.
6. **Le mock suit le protocole réel.** Si Chrome change de comportement
   (ex: /json/new en PUT), le mock ET le client s'alignent, tests à l'appui.

## Où sont les choses

```
src/cdpx/client.py        client WS CDP (commandes, évènements, timeouts)
src/cdpx/discovery.py     API HTTP /json (onglets)
src/cdpx/primitives/      nav, js, inputs, capture, net, state, audit
src/cdpx/cli.py           argparse -> primitives -> JSON
src/cdpx/testing/         mock CDP + serveur de fixtures (livrés avec le paquet)
tests/                    unitaires (mock) — c'est ici que se joue le check
tests/fixtures/           site témoin statique déterministe
tests/e2e/                Chrome réel + application Symfony, portails bloquants
docs/                     CONTEXT, PRIMITIVES, ROADMAP, TODO, milestones/
```

## Boucle de travail attendue

1. Lire `docs/TODO.md`, choisir un item, annoncer l'intention.
2. Écrire/adapter le test mock d'abord (le protocole attendu EST la spec).
3. Implémenter la primitive + la sous-commande.
4. `make check-local` pendant la boucle, puis `make check`. Itérer jusqu'au vert.
5. Mettre à jour `docs/PRIMITIVES.md` + cocher `docs/TODO.md`.
6. Commit atomique, message impératif, corps expliquant le pourquoi.

## Definition of Done

- [ ] `make check` vert
- [ ] test mock couvrant sortie + protocole émis
- [ ] doc primitive à jour (usecase + exemple CLI)
- [ ] fixture HTML ajoutée si scénario e2e pertinent (+ marqueurs testés dans
      `test_fixture_server.py`)
- [ ] aucun secret/valeur de session dans les sorties par défaut
- [ ] contribution conforme à `CONTRIBUTING.md` et `CODE_OF_CONDUCT.md`
