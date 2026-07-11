## Pourquoi

Décrivez le problème résolu et la décision prise.

## Changements

-

## Validation

- [ ] `make check-local` est vert.
- [ ] `make check` est vert, avec Docker, Chrome réel et Symfony sans skip.
- [ ] Les tests mock vérifient la sortie et le protocole CDP émis.
- [ ] La documentation et `CHANGELOG.md` sont à jour si le contrat public change.
- [ ] Aucun secret, cookie ou contenu de session n'est présent dans le diff ou les preuves.

## Contrat public

- [ ] stdout reste un objet JSON, stderr contient les diagnostics et les codes de sortie restent 0/1/2.
- [ ] Sans objet : ce changement ne modifie pas le contrat public.
