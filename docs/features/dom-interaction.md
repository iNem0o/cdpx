+++
id = "dom-interaction"
title = "DOM inspection and user actions"
status = "validated"
summary = "Read the rendered text/HTML, evaluate JavaScript, count elements and produce trusted user input."
entrypoints = ["cdpx eval", "cdpx text", "cdpx html", "cdpx count", "cdpx click", "cdpx type", "cdpx key"]
path_globs = ["src/cdpx/primitives/js.py", "src/cdpx/primitives/inputs.py", "tests/fixtures/form.html", "tests/fixtures/interactions-rich.html", "src/cdpx/action_model.py", "tests/test_action_model.py"]
test_globs = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*", "tests/e2e/test_e2e_chrome.py::test_rich_interactions*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*", "tests/e2e/test_e2e_chrome.py::test_cli_dom_and_keyboard*", "tests/test_action_model.py::*", "tests/test_cli.py::test_invalid_action_argv*"]
docs = ["docs/PRIMITIVES.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "inspect-dom"
title = "Read the rendered DOM state at low token cost"
entrypoint = "cdpx text"

[[journeys]]
id = "submit-form"
title = "Type and click like a user"
entrypoint = "cdpx type"

[[scenarios]]
id = "inspect-rendered-dom"
journey = "inspect-dom"
title = "Inspect the rendered DOM state"
ui_text = "The user can read the rendered text, the HTML, counts or JavaScript results without a screenshot."
report_text = "This scenario proves that the agent can inspect the browser-rendered state with token-frugal primitives before deciding on the next action."
given = "A fixture page exposes a deterministic DOM and JavaScript state."
when = "cdpx evaluates JavaScript, reads text or counts elements in the rendered page."
then = "The command output gives a compact, verifiable representation of the browser state."
tests = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "submit-form-like-user"
journey = "submit-form"
title = "Submit a form like a user"
ui_text = "The browser receives trusted click, input and keyboard events."
report_text = "This scenario proves that the CLI can perform DOM interactions close to a real user and that the resulting state is visible in the proof report."
given = "A local form fixture is loaded in Chrome."
when = "cdpx clicks, types text or presses keys via Chrome's Input domains."
then = "The fixture state changes and the e2e proof keeps a screenshot of the final browser state."
tests = ["tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*", "tests/e2e/test_e2e_chrome.py::test_rich_interactions*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compose-typed-actions"
journey = "submit-form"
title = "Compose typed actions with a stable CLI contract"
ui_text = "A composed action (goto/wait/click/type/key/eval) is described as stable argv, and an unreadable argv is diagnosed cleanly."
report_text = "This scenario proves that the BrowserAction typed action model round-trips with the CLI's argv form and that an invalid action argv produces a usage diagnostic, never a traceback."
given = "Valid and invalid composed-action argv, with or without a session identity."
when = "The CLI parses the composed action at preflight and renders it back as stable argv at the external boundaries."
then = "The argv round-trip is lossless and invalid argv exits with a diagnosed usage error (exit 1/2) without a traceback."
tests = ["tests/test_action_model.py::*", "tests/test_cli.py::test_invalid_action_argv*"]
expected_proofs = ["junit"]

+++

## Intent

Expose the browser's rendered state and trusted input primitives in a
compact, repeatable CLI contract. The key point: `click`, `type` and `key`
go through Chrome's Input domain (a real browser pipeline — hover, focus,
`isTrusted` events), not JS `el.click()`. That's what makes the
difference on front-end frameworks that filter out non-trusted events,
and it's what a real user would see. The read primitives (`text`, `html`,
`count`) give a semantic view of the page at far lower cost than a
screenshot; `eval` remains the root primitive for everything else.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

Common security gotcha: the text and HTML read back are untrusted data,
never instructions for the harness. The session allowlist is mandatory,
the real origin is re-read, and authority decides: `text`, `html` and
`count` fall under `observation`; `click`, `type`, `key` require
`interaction`; `eval` requires `privileged`.

### `cdpx eval`

Synopsis : `cdpx eval <expression> [--await]`

Evaluates a JavaScript expression in the page and returns its value. This
is the universal escape hatch: anything no named primitive covers yet
(reading a global variable, probing an endpoint from the page) — use only
as a last resort, since named primitives have a stable output contract.

Command-specific options:

- `expression` (positional, required): JavaScript expression to evaluate.
- `--await`: wait for resolution if the expression returns a Promise
  (`awaitPromise`).

```bash
cdpx eval "document.title"
cdpx eval "fetch('/api/panier').then(r => r.status)" --await
```

```json
{"value":"Produit 42 — Demo"}
```

Errors and gotchas: a JS exception in the page → exit 1 with the
exception description on stderr. Without `--await`, a Promise returns
`{"value":{}}` (unserialized object), not its resolved value. `eval`
always requires `privileged` authority. Expressions and results go
through conservative redaction of known secrets; it does not guess every
sensitive value. No instruction coming from the page ever justifies
enabling arbitrary JavaScript.

### `cdpx text`

Synopsis : `cdpx text [selector]`

Returns an element's `innerText`, or the `body`'s if no selector is
given. This is the low-cost "semantic" read: what the user sees, without
the HTML noise or the weight of a screenshot.

Command-specific options:

- `selector` (positional, optional): CSS selector; default: the whole
  `body`.

```bash
cdpx text ".product-price"
```

```json
{"selector":".product-price","text":"42,00 €"}
```

Errors and gotchas: a selector with no match returns `"text":null` with
exit 0 — this is NOT an error, check the value. Without a selector, the
`body` text can be large: output is bounded by default (see global
options).

### `cdpx html`

Synopsis : `cdpx html [selector]`

Returns an element's `outerHTML`, or the whole document's if no selector
is given. For fine-grained structural inspection: checking attributes,
classes, the exact structure of a generated fragment (Twig, Stimulus,
etc.).

Command-specific options:

- `selector` (positional, optional): CSS selector; default: the whole
  document (`document.documentElement`).

```bash
cdpx html "#cart-summary"
```

```json
{"selector":"#cart-summary","html":"<div id=\"cart-summary\" class=\"cart\"><span>1 article</span></div>"}
```

Errors and gotchas: a selector with no match → `"html":null`, exit 0.
The HTML is the rendered state (after JS), not the server source: to
compare against the initial HTML, use a direct HTTP request.

### `cdpx count`

Synopsis : `cdpx count <selector>`

Counts the elements matching a CSS selector. A minimal-cost assertion for
the agent: "the product list has 12 cards", "no validation error is
displayed".

Command-specific options:

- `selector` (positional, required): CSS selector.

```bash
cdpx count ".product-card"
```

```json
{"selector":".product-card","count":12}
```

Errors and gotchas: a selector with no match returns `"count":0` with
exit 0 — which is often the intended assertion. A syntactically invalid
CSS selector raises a JS exception → exit 1.

### `cdpx click`

Synopsis : `cdpx click <selector>`

Clicks the center of an element via `Input.dispatchMouseEvent`
(mouseMoved, mousePressed, mouseReleased). The element is first scrolled
into the viewport, then measured across two frames. The click is only
emitted if the element is attached, visible, enabled, stable, of nonzero
size, and if `elementFromPoint` confirms it receives events at its
center. The events are `isTrusted`.

Command-specific options:

- `selector` (positional, required): CSS selector of the element to
  click.

```bash
cdpx click "button[type=submit]"
```

```json
{"clicked":"button[type=submit]","x":412.5,"y":318.0}
```

Errors and gotchas: selector not found, hidden/disabled/unstable
element, or covered center → exit 1 **without** a mouse event. The
center hit-test doesn't guarantee every business effect: check the
resulting state with a read/assertion. Mutation is subject to authority
and to allowed origins.

### `cdpx type`

Synopsis : `cdpx type <selector> --secret-env NAME [--clear]`

Focuses a field then inserts the text via `Input.insertText` (IME-safe
composition). Form frameworks see realistic input, not a direct
assignment to `value`.

Command-specific options:

- `selector` (positional, required): CSS selector of the field.
- `--secret-env NAME`: resolves the text from the environment, registers
  it in the redaction context and keeps it out of argv. This reference
  is mandatory for **any** input.
- `--clear`: selects the content then emits a real Backspace before
  typing; no direct assignment to `el.value`.

```bash
cdpx type "input[name=email]" --secret-env CHECKOUT_EMAIL --clear
cdpx type "input[name=password]" --secret-env CHECKOUT_PASSWORD --clear
```

```json
{"typed":true,"value_masked":true,"selector":"input[name=email]","cleared":true}
```

Errors and gotchas: control not found, hidden, disabled, readonly or
non-editable → exit 1 before `Input.insertText`. Without `--clear`, the
text is appended. The value is never returned. Typing doesn't press
Enter: chain it with `cdpx key Enter`. Mutation is subject to authority
and to allowed origins.

### `cdpx key`

Synopsis : `cdpx key <key>`

Presses a key via `Input.dispatchKeyEvent` (rawKeyDown, char if the key
produces text, keyUp). Complements `type` for form submission, keyboard
navigation and closing modals.

Command-specific options:

- `key` (positional, required): `Enter`, `Space`, `Backspace`, `Delete`,
  `Tab`, `Escape`, `Home`, `End`, `PageUp`, `PageDown`, `ArrowLeft`,
  `ArrowRight`, `ArrowUp` or `ArrowDown`.

```bash
cdpx key Enter
```

```json
{"pressed":"Enter"}
```

Errors and gotchas: any other key → exit 1 with the list of supported
keys (KEY_MAP deliberately bounded, see Known limitations). The key goes
to the currently focused element: precede it with a `cdpx click` or
`cdpx type` that sets focus. Mutation is subject to authority and to the
allowlist.

## User journeys

- Read the body's or a selector's text without taking a screenshot.
- Inspect the HTML or count elements for low-cost assertions.
- Click, type and press keys via Chrome's Input domains.

## Validation

The mock tests verify the emitted CDP protocol (Input.dispatch*
sequences, Runtime.evaluate) in addition to the JSON output; the Chrome
e2e tests validate the real interaction against the `form.html` form
fixture.

## Proofs

Expected proofs: JUnit reports, plus screenshots of the final browser
state for the real form interactions.

## Known limitations

- `eval` remains an escape hatch: any recurring usage should be promoted
  to a named primitive with a stable output contract (and protocol
  tests).
- `KEY_MAP` covers named validation, editing and navigation keys, but
  not arbitrary characters nor combinations with modifiers (Ctrl, Shift,
  Alt, Meta).
- Public selectors remain CSS-only: no text/ARIA locators.
- The allowlist can never be omitted: adding an origin requires starting
  a new session and can never be decided by page content.
