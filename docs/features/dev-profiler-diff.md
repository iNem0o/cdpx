+++
id = "dev-profiler-diff"
title = "Developer diagnostics"
status = "validated"
summary = "Probe and parse Symfony Web Profiler collectors, including Shopware DAL tags, rules, feature flags, cache tags and opt-in Cart diagnostics, from a browser navigation, then compare the DOM before/after an action."
entrypoints = ["cdpx profiler", "cdpx dom-diff", "./dev test-symfony-e2e", "./dev test-shopware-e2e", "./dev check"]
path_globs = ["src/cdpx/primitives/dev.py", "src/cdpx/primitives/profiler/", "tests/fixtures/profiler/**", "tests/fixtures/form.html", "docker-compose.symfony-e2e.yml", "docker-compose.shopware-e2e.yml", "tests/e2e/test_e2e_symfony.py", "tests/e2e/test_e2e_shopware.py", "tests/symfony-app/**", "tests/shopware-app/**", "tests/test_profiler_panels.py", "src/cdpx/primitives/profiler/*.py"]
test_globs = ["tests/test_profiler_panels.py::*", "tests/test_primitives.py::test_profiler*", "tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_profiler*", "tests/test_cli.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*", "tests/e2e/test_e2e_symfony.py::*", "tests/e2e/test_e2e_shopware.py::*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-profiler"
title = "Read the Symfony profiler from a browser navigation"
entrypoint = "cdpx profiler"

[[journeys]]
id = "compare-profiler-variants"
title = "Compare deterministic variants of the Symfony profiler"
entrypoint = "./dev check"

[[journeys]]
id = "diff-dom-action"
title = "Compare the DOM before and after an action"
entrypoint = "cdpx dom-diff"

[[scenarios]]
id = "parse-profiler-html-contract"
journey = "read-profiler"
title = "Parse authentic profiler HTML contracts"
ui_text = "The parser recognizes the committed profiler panel contracts and their edge cases."
report_text = "This contract scenario proves HTML extraction only; it does not claim that Symfony or Shopware ran."
given = "Versioned profiler HTML snapshots and small malformed edge-case inputs."
when = "The panel parsers extract structured metrics."
then = "The expected metrics and graceful parse failures are returned deterministically."
target = "fixture"
proof_level = "contract"
tests = ["tests/test_profiler_panels.py::*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "exercise-profiler-protocol-contract"
journey = "read-profiler"
title = "Exercise profiler navigation and collector fallback contracts"
ui_text = "The protocol contract covers tokens, panel fetches and ordered collector fallback."
report_text = "This contract scenario proves emitted CDP commands and fallback behavior against the mock, without claiming an external framework runtime."
given = "A scripted CDP peer returns profiler headers and panel responses."
when = "cdpx follows the token and tries collector candidates in order."
then = "The protocol, redaction and timeout behavior match the public contract."
target = "cdp-mock"
proof_level = "contract"
tests = ["tests/test_primitives.py::test_profiler*", "tests/test_cli.py::test_profiler*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "read-symfony-profiler"
journey = "read-profiler"
title = "Read a real Symfony profiler"
ui_text = "The agent opens a real Symfony application and follows its WebProfiler token."
report_text = "The blocking Symfony Docker gate proves WebProfilerBundle compatibility against the installed Symfony runtime."
given = "The Docker gate starts the pinned Symfony application with WebProfilerBundle and Chrome."
when = "cdpx navigates the application and parses the real collector pages."
then = "A passed Symfony runtime proof carries the exact scenario identity, target and proof level."
target = "symfony"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_symfony.py::test_profiler_reads_real_symfony_web_profiler"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "read-shopware-profiler"
journey = "read-profiler"
title = "Read Shopware's real profiler extensions"
ui_text = "The agent probes a real Shopware profiler and reads its DAL, rules, feature flags, cache tags and opt-in Cart collector."
report_text = "The blocking Shopware Docker gate proves collector probing, direct `app.connection_collector` selection, the lightweight Feature Flags panel and the extended Cart panel against Shopware 6.7.13.1."
given = "The Docker gate starts Shopware 6.7.13.1, MariaDB and Chrome in dev mode."
when = "cdpx runs its default lightweight selection, then explicitly requests `shopware_cart`."
then = "The real Shopware collectors report repeated and tagged DAL queries, rules, cache tags, a deterministic feature flag and a real bounded cart/pipeline without exposing profiler or cart secrets."
target = "shopware"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_shopware.py::test_profiler_reads_real_shopware_connection_collector"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "compare-symfony-profiler-variants"
journey = "compare-profiler-variants"
title = "Compare the Symfony profiler variants"
ui_text = "The report compares deterministic variants of the Symfony profiler."
report_text = "This scenario proves that baseline/degraded, Doctrine-style N+1, bursts of duplicate queries, cache hit/miss/expired, Twig render cost, Stopwatch sections, HTTP client issues, Messenger messages, routing issues and response cache headers are read from the real WebProfiler panels and available as structured Symfony proofs."
given = "The Symfony test app exercises real collectors (Doctrine, cache, HTTP client, Messenger...) under `/scenario/profiler/{case}`."
when = "cdpx navigates each case, follows the real WebProfiler token and parses the panel HTML (db, twig, cache, exception, http_client, messenger, router, time, logger)."
then = "The report links the sanitized JSON proofs, the Docker logs, JUnit and the private screenshots to the developer diagnostics feature without exposing the profiler token."
target = "symfony"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_symfony.py::test_profiler_compares_deterministic_symfony_variants"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "diff-dom-contract"
journey = "diff-dom-action"
title = "Compare the DOM before and after a browser action"
ui_text = "The report explains what changed in the DOM after an action."
report_text = "This scenario proves that DOM changes can be compared around a controlled browser action and reviewed as developer proof."
given = "A fixture page has a stable before-state and a user action that mutates the DOM."
when = "cdpx records the DOM before and after the action."
then = "The diff is available as structured test proof with browser screenshots for e2e coverage."
target = "cdp-mock"
proof_level = "contract"
tests = ["tests/test_primitives.py::test_dom_diff*", "tests/test_cli.py::test_dom_diff*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "diff-dom-after-action"
journey = "diff-dom-action"
title = "Compare the DOM around a real Chrome action"
ui_text = "The report explains what changed in the browser DOM after an action."
report_text = "This runtime scenario proves the DOM comparison around a controlled action in real Chrome."
given = "A reference page has a stable before-state and an action that mutates the DOM."
when = "cdpx records the DOM before and after the real browser action."
then = "The structured diff and screenshot show the observed browser transition."
target = "chrome"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_chrome.py::test_dom_diff_real"]
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
target = "symfony"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_front_state_dom_diff"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intent

Give framework-aware diagnostic feedback without forcing the agent to
manually pick apart a full browser session. `cdpx profiler` surfaces Symfony
or Shopware WebProfiler data from a simple navigation; `cdpx dom-diff` turns
"what changed on screen?" into a stable, reviewable DOM diff; `make
docker-symfony-e2e` and `make docker-shopware-e2e` prove the named framework
integrations against real applications under Docker.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx profiler`

Synopsis: `cdpx profiler url [--settle S] [--panels LIST|all|none]`

Navigates to `url`, looks for the `X-Debug-Token-Link` header in network
responses (falling back to `X-Debug-Token` by rebuilding the
`/_profiler/<token>` URL), then fetches the Web Profiler panel pages
**from the page itself** (same-origin `fetch()`: browser cookies and host
resolution, essential behind Docker or a port-forward) and parses their
HTML. Before fetching the requested panels, cdpx parses the profiler menu once
to discover its advertised collector IDs. Those IDs select composable
extensions (`shopware` today; bundle-specific adapters can be added without a
framework-wide branch) and avoid requests for absent collectors. Since the
WebProfilerBundle exposes no JSON API, cdpx extracts a structured contract per
panel: `db` (queries, distinct statements, duplicates, SQL list and leading
SQL tags with an optional source location), `twig` (template calls, blocks,
macros), `cache`
(hits/misses/writes, per pool), `exception` (class/message), `http_client`
(outgoing requests, statuses), `messenger` (messages dispatched per bus),
`router` (route, controller, status, redirect), `time` (total/init time,
best-effort timeline), `logger` (errors, warnings, deprecations),
`shopware_rules` (Rule Builder rules active for the request),
`shopware_cache_tags` (cache-tag emissions and callers),
`shopware_feature_flags` (technical feature configuration) and
`shopware_cart` (bounded cart totals, lines and pipeline topology).

Logical panel names and their output stay stable across supported
applications. The probe maps `db` directly to Symfony's standard `db`
collector or Shopware's compatible `app.connection_collector`. If an older or
unexpected profiler menu cannot be parsed, cdpx retains the bounded candidate
fallback (`db`, then `app.connection_collector`). Unadvertised semantic panels
remain present when requested and return `{"available": false, "status": 0}`.

Command-specific options:

- `url` (positional, required) — the Symfony or Shopware app route to profile.
- `--settle S` — time window in seconds for collecting network events
  after load, giving the response carrying the token time to arrive
  (default: 0.2).
- `--panels` — omitted selects the 12 lightweight/default panels, including
  `shopware_feature_flags` but excluding `shopware_cart`; `all` selects all 13
  panels; `none` performs token discovery without JavaScript, collector probe
  or panel fetch; a CSV list targets only the named semantic panels. The
  complete catalog is
  `router,time,db,twig,cache,exception,http_client,messenger,logger,shopware_rules,shopware_cache_tags,shopware_feature_flags,shopware_cart`.
  An unknown name is a usage error (exit 2).

```bash
# Parse the lightweight/default panels for a local route
cdpx profiler http://127.0.0.1:8000/product/42

# Focus on Doctrine and cache only
cdpx profiler http://127.0.0.1:8000/product/42 --panels db,cache

# Opt into the heavier Shopware Cart HTML
cdpx profiler http://127.0.0.1:8000/checkout/cart --panels shopware_cart
```

Output (realistic excerpt, truncated to the requested panels):

```json
{
  "token_present": true,
  "url": "http://127.0.0.1:8000/product/42",
  "status": 200,
  "profiler_url": "http://127.0.0.1:8000/_profiler/***",
  "profiler_status": 200,
  "response_headers": {"x-debug-token-link": "http://127.0.0.1:8000/_profiler/***"},
  "profile": {
    "engine": "symfony_web_profiler",
    "probed": true,
    "extensions": ["shopware"],
    "collectors": {
      "items": ["request", "time", "app.connection_collector"],
      "total": 26,
      "truncated": true
    }
  },
  "panels": {
    "db": {
      "available": true,
      "queries": 6,
      "statements": 2,
      "duplicates": 4,
      "max_repetitions": 5,
      "repeated": [{"sql": "SELECT ... FROM author a0_ WHERE ...", "count": 5}],
      "time_ms": 1.76,
      "list": [{"sql": "-- product::read SELECT ...", "duration_ms": 0.42}],
      "tagged_total": 1,
      "tagged_truncated": false,
      "tagged": [{
        "tags": ["product::read"],
        "sql": "-- product::read SELECT ...",
        "count": 1,
        "duration_ms": 0.42,
        "source": {"call": "EntityRepository->search", "file": "/app/src/ProductLoader.php", "line": 27}
      }]
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

- `panels` is a structured object per panel and never a raw envelope.
- `shopware_rules` and `shopware_feature_flags` are deliberately distinct:
  the former describes Rule Builder rules active for one request, while the
  latter describes technical rollout flags. Feature flags expose total
  `count`, total `active`, `truncated` and at most 20 rows; booleans come from
  Shopware's checkmark/x SVGs.
- `shopware_cart` is extended/opt-in because its HTML contains a potentially
  large hidden VarDumper row per line. cdpx never returns those dumps, cart
  tokens, payloads, serialized Cart objects or full extensions. Localized
  prices remain `*_display` strings; no numeric amount or ISO currency is
  invented. Lines, taxes, collectors, processors and nested decorators are
  bounded to 20, with totals/truncation metadata where applicable.
- `profile.engine` identifies the common profiler protocol, while
  `profile.extensions` reports detected adapters. Collector IDs are redacted,
  capped at 20, and accompanied by `total`/`truncated`. With `--panels none`,
  `profile.probed` is false and `collectors.total` is null because the command
  deliberately performs no fetch.
- Only leading `--`, `#` and `/* ... */` SQL comments become DB tags. Inline
  comments stay in SQL text and are not promoted. `source` is best effort and
  omitted when the collector exposes no useful backtrace.
- The raw token is never returned: the output only exposes
  `token_present`, redacts the segment in `profiler_url` and sanitizes
  headers, URL/query, SQL/messages and results a second time at the
  stdout boundary.
- If no response carries `X-Debug-Token-Link` or `X-Debug-Token`
  (profiler disabled, `prod` environment), the command fails with
  `header X-Debug-Token-Link/X-Debug-Token not found` (exit 1).
- A panel whose collector isn't installed (no doctrine-bundle or compatible
  application collector, no
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
# Does the click open the cart off-canvas?
cdpx dom-diff -- click "#offcanvas-cart"

# Diff the current page against another route without mutation
cdpx dom-diff -- goto http://127.0.0.1:8000/cart

# Does typing trigger the autocomplete?
cdpx dom-diff -- type "#search" "trail shoes" --clear
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

### `./dev check`

Synopsis: `./dev check`, `./dev test-symfony-e2e` or `./dev test-shopware-e2e`

The full gate runs two profiler suites. The Symfony suite uses
`docker-compose.symfony-e2e.yml` and `tests/symfony-app/`: its controllers
exercise real Doctrine, cache, HTTP client, Messenger, Twig, exception,
routing and timing collectors. The Shopware suite uses Shopware 6.7.13.1,
MariaDB and a minimal plugin route to prove collector probing, direct
`app.connection_collector` selection without a speculative `db` request,
tagged DAL SQL/source extraction, active rules, cache-tag emissions, a real
feature flag and a real Cart/pipeline that remains opt-in. Both suites run the
public `cdpx profiler` command through the same
supervised Chromium pinned in the CI image. The remaining mock coverage pins
emitted CDP, redaction and deterministic failures only; it does not claim
framework runtime compatibility.

These runtime suites prove the named integrations. The committed Symfony and
Shopware HTML excerpts prove only the parser contract, while Chrome against
the fixture server proves browser mechanics only.

Command-specific options: none (a parameterless Make target; Docker and
Docker Compose must be installed and startable).

```bash
./dev check
# Symfony gate alone
./dev test-symfony-e2e
# Shopware gate alone, still with the pinned CI image and Chromium
./dev test-shopware-e2e
```

The resulting proofs land in `.proof/` (`symfony-e2e.log`,
`shopware-e2e.log`, both JUnit files, profiler comparison JSON, DOM diff JSON
and screenshots).

Gotchas and error cases:

- Docker missing: the target and the release proof fail with an explicit
  `unavailable` status; there is no degraded release success.
- Docker present but either framework scenario fails: `./dev proof`'s overall
  verdict is blocked — a real failure is never disguised as absence.
- The first run builds the framework images and installs Shopware into its
  disposable database, so expect a longer initial gate.
- The Shopware test requires `/usr/bin/chromium` inside the cdpx CI image and
  never falls back to a host or arbitrary system Chrome.

### `./dev test-shopware-e2e`

Runs only the blocking Shopware runtime described above. It is useful while
iterating on the collector integration; the release verdict still comes from
the complete `./dev check` gate.

### `./dev test-symfony-e2e`

Runs only the blocking Symfony runtime described above, using the same pinned
supervised Chromium and evidence entrypoint as Shopware. It is the short loop
for profiler collectors and Symfony-backed browser scenarios; the release
verdict still comes from `./dev check`.

## User journeys

- Navigate to a Symfony route, follow the profiler token and read the
  parsed panels (Doctrine, Twig, cache, exceptions, HTTP client,
  Messenger, routing, time, logs).
- Compare baseline/degraded, Doctrine-style N+1, bursts of duplicate
  queries, cache hit/miss/expired, Twig render cost, Stopwatch sections,
  HTTP client issues, Messenger messages, routing issues and response
  cache headers — from the real panels.
- Take a stable DOM diff around a browser action.
- Navigate to real Shopware, follow its profiler token, probe its collector
  menu, select `app.connection_collector` directly, and parse grouped/tagged
  DAL queries, active rules, cache tags, a deterministic technical feature
  flag and a real opt-in Cart with pipeline priorities.

## Validation

The panel parsers are unit-tested against committed HTML
(`tests/fixtures/profiler/`, normalized excerpts with provenance). The fixture
server may serve these contracts to Chrome without claiming a framework
runtime. `./dev proof` runs both Docker framework gates: unavailability, skip
or failure blocks the verdict.

## Proofs

The expected local proofs are JUnit and private screenshots for the Chrome
scenarios. The Symfony and Shopware gates add their dedicated logs and JUnit
files, the profiler diagnostics JSON, the DOM diff JSON and browser
screenshots. Opaque screenshots stay out of `.proof/shareable/`.

## Known limitations

Docker availability depends on the environment; its absence blocks the proof
and is resolved by installing Docker then re-running `./dev proof` or
`./dev check`. Panel parsing is coupled to WebProfiler HTML markup (no JSON
API exists): a major framework update may require re-capturing the fixtures
and adjusting the parsers — the tolerance contract (`available`/
`parse_error`, never an exception) guarantees that in the meantime the
command degrades cleanly instead of breaking.
