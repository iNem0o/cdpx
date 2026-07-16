# M6 — Distribution technique

## Pourquoi

Rendre cdpx installable, vérifiable et reproductible indépendamment du poste de
développement et de la plateforme CI.

## Contenu livré

- version unique exposée par `cdpx --version` ;
- wheel et sdist construits par `python -m build` et contrôlés par Twine ;
- image `cdpx-ci` contenant Chromium et l'outillage de validation ;
- application Symfony témoin orchestrée par Docker Compose ;
- `make proof` pour les JUnit, logs, scénarios et screenshots dans un arbre
  local privé, puis staging textuel partageable manifesté ;
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

## Politique des preuves distribuées

- dossiers `0700`, fichiers/manifests `0600`, écriture atomique ;
- manifest avec SHA-256, classification, autorisation d'upload, redaction et
  TTL ;
- screenshots, PDF et binaires `opaque-restricted`, conservés hors
  `.proof/shareable/` ;
- scan de canaris fail-closed avant upload ;
- rétention : preuve PR 14 jours, preuve release 30 jours, distributions
  vérifiées 90 jours.

## Definition of Done

- [x] paquet versionné, wheel et sdist vérifiés ;
- [x] image Docker et Chrome réel verts ;
- [x] suite Symfony distincte et bloquante ;
- [x] preuve consolidée disponible comme artefact ;
- [x] première exécution verte sur le runner GitHub public — attestée dans
      `docs/leverage-log.md` (runs `29161949162` et `29162518918` verts avec
      `PR Gate / Required`).
