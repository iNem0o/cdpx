+++
id = "orchestration-control"
title = "Interception, emulation and orchestration"
status = "validated"
summary = "Control network behavior, emulate device constraints, read iframes, run business scenarios and record/replay bounded browser actions."
entrypoints = ["cdpx intercept", "cdpx emulate", "cdpx frame", "cdpx record", "cdpx replay", "cdpx scenario"]
path_globs = ["src/cdpx/primitives/actions.py", "src/cdpx/primitives/emulation.py", "src/cdpx/primitives/interception.py", "src/cdpx/primitives/recording.py", "src/cdpx/journal.py", "src/cdpx/scenarios.py", "tests/fixtures/intercept.html", "tests/fixtures/iframe.html", "tests/fixtures/scenarios/*.yml", "tests/test_journal.py", "tests/test_scenarios.py", "src/cdpx/orchestration.py"]
test_globs = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_scenarios.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect", "tests/e2e/test_e2e_chrome.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*", "tests/e2e/test_e2e_chrome.py::test_cli_slow_3g*", "tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M3-interception-emulation.md", "docs/milestones/M5-orchestration.md"]
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
when = "cdpx intercept applies a fulfill, block or continue behavior during the composed navigation."
then = "The browser result and the screenshot prove the requested network path."
tests = ["tests/test_primitives.py::test_intercept*", "tests/test_cli.py::test_intercept*", "tests/e2e/test_e2e_chrome.py::test_intercept*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "orchestrate-replay-and-emulation"
journey = "replay-flow"
title = "Replay a bounded browser orchestration"
ui_text = "The report links the orchestration primitives to the replay, iframe, emulation and origin guard tests."
report_text = "This scenario proves that bounded browser actions and device constraints can actually be replayed or inspected without becoming an unbounded macro language."
given = "An NDJSON journal of recorded actions, iframe fixtures or emulation constraints are available."
when = "cdpx validates the entire journal (syntax, actions, budget) then actually replays each action against the browser, emulates, reads iframes or applies the origin guard."
then = "Each action is replayed within the budget limit, the replay stops at the first divergence, and the result stays bounded, verifiable and tied to the orchestration feature."
tests = ["tests/test_primitives.py::test_emulate*", "tests/test_primitives.py::test_frame*", "tests/test_primitives.py::test_record*", "tests/test_primitives.py::test_replay*", "tests/test_primitives.py::test_run_action*", "tests/test_primitives.py::test_origin_guard*", "tests/test_cli.py::test_record*", "tests/test_cli.py::test_replay*", "tests/test_cli.py::test_emulate*", "tests/test_journal.py::*", "tests/test_security_integration.py::test_missing_secret_ref_is_rejected_before_any_cdp_effect", "tests/e2e/test_e2e_chrome.py::test_record_replay*", "tests/e2e/test_e2e_chrome.py::test_emulate*", "tests/e2e/test_e2e_chrome.py::test_origin_guard*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "run-declarative-business-scenario"
journey = "scenario-run"
title = "Run a YAML business scenario with proofs"
ui_text = "A YAML file describes a business journey, its assertions and the proofs to collect during and after the run."
report_text = "This scenario proves that cdpx primitives can be composed into declarative business journeys with a single verdict, findings and a proof dossier."
given = "A disposable Chrome targets a local or Symfony application and a YAML file describes the steps, assertions and captures."
when = "cdpx scenario run executes the steps, continuously collects console/network, captures proofs at checkpoints and evaluates the assertions."
then = "The output contains a single pass/fail verdict, the findings, the executed steps and the produced artifacts."
tests = ["tests/test_scenarios.py::*", "tests/e2e/test_e2e_chrome.py::test_declarative_scenario*", "tests/e2e/test_e2e_symfony.py::test_declarative_scenarios*"]
expected_proofs = ["junit", "json", "screenshot"]
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
against the manifest's authority. For composed commands, the level follows
the verb (`goto`/`wait`: observation; `click`/`type`/`key`: interaction;
`eval`: privileged). `replay` and `scenario` take the maximum level of the
whole file before any CDP effect. Destinations and the real origin are
checked; page content remains an untrusted input. `frame` is an observation.

### `cdpx intercept`

Synopsis: `cdpx intercept --rule "PATTERN => ACTION" [--rule ...] [--settle S] -- goto <url>`

Intercepts network requests during a navigation and applies a deterministic
behavior to them: answering in place of the server with an HTTP code (e.g.
`503`), blocking (`block`, `BlockedByClient` failure), or letting through
(`continue`). Use case: prove that a page degrades cleanly when its API
returns 503, without touching the backend. The command is composed because
`Fetch.enable` dies with the CDP connection: interception can only exist for
the duration of one invocation, so the action to intercept must be executed
within the same command (`-- goto <url>`).

Command-specific options:

- `--rule` (required, repeatable): rule `PATTERN => ACTION`. `PATTERN` is an
  fnmatch pattern (`*api*`) or a substring of the URL; `ACTION` is a numeric
  HTTP code **from 200 to 599** (e.g. `503`, JSON response
  `{"cdpx":"intercept","status":N}`),
  `block` or `continue`. The first matching rule wins; a request with no
  matching rule continues normally.
- `--settle`: quiet period (seconds, default 0.5) after the `load` event
  before concluding the network is stable.
- `action` (after `--`): only `goto <url>`.

```bash
cdpx intercept --rule "*api* => 503" --settle 1 -- goto http://demo.test/
cdpx intercept --rule "*tracker* => block" --rule "*api* => continue" -- goto http://demo.test/produit-42
```

```json
{"url":"http://demo.test/","rules":["*api* => 503"],"hits":[{"url":"http://demo.test/","action":"continue"},{"url":"http://demo.test/api/health","action":"503"}],"count":2,"settle":1.0}
```

Errors and pitfalls: any action other than `goto <url>` after `--` is
rejected. A rule without `=>`, a typo (`typo`), or a status outside
`200..599` fails at parsing **before** `Fetch.enable`/navigation; no default
branch silently continues. If `load` never fires, the command times out.
`intercept` requires `privileged` and an allowed destination. The main
document is intercepted too: an overly broad rule (`* => 503`) breaks the
hosting page.

### `cdpx emulate`

Synopsis: `cdpx emulate [mobile|slow-3g|cpu-4x] [--reset] [-- <action ...>]`

Applies an emulation preset — `mobile` (viewport 390x844, deviceScaleFactor
3, UA `cdpx-mobile/1.0`), `slow-3g` (400 ms latency, 50 KiB/s throughput
upstream and downstream) or `cpu-4x` (CPU throttled 4x) — then, in composed
form, executes an action within the same CDP connection. Use case: check
that a page stays usable on mobile or on a degraded network. The composed
form is essential: emulation overrides DIE with the CDP connection (proven
e2e on Chrome 150), so acting under emulation requires the action to be
passed in the same invocation (`cdpx emulate mobile -- goto
http://demo.test/`).

Command-specific options:

- `preset` (positional, optional): `mobile`, `slow-3g` or `cpu-4x`.
- `--reset`: restores the default state — device metrics, user-agent (fixed
  historical bug: the mobile preset's UA used to survive the reset), network
  conditions and CPU rate. Used without a preset.
- `action` (after `--`): composed action executed under emulation —
  `goto <url>`, `wait <selector>`, `click <selector>`,
  `type <selector> <text> [--clear]`, `key <key>`, `eval <js>`.

```bash
cdpx emulate mobile -- goto http://demo.test/
cdpx emulate slow-3g -- goto http://demo.test/panier
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
(`preset inconnu: None`, exit 1). MAIN PITFALL: `cdpx emulate mobile`
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
cdpx record -o parcours.ndjson -- goto http://demo.test/
cdpx record -o parcours.ndjson -- click "#acheter"
cdpx record -o parcours.ndjson -- type "#password" @env:CHECKOUT_PASSWORD --clear
cdpx record -o parcours.ndjson -- wait "#confirmation"
```

```json
{"schema":"cdpx.record/v2","path":"parcours.ndjson","recorded":1,"replayable":true,"ok":true}
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
cdpx replay parcours.ndjson
cdpx --max-actions 20 replay parcours.ndjson
```

Full successful replay:

```json
{"path":"parcours.ndjson","events":3,"played":3,"ok":true}
```

Divergence (exit 1, the JSON stays structured on stdout):

```json
{"path":"parcours.ndjson","events":3,"played":1,"ok":false,"divergence":"event 1: selector not found after 10.0s: #acheter"}
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

Synopsis: `cdpx scenario run <file.yml> [--settle S]`

Runs a declarative YAML business scenario against the targeted tab. The
scenario describes a context (`base_url`, optional emulation), a suite of
steps, assertions, final proofs and, if needed, proofs to collect at key
moments of the run (`capture` on a step). The output is always a single
JSON object with `verdict` (`pass` or `fail`), `findings`, `steps`,
`assertions`, `artifacts` and `evidence_dir`.

Supported P0 format:

- `context.base_url`: origin or base URL for resolving relative `goto`
  calls.
- `context.emulation`: optional, `mobile`, `slow-3g` or `cpu-4x`, applied
  within the same CDP connection as the steps.
- Steps: `goto`, `wait_visible`, `click`, `type`, `key`, `eval`,
  `wait_text`. `wait_visible` requires an element that is attached,
  rendered, visible and has a non-zero box. `type` accepts only
  `{selector, secret_ref, clear}` and prevalidates the environment
  reference.
- `capture` on a step: a list among `screenshot`, `console`, `network`,
  `profiler`. These proofs are collected immediately after the step, even
  if the step fails.
- Assertions: `no_console_errors`, `network_errors_max`, `text_contains`.
- `artifacts`: same types as `capture`, collected at the end of the
  scenario.

```yaml
name: checkout_guest_add_to_cart
context:
  base_url: http://shop.localhost
  emulation: mobile
steps:
  - label: product_page
    goto: /produit/42
    capture: [screenshot, console, network]
  - label: add_to_cart
    wait_visible: '[data-testid="add-to-cart"]'
  - click: '[data-testid="add-to-cart"]'
    capture: [screenshot, console]
  - type:
      selector: '[name="password"]'
      secret_ref: CHECKOUT_PASSWORD
      clear: true
  - wait_text: ['[data-testid="cart-count"]', '1']
assertions:
  - no_console_errors: true
  - network_errors_max: 0
  - text_contains: ['[data-testid="cart-count"]', '1']
artifacts:
  - screenshot
  - console
  - network
  - profiler
```

```bash
cdpx scenario run checkout_guest_add_to_cart.yml
```

Successful output:

```json
{"name":"checkout_guest_add_to_cart","verdict":"pass","findings":[],"evidence_dir":"/runtime/session/artifacts/scenarios/checkout_guest_add_to_cart-20260706T120000Z","steps":[{"index":0,"label":"product_page","verb":"goto","ok":true}],"assertions":[{"name":"no_console_errors","expected":true,"ok":true,"actual":0}],"artifacts":[{"type":"screenshot","label":"product_page","path":"/runtime/session/artifacts/scenarios/.../000-product_page-screenshot.png","bytes":1234,"mime":"image/png","classification":"opaque-restricted","upload_allowed":false}],"_cdpx":{"content_trust":"untrusted"}}
```

Errors and pitfalls: invalid YAML or an unknown field exits with code 2. A
scenario that runs but does not conform exits with code 1 with
`verdict:"fail"` and structured `findings`. Assertions do not stop at the
first failure: they accumulate findings and then the final proofs are
collected. A `profiler` capture first uses the Symfony headers observed
during the run (`X-Debug-Token-Link` or `X-Debug-Token`); if no header was
seen, cdpx tries the last navigated URL, then adds a `profiler_unavailable`
warning finding if no profiler is available. The collector performs one
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

- Intercept a navigation and force deterministic network outcomes
  (fulfill, block, continue).
- Emulate mobile, slow network or CPU throttling and act within the same
  connection.
- Run a YAML business scenario and get back a verdict, findings and proofs
  collected at checkpoints and at the end of the run.
- Read the text of a same-origin iframe.
- Record actually executed actions then replay the journal with a budget,
  stopping at the first divergence.

## Validation

The unit tests on mock CDP validate the interception rules, the emulation
presets and reset, the execution and logging of `record`, the upfront
validation and actual replay of `replay` (including divergence and the
budget), the YAML business scenarios, the shared action interpreter and the
origin guard. The e2e tests validate real Fetch interception, the
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
  connection (Chrome behavior, verified e2e on Chrome 150). `cdpx emulate
  mobile` alone therefore has no lasting effect — always use the composed
  form `cdpx emulate mobile -- goto http://demo.test/`.
- `intercept` only composes with `goto <url>`; interception cannot yet wrap
  a `click` or a full journey.
- `frame` only reads same-origin iframes (a cross-origin iframe's
  `contentDocument` is inaccessible) and returns the first match.
- Record/replay executes real actions but the action language remains
  deliberately compact (goto, wait, click, type, key, eval) — it is not a
  full browser macro language.
- Replay compares recorded results outside volatile fields; an identical
  result alone does not guarantee the expected business effect. Add an
  observable assertion in a scenario to prove it.
