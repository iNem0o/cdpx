# M6 — Distribution technique

## Pourquoi

Rendre cdpx installable, vérifiable et reproductible indépendamment du poste de
développement et de la plateforme CI.

## Contenu livré

- version unique exposée par `cdpx --version` ;
- wheel et sdist construits par `python -m build` et contrôlés par Twine ;
- image `cdpx-ci` contenant Chromium et l'outillage de validation ;
- application Symfony témoin orchestrée par Docker Compose ;
- `make proof` pour les JUnit, logs, scénarios et screenshots ;
- snippet navigateur réutilisable dans `docs/CLAUDE-browser-snippet.md`.

L'hébergement et la publication publique sont traités par M7. GitHub Actions
appelle les mêmes cibles Make : la CI ne redéfinit pas le portail de qualité.

## Validation

```bash
make docker-check
make docker-e2e
make docker-symfony-e2e
make release
```

Docker, Chrome et Symfony sont obligatoires pour la release. Le wheel doit
également être installé dans un environnement propre avant publication.

## Definition of Done

- [x] paquet versionné, wheel et sdist vérifiés ;
- [x] image Docker et Chrome réel verts ;
- [x] suite Symfony distincte et bloquante ;
- [x] preuve consolidée disponible comme artefact ;
- [ ] première exécution verte sur le runner GitHub public — suivi en M7.
