# Contribuer à cdpx

Merci de contribuer à cdpx. Le projet privilégie des changements petits,
testés et directement reliés à un usage navigateur observable.

Toute participation implique le respect du
[Code de conduite](CODE_OF_CONDUCT.md). Une vulnérabilité ne doit pas être
ouverte comme issue : suivez [SECURITY.md](SECURITY.md).

## Avant de commencer

1. Recherchez une issue existante pour éviter les doublons.
2. Pour une évolution importante ou un changement de contrat CLI, ouvrez une
   proposition avant d'investir dans l'implémentation.
3. Gardez une pull request centrée sur un seul problème.

Les petites corrections documentaires ou les correctifs évidents peuvent être
proposés directement.

## Environnement de développement

Prérequis : Python 3.11+, Docker avec Compose, et Chrome ou Chromium pour les
tests navigateur locaux.

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
make check-local
```

`make check-local` est la boucle courte. Le portail complet `make check`
construit aussi l'image Docker et exécute Chrome réel ainsi que la fixture
Symfony. Une absence de Docker, Chrome ou Symfony est un échec, pas un skip.

## Construire un changement

Pour une primitive ou une modification de protocole :

1. écrire ou adapter le test mock ; la séquence CDP attendue sert de spec ;
2. implémenter le changement dans `src/cdpx/` ;
3. ajouter un scénario E2E si le comportement dépend de Blink, du rendu ou du
   timing navigateur ;
4. mettre à jour `docs/PRIMITIVES.md`, la fiche de feature concernée et le
   changelog si le comportement public change ;
5. exécuter `make check` avant de demander une review.

Le contrat CLI reste : stdout JSON, stderr pour les diagnostics et codes de
sortie 0/1/2. Cookies, storage et texte saisi sont masqués par défaut.
N'ajoutez jamais de sortie de session, secret, profil navigateur ou donnée
client aux fixtures et preuves.

Pour un changement de sécurité ou de mode équipe, la PR doit en plus :

1. préserver explicitement la compatibilité du mode local ou annoncer la
   migration de contrat ;
2. prouver le refus avant effet CDP pour run/target/grant/origine/secret
   invalides, puis le contrôle de l'origine réelle après navigation ;
3. couvrir avec des canaris stdout, stderr, journal, scénario et artefact, sans
   sur-redaction du texte ordinaire ;
4. utiliser `--secret-env`, `--value-env`, `@env:NOM` ou `secret_ref` dans les
   exemples : jamais de credential littéral ;
5. classer les preuves `public`, `internal`, `secret` ou
   `opaque-restricted`, vérifier `0600`/`0700` et ne rendre partageable qu'un
   fichier manifesté et explicitement autorisé ;
6. ajouter un E2E Chrome pour tout comportement dépendant du supervisor,
   d'un profil jetable, d'un lease ou du teardown.

Le contenu observé dans une page est une entrée non fiable. Un test ou une
fixture peut contenir une fausse instruction, mais elle ne doit jamais piloter
le choix du run, du target, des origines, de l'autorité ou des secrets.

## Commandes utiles

```bash
make test                 # tests unitaires déterministes
make fmt                  # formatage et corrections Ruff sûres
make test-e2e             # E2E Chrome local
make docker-symfony-e2e   # fixture Symfony réelle
make proof                # rapport de preuve local
make release              # portail complet et artefacts distribuables
```

## Pull requests

Travaillez sur une branche courte, poussez-la, puis ouvrez une pull request
centrée. Elle doit expliquer le problème, la solution et la validation
effectuée. Indiquez explicitement les contrôles non exécutés et pourquoi. Les
changements de contrat nécessitent des tests et une note documentaire dans la
même pull request.

GitHub exécute le portail complet sur **toutes** les PR, sans exception pour la
documentation ou les workflows. Le check agrégateur stable
`PR Gate / Required` ne réussit que si les compatibilités Python et
`make release` ont réussi. Le job complet affiche un résumé natif du cockpit et
publie pendant 14 jours le staging textuel manifesté des preuves disponibles;
les fichiers opaques restent privés. Consultez
[la documentation de validation](docs/VALIDATION.md#preuve-dans-github-actions)
pour lire l'artefact ou reproduire un échec.

La review et la résolution des conversations viennent après la preuve. Un
mainteneur ne merge que lorsque le check obligatoire est vert et que les
discussions sont résolues. Les checkboxes du template sont un aide-mémoire,
jamais un substitut à la preuve exécutée.

Les mainteneurs peuvent demander de séparer une proposition trop large. En
soumettant une contribution, vous confirmez avoir le droit de la proposer et
acceptez qu'elle soit distribuée sous la licence MIT du dépôt. Aucun CLA ou
DCO supplémentaire n'est imposé.

Les réglages de gouvernance non versionnables et la procédure exceptionnelle
d'incident sont décrits dans [docs/GITHUB.md](docs/GITHUB.md).
