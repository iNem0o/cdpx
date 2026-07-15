# Travail en cours

Le socle fonctionnel M0-M6 est livré. La priorité actuelle est la première
publication open source sur GitHub sous licence MIT. Le plan détaillé est
[docs/RELEASE-PLAN.md](RELEASE-PLAN.md).

## Contrat de session supervisée — validé

Les mécanismes ci-dessous sont implémentés et validés par les portails ciblés,
le portail intégré et le paquet installé :

- [x] Valider ensemble le lifecycle `session start/status/stop`, trois profils
      simultanés, target/run explicites, lease exclusif et teardown TTL/owner.
- [x] Valider la matrice `observation` / `interaction` / `privileged`, le
      loopback obligatoire et l'allowlist d'origines fail-closed.
- [x] Faire de la session supervisée l'unique contrat : identité triple avant
      discovery, aucun endpoint/target implicite et lifecycle réservé au
      superviseur.
- [x] Faire passer `make mock` par le même manifest supervisé que Chrome réel.
- [x] Aligner HARNESS, catalogue, fiches features, matrice de validation et
      cockpit de preuve sur ce contrat unique.
- [x] Valider les canaris de bout en bout : stdout/stderr, URL/headers,
      console, storage, profiler, journal v2, scénarios et staging de preuve.
- [x] Valider le staging `.proof/shareable/`, les modes `0600`/`0700`, le
      manifeste de classification/rétention et l'exclusion des binaires opaques.
- [x] Valider dans le wheel installé la surface publique de 31 commandes après
      ajout de `cdpx session` (`make dist` au sein du portail intégré).
- [x] Documenter intégralement le lancement et le lifecycle Chrome, puis exposer
      un portail CommonMark/Mermaid hors ligne dans le cockpit sans dissocier
      les fiches features de leur rôle de spécification du harness.

`SecureArtifactWriter` redige automatiquement texte, JSON et fichiers textuels
enregistrés; le scanner de canaris reste le dernier verrou de publication pour
les secrets connus. Une automatisation de purge périodique des preuves locales
reste à décider.

## Préparation open source

- [x] Refaire le README pour un utilisateur extérieur avec quickstart local,
      statut pré-1.0, sécurité et catalogue de la surface CLI.
- [x] Ajouter les politiques de contribution, sécurité, conduite et support.
- [x] Retirer de la documentation produit les références privées ou client.
- [x] Finaliser la relicence MIT et les métadonnées de paquet après validation
      du détenteur du copyright.
- [x] Remplacer GitLab CI par des workflows GitHub Actions à permissions
      minimales et actions épinglées.
- [x] Ne plus versionner `.proof/`; publier les rapports comme artefacts CI.
- [x] Vérifier le contenu exact du wheel et du sdist, y compris la licence et
      l'absence de fichiers internes.
- [x] Installer le wheel dans un environnement propre et recompter les 31
      commandes depuis l'artefact.
- [x] Exécuter `make release` sur l'état intégré puis confirmer les mêmes
      portails sur un vrai runner GitHub.
- [x] Préparer la version de publication `0.2.0`, cohérente avec les changements
      de contrat pré-1.0 ; aucun tag n'est autorisé à ce stade.

## Cockpit de preuve v2 — livré

Refonte UX/UI/DX du système de preuves (collecte, stockage, cockpit) :

- [x] Extraire la présentation du cockpit vers `src/cdpx/proofing/cockpit/`
      (shell.html, cockpit.css, cockpit.js — lintables, livrés dans le wheel).
- [x] Relier chaque test à son intention écrite dans le code : docstring =
      intention de la méthode, commentaires `#:` = déroulé annoté par
      assertion, corrélé à la ligne d'échec et rendu hiérarchiquement.
- [x] Fermer la taxonomie des artefacts et donner à chaque type un visualiseur
      dédié dans une modal contextuelle (screenshot zoomable, console filtrée
      par niveau, table réseau, arbre JSON, logs surlignés, transcript de
      commande, player cast xterm.js).
- [x] Ajouter les preuves secondaires : `attach_command_output`,
      `attach_log_excerpt`, `attach_cast`.
- [x] Rendre la preuve cast obligatoire : enregistreur natif stdlib (pty →
      asciicast v2, ni asciinema ni agg), portail bloquant dans `make proof`,
      casts inlinés et joués dans xterm.js vendoré (MIT, SHA-256 vérifié).
- [x] Guider la lecture du pack : « À lire d'abord » quand le verdict est
      rouge, timeline des commandes, badges par type de preuve, compteurs.
- [x] Généraliser docstrings + `#:` à toutes les suites : 430/430 tests ont
      une intention, 428/428 tests à assertions ont un déroulé annoté
      (`tests/test_intent.py` reste exclu — témoins à lignes figées). Diff
      prouvé purement additif (AST identique hors docstrings).
- [ ] Phase 2 preuves secondaires : dérouler le backlog
      `docs/milestones/attach-backlog.json` (61 opportunités relevées pendant
      l'annotation — `attach_cli_run`, `attach_json`, `attach_cast`,
      marqueurs `scenario` candidats), par petits lots avec `make check-local`
      systématique car ces ajouts changent le code exécuté.

## Dette technique continue

- [ ] Étendre `KEY_MAP` au-delà du jeu désormais testé (édition/navigation,
      Space et quatre flèches) uniquement avec un besoin réel, un test mock et
      un scénario navigateur.
- [ ] Promouvoir un usage répété de `eval` en primitive nommée lorsqu'il revient
      au moins trois fois.
- [x] Épingler les images de validation par digest et confier leur mise à jour
      mensuelle à Dependabot.

Cocher un item signifie : code et documentation alignés, tests proportionnés
au risque, et `make check` vert.
