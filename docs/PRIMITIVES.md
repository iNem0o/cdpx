# PRIMITIVES.md — catalogue

Chaque primitive = une fonction (`src/cdpx/primitives/`), une sous-commande
CLI, des tests mock (sortie + protocole), une fixture si scénario e2e.

## Contrat de sortie

Par défaut, le CLI imprime du JSON compact sur une ligne, optimisé pour
l'agent et le coût token. `--pretty` restaure l'affichage humain indenté.
Les champs volumineux sont bornés par défaut (`--limit`, métadonnées
`*_truncated`); `--full` demande explicitement le détail complet. Les flux
(`console --follow`, `record`) utilisent du NDJSON compact.

## Implémentées (validées contre le mock, `make check` vert)

### Navigation & synchronisation

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx goto <url> [--wait load\|domcontentloaded\|none]` | se déplacer et savoir quand la page est prête | sans attente de cycle de vie, l'agent observe des états intermédiaires |
| `cdpx wait <selector>` | attendre un élément (SPA, contenu injecté) | fixture `spa.html`: `#late-content` n'existe qu'après 300ms; le load event ne suffit pas |

```
cdpx goto http://shop.localhost/produit-42
cdpx wait "#offcanvas-cart" --timeout 5
```

### Lecture (les "sens" de l'agent)

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx text [selector]` | innerText — vision sémantique low-cost | 100x moins de tokens qu'un screenshot pour vérifier un contenu |
| `cdpx html [selector]` | outerHTML — inspection structurelle | vérifier attributs, classes, data-* |
| `cdpx count <selector>` | assertion cheap ("il y a bien 12 produits") | boucle vérif rapide après une action |
| `cdpx eval <js> [--await]` | primitive racine: tout le reste | échappatoire universelle; à utiliser en dernier recours (fragile, non typée) |
| `cdpx screenshot [-o f.png] [--full-page]` | vision pixel: bugs CSS, rendus | quand le texte ne suffit pas |
| `cdpx console [--duration s]` | logs + exceptions JS | LE feedback manquant: un front cassé se voit d'abord en console |
| `cdpx console --follow --max N` | stream NDJSON des logs | boucle continue agentique, bornable par `--max` |

```
cdpx text "#result"                       # -> {"text": "OK:Léo"}
cdpx console --duration 3                 # -> {"entries": [...], "errors": 1}
cdpx eval "window.Shopware?.Context?.api?.languageId"
```

### Action (les "mains")

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx click <selector>` | cliquer via Input domain (trusted) | scrollIntoView + mouseMoved/Pressed/Released au centre de l'élément; passe les filtres `isTrusted` |
| `cdpx type <selector> <texte> [--clear]` | remplir un champ | focus réel + `Input.insertText` (IME-safe) |
| `cdpx key <Enter\|Tab\|Escape\|ArrowUp\|ArrowDown>` | validation, navigation clavier | soumettre un formulaire comme un humain |

```
cdpx type "#name" "Léo" --clear
cdpx key Enter
cdpx text "#result"
```

### Mesure & audit

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx network <url> [--settle s]` | naviguer en capturant l'activité réseau | XHR 500, assets 404, poids: résumé + détail par requête |
| `cdpx metrics` | Performance.getMetrics (heap, nodes, layouts) | objectiver une dérive (fuite DOM, heap qui gonfle) |
| `cdpx seo [url]` | contrat SEO du DOM **rendu**: title, metas, canonical, robots, h1, hreflang, JSON-LD, alt manquants, liens int/ext/nofollow + findings | audits type Jules.com: seul le DOM final fait foi côté rendering Googlebot; fixtures `seo.html` (conforme) / `seo-broken.html` (violations connues) |

```
cdpx network http://shop.localhost/checkout
# -> {"summary": {"total": 34, "failed": 0, "errors_4xx_5xx": 1, "bytes": 812345}, ...}
cdpx seo https://www.exemple.fr/collection/robes
# -> {..., "findings": ["canonical manquant", "2 h1 (attendu: 1)"]}
```

### État & session

| CLI | Usecase | Pourquoi |
|---|---|---|
| `cdpx cookies get [--show-values]` | inspecter la session (masqué par défaut) | sécurité: cf. HARNESS.md §2 |
| `cdpx cookies set --name --value --url` / `clear` | préparer un scénario (consentement, feature flag) | reproductibilité; `clear` utilise Storage.clearCookies avec fallback |
| `cdpx storage [--kind local\|session]` | localStorage/sessionStorage | panier invité, consentement, caches front |

### Onglets & méta

| CLI | Usecase |
|---|---|
| `cdpx tabs list\|new\|activate\|close` | orchestration multi-pages (comparer prod/staging côte à côte) |
| `cdpx version` | vérifier le Chrome ciblé avant d'agir |
| `cdpx --version` | vérifier la version du paquet cdpx |

## Implémentées M2-M5

| Primitive | Milestone | Usecase | Comment (piste) |
|---|---|---|---|
| `cdpx profiler <url>` | M2 | lire le profiler Symfony de la dernière requête | `X-Debug-Token-Link` ou fallback `X-Debug-Token`, fetch côté cdpx |
| `cdpx dom-diff -- click <sel>` | M2 | snapshot avant/après une action -> diff structurel | sérialisation DOM normalisée + diff unifié |
| `cdpx intercept --rule 'PATTERN => 503' --settle 1 -- goto <url>` | M3 | mocker/bloquer des requêtes | Fetch.enable + Fetch.requestPaused, continue/fulfill/block, commande composée |
| `cdpx emulate mobile|slow-3g|cpu-4x|--reset` | M3 | device mobile, throttling réseau/CPU, user-agent | Emulation.* + Network.emulateNetworkConditions |
| `cdpx vitals <url> [--click <sel>]` | M4 | LCP/CLS/INP basiques de la page | PerformanceObserver injecté + interaction optionnelle |
| `cdpx a11y` | M4 | arbre d'accessibilité = vision sémantique structurée | Accessibility.getFullAXTree compacté |
| `cdpx coverage <url>` | M4 | coverage JS/CSS chargé sur une page | Profiler.startPreciseCoverage + CSS rule usage |
| `cdpx record` / `replay` | M5 | journaliser et relire un parcours | NDJSON compact, divergence explicite, `--max-actions` |
| `cdpx frame <selector>` | M5 | lire dans une iframe | accès DOM iframe same-origin |

## Règle d'ajout

Nouvelle primitive = usecase écrit ici D'ABORD (une ligne de tableau), puis
test mock, puis implémentation, puis fixture si e2e pertinent. Cf. CLAUDE.md
"Definition of Done".
