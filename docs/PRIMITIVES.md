# PRIMITIVES.md — catalog

Each primitive = a function (`src/cdpx/primitives/`), a CLI subcommand, mock
tests (output + protocol), a fixture if an e2e scenario makes sense. This
catalog gives the **what/why** per feature; the exhaustive reference
(options, JSON outputs, pitfalls) lives in each feature's sheet
(`docs/features/`), also displayed in the proof report (`./dev proof`).

## Output contract

By default, the CLI prints compact single-line JSON, optimized for the
agent and token cost. `--pretty` restores indented human-readable output.
Large fields are bounded by default (`--limit`, `*_truncated` metadata);
`--full` explicitly requests the complete detail. Streams (`console
--follow`, `record` logs) use compact NDJSON.
Contract details (exit codes, connection, `CDPX_ORIGINS`): the "CLI
Contract" section of the [README](../README.md).

All browser commands require a supervised session, a `run-id`, an assigned
`target`, and an explicit origin allowlist. The identity triple is supplied
via options or via `CDPX_SESSION`, `CDPX_RUN_ID`, and `CDPX_TARGET`; the
manifest is the sole source of the loopback endpoint. Every output object
carries `_cdpx.content_trust: "untrusted"`, and the `observation`,
`interaction`, or `privileged` authority applies before any CDP effect. Page
content never has authority over these parameters.

## Navigation and synchronization — [sheet](features/browser-navigation.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx tabs list` | inspect the single target assigned to the session | confirm the attestation without exposing target lifecycle |
| `cdpx version` | check the targeted Chrome before acting | never act on an unknown browser |
| `cdpx goto <url> [--wait load\|domcontentloaded\|none]` | navigate and know when the page is ready | without a lifecycle wait, the agent observes intermediate states |
| `cdpx wait <selector>` | wait for an element (SPA, injected content) | fixture `spa.html`: `#late-content` only exists after 300ms; the load event isn't enough |

`tabs list` returns a `{tabs, count}` object in order to respect the root
JSON contract and to actually apply `--limit` with truncation metadata.

```bash
cdpx goto http://shop.localhost/product-42
cdpx --timeout 5 wait "#offcanvas-cart"
```

## DOM inspection and user actions — [sheet](features/dom-interaction.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx text [selector]` | innerText — low-cost semantic vision | 100x fewer tokens than a screenshot to verify content |
| `cdpx html [selector]` | outerHTML — structural inspection | check attributes, classes, data-* |
| `cdpx count <selector>` | cheap assertion ("there really are 12 products") | quick check loop after an action |
| `cdpx eval <js>\|--file probe.js\|--stdin [--await]` | root primitive: everything else | file/stdin avoid shell quoting; output retains only source kind and SHA-256, never a copy of the script |
| `cdpx click <selector>` | click via the Input domain (trusted) | requires attached, visible, enabled, stable, a non-zero box, and a center hit-test |
| `cdpx type <selector> --secret-env NAME [--clear] [--key-events]` | fill a field from an environment reference | defaults to IME-safe `Input.insertText`; `--key-events` emits one trusted key sequence per printable ASCII character for segmented controls and rechecks the allowed origin between events |
| `cdpx key <key>` | validation, clearing, keyboard navigation | Enter/Space, Backspace/Delete, Tab/Escape, Home/End, PageUp/PageDown, and arrows; unambiguous casing aliases such as `PAGEDOWN` normalize to the canonical name |

```bash
cdpx type "#name" --secret-env CUSTOMER_NAME --clear
cdpx type ".code-digit" --secret-env CHECKOUT_OTP --key-events
cdpx key Enter
cdpx text "#result"
```

## Capture and observability — [sheet](features/browser-capture-observability.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx screenshot [-o f.png] [--full-page] [--format png\|jpeg]` | pixel vision: CSS bugs, rendering | when text isn't enough; JPEG to lighten the load |
| `cdpx pdf [-o f.pdf]` | freeze a page as PDF | printable proof of state, print rendering |
| `cdpx console [--duration s]` | logs + JS exceptions | THE missing feedback: a broken front end shows up in the console first |
| `cdpx console --follow --max N` | NDJSON stream of logs | continuous agentic loop, boundable via `--max` |
| `cdpx network <url> [--settle s]` | navigate while capturing network activity | XHR 500s, 404 assets, weight: summary + per-request detail |
| `cdpx metrics` | Performance.getMetrics (heap, nodes, layouts) | objectify a drift (DOM leak, growing heap) |

```bash
cdpx network http://shop.localhost/checkout
cdpx console --duration 3
cdpx screenshot -o state.jpg --format jpeg
```

## State and session — [sheet](features/state-session.md)

Architecture, Chrome process, profile, exposed surfaces, and teardown are
detailed in the [supervised sessions reference](SESSION-LIFECYCLE.md).

| CLI | Use case | Why |
|---|---|---|
| `cdpx session start\|status\|stop` | assign a disposable, exclusive browser session to a run | lifecycle outside the CDP authority matrix: `start` creates the grant; `status`/`stop` require the private manifest and its exact run/target identity |
| `cdpx session start ... --export` | install the identity triple in one command via `eval "$(...)"` | `export` lines quoted `ssh-agent`-style; documented exception to the stdout-JSON contract |
| `cdpx session start ... --ignore-tls-errors` / `--trust-ca-dir PATH` | reach local HTTPS behind a dev CA: skip certificate validation, or import CA PEMs from a trust directory (default `CDPX_TRUST_CA_DIR`) | development against `mkcert`/traefik authorities; see [configuration](CONFIGURATION.md#local-https-mkcert-traefik) |
| `cdpx cookies get [--show-values]` | inspect the session (redacted by default) | security: see HARNESS.md §2 |
| `cdpx cookies set --name n --value-env NAME --url u` / `clear` | prepare a scenario without exposing the value in argv | reproducibility; `clear` = Storage.clearCookies with a fallback |
| `cdpx storage [--kind local\|session] [--show-values]` | localStorage/sessionStorage, values redacted by default | guest cart, consent, front-end caches |

```bash
cdpx session start --run-id demo --authority interaction --origins "http://127.0.0.1:*" --ttl 1800 --export
```

## SEO, performance, accessibility audits — [sheet](features/seo-performance-accessibility.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx seo [url]` | SEO contract of the **rendered** DOM: title/metas/canonical/robots/h1/hreflang/JSON-LD/alt/links + findings, estimated px, duplicates | only the final DOM is authoritative on the Googlebot rendering side |
| `cdpx vitals <url> [--click sel]` | session-window CLS with bounded attribution plus approximate LCP/INP signals, bound to the measured document | `cdpx.vitals/v3`: per-metric availability (`"measured"`/`"unsupported"`) so an unsupported signal is never a silent zero, `metrics.cls` is the official maximum session window, `raw_sum` keeps the eligible-entry diagnostic sum, `status` distinguishes `"measured"`, `"partial"` (browser-announced dropped entries) and `"unavailable"`, and the winning entries include bounded sources/rectangles |
| `cdpx a11y` | compacted accessibility tree | low-cost structured semantic vision |
| `cdpx coverage <url>` | dead JS/CSS per file | front-end debt measured, not guessed |

Exact scope: `seo` is an on-page diagnostic of the rendered DOM, not a crawl
or proof of indexing; `vitals` is a bounded laboratory measurement of the
current main-frame document — official session-window aggregation of the
`layout-shift` entries exposed to that document, with approximate LCP/INP
signals, no iframe aggregation and no field-data equivalence; CLS attribution
is capped at 50 entries and five sources per entry. The origin policy is
enforced before any measurement: the real origin is judged right after the
navigation and again right after the optional interaction, always before the
isolated world is created or the isolated-world collector is read, and the
snapshot's own document binding is judged once more before the result is
returned. A forbidden document is never measured. A capture whose collector
cannot be armed or read fails the command; an incoherent or tampered
snapshot reports `status: "unavailable"` instead of a silent zero; a
browser-announced loss of buffered entries degrades the report to
`status: "partial"`. `a11y` is a compact view of the AXTree, not an
exhaustive RGAA audit.

```bash
cdpx seo https://shop.example.test/collection/dresses
cdpx vitals http://shop.localhost/ --click "#add-to-cart"
```

## Developer diagnostics — [sheet](features/dev-profiler-diff.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx profiler <url> [--settle s] [--panels ...]` | probe and parse the Web Profiler panels of the last request (Doctrine/DAL, Twig, cache, exceptions, HTTP client, Messenger, routing, time, logs, Shopware rules/cache tags/feature flags and opt-in Cart) | Collector IDs select composable panel specs before fetch; defaults stay lightweight, cache-tag rows and caller lists expose totals/truncation metadata, `--panels all` includes bounded Cart, and absent collectors cause no speculative request |
| `cdpx dom-diff -- <action>` | before/after snapshot of an action → stable structural diff | see exactly what a click changed in the DOM |

```bash
cdpx profiler http://app.localhost/api/cart
cdpx profiler http://app.localhost/checkout/cart --panels shopware_cart
cdpx dom-diff -- click "#submit-btn"
```

## Interception, emulation, orchestration — [sheet](features/orchestration-control.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx intercept --rule "PATTERN => 503\|block\|continue" [--] goto <url>\|click <selector>` | mock/block requests during a navigation or trusted click | interception is armed before the composed action and explicitly removed afterward |
| `cdpx emulate mobile\|slow-3g\|cpu-4x [--reset] [-- <action>]` | mobile device, network/CPU throttling | composed form mandatory to act under emulation: overrides die with the connection |
| `cdpx frame <selector>` | read inside a same-origin iframe — the selector targets an element **inside** the iframe's document, not the `<iframe>` tag | embedded content (payment, consent) |
| `cdpx record [-o j.ndjson] -- <action>` | run ONE action and write a redacted `cdpx.record/v2` log | `type` replayable via `@env:NAME`; eval/sensitive literals not replayable |
| `cdpx replay <j.ndjson>` | pre-validate then replay, stop at first divergence | rereads the actual URL after navigation and before mutation; `--max-actions` budget |
| `cdpx scenario validate <file.yml>` | compile a versioned scenario and its local fragments without Chrome | ordered plan, sources, authority, secret references, dependency hashes and digest |
| `cdpx scenario run <file.yml>` | run a declarative business journey after expanding local step fragments | single verdict and proof bundle; the `vitals` collector is registered before the first navigation so every journey document is instrumented from its first script, bounded `wait_ms` supports late effects, and optional interception reports matched/effective counts |

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx intercept --rule "*api/echo* => 503" --settle 1 click "#request-button"
cdpx emulate mobile -- goto http://shop.localhost/
cdpx record -o journey.ndjson -- click "#add-to-cart"
cdpx --max-actions 20 replay journey.ndjson
cdpx --max-actions 20 scenario validate checkout_guest_add_to_cart.yml
cdpx scenario run checkout_guest_add_to_cart.yml
```

An interception rule accepts only `continue`, `block`, or a `200..599`
status; any typo is rejected at parse time. `intercept` composes only with
`goto` and `click`, always requires `privileged`, resolves every paused
request, and disables Fetch in cleanup even when the action fails. The
origin guard is mandatory on both routes: a top-level document (navigated or
click-triggered) is checked against the session origin allowlist before a
rule can affect it; a forbidden document continues untouched and the command
fails. Subrequests remain eligible for interception independently of their
origin. With `--settle 0`, events already buffered by the completed action
are resolved, but CDPX does not wait for new traffic. Recorded hits are
bounded at the source (200 per action, URLs
capped) while `hits_total`, `hits_limit` and `hits_truncated` keep the exact
totals, and `matched_count`/`effective_count` let a blocking control prove
that its rule actually affected traffic. Rules are armed before each composed
action and Fetch is disabled in its cleanup. In a scenario,
`context.intercept` accepts at most 20 of the same validated rules and
applies them around every `goto` and trusted `click`; the aggregate keeps
the exact totals with a bounded hit list, and step results never duplicate
it. `wait_ms` is an integer from 0 to 60000 and must also fit the per-step
`--timeout`. A `vitals` checkpoint or final artifact reads a collector that
was registered before the first navigation (every journey document is
instrumented from its first script) and persists the `cdpx.vitals/v3`
snapshot — status, per-metric availability, collector metadata, document
binding, measurement environment and bounded metrics — as internal JSON. In a scenario, `wait_visible` genuinely checks attachment,
display/visibility, and a non-zero box. Its deadline follows the bounded
scenario `--timeout`, allowing supervised third-party widgets to opt into a
longer wait. A `type` step requires `secret_ref`
(the plain `[selector, text]` form is rejected at validation). `frame_type`
types a referenced secret into a single-field cross-origin iframe only after
the child frame's current document URL, read through CDP, matches the declared
allowlisted origin. The URL is rechecked after focus and around secret input;
paced key events stop if the frame navigates between characters. A `candidates`
list can declare several selector/origin/secret-reference triples when the page
chooses its PSP at runtime; exactly one must resolve and only its secret is
typed. `mode: key_events` is available for PSP validation that requires trusted
printable-ASCII keyboard events; `key_delay_ms` can pace those events from 0 to
250 ms when a supervised widget needs human-like processing time. Every
`frame_origin` is one exact HTTP(S) origin without wildcard, path or credentials;
the session origin allowlist may remain broader. Frame lookup,
focus, origin guards, input events and pacing share the scenario `--timeout`;
expiration prevents any later character from being dispatched. Screenshots at
or after that sensitive step, including final screenshots, are refused. The
final console/network drain precedes the assertions. `context.base_url`
accepts the
same `${NAME}`, `${NAME:-default}` and `$$` interpolation grammar as
`cdpx.yaml`; it is expanded during compilation and the result remains subject
to the session's strict HTTP(S) origin allowlist. A missing variable fails as
an exit-2 usage error naming only the variable; scenario secrets continue to
use `secret_ref`. A structured profiler request requires at least one selector,
refuses every explicit null selector value, and each step or final artifact list
accepts at most one profiler capture across its short and structured forms.
`cdpx.scenario/v1` files can
place `{include: {path, as?}}` in `steps`; the referenced
`cdpx.scenario-fragment/v1` file contributes steps at that exact position and
inherits the root context. Paths are static, relative to the including file,
workspace-confined and browser-free validation rejects cycles, duplicate
aliases and expanded action budgets before opening CDP.

Limits: `network` is not a HAR (no body or complete timeline), and `replay`
only compares recorded non-volatile fields. A green replay only proves a
business effect if the log or scenario carries a matching observable
assertion.

A transport break during passive event collection is a diagnosed error
(exit 1), so a scenario interrupted mid-course cannot return a truncated
verdict. Schema-v1 logs remain readable for non-sensitive actions; v1
`type` and `eval` actions are refused before replay.

## Harness and proof cockpit — [sheet](features/harness-proof-cockpit.md)

Quality gates (`./dev check`, `./dev test-e2e`, Docker images) and generation
of the proof report (`./dev proof` → `.proof/proof-report.html`), which
serves as human-facing product documentation: per-feature user docs,
scenarios, tests, proofs, gaps. See the sheet for each `./dev` command.

## Addition rule

New primitive = use case written here FIRST (one table row), then mock
test, then implementation, then fixture if e2e is relevant, then a
`### cdpx <cmd>` section in the feature sheet (mechanically verified: a
command without user documentation breaks `./dev proof`). See the
[contribution guide](../CONTRIBUTING.md) and `AGENTS.md`.
