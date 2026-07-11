# Gouvernance GitHub

Ce document est la source de vérité pour les réglages GitHub qui ne peuvent pas
être versionnés. `HARNESS.md` reste normatif pour la qualité et la sécurité ;
[VALIDATION.md](VALIDATION.md) décrit le cockpit et ses couches.

## Cycle d'une contribution

1. créer une branche courte depuis `master` et y regrouper un changement ciblé ;
2. ouvrir une draft PR, compléter le template et laisser `CI` produire la preuve ;
3. lire le résumé **Full release gate** et, si nécessaire, télécharger le cockpit ;
4. corriger jusqu'à ce que **`PR Gate / Required`** soit vert ;
5. passer la PR en review, résoudre les conversations et merger selon la politique ;
6. laisser GitHub supprimer la branche mergée.

`make check` est le portail qualité normatif. `make release` ajoute le cockpit
et la validation des distributions ; il ne publie rien à lui seul. La procédure
future de release reste dans [RELEASE-PLAN.md](RELEASE-PLAN.md). Pousser un tag
`vX.Y.Z` est, lui, une action de publication et nécessite une autorisation
explicite.

## Réglages attendus

État cible du dépôt privé :

| Réglage | Valeur |
| --- | --- |
| Branche par défaut | `master` |
| Check requis | `PR Gate / Required` |
| Branche à jour | requise si la durée du portail reste acceptable |
| Conversations | résolution obligatoire |
| Approbations | 0 tant que le projet doit rester administrable par un mainteneur seul |
| Force-push / suppression | interdits sur `master` |
| Merge | squash uniquement ; branche supprimée après merge |
| Actions par défaut | `contents: read`, pas d'approbation de PR par workflow |
| Actions tierces | GitHub et actions explicitement autorisées, toutes épinglées par SHA |
| Artefacts PR | 30 jours |
| Vulnérabilités | signalement privé et alertes Dependabot activés si le plan le permet |

Le dépôt ne contient volontairement ni `.github/settings.yml` sans application
consommatrice, ni `CODEOWNERS` tant qu'un propriétaire de code durable n'a pas
été explicitement désigné.

## Vérifier les réglages

Les commandes suivantes doivent être exécutées avec un compte administrateur :

```bash
gh repo view inem0o/cdpx --json visibility,defaultBranchRef,deleteBranchOnMerge,squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed
gh api repos/inem0o/cdpx/actions/permissions
gh api repos/inem0o/cdpx/actions/permissions/workflow
gh api repos/inem0o/cdpx/rulesets
gh api repos/inem0o/cdpx/branches/master/protection
gh api repos/inem0o/cdpx/private-vulnerability-reporting
gh pr checks <PR_NUMBER> --repo inem0o/cdpx
```

Un HTTP 403 mentionnant un upgrade de plan signifie que GitHub n'offre pas les
rulesets ou la protection de branches pour ce dépôt privé avec l'abonnement
actuel. Ce n'est pas équivalent à une règle active : le risque doit rester
explicite jusqu'à upgrade. Ne rendez jamais le dépôt public pour contourner
cette limite.

## Diagnostiquer un merge bloqué

1. vérifier le nom exact et l'état de `PR Gate / Required` avec `gh pr checks` ;
2. ouvrir le job requis et identifier la valeur failed/cancelled/skipped ;
3. lire le résumé puis l'artefact comme décrit dans [VALIDATION.md](VALIDATION.md) ;
4. vérifier que la branche est à jour et que toutes les conversations sont résolues ;
5. reproduire la cible Make rouge localement, corriger, commit et push.

Un workflow modifié dans une PR exécute le code de cette PR. Le check
agrégateur, les permissions en lecture et l'absence de `pull_request_target`
réduisent le risque, mais seul un required workflow/ruleset administré hors de
la branche peut empêcher absolument une PR de neutraliser son propre YAML. Ce
garde-fou doit être activé dès que le plan GitHub le rend disponible.

## Incident exceptionnel

Une protection ne se désactive que pour un incident bloquant vérifié, jamais
pour faire passer une CI rouge. Avant l'action, consigner l'URL de la PR, le run,
la cause et l'approbation du propriétaire. Exporter la règle avec `gh api`, la
désactiver dans *Settings → Rules → Rulesets* (ou via l'API), effectuer le
correctif minimal, puis restaurer immédiatement la règle et vérifier à nouveau
son JSON. Toute intervention doit rester visible dans la PR ou un journal
d'incident privé.

## Publication future

Le workflow `Release` ne part que sur un tag `v*`, vérifie la version et que le
commit taggé appartient à `master`, puis utilise l'environnement `pypi`. Avant
le premier tag, protéger les tags `v*`, exiger une approbation sur cet
environnement et vérifier Trusted Publishing. Aucune PR ordinaire ne doit
déclencher PyPI ou une GitHub Release.
