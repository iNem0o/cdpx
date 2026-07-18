# Casts de démonstration de la homepage

Ces fichiers asciicast v2 sont rejoués par asciinema-player dans
`site/index.html`. Ils ne sont pas des maquettes : chaque sortie JSON et
chaque durée proviennent de commandes réellement exécutées contre un
Chrome réel et le site témoin du dépôt (`tests/fixtures/`). Seule la
frappe clavier est synthétisée (cadence déterministe) pour la lisibilité.

## Génération scriptée

Le protocole complet vit dans `scripts/site_casts/` :

```bash
# depuis la racine du dépôt, Chrome/Chromium + Docker installés
make site-casts                                  # tout, app Symfony comprise

# ou à la main, sans le scénario profiler :
python3 scripts/site_casts/generate.py list      # catalogue des scénarios
python3 scripts/site_casts/generate.py record    # (ré)enregistre sur :8899
python3 scripts/site_casts/generate.py check     # valide format + interdits
```

`record` démarre pour chaque scénario le serveur de fixtures et une session
supervisée jetable (`authority privileged`, origines loopback), exécute les
commandes du scénario, vérifie leurs attentes (`expect`, code de sortie) et
n'écrit le cast que si tout est vert. `--only id,id` et `--port N` permettent
d'enregistrer un sous-ensemble ou d'éviter un port occupé.

Chaque scénario est un module de `scripts/site_casts/scenarios/` : étapes
`Comment` (ligne `#` pédagogique), `Cmd` (commande cdpx) ou `Shell`
(pipeline bash, ex. `jq -e`). Ajouter un cast = ajouter un module, l'inscrire
dans le registre, puis l'intégrer à `site/index.html`.

## Casts publiés

| Cast | Groupe | Commandes couvertes |
|---|---|---|
| `session.cast` | Session | session start --export / status / stop, version, tabs list |
| `nav.cast` | Navigation | goto, wait, count, text |
| `read.cast` | Lecture | text, html, count, eval, frame |
| `act.cast` | Interaction | type --secret-env, click, key, dom-diff |
| `observe.cast` | Observabilité | console --duration, network, metrics |
| `capture.cast` | Capture | screenshot, pdf, a11y |
| `state.cast` | État | storage, cookies get/set/clear --value-env |
| `seo.cast` | SEO | seo (+ jq sur findings, page conforme vs cassée) |
| `perf.cast` | Performance | vitals --click, coverage, budget jq -e |
| `journey.cast` | Parcours | record ×3, replay, scenario run |
| `resilience.cast` | Résilience | intercept "*api* => 503", text, emulate |
| `profiler.cast` | Profiler Symfony | profiler --panels db,cache (variantes saine et N+1, porte jq -e) |

Le scénario `profiler` s'enregistre contre la vraie app Symfony témoin
(`tests/symfony-app`) : `make site-casts` la démarre via l'overlay
`docker-compose.site-casts.yml` (loopback :8025) et passe `--symfony-base`
au générateur. Sans base fournie, il est sauté proprement (`skipped`),
et `check` ne le considère en erreur que si son cast est présent mais
invalide.

Les valeurs de session visibles dans les sorties (identifiants, chemins
`/run/user/...`) appartiennent à des sessions jetables détruites après
l'enregistrement. Les scénarios déclarent leurs valeurs interdites
(`forbidden`) : une fuite de secret fait échouer la génération.
