# HARNESS.md — cadre d'exécution cdpx

Ce document borne l'environnement dans lequel un agent (et l'humain qui le
pilote) exerce du pouvoir via cdpx. Un CLI qui pilote un navigateur est un
outil à double tranchant: `eval` est un accès arbitraire à toute session
ouverte dans ce Chrome. Le harness existe pour que ce pouvoir soit **borné,
observable et réversible**.

## 1. Périmètre du navigateur (règle n°1)

- **Jamais** brancher cdpx sur le Chrome personnel (sessions bancaires, mails,
  admin prod). Toujours un profil jetable:

  ```
  chromium --headless=new --remote-debugging-port=9222 \
    --user-data-dir=$(mktemp -d /tmp/cdpx-XXXX) --no-first-run
  ```

- Le port de debug n'écoute que sur loopback (comportement Chrome par défaut).
  Ne jamais l'exposer (`--remote-debugging-address=0.0.0.0` est interdit ici).
- Cible par défaut du travail agentique: environnements de dev/staging
  (docker compose local, .test, .localhost). Naviguer sur des sites tiers en
  lecture (audit SEO) est légitime; **agir** (click/type/eval mutant) sur un
  site tiers ne l'est pas sans instruction humaine explicite.

## 2. Fuites d'information

- Les valeurs de cookies sont **masquées par défaut** dans toutes les sorties
  (`cookies get` -> `***`). `--show-values` est un acte volontaire, et ses
  sorties ne vont ni dans un commit, ni dans un ticket, ni dans un log partagé.
- Même prudence pour `storage` et `html` sur des pages authentifiées: l'agent
  qui colle ses sorties dans une doc doit se demander ce qu'elles contiennent.

## 3. Qualité et déterminisme

- Boucle courte: `make check-local` (lint Ruff + format + mypy + tests
  unitaires). Portail obligatoire: `make check`, qui ajoute la reproduction
  Docker, Chrome réel et Symfony réel.
- Tests unitaires: 100% déterministes. Deux serveurs loopback lancés par les
  tests eux-mêmes (mock CDP + fixtures), ports éphémères, aucune dépendance à
  l'ordre d'exécution, aucun réseau externe, aucun navigateur.
- Le mock **enregistre le protocole émis**: un test de primitive valide la
  sortie JSON *et* la séquence exacte de commandes CDP. C'est ce qui rend une
  régression de protocole impossible à rater.
- e2e Chrome réel: séparé (`make test-e2e`), réutilise les mêmes fixtures,
  et échoue si aucun binaire Chrome/Chromium n'est disponible. La suite
  Symfony Dockerisée est également bloquante dans `make check`. Voir
  `docs/milestones/M1-e2e-chrome.md`.

## 4. Supervision et pilotage humain

- Contrat CLI: stdout = un objet JSON compact par défaut (machine, sobre en
  tokens), `--pretty` = lecture humaine, stderr = messages, exit 0 = ok / 1 =
  erreur d'exécution / 2 = mauvaise invocation. Un agent qui boucle sur des
  exit 1 doit remonter à l'humain, pas insister à l'aveugle.
- Sorties volumineuses: bornées par défaut (`--limit`), métadonnées
  `*_truncated`; `--full` est volontaire. Streams et traces = NDJSON compact.
- Reproduction manuelle triviale: toute action d'agent est UNE commande cdpx
  copiable-collable par l'humain. Pas d'état caché côté CLI (chaque invocation
  ouvre et ferme sa connexion; l'état vit dans le navigateur, inspectable).
- Debug sans navigateur: `make mock` donne un faux Chrome à qui parler; les
  commandes reçues sont la trace exacte de ce que cdpx émet.
- Preuve de handoff: `make proof` écrit un rapport humain
  `.proof/proof-report.html`, `.proof/validation-summary.json`, les logs
  pytest/lint, les JUnit XML, l'aide CLI capturée et les preuves de scénarios
  `.proof/evidence/` dont les screenshots e2e Chrome. `.proof/` est un produit
  de build local ou un artefact CI, pas une source à éditer ou versionner.

## 5. Reprise humaine

- Tout le "pourquoi" est dans `docs/CONTEXT.md`, le "quoi ensuite" dans
  `docs/TODO.md` + `docs/ROADMAP.md`, le "comment" dans le code testé.
- Chaque module de primitives ouvre sur un docstring usecase: le code se lit
  comme la doc.
- Ce qui n'a PAS été validé en runtime est marqué comme tel dans la fiche de
  feature ou la roadmap. Aucun code non validé ne se présente comme fini.

## 6. Évolutions du harness lui-même

Toute règle ajoutée ici doit être **exécutable ou vérifiable** (un check, un
test, un défaut de comportement dans le code — comme le masquage cookies).
Une règle purement déclarative sans garde-fou mécanique est un vœu, pas un
harness.
