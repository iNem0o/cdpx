## Problème résolu

Décrivez le problème observable, la décision prise et son impact utilisateur.

## Type de changement

- [ ] Correctif
- [ ] Nouvelle primitive ou évolution fonctionnelle
- [ ] Documentation ou processus
- [ ] CI, packaging ou maintenance

## Contrat CLI et protocole CDP

- Impact sur stdout JSON, stderr et les codes de sortie 0/1/2 :
- Commandes CDP attendues (méthodes, paramètres et ordre), ou `N/A` motivé :

## Tests et documentation

- Tests ajoutés ou modifiés :
- Fixture et scénario Chrome/Symfony E2E, ou raison pour laquelle ils sont `N/A` :
- Documentation (`docs/PRIMITIVES.md`, fiche feature, changelog) mise à jour :

## Sécurité et redaction

- Risques liés aux cookies, tokens, profils ou données de session :
- Mesures de masquage et vérification du diff/des preuves :

## Validation locale

Listez les commandes réellement exécutées et leur résultat. Signalez clairement
tout contrôle non exécuté et pourquoi.

- [ ] `make check-local`
- [ ] `make check` (Docker, Chrome réel et Symfony sans skip)
- [ ] `make release`

## Preuve GitHub automatique

Toutes les PR, y compris documentaires, CI et packaging, doivent obtenir le
check stable **`PR Gate / Required`**. Le run publie un résumé natif et
l'artefact cockpit (JSON, HTML, JUnit, logs, captures et distributions
disponibles). Une checkbox déclarative ne remplace jamais ce check obligatoire.
