+++
id = "orchestration-control"
title = "Interception, emulation and orchestration"
status = "validated"
summary = "Control network behavior around navigation or trusted clicks, emulate device constraints, capture attributed vitals in journeys, read iframes, run business scenarios and record/replay bounded browser actions."
entrypoints = ["cdpx intercept", "cdpx emulate", "cdpx frame", "cdpx record", "cdpx replay", "cdpx scenario"]
path_globs = ["src/cdpx/primitives/actions.py", "src/cdpx/primitives/inputs.py", "src/cdpx/primitives/emulation.py", "src/cdpx/primitives/interception.py", "src/cdpx/primitives/recording.py", "src/cdpx/journal.py", "src/cdpx/scenarios.py", "src/cdpx/scenario_compiler.py", "schemas/scenario-*.json", "tests/fixtures/interactions-rich.html", "tests/fixtures/intercept.html", "tests/fixtures/iframe.html", "tests/fixtures/scenarios/*.yml", "tests/fixtures/scenarios/fragments/*.yml", "tests/test_journal.py", "tests/test_scenarios.py", "src/cdpx/orchestration.py"]
test_globs = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_scenarios.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect", "tests/e2e/test_e2e_chrome.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_key_events*", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*", "tests/e2e/test_e2e_chrome.py::test_cli_slow_3g*", "tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*", "tests/e2e/test_e2e_shopware.py::test_scenario_targets_real_shopware_fetch_profiler"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "intercept-network"
title = "Force, block or let through matching network requests"
entrypoint = "cdpx intercept"

[[journeys]]
id = "replay-flow"
title = "Record and replay bounded browser actions"
entrypoint = "cdpx replay"

[[journeys]]
id = "scenario-run"
title = "Run a declarative business scenario with proofs"
entrypoint = "cdpx scenario"

[[scenarios]]
id = "intercept-network-request"
journey = "intercept-network"
title = "Intercept a network request deterministically"
ui_text = "The browser run can force, block or let through network outcomes."
report_text = "This scenario proves that network behavior can be controlled during browser validation and linked to a human-readable proof."
given = "A fixture page issues requests that the interception rules can match."
when = "cdpx intercept applies a fulfill, block or continue behavior during the composed navigation or trusted click."
then = "The browser result and the screenshot prove the requested network path."
target = "cdp-mock"
proof_level = "contract"
tests = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_intercept*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "enforce-replay-contract"
journey = "replay-flow"
title = "Enforce the bounded replay contract"
ui_text = "The protocol contract stops replay on divergence and before a missing secret can reach CDP."
report_text = "This contract scenario proves journal validation, fail-closed secret resolution and stop-on-divergence behavior against the CDP mock; browser execution is covered separately."
given = "A journal diverges or references an unavailable secret."
when = "cdpx validates and replays the bounded action sequence."
then = "Execution stops at the first divergence and emits no later CDP effect."
target = "cdp-mock"
proof_level = "contract"
tests = ["tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect"]
expected_proofs = ["junit"]

[[scenarios]]
id = "orchestrate-replay-and-emulation"
journey = "replay-flow"
title = "Replay a bounded browser orchestration"
ui_text = "The report links the orchestration primitives to the replay, iframe, emulation and origin guard tests."
report_text = "This scenario proves that bounded browser actions and device constraints can actually be replayed or inspected without becoming an unbounded macro language."
given = "An NDJSON journal of recorded actions, iframe fixtures or emulation constraints are available."
when = "cdpx validates the entire journal (syntax, actions, budget) then actually replays each action against the browser, emulates, reads iframes or applies the origin guard."
then = "Each action is replayed within the budget limit, the replay stops at the first divergence, and the result stays bounded, verifiable and tied to the orchestration feature."
target = "chrome"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "run-declarative-business-scenario"
journey = "scenario-run"
title = "Run a YAML business scenario with proofs"
ui_text = "A versioned YAML scenario composes reusable step fragments, assertions and proofs into one validated journey."
report_text = "This scenario proves that reusable fragments are expanded deterministically and preflighted as one declarative journey before Chrome receives an action."
given = "A disposable Chrome targets a local or Symfony application and a YAML scenario includes local, versioned fragments."
when = "cdpx compiles the complete include graph, validates its budget, authority, origins and secrets, then scenario run executes the flattened steps and collects proofs."
then = "The output contains one verdict plus qualified labels, source provenance, a composition digest, findings and artifacts."
target = "cdp-mock"
proof_level = "contract"
tests = ["tests/test_scenarios.py::*"]
expected_proofs = ["junit"]

[[scenarios]]
id = "run-declarative-business-scenario-in-chrome"
journey = "scenario-run"
title = "Run a YAML business scenario in real Chrome"
ui_text = "The validated scenario drives the reproducible Chrome runtime and captures its browser proofs."
report_text = "This runtime scenario proves execution of the declarative action language against reference pages in the pinned real Chromium."
given = "A fully preflighted declarative scenario targets local reference pages."
when = "cdpx executes the flattened steps in the pinned Chromium runtime."
then = "Browser assertions and artifacts reflect the observed real-Chrome journey."
target = "chrome"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_chrome.py::test_key_events*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "run-declarative-business-scenario-on-symfony"
journey = "scenario-run"
title = "Run a YAML business scenario against real Symfony"
ui_text = "The declarative runner drives the real Symfony reference application and records its framework-backed proofs."
report_text = "This runtime scenario proves the declarative runner against the installed Symfony application rather than a fixture backend."
given = "The real Symfony application exposes deterministic business scenario routes."
when = "cdpx executes passing, controlled-failure, profiler and vitals journeys against it."
then = "The reports and artifacts come from the Symfony runtime and its real collectors."
target = "symfony"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "target-shopware-profiler-request"
journey = "scenario-run"
title = "Target one Shopware profiler request from a scenario"
ui_text = "A structured profiler capture selects the Fetch that recalculated the real Shopware cart."
report_text = "The blocking Shopware gate proves that a scenario can select one observed Fetch by path, resource type and method, then fetch only its requested profiler panels including Cart."
given = "The real Shopware application exposes a document route and a distinct deterministic Cart route."
when = "The scenario opens the document, performs a Fetch to the Cart route and captures that request's profiler token."
then = "The artifact identifies the request selection and contains exactly the requested real Shopware panels without cart payload secrets."
target = "shopware"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_shopware.py::test_scenario_targets_real_shopware_fetch_profiler"]
expected_proofs = ["junit", "json"]

[[scenarios]]
id = "type-secret-into-cross-origin-frame"
journey = "scenario-run"
title = "Type a referenced secret into a cross-origin field"
ui_text = "A scenario can complete hosted payment fields without exposing their values."
report_text = "A real Chromium run proves that a referenced secret reaches a single-field cross-origin iframe only after origin verification, while screenshots are forbidden from that step onward."
given = "A top-level checkout embeds a card-like field from a distinct allowlisted origin."
when = "frame_type verifies the child frame's current URL, focuses it through the input pipeline and inserts the referenced secret."
then = "The child renderer receives the value, the result stays masked and the evidence contains no sensitive screenshot."
target = "chrome"
proof_level = "runtime"
tests = ["tests/e2e/test_e2e_chrome.py::test_declarative_scenario_types_secret_into_cross_origin_frame"]
expected_proofs = ["junit", "logs"]
+++

## Intent

Enable controlled browser experiments where the network, device conditions
or a multi-step action journal are part of the validation. While building a
Symfony or e-commerce app, one needs to force a backend into an error state
without breaking it (`intercept`), check a render under mobile or slow
network constraints (`emulate`), read content embedded in an iframe
(`frame`), and build then replay a reproducible journey (`record` /
`replay`), or elevate these primitives into a declarative business scenario
(`scenario run`). The action language stays deliberately compact (goto,
wait, click, type, key, eval): one action = one named primitive, never a
shell escape hatch.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

The session's allowlist is mandatory and every action is preflighted
against the manifest's authority. `intercept` always requires `privileged`
because it changes network behavior, including when its enclosed action is a
`goto` or `click`. Other composed commands follow their documented action
authority, and `replay` and `scenario` take the maximum level of the whole
file before any CDP effect. Destinations and the real origin are checked;
page content remains an untrusted input. `frame` is an observation.

### `cdpx intercept`

Synopsis: `cdpx intercept --rule "PATTERN => ACTION" [--rule ...] [--settle S] [--] goto <url>|click <selector>`

Intercepts network requests during a navigation or trusted click and applies
a deterministic behavior to them: answering in place of the server with an
HTTP code (e.g. `503`), blocking (`block`, `BlockedByClient` failure), or
letting through (`continue`). Use case: prove that a page degrades cleanly
when an API request triggered by loading or user input fails, without touching
the backend. Interception is enabled before the enclosed action, remains
active through its stabilization period, and is explicitly disabled before
the command returns. The optional `--` separator has no effect on behavior.

Command-specific options:

- `--rule` (required, repeatable): rule `PATTERN => ACTION`. `PATTERN` is an
  fnmatch pattern (`*api*`) or a substring of the URL; `ACTION` is a numeric
  HTTP code **from 200 to 599** (e.g. `503`, JSON response
  `{"cdpx":"intercept","status":N}`),
  `block` or `continue`. The first matching rule wins; a request with no
  matching rule continues normally.
- `--settle`: quiet period in seconds (default 0.5). For `goto`, it starts
  after the `load` event; for `click`, it starts after the click primitive
  completes. Each observed CDP event restarts the quiet period, while the
  global `--timeout` bounds the complete action and observation window. A
  zero period still resolves events already buffered by the completed action,
  then returns without waiting for new traffic.
- `action`: `goto <url>` or `click <selector>`. `click` uses the same fixed
  actionability probe and trusted Input-domain mouse sequence as standalone
  `cdpx click`; no caller-provided JavaScript or `eval` action is accepted. If
  that click starts a top-level navigation, its destination is checked before
  any interception rule is applied. A forbidden document is continued
  untouched, the command fails with the origin-policy diagnostic, and cleanup
  still disables interception.

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx intercept --rule "*tracker* => block" --rule "*api* => continue" -- goto http://demo.test/product-42
cdpx intercept --rule "*api/echo* => 503" --settle 1 click "#request-button"
```

```json
{"url":"http://demo.test/","rules":["*api* => 503"],"hits":[{"url":"http://demo.test/","action":"continue"},{"url":"http://demo.test/api/health","action":"503"}],"count":2,"hits_total":2,"hits_limit":200,"hits_truncated":false,"settle":1.0}
```

Click result:

```json
{"action":{"argv":["click","#request-button"],"result":{"clicked":"#request-button","x":412.5,"y":318.0}},"rules":["*api/echo* => 503"],"hits":[{"url":"http://demo.test/api/echo","action":"503"}],"count":1,"hits_total":1,"hits_limit":200,"hits_truncated":false,"matched_count":1,"effective_count":1,"settle":1.0}
```

For click composition, `count` is the number of recorded hits (bounded at
200 per action with URLs capped at 2048 characters), `hits_total` is the real
number of paused requests, and `hits_truncated` announces when recording was
capped while every request was still resolved. `matched_count` is the number
matched by an explicit rule (including an
explicit `continue`), and `effective_count` counts status fulfillments and
blocks. A valid rule with no match is a successful command with
`matched_count:0` and `effective_count:0`; callers can make that an assertion
failure without conflating it with an action or transport error.

Errors and pitfalls: any action other than `goto` or `click` is rejected
before `Fetch.enable`. A rule without `=>`, a typo (`typo`), or a status
outside `200..599` fails at parsing before any interception; no default branch
silently changes traffic. A missing, invalid, hidden, disabled, unstable, or
covered click target follows the normal click error contract. If `load`, the
click, or stabilization exceeds `--timeout`, the command exits 1. Every
observed paused request receives a decision, and `Fetch.disable` runs in a
`finally` path on success or failure; a cleanup failure is itself an execution
error, with connection closure as the transport fallback. `intercept` requires
`privileged`; both routes judge the top-level document against the session
origin allowlist BEFORE any rule can affect it: a forbidden document
(redirected or click-triggered) continues untouched and the command fails.
Within an allowed origin, an overly broad rule (`* => 503`) can still replace
the hosting page.

### `cdpx emulate`

Synopsis: `cdpx emulate [mobile|slow-3g|cpu-4x] [--reset] [-- <action ...>]`

Applies an emulation preset — `mobile` (viewport 390x844, deviceScaleFactor
3, UA `cdpx-mobile/1.0`), `slow-3g` (400 ms latency, 50 KiB/s throughput
upstream and downstream) or `cpu-4x` (CPU throttled 4x) — then, in composed
form, executes an action within the same CDP connection. Use case: check
that a page stays usable on mobile or on a degraded network. The composed
form is essential: emulation overrides DIE with the CDP connection (proven
e2e on Chrome 151), so acting under emulation requires the action to be
passed in the same invocation (`cdpx emulate mobile -- goto
http://demo.test/`).

Command-specific options:

- `preset` (positional, optional): `mobile`, `slow-3g` or `cpu-4x`.
- `--reset`: restores the default state — device metrics, user-agent, network
  conditions and CPU rate. Used without a preset.
- `action` (after `--`): composed action executed under emulation —
  `goto <url>`, `wait <selector>`, `click <selector>`,
  `type <selector> <text> [--clear] [--key-events]`, `key <key>`, `eval <js>`.

```bash
cdpx emulate mobile -- goto http://demo.test/
cdpx emulate slow-3g -- goto http://demo.test/cart
cdpx emulate mobile -- eval "navigator.userAgent"
cdpx emulate --reset
```

Output with a composed action:

```json
{"preset":"mobile","applied":true,"action":{"argv":["goto","http://demo.test/"],"result":{"url":"http://demo.test/","frameId":"7C93","loaderId":"A1F0","errorText":null,"waited":"load","ok":true,"elapsed_ms":52.7}}}
```

Output of `--reset`:

```json
{"reset":true}
```

Errors and pitfalls: without a preset or `--reset`, the command fails
(`unknown preset: None`, exit 1). MAIN PITFALL: `cdpx emulate mobile`
without an action does apply the overrides, but they vanish as soon as the
command ends — a `cdpx goto` launched afterward runs WITHOUT emulation (see
Known limitations). The command is classified by its action's verb:
`emulate mobile -- goto ...` counts as observation, `emulate mobile --
click ...` requires interaction and any destination remains bounded by the
allowlist.

### `cdpx frame`

Synopsis: `cdpx frame <selector>`

Reads the `innerText` of an element located INSIDE a same-origin iframe of
the current page: every iframe is scanned, the first one containing the
selector provides the text. Use case: check the content of an embedded
widget (sandboxed payment, CMS preview) without switching CDP target.

Command-specific options:

- `selector` (positional, required): CSS selector searched for in the
  document of each iframe.

```bash
cdpx frame "#status"
```

```json
{"selector":"#status","text":"Payment accepted"}
```

Errors and pitfalls: if no element matches, or if the iframe is
cross-origin (its `contentDocument` is inaccessible), the output carries
`"text":null` with exit 0 — check the value, not the exit code. `frame`
counts as observation but still requires the current origin to belong to
the mandatory allowlist.

### `cdpx record`

Synopsis: `cdpx record [-o journal.ndjson] -- <action ...>`

ACTUALLY executes an action (via the shared action interpreter:
`goto <url>`, `wait <selector>`, `click <selector>`,
`type <selector> <text> [--clear]`, `key <key>`, `eval <js>`) then logs it
in the `cdpx.record/v2` NDJSON schema. The journal is opened in append mode:
several invocations build up a journey. Each line contains the schema,
`run_id`, structured action or argv, `replayable`, verdict, cleaned result
and timestamp. A failure is written before the exit 1.

`record type` requires `@env:NAME`: only the reference is persisted, the
value is resolved in memory and recorded in the redaction context. `eval`
is always redacted, hashed and non-replayable. Any other form of input is
rejected before connecting.

Command-specific options:

- `-o`, `--output`: name of the NDJSON journal (default
  `cdpx-record.ndjson`). Only its basename is kept.
- `action` (after `--`): the action to execute and log.

The journal is confined under the session's `artifacts/journals/`, at
`0600`, with metadata
`classification:"internal"`, `upload_allowed:false`, `retention:"session"`.
`replay` can only read a private regular file from that same folder.

```bash
cdpx record -o journey.ndjson -- goto http://demo.test/
cdpx record -o journey.ndjson -- click "#buy"
cdpx record -o journey.ndjson -- type "#password" @env:CHECKOUT_PASSWORD --clear
cdpx record -o journey.ndjson -- wait "#confirmation"
```

```json
{"schema":"cdpx.record/v2","path":"journey.ndjson","recorded":1,"replayable":true,"ok":true}
```

NDJSON line written to the journal:

```json
{"schema":"cdpx.record/v2","run_id":"checkout-17","action":{"verb":"type","selector":"#password","input":{"secret_ref":"CHECKOUT_PASSWORD","source":"env"},"clear":true},"replayable":true,"ok":true,"result":{"typed":true,"value_masked":true,"selector":"#password","cleared":true},"ts":1783814400.123}
```

Errors and pitfalls: a missing env reference is rejected before any CDP
effect. A failing action is logged with `ok:false` before the exit 1. The
file and its folder are forced to `0600` and `0700` respectively. The
required authority follows the action and the real origin is revalidated
after execution.

### `cdpx replay`

Synopsis: `cdpx replay <journal.ndjson>` (budget: global option `--max-actions`)

Replays an NDJSON journal produced by `cdpx record` against the browser,
action by action, and stops at the first divergence. All validation happens
BEFORE the first execution: JSON syntax of every line, presence of an
action, schema/replayability, resolution of every secret reference, maximum
authority and the `--max-actions` budget. A single missing reference
guarantees `played:0` and no CDP command. Each action is then actually
executed and its non-volatile result is compared to the recorded result.

After every `goto`, replay re-reads `window.location.href` instead of
keeping the requested URL. This final URL is checked immediately and again
just before the next mutation: an allowed → forbidden origin redirect
cannot receive the next click.

Command-specific options:

- `path` (positional, required): path of the NDJSON journal to replay.
- The action budget comes from the global option `--max-actions`: a journal
  exceeding it is rejected before any replay.

```bash
cdpx replay journey.ndjson
cdpx --max-actions 20 replay journey.ndjson
```

Full successful replay:

```json
{"path":"journey.ndjson","events":3,"played":3,"ok":true}
```

Divergence (exit 1, the JSON stays structured on stdout):

```json
{"path":"journey.ndjson","events":3,"played":1,"ok":false,"divergence":"event 1: selector not found after 10.0s: #buy"}
```

Errors and pitfalls: a non-JSON line or one without `action` produces
`"ok":false` with `"divergence":"line N: ..."` and `"played":0` (exit 1). A
journal longer than `--max-actions` triggers `--max-actions budget
exceeded` (exit 1, nothing is replayed). `played` counts the actions
actually replayed successfully; the `divergence` index is that of the
offending event (0-based). Volatile keys (`elapsed_ms`, loader/frame IDs,
coordinates) are ignored in the comparison. v1 journals containing `type`
or `eval` are rejected; non-sensitive v1 actions remain compatible.

### `cdpx scenario`

Synopsis: `cdpx scenario run <file.yml> [--settle S]` or
`cdpx scenario validate <file.yml>`

Runs a declarative YAML business scenario against the targeted tab. The
scenario describes a context (`base_url`, optional emulation/interception), a suite of
steps, assertions, final proofs and, if needed, proofs to collect at key
moments of the run (`capture` on a step). The output is always a single
JSON object with `verdict` (`pass` or `fail`), `findings`, `steps`,
`assertions`, `artifacts`, `evidence_dir` and a composition digest.

`scenario validate` uses the exact same compiler without requiring a session
or resolving secret values. It reports the expanded ordered steps, their
sources, dependencies and hashes, required authority, referenced environment
names and digest. Use it while authoring a scenario or in a static CI check.

Supported executable schema (`cdpx.scenario/v1`):

- `context.base_url`: origin or base URL for resolving relative `goto`
  calls. This field alone supports the workspace placeholder grammar:
  `${NAME}`, `${NAME:-default}` and `$$`. Expansion happens during scenario
  compilation, before the strict session origin preflight; an undefined
  variable is an exit-2 usage error that names the variable.
- `context.emulation`: optional, `mobile`, `slow-3g` or `cpu-4x`, applied
  within the same CDP connection as the steps.
- `context.intercept`: optional list of at most 20 normal interception rules.
  They wrap every `goto` and trusted `click`, require `privileged`, clean up
  Fetch after each action, and aggregate bounded hits plus matched/effective
  counts in the result.
- Steps: `goto`, `wait_visible`, `click`, `type`, `frame_type`, `key`, `eval`,
  `wait_text`, `wait_ms`. `wait_ms` is a 0..60000 integer and must fit the
  per-step scenario `--timeout`. `wait_visible` requires an element that is attached,
  rendered, visible and has a non-zero box; its deadline is the bounded
  scenario `--timeout`. `type` accepts only
  `{selector, secret_ref, clear, mode}` and prevalidates the environment
  reference. `mode` defaults to `insert_text`; `key_events` emits a trusted
  key sequence for each printable ASCII character so segmented controls can
  advance focus exactly as they do for a user. `frame_type` accepts
  `{selector, frame_origin, secret_ref}` for
  a single-field cross-origin iframe: cdpx requires an actionable iframe,
  resolves its owner node to the current child document URL through CDP,
  verifies that URL against the declared allowlisted origin, focuses it through
  a trusted click, then rechecks the child URL around secret insertion without
  reading the child DOM. Paced key events recheck between characters and stop
  if the frame navigates. When a controlled page may select one of several
  PSPs, replace
  `selector` and `frame_origin` with
  `candidates: [{selector, frame_origin, secret_ref}, ...]`; exactly one
  declared iframe must exist, its runtime origin must match, and only that
  candidate's referenced secret is typed. Zero or multiple matches fail closed.
  Every `frame_origin` is one exact HTTP(S) origin without wildcard, path or
  credentials; the session origin allowlist may remain broader.
  `mode: key_events` emits trusted printable-ASCII keyboard events for PSPs
  whose validation does not react to IME insertion. `key_delay_ms` optionally
  spaces those events by 0 to 250 ms and is rejected for other modes. The
  complete frame lookup, focus, origin guards, key sequence and pacing consume
  the scenario `--timeout`; expiration stops before the next browser effect.
  Clearing is deliberately unsupported.
- `capture` on a step: a list among `screenshot`, `console`, `network`,
  `profiler`, `vitals`. A vitals capture arms the isolated-world collector at
  capture time; the internal `cdpx.vitals/v2` JSON contains the availability
  status, the document binding, official session-window CLS, `raw_sum`, the
  winning entries, sources and rectangles.
  These proofs are collected immediately after the step, even
  if the step fails. `profiler` also accepts the structured form documented
  below; only one profiler capture is allowed at each checkpoint.
- Assertions: `no_console_errors`, `network_errors_max`, `text_contains`.
- `artifacts`: same types as `capture`, collected at the end of the
  scenario.
- A scenario containing `frame_type` may capture screenshots only before its
  first such step. A screenshot on that step, a later step, or in final
  `artifacts` is rejected before Chrome is contacted, because cross-origin
  payment fields cannot be inspected for redaction.
- An `include` step contains `{path, as?}`. `path` is resolved relative to
  the including file; `as` defaults to the fragment name and qualifies every
  included label. A fragment has schema `cdpx.scenario-fragment/v1`, a name
  and steps only. It inherits the executable scenario's context.

`checkout_guest_add_to_cart.yml`:

```yaml
schema: cdpx.scenario/v1
name: checkout_guest_add_to_cart
context:
  base_url: "${APP_URL:-http://shop.localhost}"
  emulation: mobile
  intercept: ["*optional-widget.js* => block"]
steps:
  - label: product_page
    goto: /product/42
    capture: [screenshot, console, network]
  - include:
      path: fragments/add_to_cart.yml
      as: cart
  - type:
      selector: '[name="password"]'
      secret_ref: CHECKOUT_PASSWORD
      clear: true
  - wait_text: ['[data-testid="cart-count"]', '1']
  - wait_ms: 750
assertions:
  - no_console_errors: true
  - network_errors_max: 0
  - text_contains: ['[data-testid="cart-count"]', '1']
artifacts:
  - screenshot
  - console
  - network
  - profiler
  - vitals
```

A profiler capture may select panels and the last observed request matching
its path, CDP resource type and HTTP method. `panels` omitted keeps the
lightweight defaults; an empty list records token discovery only. The request
object must contain at least one non-null selector, explicit null selector values
are refused, `url_prefix` is a path without query or fragment, `resource_type`
is `document`, `xhr` or `fetch`, and method names are normalized to uppercase.
Each `capture` list and the final `artifacts` list may contain at most one
profiler capture, whether expressed as `profiler` or as the structured form.
The same structured form is accepted in final `artifacts`:

```yaml
capture:
  - profiler:
      panels: [time, db, shopware_rules, shopware_cache_tags, shopware_cart]
      request:
        url_prefix: /checkout/cart
        resource_type: fetch
        method: POST
```

An explicit request selector fails the scenario with
`profiler_request_not_found` when no observed profiler response matches. It
never falls back to the current document or triggers a replacement navigation.
The profiler artifact records safe `selection` criteria and matched method/type;
its existing `url` remains redacted. A requested but unavailable panel remains
the non-failing `available:false` diagnostic.

`fragments/add_to_cart.yml`:

```yaml
schema: cdpx.scenario-fragment/v1
name: add_to_cart
steps:
  - label: ready
    wait_visible: '[data-testid="add-to-cart"]'
  - label: submit
    click: '[data-testid="add-to-cart"]'
    capture: [screenshot, console]
```

```bash
cdpx --max-actions 20 scenario validate checkout_guest_add_to_cart.yml
cdpx scenario run checkout_guest_add_to_cart.yml
```

Successful output:

```json
{"name":"checkout_guest_add_to_cart","verdict":"pass","findings":[],"evidence_dir":"/runtime/session/artifacts/scenarios/checkout_guest_add_to_cart-20260706T120000Z","steps":[{"index":0,"label":"product_page","verb":"goto","ok":true,"source":{"path":"checkout_guest_add_to_cart.yml","step":0,"include_chain":[]}}],"assertions":[{"name":"no_console_errors","expected":true,"ok":true,"actual":0}],"artifacts":[{"type":"screenshot","label":"product_page","path":"/runtime/session/artifacts/scenarios/.../000-product_page-screenshot.png","bytes":1234,"mime":"image/png","classification":"opaque-restricted","upload_allowed":false}],"composition":{"entrypoint":"checkout_guest_add_to_cart.yml","sha256":"...","dependencies":[{"path":"checkout_guest_add_to_cart.yml","sha256":"...","kind":"scenario"},{"path":"fragments/add_to_cart.yml","sha256":"...","kind":"fragment"}]},"_cdpx":{"content_trust":"untrusted"}}
```

Errors and pitfalls: invalid YAML or an unknown field exits with code 2. A
missing fragment, path outside the scenario root, absolute/remote/glob path,
duplicate alias, include cycle, excessive depth/file/step count or exceeded
`--max-actions` budget also exits with code 2 before any CDP command. Includes
may be nested, are read once and expanded depth-first at their exact position.
Hard ceilings are 16 nested includes, 128 unique scenario files and 1,000
expanded steps; `--max-actions` sets a lower per-invocation step budget.
The two machine-readable contracts are
[`scenario-v1.json`](../../schemas/scenario-v1.json) and
[`scenario-fragment-v1.json`](../../schemas/scenario-fragment-v1.json).
A scenario that runs but does not conform exits with code 1 with
`verdict:"fail"` and structured `findings`. Assertions do not stop at the
first failure: they accumulate findings and then the final proofs are
collected. An unconfigured `profiler` capture first uses the Symfony headers
observed during the run (`X-Debug-Token-Link` or `X-Debug-Token`), preferring
the current document; if no header was seen, cdpx tries the last navigated URL,
then adds a `profiler_unavailable` warning finding if no profiler is available.
An explicit request selector instead fails closed as described above. The collector performs one
final console/network drain **before** the assertions, so a late error
counts toward the verdict. Every origin is checked before the step and
after stabilization; a redirect outside the allowlist blocks the following
mutation, capture and assertions.

The run folder is `0700`, its files and manifest are `0600`. The
console/network/profiler JSON files are `internal`; screenshots and other
binaries are `opaque-restricted`, with `upload_allowed:false`. The result
and errors are redacted before persistence. The scenario folder is forced
under the session's artifacts and its TTL never exceeds the manifest's
remaining time; the teardown removes everything.

## User journeys

- Intercept a navigation or trusted click and force deterministic network
  outcomes (fulfill, block, continue).
- Emulate mobile, slow network or CPU throttling and act within the same
  connection.
- Run a YAML business scenario and get back a verdict, findings and proofs
  collected at checkpoints and at the end of the run.
- Read the text of a same-origin iframe.
- Record actually executed actions then replay the journal with a budget,
  stopping at the first divergence.

## Validation

The unit tests on mock CDP validate the interception rules, trusted click
protocol ordering, explicit Fetch cleanup, the emulation
presets and reset, the execution and logging of `record`, the upfront
validation and actual replay of `replay` (including divergence and the
budget), the YAML business scenarios, the shared action interpreter and the
origin guard. The e2e tests validate real Fetch interception during both
navigation and click, including a second non-intercepted click that proves
cleanup isolation, the
non-persistence of emulation overrides across connections, the full
record/replay cycle on real Chrome, and declarative pass/fail scenarios with
proofs. The Symfony e2e tests also run YAML scenarios against the
deterministic routes `/scenario/front/*`, `/scenario/vitals/*` and
`/scenario/profiler/*`.

## Proofs

Expected proofs: JUnit reports, screenshots for the orchestration e2e
scenarios (intercepted page, render under emulation), JSON of declarative
runs, console, network and profiler collected by `cdpx scenario run`.

## Known limitations

- Emulation overrides do NOT survive the command: they die with the CDP
  connection (Chrome behavior, verified e2e on Chrome 151). `cdpx emulate
  mobile` alone therefore has no lasting effect — always use the composed
  form `cdpx emulate mobile -- goto http://demo.test/`.
- The standalone `intercept` command composes with one `goto` or `click`.
  Scenario `context.intercept` reuses those typed rules across a journey but
  still applies them only around `goto` and `click`, not `type`, `key` or
  `eval`.
- `frame` only reads same-origin iframes (a cross-origin iframe's
  `contentDocument` is inaccessible) and returns the first match. Scenario
  `frame_type` can focus and type into a single-field cross-origin iframe but
  cannot read or clear its contents.
- Record/replay executes real actions but the action language remains
  deliberately compact (goto, wait, click, type, key, eval) — it is not a
  full browser macro language.
- Replay compares recorded results outside volatile fields; an identical
  result alone does not guarantee the expected business effect. Add an
  observable assertion in a scenario to prove it.
