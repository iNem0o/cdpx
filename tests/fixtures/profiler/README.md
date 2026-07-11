# Fixtures HTML du Web Profiler Symfony

Ces fichiers figent le markup des panels du WebProfilerBundle (Symfony 7.3)
tel que parsé par `src/cdpx/primitives/profiler_panels.py`. Ils sont servis par
le serveur de fixtures sur `/_profiler/<token>?panel=<nom>` (le nom du fichier
est la valeur du paramètre `panel`; `exception-raised.html` n'est utilisé que
par les tests de parsing).

Provenance: structures calquées sur des captures réelles de l'app témoin
`tests/symfony-app/` (WebProfilerBundle 7.3, lancée via
`make docker-symfony-e2e`), puis élaguées: sidebar + contenu du panel, sans
CSS ni JS d'interface, en conservant les marqueurs porteurs de sens (blocs
`metric`, onglets `tab-title` + badge des pools et clients HTTP, rangées
mixtes `<th>clé</th><td>valeur</td>` des panels request/messenger, dumps
`sf-dump` avec leur `<script>` parasite, spans
`status-response-status-code`, badges des filtres du panel logger). Les
valeurs sont choisies distinctives et assertées à l'identique par
`tests/test_profiler_panels.py` — ne pas les modifier sans adapter les tests.

Ces structures adaptées restent soumises à la licence MIT du
WebProfilerBundle. La notice de copyright et le texte de licence upstream sont
conservés dans [`LICENSE.SYMFONY`](LICENSE.SYMFONY).

Re-capture (si le markup du WebProfilerBundle évolue):

```
make docker-symfony-e2e            # ou lancer l'app témoin à la main
TOKEN=$(curl -si http://localhost:8000/profiler-target | sed -n 's/^X-Debug-Token: //p' | tr -d '\r')
curl "http://localhost:8000/_profiler/$TOKEN?panel=db" > db.html   # etc.
```

Puis élaguer à la main, relancer `make check` et ajuster les parseurs si un
marqueur a bougé.
