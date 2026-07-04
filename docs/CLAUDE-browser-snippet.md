# Snippet CLAUDE.md — outillage navigateur cdpx

Utiliser cdpx uniquement contre un Chrome de dev à profil jetable:

```bash
chromium --headless=new --remote-debugging-port=9222 \
  --user-data-dir=$(mktemp -d /tmp/cdpx-XXXX) --no-first-run
```

Contrat agentique:
- stdout JSON compact par défaut; `--pretty` seulement pour lecture humaine.
- Sorties volumineuses bornées; utiliser `--full` seulement si nécessaire.
- Streams/traces en NDJSON compact (`console --follow`, `record`).
- Cookies masqués par défaut; ne pas utiliser `--show-values` dans un log partagé.

Commandes de boucle:

```bash
cdpx goto http://app.test/
cdpx console --follow --max 20
cdpx network http://app.test/checkout
cdpx profiler http://app.test/profiler-target
cdpx dom-diff -- click "#submit"
cdpx seo http://app.test/produit
```

Garde-fou autonome:

```bash
export CDPX_ORIGINS="http://*.test,http://localhost:*,http://127.0.0.1:*"
```

Avec `CDPX_ORIGINS`, les lectures restent permises, mais les mutations
(`click`, `type`, `eval`, `intercept`, `replay`) sont refusées hors allowlist.
