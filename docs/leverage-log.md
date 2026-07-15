# Journal de retours d'expérience

Ce journal conserve les pièges techniques réutilisables sans chemin local ni
dépendance à un outil privé. Une clé de session est uniquement une référence
Git publique utilisée pour empêcher les doublons.

- Session-Key: master@b647d66
  - Symptom: les tests de packaging passaient sur l'hôte mais échouaient dans
    `docker-check` lorsqu'ils lisaient les politiques `.gitignore` et
    `.dockerignore` absentes de l'image.
  - Root cause (missing capability): l'image de validation ne copiait pas tous
    les fichiers de politique que ses propres tests considèrent comme des
    entrées du harness.
  - Fix encoded (doc/script/lint): le Dockerfile copie les politiques publiques;
    `.dockerignore` exclut les workspaces non suivis et le test de packaging
    verrouille cette reproduction.
  - Verification (commande/CI): `make docker-check` puis `make release` verts.

- Session-Key: agent/github-integration-hardening@cdc4868
  - Symptom: `make check` était vert sur GitHub, puis le Chrome relancé par
    `make proof` annonçait DevTools mais les 32 E2E expiraient pendant la
    découverte de `127.0.0.1`.
  - Root cause (missing capability): la découverte HTTP loopback héritait des
    proxys du runner et son délai de readiness de 10 secondes était trop court
    pour diagnostiquer proprement un démarrage chargé.
  - Fix encoded (doc/script/lint): les appels CDP loopback utilisent une
    connexion urllib directe sans proxy, le délai reste borné à 30 secondes et
    un test force un proxy mort sans casser la découverte mock.
  - Verification (commande/CI): `make release` local vert, puis runs GitHub
    `29161949162` et `29162518918` verts avec `PR Gate / Required`.

- Session-Key: agent/github-integration-hardening@3547736
  - Symptom: `make proof` échouait alors que les tests passaient, car deux noms
    de tests record/replay ne correspondaient plus aux globs de preuve; la
    première passe `make cov` restait aussi sous le seuil à cause du superviseur.
  - Root cause (missing capability): le cockpit relie les preuves aux node IDs
    pytest et les branches bootstrap/readiness/signaux de session manquaient de
    couverture déterministe.
  - Fix encoded (doc/script/lint): les node IDs record/replay sont réalignés et
    des tests unitaires bornés couvrent démarrage, erreurs, readiness et teardown
    du superviseur sans Chrome réel.
  - Verification (commande/CI): `make proof` vert; `make cov` vert à 85,69 %.

- Session-Key: agent/github-integration-hardening@0c4353d
  - Symptom: [HIGH] cette standardisation transverse a dépassé deux heures sans
    ExecPlan suivi dans le dépôt; la première passe release a aussi interrompu
    un arrêt de session E2E après 20 secondes alors que le CLI en autorise 30.
  - Root cause (missing capability): le dépôt ne fournit ni `PLANS.md` ni
    répertoire `docs/exec-plans/`, et le timeout du wrapper E2E était plus court
    que celui du contrat qu'il vérifie.
  - Fix encoded (doc/script/lint): le wrapper E2E attend désormais 45 secondes
    et le contrat supervisé est verrouillé par les features et le cockpit;
    l'absence d'ExecPlan reste à traiter dans une évolution dédiée du harness.
  - Verification (commande/CI): test E2E ciblé de session puis `make release`
    verts, cockpit à 551/551 tests sans violation ni avertissement.

- Session-Key: agent/github-integration-hardening@7b7f4c0
  - Symptom: sur GitHub, `make check` était vert puis le Chrome supervisé froid
    relancé par `make proof` expirait après 30 secondes; le teardown supprimait
    `supervisor.log` et `chrome-stderr.log` avant que le gate puisse les montrer.
  - Root cause (missing capability): le parent et le superviseur partageaient le
    même timeout sans marge ni deadline globale, et Chrome utilisait le `/dev/shm`
    contraint du runner CI sans adaptation.
  - Fix encoded (doc/script/lint): le bootstrap possède un budget dédié borné,
    une deadline partagée avec marge parent, `--disable-dev-shm-usage` en CI et
    des tails privés, bornés et expurgés capturés avant le teardown.
  - Verification (commande/CI): tests unitaires ciblés, E2E lifecycle sur Chrome
    réel, `make check-local` et `make release` locaux verts.

- Session-Key: agent/github-integration-hardening@336e519
  - Symptom: le cockpit affichait les sources Mermaid sans SVG dans Chrome réel;
    puis `docker-check` échouait en lisant une notice tierce absente de l'image.
  - Root cause (missing capability): un échappement global de `</` corrompait
    des expressions régulières du bundle minifié, et le contexte Docker ne
    reproduisait pas encore toutes les entrées du test de packaging.
  - Fix encoded (doc/script/lint): l'inclusion vérifie le SHA-256 et refuse
    uniquement une fermeture `</script`; l'E2E exige quatre SVG hors ligne, et
    le Dockerfile ainsi que le test de packaging verrouillent la notice tierce.
  - Verification (commande/CI): E2E cockpit ciblé, `make check`, `make proof`
    et `make dist` verts.

- Session-Key: agent/github-integration-hardening@bc45078
  - Symptom: le rapport généré levait `Unexpected token ';'` car des marqueurs
    `cdpx-redacted` apparaissaient au milieu du bundle Mermaid minifié.
  - Root cause (missing capability): le rapport entier, code statique compris,
    repassait deux fois dans des détecteurs conçus pour du texte libre; `data:`
    dans une propriété JavaScript était confondu avec une Data URL.
  - Fix encoded (doc/script/lint): le résumé dynamique est redacted avant rendu,
    le rapport pré-nettoyé traverse le staging sans mutation et le détecteur de
    Data URL exige désormais un en-tête conforme; le scan de canaris reste final.
  - Verification (commande/CI): tests redaction et staging, E2E Chrome sur le
    rapport partageable, `make check`, `make proof`, `node --check` et `cmp` verts.

## Réponses CDP croisées pendant une interception

- **Symptôme :** `Page.navigate` expirait dans Chrome Docker lorsque Fetch
  suspendait la requête document avant la réponse CDP de navigation.
- **Cause :** le client synchrone perdait les réponses de commandes consommées
  pendant le traitement d'évènements bloquants.
- **Correction durable :** `CDPClient.wait_response()` et un buffer de réponses
  permettent d'envoyer la navigation, traiter `Fetch.requestPaused`, puis
  récupérer la réponse correspondante.
- **Vérification :** le test d'interception Chrome réel et les portails
  `make check`, `make proof` et `make release` couvrent ce chemin.

## Focus visible sous Chrome headless

- **Symptôme :** un contrôle RGAA fondé uniquement sur
  `getComputedStyle(...).outlineStyle` variait sous Chrome headless.
- **Cause :** l'intention CSS ne constituait pas un contrat machine stable pour
  la fixture déterministe.
- **Correction durable :** l'application Symfony témoin expose aussi un
  marqueur `data-focus-visible`, vérifié par l'E2E tout en conservant le style
  de focus réel.
- **Vérification :** `make docker-symfony-e2e` puis `make proof`.

Ajouter une entrée uniquement lorsqu'un écart runtime produit une connaissance
généralisable et une vérification reproductible.
