# Roadmap

Chaque milestone technique possède une fiche détaillée dans
`docs/milestones/`. Un jalon n'est déclaré terminé que lorsque ses preuves mock
et runtime passent par les cibles Make correspondantes.

## M0 — Socle CDP et CLI ✅

Client CDP synchrone, découverte `/json`, contrat CLI JSON/exit 0-1-2, mock
scriptable, serveur de fixtures et premières primitives déterministes.

## M1 — Chrome réel ✅

Les primitives sont exercées contre Blink/V8 avec un profil jetable et les
mêmes fixtures que les tests unitaires. Chrome absent est un échec du portail,
pas un succès dégradé. Voir [M1](milestones/M1-e2e-chrome.md).

## M2 — Boucle Symfony ✅

Profiler WebProfiler, console suivie, diff DOM et scénarios contre une vraie
application Symfony Dockerisée. Voir [M2](milestones/M2-boucle-symfony.md).

## M3 — Interception et émulation ✅

Interception Fetch continue/fulfill/block et profils mobile, réseau et CPU,
validés dans une connexion persistante. Voir
[M3](milestones/M3-interception-emulation.md).

## M4 — SEO, performance et accessibilité ✅

Vitals, arbre d'accessibilité, couverture JS/CSS et audit SEO enrichi du DOM
rendu. Voir [M4](milestones/M4-seo-perf.md).

## M5 — Orchestration et garde-fous ✅

Record/replay, scénarios YAML, iframe, `CDPX_ORIGINS` et budgets d'action. Voir
[M5](milestones/M5-orchestration.md).

## M6 — Distribution technique ✅

Version du paquet, wheel/sdist, image `cdpx-ci`, Compose Symfony et cockpit de
preuve. Ces capacités sont indépendantes de la plateforme d'hébergement. Voir
[M6](milestones/M6-distribution.md).

## M7 — Publication open source GitHub 🚧

Objectif : rendre le dépôt compréhensible, testable et publiable par une
personne extérieure.

- licence MIT et métadonnées publiques cohérentes ;
- GitHub Actions comme CI principale, avec Docker, Chrome et Symfony
  obligatoires ;
- contribution, sécurité, support et modèles GitHub ;
- artefacts de preuve publiés par la CI sans être versionnés ;
- GitHub Release sur tag et publication PyPI par Trusted Publishing ;
- validation finale sur un runner GitHub avant le premier tag public.

Le suivi opérationnel vit dans [TODO.md](TODO.md) et
[RELEASE-PLAN.md](RELEASE-PLAN.md).

## M8 — Sessions supervisées et frontière de confiance ✅

Objectif : rendre l'exécution déterministe lorsque plusieurs agents utilisent
cdpx en parallèle et faire de cette supervision le contrat unique du produit.

- session Chrome supervisée par run : profil jetable, target explicite, lease
  exclusif, TTL/owner et teardown ;
- niveaux `observation`, `interaction`, `privileged`, avec commandes composées
  préflightées et refus par défaut des capacités non classées ;
- identité manifest/run/target et allowlist obligatoires avant discovery,
  contrôle avant/après navigation pour fermer la fenêtre de redirection ;
- suppression de la connexion directe, du target implicite et du lifecycle
  public des targets ; backend mock exercé par le même contrat supervisé ;
- journal v2 et références de secrets, redaction transversale, artefacts privés
  classifiés et staging CI allowlisté ;
- interactions renforcées (`wait_visible`, actionability, hit-test, clear par
  événements) et assertions scénario après drainage final.

Le code, les tests ciblés, le HARNESS, les fiches features, le cockpit de
preuve, `make check`, `make proof` et la validation du paquet distribué avec
31 commandes sont verts.
Voir [M8](milestones/M8-isolation-securite-session.md).
