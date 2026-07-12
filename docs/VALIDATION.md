# VALIDATION.md

Preuve reproductible des milestones cdpx. Les sorties restent compactes pour
les agents : stdout JSON quand c'est utile, logs privés dans `.proof/`, et les
checks lourds explicitement séparés. `.proof/` n'est pas versionné. Seul son
staging manifesté `.proof/shareable/`, nettoyé et sans artefact opaque, est
publiable par GitHub Actions.

## Portails

- `make check-local`: sous-portail de développement sans navigateur: lint,
  format, mypy et tests unitaires déterministes — y compris les garde-fous
  documentation (`tests/test_docs.py`:
  chaque commande documentée dans README et PRIMITIVES, chaque fiche routée,
  chaque exemple `cdpx` parsé contre le vrai parseur).
- `make check`: portail qualité standard et bloquant: `check-local`, puis le
  même contrôle dans l'image Docker, Chrome réel dans Docker et Symfony réel.
- `make test-e2e`: scénarios Chrome réel contre les fixtures locales.
- `make docker-check`: `make check-local` dans l'image portable `cdpx-ci`.
- `make docker-e2e`: Chrome réel dans l'image `cdpx-ci`.
- `make docker-symfony-e2e`: e2e profiler contre une vraie app Symfony Docker.
- `make proof`: collecte lint, format, tests unitaires/intégration, e2e Chrome,
  e2e Symfony (Docker), aide CLI, JUnit XML, logs, scénarios pytest et
  screenshots e2e, puis écrit `.proof/proof-report.html` et
  `.proof/validation-summary.json`. L'arbre local complet reste privé ; une
  seconde étape construit le staging partageable et scanne les canaris.
- `make release`: portail agrégé bloquant. Il exige `check`, les contrôles
  Docker, Chrome réel, Symfony réel sans skip, la preuve complète et les
  artefacts wheel/sdist. `check-local` seul ne constitue jamais un verdict de
  release.
- `make dist`: construit wheel et sdist, applique `twine check --strict`,
  contrôle les contenus requis/interdits, puis installe le wheel dans un venv
  temporaire pour vérifier la licence, l'aide et les 31 commandes.

## Le rapport de preuve

`.proof/proof-report.html` est une application monopage navigable, pensée
comme la documentation humaine du produit:

- **Features**: doc utilisateur complète de chaque feature (générée depuis
  `docs/features/*.md`), parcours, scénarios given/when/then, tests exécutés,
  preuves (screenshots Chrome réels).
- **CLI**: surface complète des commandes et rattachement entrypoint →
  feature. Un entrypoint public non rattaché est une violation bloquante.
- **Validation**: matrice milestone → preuve (tableau ci-dessous), tests par
  module, risques/mitigations, inconnues assumées.
- **Gaps**: violations (bloquantes) et warnings du catalogue. Le budget de
  tests « legacy » (rattachés sans scénario documenté) est un ratchet à 0.
- **Run**: commandes du run, suites JUnit, tests en échec ou les plus lents,
  fins de logs repliables.

Tous les fichiers gérés sont écrits sous des dossiers `0700` et en `0600`.
Le manifest `cdpx.artifacts/v1` porte SHA-256, classification, autorisation
d'upload, version de redaction et expiration. Les textes nettoyés sont
`internal`; screenshots, PDF et binaires sont `opaque-restricted` et ne sont
jamais copiés dans le staging. Le scan de canaris échoue fermé avant upload.
Le manifest du proof porte le TTL effectif de publication : 14 jours par
défaut et en PR, 30 jours sur un tag. La variable
`CDPX_PROOF_RETENTION_DAYS` accepte uniquement un entier de 1 à 90 ; une
valeur invalide bloque la preuve avant de remplacer l'arbre existant. Ce TTL
est une donnée de rétention purgeable, pas un daemon de suppression
automatique.

Politique Symfony: Docker, Compose et la suite Symfony réelle sont obligatoires
pour toute preuve de release. Une preuve `unavailable` ou un test Symfony
skippé rend le verdict rouge. Il n'existe pas de succès release dégradé sans
Docker. `make check-local` sert seulement à raccourcir la boucle de
développement; le portail standard `make check` reste complet.

Les workflows GitHub Actions appellent ces cibles Make plutôt que de réécrire
leur logique. Un résultat de runner GitHub reste requis avant tag, même lorsque
les mêmes commandes ont réussi localement.

## Preuve dans GitHub Actions

Le workflow `CI` s'exécute sur toute pull request, sans filtre de chemins. Il
comprend les compatibilités Python 3.11/3.12 et un portail complet qui appelle
`make release`. Ce dernier couvre successivement lint, format, mypy, unitaires,
Docker, Chrome réel, Symfony réel, cockpit, wheel/sdist, `twine check --strict`,
contenu des archives, installation isolée du wheel et comptage des 31 commandes.

Le check stable **`PR Gate / Required`** dépend de tous ces jobs. Il échoue si
l'un d'eux échoue, est annulé ou est skippé. C'est le seul nom destiné à la
protection de `master`; une évolution de matrice ne change donc pas la règle.
Les checkboxes de PR ne remplacent jamais ce résultat exécuté.

Le job **Full release gate** publie dans son onglet *Summary* un tableau dérivé
de `.proof/validation-summary.json`, de l'issue réelle de `make release` et des
archives réellement présentes. Il affiche verdict, SHA, version, tests
passed/failed/skipped/unavailable, Chrome, Symfony, commandes CLI, catalogue,
packaging et nom de l'artefact. Aucun nombre n'est codé en dur, hors le contrat
public attendu de 31 commandes.

L'artefact `pr-proof-<run-id>-<attempt>` est conservé **14 jours** et publié
avec `if: always()`. Il prend exclusivement `.proof/shareable/`; une absence de
staging est une erreur d'upload. Selon le point d'échec, il contient les
fichiers textuels manifestés disponibles, notamment :

- `proof-report.html` et `validation-summary.json` ;
- les JUnit unitaires, Chrome et Symfony ;
- les logs textuels redacted produits par le cockpit : Ruff, mypy,
  pytest et Docker/Chrome/Symfony ;
- les scénarios et métadonnées textuelles sous `.proof/evidence/` ;
- `artifact-manifest.json`, qui indique aussi les fichiers opaques retenus en
  local mais volontairement exclus de l'upload.

Le log brut du portail, les screenshots/PDF/binaires du proof local et les
distributions ne sont pas dans cet artefact PR. Sur tag, `release-proof`
conserve un staging manifesté avec un TTL aligné de 30 jours ; wheel et sdist
sont publiés séparément dans `python-package-distributions` pendant 90 jours.

Depuis le run GitHub, téléchargez l'artefact dans la section *Artifacts*. En
CLI : `gh run download <RUN_ID> -n pr-proof-<RUN_ID>-<ATTEMPT>`. Commencez par
`validation-summary.json`, ouvrez ensuite `proof-report.html`, puis le JUnit ou
le log de la couche rouge. Lors d'un échec avant la construction du staging,
l'upload peut lui-même signaler l'absence de fichier : le log du job et le
résumé GitHub restent alors les diagnostics disponibles. Aucune suite lourde
n'est relancée seulement pour fabriquer un artefact.

Reproduisez d'abord avec la cible indiquée (`make check-local`, `make
docker-e2e`, `make docker-symfony-e2e`, `make proof` ou `make dist`), puis avec
`make release`. Après correction, un mainteneur peut relancer les jobs échoués
depuis *Re-run jobs* ou avec `gh run rerun <RUN_ID> --failed`. Un rerun produit
un nouvel artefact suffixé par son numéro d'attempt.

Les règles GitHub, leur vérification et le diagnostic d'un blocage de merge
sont centralisés dans [GITHUB.md](GITHUB.md).

## Matrice

| Milestone | Preuve |
| --- | --- |
| M0 socle | `make check-local`, mock CDP qui valide sorties, méthodes, params et ordre |
| M1 Chrome réel | `make test-e2e`, suite Blink/V8 complète sur les mêmes fixtures |
| M2 Symfony | `make docker-symfony-e2e`, extraction profiler via header réel |
| M3 interception | unit + e2e Fetch continue/fulfill/block, timing settle |
| M4 SEO/perf | vitals avec interaction, a11y AXTree, coverage JS/CSS, SEO edge |
| M5 orchestration | record/replay avec divergence, frame, allowlist, max-actions |
| M6 distribution | `make docker-check`, `make docker-e2e`, image `cdpx-ci` |
| M8 équipe/sécurité | unitaires policy/session/journal/redaction/artefacts + E2E multi-session Chrome réel |
| Release | `make release`, tous les portails précédents + proof + wheel/sdist |

## Cas limites couverts

- Absence de Chrome: échec e2e explicite, sans faux succès par skip.
- Absence de Docker/Compose ou skip Symfony: échec explicite de la preuve et
  du portail release.
- Preuve e2e: chaque scénario Chrome non skippé doit exposer au moins un
  screenshot dans `.proof/evidence/`.
- Cookies: `Storage.clearCookies` avec fallback CDP historique.
- Interception: réponse fulfill encodée, block réseau, continue, règle invalide.
- Replay: journal v1/v2, secret ref absente avant effet CDP, NDJSON invalide,
  action manquante, divergence `ok:false`, budget, comparaison sémantique et
  origine réelle relue après redirection/avant mutation.
- Interception: seules les actions `continue`, `block` ou statuts `200..599`
  sont acceptées ; une action inconnue échoue avant navigation.
- SEO: JSON-LD invalide, Product incomplet, H1 dupliqués, longueurs estimées.
- Mode équipe: session/run/target requis avant découverte, manifest/lease
  privés, trois profils simultanés isolés, loopback, teardown et matrice
  d'autorités exercés sur mock et Chrome réel.
- Origines: legacy opt-in pour les mutations ; équipe fail-closed avec
  `CDPX_ORIGINS` obligatoire, destinations et origine réelle contrôlées.
- Sorties agentiques: JSON compact par défaut, limites `--limit`/`--max-actions`,
  NDJSON pour les flux, cookies/storage/saisies masqués, URL/query/headers/
  console/profiler nettoyés et contenu page marqué non fiable.
- Interactions: `wait_visible` distingue visibilité et présence DOM ; `click`
  refuse détaché, caché, désactivé, instable ou recouvert ; `type --clear`
  sélectionne puis émet Backspace avant `Input.insertText`; le clavier étendu
  est verrouillé par mock et par Home/Delete/End/Space sur Chrome réel.
- Scénarios: drainage console/réseau final avant assertions et `secret_ref`
  absent refusé avant action.

## Dette non bloquante

- `KEY_MAP` reste volontairement borné aux touches nommées testées; toute
  extension exige besoin, mock et scénario Chrome.
- `eval` reste une échappatoire surveillée; un usage répété se promeut en
  primitive nommée.
- `a11y` est une vue AX compacte, `vitals` une mesure locale bornée, `seo` un
  contrôle on-page, `network` un résumé non-HAR et `replay` une comparaison
  partielle : aucun de ces signaux ne doit être présenté comme exhaustif.
- Le TTL des artefacts autonomes est manifesté et purgeable mais aucun daemon
  global ne déclenche actuellement `purge_expired` hors session supervisée.
- `key` et le cycle de vie CLI `tabs` new/activate/close disposent de scénarios
  Chrome dédiés, en complément du protocole figé par le mock.
