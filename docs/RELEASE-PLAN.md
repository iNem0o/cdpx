# Plan de publication open source

Ce document suit la transition du dépôt vers GitHub et MIT. Il remplace le plan
historique de release propriétaire, désormais obsolète. Aucune étape ne donne
par elle-même l'autorisation de pousser, taguer ou publier.

## Baseline

- 30 commandes CLI regroupées en huit features documentées ;
- contrat stdout JSON, stderr diagnostics et exit 0/1/2 ;
- tests mock déterministes, Chrome réel et Symfony Dockerisé ;
- `make check-local`, `make check`, `make proof` et `make release` comme sources
  de vérité ;
- wheel et sdist construits par `python -m build` puis contrôlés par Twine.

## 1. Licence et métadonnées

- [x] Confirmer que le détenteur indiqué dans la licence possède les droits
      nécessaires à la relicence.
- [x] Installer le texte MIT sans inventer de nom ni d'année.
- [x] Aligner `pyproject.toml`, README, changelog et tests de packaging.
- [ ] Vérifier la licence dans le wheel et le sdist reconstruits.

## 2. Dépôt public

- [x] README d'installation source et quickstart loopback reproductible.
- [x] `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` et `SUPPORT.md`.
- [x] Documentation débarrassée des références client et du statut GitLab
      actif.
- [x] Retirer les preuves générées de l'index et ignorer `.proof/`.
- [ ] Scanner l'état courant et tout l'historique avec un outil dédié avant le
      premier push public.

## 3. GitHub Actions

- [x] Pull requests : appeler les cibles Make sans dupliquer leur logique.
- [x] Conserver Docker, Chrome et Symfony comme portes obligatoires.
- [x] Utiliser des permissions minimales et épingler les actions tierces.
- [x] Publier JUnit, logs et cockpit `.proof/` comme artefacts temporaires.
- [x] Valider localement la syntaxe des workflows avec `actionlint`.
- [ ] Exécuter les workflows sur un vrai runner GitHub.

## 4. Distribution

- [x] Construire wheel et sdist depuis l'état intégré et contrôler leur contenu.
- [x] Installer le wheel dans un environnement vierge et vérifier
      `cdpx --help`, `cdpx --version` et les 30 commandes.
- [x] Préparer une GitHub Release sur tag sans la déclencher.
- [x] Préparer PyPI Trusted Publishing par OIDC, sans token longue durée.
- [x] Préparer la version `0.2.0`, adaptée aux changements pré-1.0.
- [ ] Créer le tag uniquement après validation explicite du propriétaire.

## Portail final

Avant toute ouverture ou publication :

1. `make release` vert dans l'état intégré ;
2. Docker construit depuis un contexte propre ;
3. Chrome réel et les scénarios Symfony sans skip ;
4. cockpit de preuve vert et non versionné ;
5. wheel/sdist inspectés et contrôlés par Twine ;
6. GitHub Actions vertes sur le dépôt distant ;
7. paramètres GitHub de signalement privé et Trusted Publishing activés.

Les limites de laboratoire des vitals et le statut beta pré-1.0 restent
documentés; ils ne bloquent pas une publication honnête.
