# M2 — Boucle de dev Symfony/Shopware

## Pourquoi
C'est le milestone à plus forte valeur métier: transformer cdpx en boucle de
feedback complète pour le dev Symfony. Un agent qui lit le profiler après
chaque action détecte le N+1, la requête à 2s, l'exception avalée — sans
que l'humain ouvre quoi que ce soit.

## Primitives
### cdpx profiler
- Comment: activer Network, naviguer/agir, lire le header `x-debug-token-link`
  de la réponse principale (Network.responseReceived -> response.headers,
  redirectResponse pour les 302), puis fetch
  `http://app.test/_profiler/{token}?panel=db` et parser le HTML des panels.
- Note d'évolution (post-M2): l'API JSON du profiler évoquée à l'origine
  n'existe pas côté Symfony — l'adaptateur réel parse le HTML des panels
  (voir `src/cdpx/primitives/profiler_panels.py`) et le fetch se fait DEPUIS
  la page (fetch même origine), pas depuis cdpx: cookies et résolution d'hôte
  du navigateur, indispensable derrière Docker.
- Sortie: {token, url, panels: {db: {queries, statements, duplicates, ...},
  twig, cache, exception, http_client, messenger, router, time, logger}}.
- Fixture: le serveur de fixtures gagne un endpoint `/api/profiler-sim` qui
  émet le header et sert des panels HTML figés (markup WebProfilerBundle
  réel élagué) -> testable sans Symfony.
- Test mock: scripter Network.responseReceived avec le header; vérifier le
  fetch page-context des panels et leur parsing.

### cdpx console --follow
- Comment: boucle collect_events sans durée fixe, sortie NDJSON (1 ligne =
  1 entrée), arrêt Ctrl-C ou --max n. Contrat: NDJSON sur stdout est une
  EXCEPTION documentée au "un objet JSON".

### cdpx dom-diff
- Comment: `snapshot avant` (sérialisation normalisée: tag, id, classes triées,
  attributs data-*), action, `snapshot après`, diff unifié.
- Usecase: "qu'est-ce que ce click a changé dans le DOM ?" — réponse exacte.
- Fixture: form.html suffit (data-state passe de idle à submitted).

## Definition of Done
- [x] profiler testé contre fixture simulant le header + contre un vrai
      Symfony demo (`make docker-symfony-e2e`: vrais collecteurs Doctrine,
      cache, HTTP client, Messenger — panels parsés, plus aucun signal
      X-CDPX fabriqué)
- [ ] follow: NDJSON documenté dans PRIMITIVES.md
- [ ] dom-diff: diff stable (2 runs = même diff)
