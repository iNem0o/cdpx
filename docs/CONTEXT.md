# CONTEXT.md — d'où vient ce projet

## L'existant (point de départ)

Léo a développé des outils CLI exploitant le Chrome Debug Protocol pour donner
à son agent la capacité de **voir et naviguer** dans un Chrome partagé
humain/agent, dans le contexte du dev d'applications Symfony et de sites
e-commerce (Shopware/PrestaShop) et d'opérations SEO.

Capacités déjà en place côté Léo:
- voir la page,
- naviguer,
- gérer les onglets,
- exécuter du JS brut dans la page.

## La demande

1. **Trouver de nouveaux usecases** offerts par le câblage CDP déjà en place.
2. **Scripter de nouvelles primitives** qui améliorent la production de
   l'agent ET du dev qui le pilote.
3. Livrer une **stack complète**: CLI pour l'agent, toutes les primitives,
   et surtout un **système de test déterministe** — un serveur HTTP simple +
   des HTML statiques couvrant tous les usecases.
4. Harness agent intégré, documentation des échanges, todolist complète
   (quoi/comment/pourquoi + intentions d'origine + exemples), roadmap par
   milestones pour ce qui ne peut pas être pris tout de suite.
5. Ne mettre en place **que ce qui est validable à 100% en runtime** au moment
   de la génération; documenter le reste.

## L'idée directrice (issue de l'échange)

Le câblage existant (voir/naviguer/onglets/JS brut) donne à l'agent des
**mains**. Ce qui lui manque pour produire mieux, ce sont des **sens** et des
**instruments de mesure**:

- **console + réseau**: un agent qui ne lit ni la console JS ni les requêtes
  en échec debugge un front à l'aveugle. Ce sont les deux feedback loops
  du dev front, et les primitives les plus rentables à ajouter.
- **attente explicite** (`wait`): sans elle, l'agent lit des états
  intermédiaires (SPA, contenu injecté) et tire des conclusions fausses.
- **interaction "trusted"** (`click`/`type` via Input domain, pas `el.click()`
  JS): reproduire ce qu'un utilisateur réel produit, y compris pour les
  frameworks qui filtrent `isTrusted`.
- **audit SEO du DOM rendu** (`seo`): pour iamoni et les audits clients, ce
  qui compte est le DOM final vu par le rendering de Googlebot, pas le HTML
  servi. Une primitive = un contrat SEO extrait en un appel (title, metas,
  canonical, hreflang, JSON-LD, h1, alt, liens).
- **mesure** (`metrics`, poids réseau): objectiver au lieu de constater.
- **état** (`cookies`/`storage`): comprendre et préparer des scénarios
  (sessions, consentement, panier) — avec masquage par défaut, cf. HARNESS.md.

Usecases plus lourds identifiés mais reportés en roadmap (voir ROADMAP.md):
lecture du profiler Symfony via `x-debug-token-link`, interception/mock de
requêtes (Fetch domain), DOM diff avant/après action, émulation device/réseau,
arbre d'accessibilité comme "vision sémantique" low-cost, record/replay de
sessions, Core Web Vitals.

## Contrainte d'exécution Chrome

Les e2e Chrome réel sont désormais un portail bloquant: `make test-e2e` et
`make proof` échouent si aucun binaire Chrome/Chromium n'est disponible dans
le `PATH`. Les tests démarrent leur propre profil headless jetable et ne
s'attachent pas au Chrome personnel déjà ouvert.

## Décisions techniques et leurs raisons

| Décision | Raison |
|---|---|
| Python 3.11+, stdlib + `websockets` seul | zéro framework, lisible, installable partout; l'outillage crawler de Léo est déjà en Python |
| Client **sync** (`websockets.sync`) | un CLI est séquentiel; pas d'asyncio à propager dans les primitives ni les tests |
| Connexion directe au `webSocketDebuggerUrl` du target page | même modèle que l'outillage existant de Léo; pas de sessions flatten à gérer |
| Mock CDP qui **enregistre les commandes** | tester le protocole émis, pas seulement la sortie: une régression de params CDP casse un test, pas une session de dev |
| Découverte HTTP et WS sur deux ports dans le mock | simplicité; le client suit l'URL publiée par /json, donc compat totale avec le vrai Chrome (un seul port) |
| `/json/new` en PUT avec fallback GET | Chrome ≥ 111 exige PUT; les vieux headless acceptent GET |
| Cookies masqués par défaut | un agent recopie ses sorties; une session ne doit pas fuiter par accident |
| Fixtures HTML sans aucune ressource externe ni aléa | déterminisme total; les seuls délais sont explicites (`/api/slow?ms=`, setTimeout 300ms de spa.html) |
| `Input.dispatch*` plutôt que `el.click()` | évènements trusted, pipeline navigateur réel |
| Sortie CLI = un objet JSON | parsable par l'agent, lisible par l'humain, diffable dans les logs |
