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
