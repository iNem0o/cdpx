# PRIMITIVES.md — catalogue

Chaque primitive = une fonction (`src/cdpx/primitives/`), une sous-commande
CLI, des tests mock (sortie + protocole), une fixture si scénario e2e. Ce
catalogue donne le **quoi/pourquoi** par feature; la référence exhaustive
(options, sorties JSON, pièges) vit dans la fiche de chaque feature
(`docs/features/`), affichée aussi dans le rapport de preuve (`make proof`).

## Contrat de sortie

Par défaut, le CLI imprime du JSON compact sur une ligne, optimisé pour
l'agent et le coût token. `--pretty` restaure l'affichage humain indenté.
Les champs volumineux sont bornés par défaut (`--limit`, métadonnées
`*_truncated`); `--full` demande explicitement le détail complet. Les flux
(`console --follow`, journaux `record`) utilisent du NDJSON compact.
Détail du contrat (codes de sortie, connexion, `CDPX_ORIGINS`): section
« Contrat CLI » du [README](../README.md).

## Navigation et synchronisation — [fiche](features/browser-navigation.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx tabs list\|new\|activate\|close` | orchestration multi-pages (comparer prod/staging côte à côte) | plusieurs contextes sans plusieurs Chrome |
| `cdpx version` | vérifier le Chrome ciblé avant d'agir | ne jamais agir sur un navigateur inconnu |
| `cdpx goto <url> [--wait load\|domcontentloaded\|none]` | se déplacer et savoir quand la page est prête | sans attente de cycle de vie, l'agent observe des états intermédiaires |
| `cdpx wait <selector>` | attendre un élément (SPA, contenu injecté) | fixture `spa.html`: `#late-content` n'existe qu'après 300ms; le load event ne suffit pas |

`tabs list` retourne un objet `{tabs, count}` afin de respecter le contrat JSON
racine et d'appliquer réellement `--limit` avec les métadonnées de troncature.

```bash
cdpx goto http://shop.localhost/produit-42
cdpx --timeout 5 wait "#offcanvas-cart"
```

## Inspection du DOM et actions utilisateur — [fiche](features/dom-interaction.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx text [selector]` | innerText — vision sémantique low-cost | 100x moins de tokens qu'un screenshot pour vérifier un contenu |
| `cdpx html [selector]` | outerHTML — inspection structurelle | vérifier attributs, classes, data-* |
| `cdpx count <selector>` | assertion cheap ("il y a bien 12 produits") | boucle vérif rapide après une action |
| `cdpx eval <js> [--await]` | primitive racine: tout le reste | échappatoire universelle; dernier recours (fragile, non typée) |
| `cdpx click <selector>` | cliquer via Input domain (trusted) | scrollIntoView + mouse events au centre; passe les filtres `isTrusted` |
| `cdpx type <selector> <texte> [--clear]` | remplir un champ | focus réel + `Input.insertText` (IME-safe) |
| `cdpx key <touche>` | validation, navigation clavier (Enter, Tab, Escape, flèches) | soumettre un formulaire comme un humain |

```bash
cdpx type "#name" "Léo" --clear
cdpx key Enter
cdpx text "#result"
```

## Capture et observabilité — [fiche](features/browser-capture-observability.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx screenshot [-o f.png] [--full-page] [--format png\|jpeg]` | vision pixel: bugs CSS, rendus | quand le texte ne suffit pas; JPEG pour alléger |
| `cdpx pdf [-o f.pdf]` | figer une page en PDF | preuve d'état imprimable, rendu print |
| `cdpx console [--duration s]` | logs + exceptions JS | LE feedback manquant: un front cassé se voit d'abord en console |
| `cdpx console --follow --max N` | stream NDJSON des logs | boucle continue agentique, bornable par `--max` |
| `cdpx network <url> [--settle s]` | naviguer en capturant l'activité réseau | XHR 500, assets 404, poids: résumé + détail par requête |
| `cdpx metrics` | Performance.getMetrics (heap, nodes, layouts) | objectiver une dérive (fuite DOM, heap qui gonfle) |

```bash
cdpx network http://shop.localhost/checkout
cdpx console --duration 3
cdpx screenshot -o etat.jpg --format jpeg
```

## État et session — [fiche](features/state-session.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx cookies get [--show-values]` | inspecter la session (masqué par défaut) | sécurité: cf. HARNESS.md §2 |
| `cdpx cookies set --name n --value v --url u` / `clear` | préparer un scénario (consentement, feature flag) | reproductibilité; `clear` = Storage.clearCookies avec repli |
| `cdpx storage [--kind local\|session]` | localStorage/sessionStorage | panier invité, consentement, caches front |

## Audits SEO, performance, accessibilité — [fiche](features/seo-performance-accessibility.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx seo [url]` | contrat SEO du DOM **rendu**: title/metas/canonical/robots/h1/hreflang/JSON-LD/alt/liens + findings, px estimés, doublons | seul le DOM final fait foi côté rendering Googlebot |
| `cdpx vitals <url> [--click sel]` | LCP/CLS/INP basiques | objectiver la perf perçue, interaction pour INP |
| `cdpx a11y` | arbre d'accessibilité compacté | vision sémantique structurée low-cost |
| `cdpx coverage <url>` | JS/CSS mort par fichier | dette front mesurée, pas devinée |

```bash
cdpx seo https://www.exemple.fr/collection/robes
cdpx vitals http://shop.localhost/ --click "#add-to-cart"
```

## Diagnostics développeur — [fiche](features/dev-profiler-diff.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx profiler <url> [--settle s] [--panels ...]` | parser les panels du Web Profiler de la dernière requête (Doctrine, Twig, cache, exceptions, HTTP client, Messenger, routing, temps, logs) | N+1, duplicats SQL et exceptions chiffrés par l'agent sans ouvrir le browser; `X-Debug-Token-Link` + repli `X-Debug-Token`, HTML des panels parsé (aucune API JSON côté Symfony) |
| `cdpx dom-diff -- <action>` | snapshot avant/après une action → diff structurel stable | voir exactement ce qu'un clic a changé dans le DOM |

```bash
cdpx profiler http://app.localhost/api/panier
cdpx dom-diff -- click "#submit-btn"
```

## Interception, émulation, orchestration — [fiche](features/orchestration-control.md)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx intercept --rule "PATTERN => 503\|block\|continue" -- goto <url>` | mocker/bloquer des requêtes pendant une navigation | commande composée: `Fetch.enable` meurt avec la connexion |
| `cdpx emulate mobile\|slow-3g\|cpu-4x [--reset] [-- <action>]` | device mobile, throttling réseau/CPU | forme composée obligatoire pour agir sous émulation: les overrides meurent avec la connexion |
| `cdpx frame <selector>` | lire dans une iframe same-origin | contenus embarqués (paiement, consentement) |
| `cdpx record [-o j.ndjson] -- <action>` | exécuter UNE action et la journaliser (résultat compris) | construire un parcours rejouable, trace fidèle |
| `cdpx replay <j.ndjson>` | rejouer le journal, stop à la première divergence | non-régression de parcours; budget `--max-actions` |
| `cdpx scenario run <fichier.yml>` | exécuter un parcours métier déclaratif | verdict unique pass/fail, findings et dossier de preuves |

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx emulate mobile -- goto http://shop.localhost/
cdpx record -o parcours.ndjson -- click "#add-to-cart"
cdpx --max-actions 20 replay parcours.ndjson
cdpx scenario run checkout_guest_add_to_cart.yml
```

## Harness et cockpit de preuve — [fiche](features/harness-proof-cockpit.md)

Portails qualité (`make check`, `make test-e2e`, images Docker) et génération
du rapport de preuve (`make proof` → `.proof/proof-report.html`), qui sert de
documentation humaine du produit: doc utilisateur par feature, scénarios,
tests, preuves, gaps. Voir la fiche pour chaque cible make.

## Règle d'ajout

Nouvelle primitive = usecase écrit ici D'ABORD (une ligne de tableau), puis
test mock, puis implémentation, puis fixture si e2e pertinent, puis section
`### cdpx <cmd>` dans la fiche feature (vérifié mécaniquement: une commande
sans doc utilisateur casse `make proof`). Cf. CLAUDE.md « Definition of Done ».
