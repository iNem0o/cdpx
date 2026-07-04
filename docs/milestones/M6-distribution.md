# M6 — Distribution

## Pourquoi
Rendre cdpx installable en 1 geste sur les postes inem0o et dans les CI.

## Contenu
- pipx install (déjà packagé pyproject; vérifier metadata, ajouter version
  --version au CLI).
- Image Docker: chromium headless + cdpx + fixtures, entrypoint `make test-e2e`
  -> job GitLab CI réutilisable (nightly e2e des projets qui embarquent cdpx).
- docker-compose.e2e.yml de référence pour les projets Symfony/Shopware:
  service app + service chrome (port 9222 interne) + job cdpx.
- Snippet CLAUDE.md "outillage navigateur" à copier dans les projets clients:
  quelles commandes, quels garde-fous (profil jetable, allowlist M5).

## Definition of Done
- [ ] `pipx install .` fonctionnel
- [ ] image Docker construite en CI, e2e vert dedans
- [ ] snippet CLAUDE.md validé sur un projet pilote inem0o
