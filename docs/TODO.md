# Travail en cours

Le socle fonctionnel M0-M6 est livré. La priorité actuelle est la première
publication open source sur GitHub sous licence MIT. Le plan détaillé est
[docs/RELEASE-PLAN.md](RELEASE-PLAN.md).

## Préparation open source

- [x] Refaire le README pour un utilisateur extérieur avec quickstart local,
      statut pré-1.0, sécurité et catalogue des 30 commandes.
- [x] Ajouter les politiques de contribution, sécurité, conduite et support.
- [x] Retirer de la documentation produit les références privées ou client.
- [x] Finaliser la relicence MIT et les métadonnées de paquet après validation
      du détenteur du copyright.
- [x] Remplacer GitLab CI par des workflows GitHub Actions à permissions
      minimales et actions épinglées.
- [x] Ne plus versionner `.proof/`; publier les rapports comme artefacts CI.
- [x] Vérifier le contenu exact du wheel et du sdist, y compris la licence et
      l'absence de fichiers internes.
- [x] Installer le wheel dans un environnement propre et recompter les 30
      commandes depuis l'artefact.
- [x] Exécuter `make release` sur l'état intégré puis confirmer les mêmes
      portails sur un vrai runner GitHub.
- [x] Préparer la version de publication `0.2.0`, cohérente avec les changements
      de contrat pré-1.0 ; aucun tag n'est autorisé à ce stade.

## Dette technique continue

- [ ] Étendre `KEY_MAP` uniquement lorsqu'un besoin réel est accompagné d'un
      test mock et d'un scénario navigateur.
- [ ] Promouvoir un usage répété de `eval` en primitive nommée lorsqu'il revient
      au moins trois fois.
- [x] Épingler les images de validation par digest et confier leur mise à jour
      mensuelle à Dependabot.

Cocher un item signifie : code et documentation alignés, tests proportionnés
au risque, et `make check` vert.
