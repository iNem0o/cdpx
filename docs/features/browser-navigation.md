+++
id = "browser-navigation"
title = "Navigation and synchronization"
status = "validated"
summary = "Inspect the assigned target, open pages, and wait for deterministic browser states before reading or acting."
entrypoints = ["cdpx tabs", "cdpx version", "cdpx goto", "cdpx wait"]
path_globs = ["src/cdpx/discovery.py", "src/cdpx/client.py", "src/cdpx/primitives/nav.py", "tests/test_discovery_and_client.py", "tests/fixtures/index.html", "tests/fixtures/spa.html", "src/cdpx/cdp_types.py"]
test_globs = ["tests/test_discovery_and_client.py::*", "tests/test_primitives.py::test_navigate*", "tests/test_primitives.py::test_wait*", "tests/test_cli.py::test_tabs*", "tests/test_cli.py::test_goto*", "tests/e2e/test_e2e_chrome.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_cli_browser_lifecycle*", "tests/test_primitives.py::test_event_primitives_reject_negative_budgets*", "tests/test_cli.py::test_connection_failure_exits_1*", "tests/test_cli.py::test_send_failure_exits_1*", "tests/test_cli.py::test_transport_failure_exits_1*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "open-page"
title = "Open a target page and confirm the end of the lifecycle"
entrypoint = "cdpx goto"

[[journeys]]
id = "wait-spa-content"
title = "Wait for content injected after the initial load"
entrypoint = "cdpx wait"

[[scenarios]]
id = "open-page-success"
journey = "open-page"
title = "Successfully open a target page"
ui_text = "The browser opens a local URL and confirms that the page reached a usable state."
report_text = "This scenario proves that a user can request a navigation and obtain a deterministic browser state without manual inspection."
given = "A local fixture page is available and Chrome exposes a debuggable target."
when = "cdpx goto opens the URL and waits for the end of the page lifecycle."
then = "The command returns a compact success payload and the page can be captured by the proof run."
tests = ["tests/test_cli.py::test_goto", "tests/test_primitives.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_navigate*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "wait-for-rendered-state"
journey = "wait-spa-content"
title = "Wait for rendered content before reading the state"
ui_text = "The agent waits for the content to be present in the DOM before reading or acting on it."
report_text = "This scenario proves the synchronization between the assigned target's attestation and DOM content rendered late."
given = "A target tab exists and a fixture can inject content after the initial load."
when = "cdpx waits for a selector or inspects the target assigned to the session."
then = "The target is assigned and the expected selector is attached to the DOM for the following primitives."
tests = ["tests/test_discovery_and_client.py::*", "tests/test_cli.py::test_tabs*", "tests/test_primitives.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_wait*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "diagnose-transport-failures"
journey = "open-page"
title = "Diagnose transport failures and reject invalid budgets"
ui_text = "A CDP connection or send that fails becomes an exit 1 diagnostic, and a negative time budget is rejected before any I/O."
report_text = "This scenario proves that CDP transport failures exit with a diagnosed error on stderr (never a misleading partial success) and that invalid time budgets are rejected before touching the browser."
given = "A CDP transport scripted to fail at connection, at send, or during collection, and negative time budgets."
when = "The CLI runs a browser command and the primitives validate their budget before emitting."
then = "Every transport failure returns exit 1 with its reason on stderr and no CDP message is emitted for an invalid budget."
tests = ["tests/test_cli.py::test_connection_failure_exits_1*", "tests/test_cli.py::test_send_failure_exits_1*", "tests/test_cli.py::test_transport_failure_exits_1*", "tests/test_primitives.py::test_event_primitives_reject_negative_budgets*"]
expected_proofs = ["junit"]

+++

## Intent

Give the agent (or the dev driving it) a Chrome target assigned
deterministically, then let it navigate and wait for a useful state
before any reading or action. While building a Symfony or e-commerce
app, a page "still loading" is a trap: an agent that reads too early
observes an intermediate state and draws false conclusions from it.
`goto` waits for the page lifecycle; `wait` covers client-side rendering
(SPA, content injected via JS); `tabs` and `version` anchor the session
on the right browser and the right tab.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx tabs`

Synopsis: `cdpx tabs list`

Inspects, via `/json`, the single `page` target assigned to the
session. The manifest, the run, and the target are required, either
explicitly or via environment; the command filters discovery on this
exact attestation. The lifecycle of targets belongs to the `cdpx
session` supervisor and is not exposed as a public action.

```bash
cdpx tabs list
```

Output of `list` (collection boundable by `--limit`, with the total count):

```json
{"tabs":[{"id":"4FA1B2C3D4E5F6","type":"page","title":"Product 42","url":"http://demo.test/product-42"}],"count":1,"_cdpx":{"content_trust":"untrusted"}}
```

Errors: exit 1 if the attested endpoint becomes unreachable or if the
target no longer matches the manifest; exit 2 if a session identifier is
missing or if another action is requested. cdpx never targets the
user's personal Chrome: the disposable profile is created and destroyed
by the supervisor.

### `cdpx version`

Synopsis: `cdpx version`

Returns browser information (`/json/version`). Serves as a session
"ping": checking that the debug port responds and identifying the
Chrome version before attributing a behavior to the protocol.

Command-specific options: none.

```bash
cdpx version
```

```json
{"Browser":"Chrome/126.0.6478.61","Protocol-Version":"1.3","User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36","V8-Version":"12.6.228.13","WebKit-Version":"537.36"}
```

Errors (exit 1): the supervised browser no longer responds on its
attested endpoint (`/json/version`).

### `cdpx goto`

Synopsis: `cdpx goto <url> [--wait {load,domcontentloaded,none}]`

Navigates to a URL and waits for the requested lifecycle event before
returning control. This is what keeps the agent from reading
intermediate states: the command only returns once the page has
actually reached the requested state.

Command-specific options:

- `url` (positional, required): target URL.
- `--wait`: expected event — `load` (default), `domcontentloaded`, or
  `none` (immediate return after the navigation is accepted).

```bash
cdpx goto http://demo.test/product-42
cdpx goto http://demo.test/cart --wait domcontentloaded
```

```json
{"url":"http://demo.test/product-42","frameId":"7C93","loaderId":"A1F0","errorText":null,"waited":"load","ok":true,"elapsed_ms":48.2}
```

Errors and gotchas: if Chrome refuses the navigation (DNS, connection
refused), the output carries `"ok":false` with `errorText` populated
(e.g. `net::ERR_CONNECTION_REFUSED`) — check `ok`, not just the exit
code. A lifecycle event that never arrives within the allotted time
(global `--timeout` option) triggers an exit 1. `--wait none` guarantees
nothing about the DOM state: reserve it for cases chained with `cdpx
wait`. The destination is checked before connecting, then
`window.location.href` is read back after navigation: a redirect
outside the allowlist turns the command into a failure before any
following action.

### `cdpx wait`

Synopsis: `cdpx wait <selector>`

Waits for a CSS selector to exist in the DOM, via lightweight polling
(`Runtime.evaluate`, with no residual state injected into the page).
This is the synchronization for SPAs and client-side rendered content:
the `spa.html` fixture from the reference site injects `#late-content`
300 ms after `load`, and `wait` is what makes it possible to read it
reliably.

Command-specific options:

- `selector` (positional, required): CSS selector to wait for. The
  maximum delay comes from the global `--timeout` option (default 15s).

```bash
cdpx wait "#late-content"
```

```json
{"found":true,"selector":"#late-content","elapsed_ms":312.4}
```

Errors and gotchas: selector still absent at the deadline → exit 1
with a diagnostic on stderr (`selector not found after Ns`). `wait`
tests for existence in the DOM, not visibility: an element that is
present but `display:none` is considered found. Always quote the
selector (`"#id"`) to keep the shell from interpreting `#` as a
comment. The `wait_visible` YAML step uses a distinct primitive: it
additionally requires a connected element, visible
`display`/`visibility`, and a non-zero box.

## User journeys

- Open a URL and receive a compact JSON navigation result.
- Wait for a selector that appears after client-side rendering.
- Inspect the single assigned target without being able to modify its
  lifecycle.

## Validation

Validation combines protocol tests on mock CDP (the sequence of
emitted commands IS the spec) with e2e tests on real Chrome against
local fixtures, including `spa.html` for late content.

## Proofs

Expected proofs: JUnit reports, plus e2e screenshots for the
journeys visible in the browser.

## Known limitations

The `wait` CLI only tests DOM presence, not visibility or
interactivity; `scenario wait_visible` covers visibility, while full
actionability remains verified at `click`/`type` time. Content returned
by the page is untrusted and cannot choose another target.
