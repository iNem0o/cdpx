# Snippet CLAUDE.md — cdpx browser tooling

Use cdpx only against a dev Chrome with a disposable profile:

```bash
chromium --headless=new --remote-debugging-port=9222 \
  --user-data-dir=$(mktemp -d /tmp/cdpx-XXXX) --no-first-run
```

Agentic contract:
- compact stdout JSON by default; `--pretty` only for human reading.
- Large outputs bounded; use `--full` only when necessary.
- Streams/traces in compact NDJSON (`console --follow`, `record`).
- Cookies redacted by default; do not use `--show-values` in a shared log.

Loop commands:

```bash
cdpx goto http://app.test/
cdpx console --follow --max 20
cdpx network http://app.test/checkout
cdpx profiler http://app.test/profiler-target
cdpx dom-diff -- click "#submit"
cdpx seo http://app.test/produit
```

Autonomous guard:

```bash
export CDPX_ORIGINS="http://*.test,http://localhost:*,http://127.0.0.1:*"
```

With `CDPX_ORIGINS`, reads remain permitted, but mutations
(`click`, `type`, `eval`, `intercept`, `replay`) are refused outside the allowlist.
