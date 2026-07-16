+++
id = "seo-performance-accessibility"
title = "Audits SEO, performance et accessibilité"
status = "validated"
summary = "Auditer le contrat SEO du DOM rendu, les diagnostics Web Vitals, l'arbre d'accessibilité, un sous-ensemble RGAA automatisé côté front et la couverture JS/CSS."
entrypoints = ["cdpx seo", "cdpx vitals", "cdpx a11y", "cdpx coverage"]
path_globs = ["src/cdpx/primitives/audit.py", "src/cdpx/primitives/diagnostics.py", "src/cdpx/primitives/frames.py", "tests/fixtures/seo*.html", "tests/fixtures/vitals.html", "tests/fixtures/coverage.html", "tests/fixtures/coverage.css", "tests/fixtures/coverage.js", "tests/fixtures/iframe.html", "tests/fixtures/child.html", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**"]
test_globs = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_vitals*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*", "tests/e2e/test_e2e_symfony.py::test_symfony_vitals*", "tests/e2e/test_e2e_symfony.py::test_symfony_rgaa*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md", "docs/milestones/M4-seo-perf.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "audit-seo-rendered-dom"
title = "Auditer le SEO sur le DOM rendu"
entrypoint = "cdpx seo"

[[journeys]]
id = "measure-vitals"
title = "Mesurer des Web Vitals basiques après interaction optionnelle"
entrypoint = "cdpx vitals"

[[journeys]]
id = "audit-front-accessibility"
title = "Auditer des contrôles d'accessibilité front déterministes"
entrypoint = "cdpx a11y"

[[scenarios]]
id = "audit-rendered-seo-and-a11y"
journey = "audit-seo-rendered-dom"
title = "Auditer les contrats SEO et accessibilité du DOM rendu"
ui_text = "Le rapport présente les contrôles SEO et accessibilité de la page rendue comme preuves produit."
report_text = "Ce scénario prouve que les primitives d'audit rendues par le navigateur valident des signaux SEO et accessibilité que le HTML brut ne montrerait pas."
given = "Les fixtures SEO, cas limites et iframe sont disponibles dans un vrai navigateur."
when = "cdpx exécute les primitives d'audit SEO, d'arbre d'accessibilité ou de couverture."
then = "Les contrôles résultants sont rattachés à la feature avec JUnit et captures d'écran navigateur."
tests = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "measure-local-vitals"
journey = "measure-vitals"
title = "Mesurer des Web Vitals en local"
ui_text = "L'utilisateur peut mesurer des Web Vitals basiques après une interaction optionnelle."
report_text = "Ce scénario prouve que des mesures de performance navigateur sont disponibles comme preuves compactes sur fixtures locales."
given = "Une fixture vitals est chargée dans Chrome."
when = "cdpx vitals collecte les signaux de performance navigateur supportés."
then = "Le résultat est rapporté avec sa couverture de tests et un scénario e2e adossé à une capture d'écran."
tests = ["tests/test_primitives.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_vitals*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compare-symfony-vitals"
journey = "measure-vitals"
title = "Comparer les pages Symfony vitals baseline et dégradée"
ui_text = "Le rapport compare des variantes de performance Symfony déterministes."
report_text = "Ce scénario prouve que Web Vitals, métriques Performance et captures d'écran peuvent être orchestrés contre les pages Symfony baseline/dégradée."
given = "Le moteur de scénarios Symfony expose `/scenario/vitals/baseline` et `/scenario/vitals/degraded`."
when = "cdpx collecte les vitals, les métriques navigateur, les métadonnées de scénario et les captures d'écran pour les deux variantes."
then = "Le rapport montre les deltas entre variantes et lie les preuves JSON, JUnit, logs et capture d'écran."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_compare_baseline_degraded"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "symfony-vitals-diagnostic-attribution"
journey = "measure-vitals"
title = "Collecter l'attribution diagnostique des Web Vitals Symfony"
ui_text = "Le rapport garde LCP, INP et CLS au premier plan tout en montrant des diagnostics d'attribution déterministes."
report_text = "Ce scénario prouve que les routes Symfony pour LCP image/texte, CLS injecté, INP long-task et ressources bloquantes exposent seuils, navigation timing, buckets de resource timing, métadonnées de source et métadonnées d'émulation comme preuves JSON."
given = "Le moteur de scénarios Symfony expose `/scenario/vitals/lcp-image`, `/scenario/vitals/lcp-text`, `/scenario/vitals/cls-injected-banner`, `/scenario/vitals/inp-long-task` et `/scenario/vitals/resource-blocking`."
when = "cdpx collecte les Web Vitals, les métadonnées d'attribution déterministes et les captures d'écran de chaque route."
then = "Le cockpit de preuve lie JUnit, diagnostics JSON, logs Docker et captures d'écran sans transformer les trous d'attribution en succès cachés."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_diagnostics_cover_attribution_routes"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "audit-symfony-rgaa-subset"
journey = "audit-front-accessibility"
title = "Auditer le sous-ensemble RGAA déterministe Symfony"
ui_text = "Le rapport distingue les contrôles automatisés inspirés du RGAA d'une couverture RGAA complète."
report_text = "Ce scénario prouve que des contrôles automatisés à thématique RGAA peuvent être regroupés par images, cadres, couleurs, multimédia, tableaux, liens, scripts/composants, éléments obligatoires, structure, présentation, formulaires, navigation et consultation sans revendiquer une couverture RGAA complète."
given = "Le moteur de scénarios Symfony expose des pages accessibles et régressées sous `/scenario/rgaa/{case}`."
when = "cdpx lit l'arbre d'accessibilité et des contrôles DOM déterministes pour les deux variantes."
then = "Le rapport inclut des contrôles JSON par thème avec critères, périmètre automatisé, statut, limites, JUnit, logs et captures d'écran comme preuves."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_rgaa_subset_checks_are_deterministic"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intention

Fournir des primitives d'audit rendues par le navigateur pour les pages où le
HTML brut n'est pas la source de vérité. Sur un front JS, canonical injecté,
JSON-LD posé par GTM ou hreflang réécrits n'existent que dans le DOM final —
et c'est ce DOM final que Googlebot évalue au rendering. Auditer la réponse
HTTP ne suffit donc pas: `cdpx seo`, `cdpx vitals`, `cdpx a11y` et
`cdpx coverage` mesurent la page telle que l'utilisateur (et le crawler en
mode rendering) la reçoit réellement.

## Usage

Options globales et codes de sortie: voir la section Contrat CLI du README.

### `cdpx seo`

Synopsis: `cdpx seo [url]`

Extrait en un appel le contrat SEO on-page du DOM **rendu**: title, metas,
canonical, robots, h1, hreflang, blocs JSON-LD (validés contre un schéma
minimal `Product`: `sku` ou `name` requis), images sans `alt`, comptage des
liens internes/externes/nofollow, estimation en pixels de la largeur SERP du
title et de la meta description, détection des h1 dupliqués. Les anomalies
sont agrégées dans `findings` (liste vide = aucun problème détecté).

Options propres:

- `url` (positionnel, optionnel) — naviguer d'abord vers cette URL. Sans
  `url`, la commande audite la page actuellement affichée dans l'onglet
  cible: pratique pour auditer un état obtenu après interactions (panier
  ouvert, variante sélectionnée, page SPA après route côté client).

```bash
# Auditer une fiche produit (navigation puis audit du DOM rendu)
cdpx seo https://www.exemple.fr/produit-42

# Auditer la page courante, sans navigation (état post-interaction)
cdpx seo

# Lecture humaine
cdpx --pretty seo https://www.exemple.fr/produit-42
```

Sortie (extrait réaliste):

```json
{
  "url": "https://www.exemple.fr/produit-42",
  "lang": "fr",
  "title": "Chaussures de trail Vertex 42 | Exemple.fr",
  "metas": {
    "description": "Chaussures de trail Vertex 42, accroche maximale.",
    "robots": "index,follow",
    "og:title": "Chaussures de trail Vertex 42"
  },
  "canonical": "https://www.exemple.fr/produit-42",
  "robots": "index,follow",
  "h1": ["Chaussures de trail Vertex 42"],
  "hreflang": [
    {"lang": "fr", "href": "https://www.exemple.fr/produit-42"},
    {"lang": "en", "href": "https://www.exemple.fr/en/product-42"}
  ],
  "jsonld": [
    {"@type": "Product", "name": "Vertex 42", "sku": "VTX-42"}
  ],
  "images_without_alt": 2,
  "links": {"internal": 34, "external": 3, "nofollow": 1},
  "title_px_estimate": 331,
  "description_px_estimate": 353,
  "findings": ["2 image(s) sans alt"]
}
```

Pièges et cas d'erreur:

- Sans `url`, il faut qu'une page soit déjà chargée dans l'onglet cible;
  sur `about:blank` l'audit renvoie un contrat quasi vide avec de nombreux
  `findings`.
- Un JSON-LD non parsable est signalé (`"JSON-LD invalide"` dans `findings`)
  au lieu de faire échouer la commande.
- Les estimations `*_px_estimate` sont une approximation stable pour agent/CI
  (largeur moyenne SERP desktop), pas un rendu au pixel près.

### `cdpx vitals`

Synopsis: `cdpx vitals url [--click SELECTEUR] [--settle S]`

Mesure LCP, CLS et INP via des `PerformanceObserver` pré-injectés **avant**
la navigation (`Page.addScriptToEvaluateOnNewDocument`), ce qui capture les
entrées buffered dès le premier paint. L'interaction optionnelle `--click`
déclenche un événement réel pour alimenter la mesure INP.

Options propres:

- `url` (positionnel, requis) — page à mesurer.
- `--click SELECTEUR` — sélecteur CSS à cliquer après chargement pour
  mesurer l'INP (sans clic, `inp` reste à 0).
- `--settle S` — délai en secondes laissé aux observers pour collecter les
  entrées après chargement/interaction (défaut: 0.5).

```bash
# Vitals de chargement simple
cdpx vitals https://www.exemple.fr/produit-42

# Mesurer l'INP en cliquant le bouton d'ajout panier
cdpx vitals https://www.exemple.fr/produit-42 --click "#ajouter-panier" --settle 1.0
```

Sortie:

```json
{
  "url": "https://www.exemple.fr/produit-42",
  "lcp": 812.4,
  "cls": 0.031,
  "inp": 96
}
```

Pièges et cas d'erreur:

- `inp` vaut 0 sans `--click` (aucune interaction = rien à mesurer).
- L'observer `event` (INP) est optionnel selon le support navigateur: son
  absence n'est pas une erreur, la valeur reste simplement à 0.
- Un `--settle` trop court peut sous-estimer CLS/LCP sur des pages qui
  injectent du contenu tardivement.

### `cdpx a11y`

Synopsis: `cdpx a11y`

Retourne l'arbre d'accessibilité (AXTree) compacté de la page courante:
la vision **sémantique** de la page à bas coût en tokens. Chaque nœud non
ignoré expose son `role` et son `name` dans la vue AX compacte de Chrome.
C'est un signal utile pour vérifier libellés,
structure de titres et zones de repère sans parser le HTML complet, mais pas
une reproduction exhaustive de chaque lecteur d'écran.

Options propres: aucune (la commande opère sur la page courante de l'onglet
cible; naviguer d'abord avec `cdpx goto` si besoin).

```bash
cdpx goto https://www.exemple.fr/produit-42
cdpx a11y

# Lecture humaine
cdpx --pretty a11y
```

Sortie:

```json
{
  "nodes": [
    {"role": "RootWebArea", "name": "Chaussures de trail Vertex 42", "ignored": false},
    {"role": "banner", "name": "", "ignored": false},
    {"role": "heading", "name": "Chaussures de trail Vertex 42", "ignored": false},
    {"role": "button", "name": "Ajouter au panier", "ignored": false},
    {"role": "link", "name": "Guide des tailles", "ignored": false}
  ],
  "count": 5
}
```

Pièges et cas d'erreur:

- Les nœuds `ignored` sont filtrés: un élément invisible pour l'API
  d'accessibilité n'apparaît pas, ce qui est justement le signal recherché
  (bouton sans nom accessible, icône sans alternative...).
- Sur de grosses pages la liste est bornée par `--limit` (50 par défaut);
  utiliser `--full` pour tout voir.

### `cdpx coverage`

Synopsis: `cdpx coverage url`

Mesure le JS et le CSS morts après chargement d'une page: couverture précise
par fichier JS (`Profiler.takePreciseCoverage`) et usage des règles CSS
(`CSS.stopRuleUsageTracking`). Usecase: quantifier le poids du code jamais
exécuté (bundles surdimensionnés, CSS de thème inutilisé) avant un chantier
de performance.

Options propres:

- `url` (positionnel, requis) — page à charger sous instrumentation (le
  tracking démarre avant la navigation pour ne rien manquer).

```bash
cdpx coverage https://www.exemple.fr/produit-42
```

Sortie:

```json
{
  "url": "https://www.exemple.fr/produit-42",
  "files": [
    {"url": "https://www.exemple.fr/assets/app.js", "functions": 214, "used_ranges": 87},
    {"url": "https://www.exemple.fr/assets/vendor.js", "functions": 1032, "used_ranges": 240}
  ],
  "count": 2,
  "css": {"rules": 418, "used": 137, "unused": 281}
}
```

Pièges et cas d'erreur:

- La mesure reflète le chargement seul: le code exécuté uniquement après
  interaction (menus, carrousels) compte comme "mort" si on n'interagit pas.
- Les scripts inline apparaissent avec une `url` vide ou celle du document.

## Parcours utilisateur

- Vérifier title, metas, canonical, h1, hreflang, JSON-LD, alt des images et
  liens sur le DOM rendu.
- Lire l'arbre d'accessibilité compact comme vision sémantique de la page.
- Mesurer les Web Vitals et la couverture JS/CSS.
- Comparer les variantes de performance Symfony baseline/dégradée ainsi que
  les variantes diagnostiques LCP, CLS, INP et ressources.
- Passer en revue les contrôles RGAA automatisés déterministes regroupés par
  les 13 thématiques RGAA, avec limites explicites.

## Validation

Les fixtures couvrent des cas SEO propres, cassés et limites, plus des pages
vitals et coverage. Le moteur de scénarios Symfony sous Docker ajoute des
variantes déterministes de vitals, de diagnostics d'attribution et
d'accessibilité front.

## Preuves

Les preuves attendues sont JUnit et captures d'écran issues des scénarios
d'audit e2e Chrome. Les scénarios Symfony attachent en plus les vitals JSON,
seuils, resource timing, métadonnées d'attribution, métriques et contrôles
du sous-ensemble RGAA automatisé.

## Limites connues

- `seo` contrôle le DOM rendu de **la page courante**. Il ne crawl pas un site,
  ne vérifie ni indexation réelle, robots serveur, backlinks, logs ni données
  Search Console.
- `vitals` est un diagnostic local borné par le navigateur et la fenêtre
  `--settle`, pas une méthodologie de laboratoire multi-run ni une mesure de
  terrain CrUX/RUM. Le support navigateur détermine les signaux INP/event
  timing disponibles.
- `a11y` compacte l'AXTree et ne constitue ni un test avec technologies
  d'assistance réelles ni un audit RGAA complet. La couverture RGAA Symfony est
  un sous-ensemble automatisé regroupé par thème.
- `coverage` ne voit que le code exécuté pendant le chargement instrumenté et
  n'est pas une analyse statique complète des bundles.
