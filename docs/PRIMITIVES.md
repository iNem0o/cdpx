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
| `cdpx eval <js> [--await]` | root primitive: everything else | universal escape hatch; last resort (fragile, untyped) |
| `cdpx click <selector>` | click via the Input domain (trusted) | requires attached, visible, enabled, stable, a non-zero box, and a center hit-test |
| `cdpx type <selector> --secret-env NAME [--clear]` | fill a field from an environment reference | avoids the secret in argv; requires a visible/editable control, then IME-safe `Input.insertText` |
| `cdpx key <key>` | validation, clearing, keyboard navigation | Enter/Space, Backspace/Delete, Tab/Escape, Home/End, PageUp/PageDown, and the four arrow keys |

```bash
cdpx type "#name" --secret-env CUSTOMER_NAME --clear
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
| `cdpx vitals <url> [--click sel]` | basic LCP/CLS/INP | objectify perceived performance, interaction for INP |
| `cdpx a11y` | compacted accessibility tree | low-cost structured semantic vision |
| `cdpx coverage <url>` | dead JS/CSS per file | front-end debt measured, not guessed |

Exact scope: `seo` is an on-page diagnostic of the rendered DOM, not a crawl
or proof of indexing; `vitals` is a bounded local measurement, not a
complete lab/field methodology; `a11y` is a compact view of the AXTree, not
an exhaustive RGAA audit.

```bash
cdpx seo https://shop.example.test/collection/dresses
cdpx vitals http://shop.localhost/ --click "#add-to-cart"
```

## Developer diagnostics — [sheet](features/dev-profiler-diff.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx profiler <url> [--settle s] [--panels ...]` | parse the Web Profiler panels of the last request (Doctrine, Twig, cache, exceptions, HTTP client, Messenger, routing, time, logs) | N+1s, SQL duplicates, and exceptions quantified by the agent without opening the browser; `X-Debug-Token-Link` + `X-Debug-Token` fallback, panel HTML parsed (no JSON API on the Symfony side) |
| `cdpx dom-diff -- <action>` | before/after snapshot of an action → stable structural diff | see exactly what a click changed in the DOM |

```bash
cdpx profiler http://app.localhost/api/cart
cdpx dom-diff -- click "#submit-btn"
```

## Interception, emulation, orchestration — [sheet](features/orchestration-control.md)

| CLI | Use case | Why |
|---|---|---|
| `cdpx intercept --rule "PATTERN => 503\|block\|continue" -- goto <url>` | mock/block requests during a navigation | composed command: `Fetch.enable` dies with the connection |
| `cdpx emulate mobile\|slow-3g\|cpu-4x [--reset] [-- <action>]` | mobile device, network/CPU throttling | composed form mandatory to act under emulation: overrides die with the connection |
| `cdpx frame <selector>` | read inside a same-origin iframe — the selector targets an element **inside** the iframe's document, not the `<iframe>` tag | embedded content (payment, consent) |
| `cdpx record [-o j.ndjson] -- <action>` | run ONE action and write a redacted `cdpx.record/v2` log | `type` replayable via `@env:NAME`; eval/sensitive literals not replayable |
| `cdpx replay <j.ndjson>` | pre-validate then replay, stop at first divergence | rereads the actual URL after navigation and before mutation; `--max-actions` budget |
| `cdpx scenario run <file.yml>` | run a declarative business journey | single pass/fail verdict, findings, and proof bundle |

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx emulate mobile -- goto http://shop.localhost/
cdpx record -o journey.ndjson -- click "#add-to-cart"
cdpx --max-actions 20 replay journey.ndjson
cdpx scenario run checkout_guest_add_to_cart.yml
```

An interception rule accepts only `continue`, `block`, or a `200..599`
status; any typo is rejected at parse time. In a scenario, `wait_visible`
genuinely checks attachment, display/visibility, and a non-zero box, and a
`type` step requires `secret_ref` (the plain `[selector, text]` form is
rejected at validation). The final console/network drain precedes the
assertions.

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
