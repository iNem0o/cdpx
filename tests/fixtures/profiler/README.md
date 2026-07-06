# Fixtures HTML du Web Profiler Symfony

Ces fichiers figent le markup des panels du WebProfilerBundle (Symfony 7.3)
tel que parsé par `src/cdpx/primitives/profiler_panels.py`. Ils sont servis par
le serveur de fixtures sur `/_profiler/<token>?panel=<nom>` (le nom du fichier
est la valeur du paramètre `panel`; `exception-raised.html` n'est utilisé que
par les tests de parsing).

Provenance: capturés depuis l'app témoin `tests/symfony-app/` lancée via
`make docker-symfony-e2e`, puis élagués (sidebar + contenu du panel, sans CSS
ni JS d'interface). Les valeurs sont choisies distinctives et assertées à
l'identique par `tests/test_profiler_panels.py` — ne pas les modifier sans
adapter les tests.

Re-capture (si le markup du WebProfilerBundle évolue):

```
make docker-symfony-e2e            # ou lancer l'app témoin à la main
TOKEN=$(curl -si http://localhost:8000/profiler-target | sed -n 's/^X-Debug-Token: //p' | tr -d '\r')
curl "http://localhost:8000/_profiler/$TOKEN?panel=db" > db.html   # etc.
```

Puis élaguer à la main, relancer `make check` et ajuster les parseurs si un
marqueur a bougé.
