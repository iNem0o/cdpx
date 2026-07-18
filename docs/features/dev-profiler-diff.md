+++
id = "dev-profiler-diff"
title = "Developer diagnostics"
status = "validated"
summary = "Parse the Symfony Web Profiler panels (Doctrine, Twig, cache, exceptions, HTTP client, Messenger, routing, time, logs) from a browser navigation, then compare the DOM before/after an action."
entrypoints = ["cdpx profiler", "cdpx dom-diff", "make docker-symfony-e2e"]
path_globs = ["src/cdpx/primitives/dev.py", "src/cdpx/primitives/profiler/", "tests/fixtures/profiler/**", "tests/fixtures/form.html", "docker-compose.symfony-e2e.yml", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**", "tests/test_profiler_panels.py", "src/cdpx/primitives/profiler/*.py"]
test_globs = ["tests/test_profiler_panels.py::*", "tests/test_primitives.py::test_profiler*", "tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_profiler*", "tests/test_cli.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*", "tests/e2e/test_e2e_symfony.py::*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M2-boucle-symfony.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-profiler"
title = "Read the Symfony profiler from a browser navigation"
entrypoint = "cdpx profiler"

[[journeys]]
id = "compare-profiler-variants"
title = "Compare deterministic variants of the Symfony profiler"
entrypoint = "make docker-symfony-e2e"

[[journeys]]
id = "diff-dom-action"
title = "Compare the DOM before and after an action"
entrypoint = "cdpx dom-diff"

[[scenarios]]
id = "read-symfony-profiler"
journey = "read-profiler"
title = "Read Symfony profiler data from a navigation"
ui_text = "The agent can open a Symfony page and follow the profiler proof."
report_text = "This scenario proves that framework diagnostics are reachable from a browser navigation. `make proof` automatically attempts the real Docker Symfony gate, records an unavailable Docker as an explicit non-blocking status, and blocks the verdict when Docker is available but the Symfony scenario fails."
given = "A fixture or the Symfony test app exposes profiler-style headers and pages."
when = "cdpx reads the profiler data after navigation during the Chrome e2e and, when Docker is available, via the Symfony e2e gate."
then = "The report links the profiler tests, the Docker status, JUnit, logs, the profiler JSON output and the screenshots to the developer diagnostics feature."
tests = ["tests/test_profiler_panels.py::*", "tests/test_primitives.py::test_profiler*", "tests/test_cli.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_symfony.py::*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compare-symfony-profiler-variants"
journey = "compare-profiler-variants"
title = "Compare the Symfony profiler variants"
ui_text = "The report compares deterministic variants of the Symfony profiler."
report_text = "This scenario proves that baseline/degraded, Doctrine-style N+1, bursts of duplicate queries, cache hit/miss/expired, Twig render cost, Stopwatch sections, HTTP client issues, Messenger messages, routing issues and response cache headers are read from the real WebProfiler panels and available as structured Symfony proofs."
given = "The Symfony test app exercises real collectors (Doctrine, cache, HTTP client, Messenger...) under `/scenario/profiler/{case}`."
when = "cdpx navigates each case, follows the real WebProfiler token and parses the panel HTML (db, twig, cache, exception, http_client, messenger, router, time, logger)."
then = "The report links the sanitized JSON proofs, the Docker logs, JUnit and the private screenshots to the developer diagnostics feature without exposing the profiler token."
tests = ["tests/e2e/test_e2e_symfony.py::test_profiler_compares_deterministic_symfony_variants"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "diff-dom-after-action"
journey = "diff-dom-action"
title = "Compare the DOM before and after a browser action"
ui_text = "The report explains what changed in the DOM after an action."
report_text = "This scenario proves that DOM changes can be compared around a controlled browser action and reviewed as developer proof."
given = "A fixture page has a stable before-state and a user action that mutates the DOM."
when = "cdpx records the DOM before and after the action."
then = "The diff is available as structured test proof with browser screenshots for e2e coverage."
tests = ["tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "symfony-front-state-regression"
journey = "diff-dom-action"
title = "Compare the Symfony front-end state before and after an action"
ui_text = "The report shows a deterministic Symfony front-end state transition."
report_text = "This scenario proves that a Symfony route can expose a controlled front-end state and that cdpx can capture the DOM diff after a browser action."
given = "The Symfony scenario engine exposes `/scenario/front/states`."
when = "cdpx captures the DOM, clicks the state-transition button and captures the DOM again."
then = "The DOM diff and the screenshot are attached as Symfony proofs."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_front_state_dom_diff"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intent

Give framework-aware diagnostic feedback without forcing the agent to
manually pick apart a full browser session. `cdpx profiler` surfaces the
Symfony WebProfiler data from a simple navigation; `cdpx dom-diff` turns
"what changed on screen?" into a stable, reviewable DOM diff; `make
docker-symfony-e2e` proves it all against a real Symfony application under
Docker.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx profiler`

Synopsis: `cdpx profiler url [--settle S] [--panels LIST|all|none]`

Navigates to `url`, looks for the `X-Debug-Token-Link` header in network
responses (falling back to `X-Debug-Token` by rebuilding the
`/_profiler/<token>` URL), then fetches the Web Profiler panel pages
**from the page itself** (same-origin `fetch()`: browser cookies and host
resolution, essential behind Docker or a port-forward) and parses their
HTML. Since the WebProfilerBundle exposes no JSON API, cdpx extracts a
structured contract per panel: `db` (queries, distinct statements,
duplicates, SQL list), `twig` (template calls, blocks, macros), `cache`
(hits/misses/writes, per pool), `exception` (class/message), `http_client`
(outgoing requests, statuses), `messenger` (messages dispatched per bus),
`router` (route, controller, status, redirect), `time` (total/init time,
best-effort timeline) and `logger` (errors, warnings, deprecations).

Command-specific options:

- `url` (positional, required) — the Symfony app route to profile.
- `--settle S` — time window in seconds for collecting network events
  after load, giving the response carrying the token time to arrive
  (default: 0.2).
- `--panels` — `all` (default, all 9 panels), `none` (token probe only,
  no panel fetch) or a CSV list
  (`router,time,db,twig,cache,exception,http_client,messenger,logger`);
  an unknown name is a usage error (exit 2).

```bash
# Parser tous les panels d'une route locale
cdpx profiler http://127.0.0.1:8000/produit/42

# Cibler la boucle Doctrine + cache uniquement
cdpx profiler http://127.0.0.1:8000/produit/42 --panels db,cache
```

Output (realistic excerpt, truncated to the requested panels):

```json
{
  "token_present": true,
  "url": "http://127.0.0.1:8000/produit/42",
  "status": 200,
  "profiler_url": "http://127.0.0.1:8000/_profiler/***",
  "profiler_status": 200,
  "response_headers": {"x-debug-token-link": "http://127.0.0.1:8000/_profiler/***"},
  "panels": {
    "db": {
      "available": true,
      "queries": 6,
      "statements": 2,
      "duplicates": 4,
      "time_ms": 1.76,
      "list": [{"sql": "SELECT ... FROM book b0_", "duration_ms": 0.42}]
    },
    "cache": {
      "available": true,
      "calls": 4,
      "hits": 3,
      "misses": 1,
      "writes": 1,
      "deletes": 0,
      "pools": {"app.scenario_pool": {"calls": 4, "hits": 3, "misses": 1, "writes": 1, "deletes": 0, "reads": 4}}
    }
  }
}
```

Gotchas and error cases:

- **Breaking change** (post-0.1.0): the `signals` fields (`X-CDPX-*`
  headers) and `profiler_bytes` are gone; `panels` is now a structured
  object per panel, never a `raw` envelope.
- The raw token is never returned: the output only exposes
  `token_present`, redacts the segment in `profiler_url` and sanitizes
  headers, URL/query, SQL/messages and results a second time at the
  stdout boundary.
- If no response carries `X-Debug-Token-Link` or `X-Debug-Token`
  (profiler disabled, `prod` environment), the command fails with
  `header X-Debug-Token-Link/X-Debug-Token introuvable` (exit 1).
- A panel whose collector isn't installed (no doctrine-bundle, no
  messenger...) outputs `{"available": false}` — this is not an error. A
  panel that is present but has unexpected markup outputs
  `{"available": true, "parse_error": ...}`: parsing never raises.
- Parsing is coupled to the WebProfilerBundle 7.x HTML markup (metric
  label/value blocks, tables). A major Symfony version can move it: the
  committed fixtures (`tests/fixtures/profiler/`) pin the contract and
  their README documents the re-capture.
- Durations (`*_ms`) are indicative only; only assert counts, classes,
  routes and statuses.
- A `--settle` that's too short means a missed token if the response
  arrives late; increase the window rather than retrying in a loop.

### `cdpx dom-diff`

Synopsis: `cdpx dom-diff -- <action>`

Takes a normalized DOM snapshot (tags, id, sorted classes, `data-*`
attributes, text), runs **one** action, takes another snapshot, then
renders a stable unified diff. Use case: check that a click actually opens
the cart off-canvas, that a submit shows the expected error, that an SPA
route swaps the right fragment — without re-reading two full HTML pages.

The accepted actions come from the shared interpreter
(`src/cdpx/primitives/actions.py`), the same one used by `record`,
`replay` and `emulate`:

- `goto <url>` — navigate.
- `wait <selector>` — wait for a CSS selector.
- `click <selector>` — click an element.
- `type <selector> <text> [--clear]` — type non-sensitive text (the
  `--clear` option empties the field first). Secrets belong to the
  dedicated surfaces that accept an environment reference.
- `key <key>` — press a key (Enter, Tab, Escape, ArrowUp/Down).
- `eval <js>` — evaluate JavaScript.

Command-specific options:

- `action` (positional, rest of the line) — the action to bracket; the
  `--` separator is supported and recommended to isolate the action from
  cdpx's own options.

```bash
# Le clic ouvre-t-il l'off-canvas panier ?
cdpx dom-diff -- click "#offcanvas-cart"

# Diff entre la page courante et une autre route (lecture pure)
cdpx dom-diff -- goto http://127.0.0.1:8000/panier

# Does typing trigger the autocomplete?
cdpx dom-diff -- type "#recherche" "chaussures trail" --clear
```

Output:

```json
{
  "action": ["click", "#offcanvas-cart"],
  "changed": true,
  "diff": [
    "--- before",
    "+++ after",
    "@@ -12,6 +12,9 @@",
    "   <div#offcanvas-cart.cart>",
    "+    <div.cart-panel.open>",
    "+      \"1 article - 89,00 EUR\""
  ],
  "lines": 6
}
```

Gotchas and error cases:

- **Security**: the allowlist is mandatory and authority follows the
  action (`eval` requires `privileged`), including for reads. Never pass
  a secret to the composed `type` action; use `cdpx type --secret-env` or
  a scenario with `secret_ref`.
- A missing or unknown action fails with the interpreter's usage reminder
  (exit 2 for a usage error).
- `changed: false` with `diff: []` is a valid result: the action mutated
  nothing — useful for detecting a dead button.
- The diff is bounded by `--limit` (50 lines by default); pass `--full`
  for a complete diff on large mutations.

### `make docker-symfony-e2e`

Synopsis: `make docker-symfony-e2e`

Runs the profiler e2e suite against a **real** Symfony application served
by Docker (`docker-compose.symfony-e2e.yml` + `tests/symfony-app/`): the
scenario controllers exercise **real collectors** (real Doctrine queries
on SQLite — N+1 and duplicates included —, cache pool, HTTP client to
local endpoints, Messenger messages, exceptions and redirects) under
`/scenario/profiler/{case}`, and `/scenario/front/states` for the DOM
diff. This is the proof that `cdpx profiler` parses the real WebProfiler
panels, not just the committed HTML fixtures.

Command-specific options: none (a parameterless Make target; Docker and
Docker Compose must be installed and startable).

```bash
make docker-symfony-e2e
```

The resulting proofs land in `.proof/` (`symfony-e2e.log`,
`symfony-e2e-junit.xml`, profiler comparison JSON, DOM diff JSON,
screenshots).

Gotchas and error cases:

- Docker missing: the target and the release proof fail with an explicit
  `unavailable` status; there is no degraded release success.
- Docker present but the Symfony scenario fails: `make proof`'s overall
  verdict is blocked — a real failure is never disguised as absence.
- The first run builds the Symfony image: expect an initial build time
  before the scenarios execute.

## User journeys

- Navigate to a Symfony route, follow the profiler token and read the
  parsed panels (Doctrine, Twig, cache, exceptions, HTTP client,
  Messenger, routing, time, logs).
- Compare baseline/degraded, Doctrine-style N+1, bursts of duplicate
  queries, cache hit/miss/expired, Twig render cost, Stopwatch sections,
  HTTP client issues, Messenger messages, routing issues and response
  cache headers — from the real panels.
- Take a stable DOM diff around a browser action.

## Validation

The panel parsers are unit-tested against committed HTML
(`tests/fixtures/profiler/`, trimmed real WebProfilerBundle markup), also
served by the fixture server for the Chrome e2e. `make proof` runs the
Docker Symfony gate: unavailability, skip or failure blocks the verdict.

## Proofs

The expected local proofs are JUnit and private screenshots for the
Chrome scenarios. The Symfony gate adds `.proof/symfony-e2e.log`,
`.proof/symfony-e2e-junit.xml`, the profiler diagnostics comparison JSON,
the DOM diff JSON and browser screenshots. Opaque screenshots stay out of
`.proof/shareable/`.

## Known limitations

Docker availability depends on the environment; its absence blocks the
proof and is resolved by installing Docker then re-running `make proof`
or `make docker-symfony-e2e`. Panel parsing is coupled to the
WebProfilerBundle 7.x HTML markup (no JSON API exists on the Symfony
side): a major bundle evolution may require re-capturing the fixtures
and adjusting the parsers — the tolerance contract (`available`/
`parse_error`, never an exception) guarantees that in the meantime the
command degrades cleanly instead of breaking.
