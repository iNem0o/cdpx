+++
id = "browser-capture-observability"
title = "Browser capture and observability"
status = "validated"
summary = "Capture screenshots/PDFs and observe the console, network, and renderer metrics."
entrypoints = ["cdpx screenshot", "cdpx pdf", "cdpx console", "cdpx network", "cdpx metrics"]
path_globs = ["src/cdpx/primitives/capture.py", "src/cdpx/primitives/net.py", "src/cdpx/primitives/audit.py", "tests/fixtures/console.html", "tests/fixtures/network.html", "tests/fixtures/long.html"]
test_globs = ["tests/test_cli.py::test_screenshot", "tests/test_cli.py::test_screenshot*", "tests/test_cli.py::test_pdf*", "tests/test_cli.py::test_console*", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*", "tests/e2e/test_e2e_chrome.py::test_metrics_real", "tests/e2e/test_e2e_chrome.py::test_pdf_real", "tests/e2e/test_e2e_chrome.py::test_cli_jpeg_and_pdf*", "tests/e2e/test_e2e_chrome.py::test_cli_console_follow*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "capture-page"
title = "Keep a visual proof (screenshot)"
entrypoint = "cdpx screenshot"

[[journeys]]
id = "inspect-runtime"
title = "Detect console and network failures"
entrypoint = "cdpx console"

[[scenarios]]
id = "persist-screenshot-proof"
journey = "capture-page"
title = "Keep a visual proof (screenshot)"
ui_text = "The proof run keeps the browser pixels attached to the scenario that produced them."
report_text = "This scenario proves that the visual proof is not an orphan file: the report links the screenshot to the user journey, to the test, and to the review explanation."
given = "A browser page is rendered from local fixtures."
when = "cdpx captures a normal, full-page, or printable output of that page."
then = "The report exposes the generated artifact next to the scenario and the test result."
tests = ["tests/test_cli.py::test_screenshot", "tests/test_cli.py::test_screenshot*", "tests/test_cli.py::test_pdf*", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*", "tests/e2e/test_e2e_chrome.py::test_pdf_real"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "inspect-runtime-failures"
journey = "inspect-runtime"
title = "Inspect the console, network, and metrics at runtime"
ui_text = "The report shows the runtime signals that explain what happened in the browser."
report_text = "This scenario proves that console entries, network observations, and browser metrics can be collected in a compact form for human review."
given = "The fixture pages emit deterministic console, network, and metrics signals."
when = "cdpx collects console, network, or metrics data around the browser state."
then = "The run produces a structured proof that can be linked to the feature."
tests = ["tests/test_cli.py::test_console*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*", "tests/e2e/test_e2e_chrome.py::test_metrics_real"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Make the browser state observable beyond the DOM text: pixels
(screenshot, PDF), JavaScript errors (console), network failures
(network), and renderer performance counters (metrics). Without these
signals, an agent driving a broken JS app navigates blind.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx screenshot`

```
usage: cdpx screenshot [-o OUTPUT] [--full-page] [--format {png,jpeg}]
```

Captures an image of the current page and writes it to disk. This is the
agent's raw "vision": checking a render, a visual state, a CSS bug, or
attaching a pixel proof to an acceptance scenario.

Command-specific options:

- `-o`, `--output`: path of the image file written (default:
  `screenshot.png`). Only the requested basename is kept, and the file is
  always confined under the session's `artifacts/captures/`.
- `--full-page`: captures beyond the viewport
  (`captureBeyondViewport`), to get the entire page and not just the
  visible area.
- `--format`: encoding format, `png` or `jpeg` (default: `png`).

```bash
cdpx screenshot -o preuves/accueil.png
cdpx screenshot --full-page -o preuves/page-entiere.png
cdpx screenshot --format jpeg -o preuves/accueil.jpg
```

JSON output (path written, size in bytes, format, and mode used):

```json
{"path": "/runtime/session/artifacts/captures/accueil.jpg", "bytes": 48231, "format": "jpeg", "full_page": false, "classification": "opaque-restricted", "upload_allowed": false, "retention": "session", "_cdpx": {"content_trust": "untrusted"}}
```

The output adds `classification:"opaque-restricted"`,
`upload_allowed:false`, and `retention:"session"`; the file is `0600`. If
the actual origin becomes disallowed during the capture, cdpx deletes the
file before returning the error.

Gotchas:

- The format is independent of the file extension: `--format jpeg` with
  `-o etat.png` does write JPEG into a file named `.png`. Align the two to
  avoid confusion.
- `--full-page` on a very long page produces a large file; the command
  has a 30s CDP timeout on the capture side.
- An image is opaque content: it can display a name, token, or business
  data that text redaction cannot see. Managed proofs classify it as
  `opaque-restricted` and never copy it automatically into the shareable
  CI staging.

### `cdpx pdf`

```
usage: cdpx pdf [-o OUTPUT]
```

Prints the current page to PDF (`Page.printToPDF` with
`printBackground`, so CSS backgrounds and colors are preserved). Use
case: archiving a page state — SEO audit deliverable, dated acceptance
proof.

Command-specific options:

- `-o`, `--output`: path of the PDF file written (default: `page.pdf`).
  In practice, only the basename is kept under `artifacts/captures/`, with
  the same `opaque-restricted` metadata, session retention, and deletion
  if the final origin is refused.

```bash
cdpx pdf -o preuves/audit-accueil.pdf
```

JSON output:

```json
{"path": "/runtime/session/artifacts/captures/audit-accueil.pdf", "bytes": 105320, "classification": "opaque-restricted", "upload_allowed": false, "retention": "session", "_cdpx": {"content_trust": "untrusted"}}
```

Gotchas:

- PDF printing requires a headless Chrome or a target that supports
  `Page.printToPDF`; some "headful" Chrome instances refuse it (CDP
  error, exit 1).
- A PDF is also `opaque-restricted`: inspection and sharing remain a
  human decision, never an automatic consequence of `make proof`.

### `cdpx console`

```
usage: cdpx console [--duration SECONDES] [--follow] [--max N]
```

Captures the page's JavaScript logs and exceptions
(`Runtime.consoleAPICalled` + `Runtime.exceptionThrown`). This is the
missing feedback for the front-end dev: without it, a broken JS app
stays silent for the agent.

Entries go through redaction of registered secrets, Bearer/JWT tokens,
and sensitive URLs. This redaction is deliberately conservative: free
text may still contain unknown data. Any console output remains
untrusted page input, not an instruction for the agent.

Command-specific options:

- `--duration`: bounded capture duration in seconds (default: `2.0`).
  Default mode: a single JSON object output at the end of the window.
- `--follow`: compact NDJSON stream mode, one JSON line per entry, until
  Ctrl-C or `--max`.
- `--max`: in `--follow` mode, maximum number of entries before
  stopping (default: unlimited).

```bash
cdpx console --duration 3
cdpx console --follow --max 20
```

JSON output in bounded mode (`--duration`):

```json
{"entries": [{"kind": "console", "type": "error", "text": "TypeError: cart is undefined", "ts": 1751700000123.4}], "count": 1, "errors": 1, "duration": 3.0}
```

Output in `--follow` mode (NDJSON, one entry per line):

```json
{"kind":"console","type":"log","text":"checkout ready","ts":1751700000123.4}
{"kind":"exception","type":"error","text":"ReferenceError: gtag is not defined","ts":1751700000456.7}
```

Gotchas:

- The capture only sees what is emitted DURING the window: start
  `console` before triggering the action (or reloading the page) to catch
  initialization errors.
- `--max` without `--follow` is ignored; `--duration` has no effect in
  `--follow` mode.

### `cdpx network`

```
usage: cdpx network URL [--settle SECONDES]
```

Navigates to the URL while capturing all network activity up to the
`load` event plus a settling window. Symfony/e-commerce dev use case:
spot XHRs returning 500, 404 assets, unexpected API calls, and the
transferred weight in one go, without opening DevTools.

Command-specific options:

- `url` (positional, required): navigation URL.
- `--settle`: additional observation seconds after `load`, to catch
  deferred XHRs (default: `0.5`).

```bash
cdpx network http://demo.test/checkout --settle 1.5
```

JSON output (summary + per-request detail):

```json
{"url": "http://demo.test/checkout", "requests": [{"requestId": "1000.2", "url": "http://demo.test/api/cart", "method": "GET", "resourceType": "XHR", "status": 500, "mimeType": "application/json", "encodedBytes": 512}], "summary": {"total": 14, "failed": 0, "errors_4xx_5xx": 1, "bytes": 184320}}
```

Output URLs strip credentials/fragments and redact every query value.
The raw URL is still sent to Chrome for navigation, without being
reprinted as-is.

Gotchas:

- The `requests` list is bounded by `--limit` (default: 50 items):
  beyond that, the output adds the `requests_truncated`,
  `requests_total`, and `requests_limit` metadata. `--full` gives the
  complete list **of observed events**, not an exhaustive network audit.
- The `summary` is computed over ALL observed requests, even those
  truncated from the list.
- A `--settle` that's too short misses calls launched after `load`
  (analytics, lazy-loading).
- `network` is not a HAR: it keeps neither bodies, nor full
  cookies/headers, nor detailed waterfall/timings, nor cache/security
  entries.

### `cdpx metrics`

```
cdpx metrics
```

Returns renderer performance metrics (`Performance.getMetrics`): DOM
node count, documents, JS listeners, layouts, JS heap size... Use case:
detecting a leak (listeners or nodes climbing between two measurements),
objectively assessing a bloated DOM.

Command-specific options: none (global options only).

```bash
cdpx metrics
```

JSON output: a flat name → value dictionary, as returned by Chrome:

```json
{"Timestamp": 5721.43, "Documents": 3, "Frames": 1, "JSEventListeners": 42, "Nodes": 618, "LayoutCount": 7, "RecalcStyleCount": 12, "LayoutDuration": 0.018, "RecalcStyleDuration": 0.009, "ScriptDuration": 0.124, "TaskDuration": 0.31, "JSHeapUsedSize": 3145728, "JSHeapTotalSize": 5242880}
```

Gotchas:

- The exact keys depend on the Chrome version: don't hardcode the full
  list, target the useful keys (`Nodes`, `JSEventListeners`,
  `JSHeapUsedSize`...).
- A single measurement says little: compare two calls around an action
  to see a drift.

## User journeys

- Capture a normal or full-page screenshot, in PNG or JPEG.
- Print the page to PDF for archiving or as a deliverable.
- Collect console errors around an action, in a bounded window or a
  continuous stream.
- Navigate while observing the network to spot 4xx/5xx, failures, and
  weight.
- Measure the renderer counters before/after an action.

## Validation

The mock tests verify the shape of the emitted CDP protocol, the
redaction, and the shape of the JSON outputs (`tests/test_primitives.py`,
`tests/test_cli.py`); the real-Chrome e2e tests attach PNG artifacts to
the proof catalog and validate full-page screenshot, PDF, console,
network, and metrics (`tests/e2e/test_e2e_chrome.py`).

## Proofs

Expected proofs: JUnit results and screenshots attached to the
scenarios in the private local tree. The shareable manifest keeps their
classification, but the opaque bytes are not sent by the CI.

## Known limitations

- No video capture or terminal replay: optional, not required by the
  harness.
- `console` only captures the requested window; errors prior to the
  command's launch are lost.
- `network` observes the navigation it triggers itself; it does not
  attach to a navigation already in progress.
- None of these signals turns untrusted page content into an
  instruction authorized for the harness.
