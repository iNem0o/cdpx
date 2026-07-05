+++
id = "browser-navigation"
title = "Navigation and synchronization"
status = "validated"
summary = "Open pages, select tabs and wait for deterministic browser states before reading or acting."
entrypoints = ["cdpx tabs", "cdpx version", "cdpx goto", "cdpx wait"]
path_globs = ["src/cdpx/discovery.py", "src/cdpx/client.py", "src/cdpx/primitives/nav.py", "tests/test_discovery_and_client.py", "tests/fixtures/index.html", "tests/fixtures/spa.html"]
test_globs = ["tests/test_discovery_and_client.py::*", "tests/test_primitives.py::test_navigate*", "tests/test_primitives.py::test_wait*", "tests/test_cli.py::test_tabs*", "tests/test_cli.py::test_goto", "tests/e2e/test_e2e_chrome.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_wait*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "open-page"
title = "Open a target page and confirm lifecycle completion"
entrypoint = "cdpx goto"

[[journeys]]
id = "wait-spa-content"
title = "Wait for content injected after load"
entrypoint = "cdpx wait"

[[scenarios]]
id = "open-page-success"
journey = "open-page"
title = "Open a target page successfully"
ui_text = "The browser opens a local URL and confirms that the page reached a usable state."
report_text = "This scenario proves that a user can request navigation and receive a deterministic browser state without manual inspection."
given = "A local fixture page is available and Chrome exposes a debuggable target."
when = "cdpx goto opens the URL and waits for lifecycle completion."
then = "The command returns a compact success payload and the page can be captured by the proof run."
tests = ["tests/test_cli.py::test_goto", "tests/test_primitives.py::test_navigate*", "tests/e2e/test_e2e_chrome.py::test_navigate*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "wait-for-rendered-state"
journey = "wait-spa-content"
title = "Wait for rendered content before reading state"
ui_text = "The agent waits for browser-visible content before it reads or acts."
report_text = "This scenario proves synchronization across target discovery, tab selection and late-rendered DOM content."
given = "A target tab exists and a fixture can inject content after initial load."
when = "cdpx waits for a selector or lists the browser targets that can be selected."
then = "The expected target or selector is visible to subsequent primitives."
tests = ["tests/test_discovery_and_client.py::*", "tests/test_cli.py::test_tabs*", "tests/test_primitives.py::test_wait*", "tests/e2e/test_e2e_chrome.py::test_wait*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Give the agent a deterministic way to choose a browser target, navigate, and
wait until a useful state exists.

## User journeys

- Open a URL and receive a compact JSON navigation result.
- Wait for a selector that appears after client-side rendering.
- List, create, activate and close Chrome targets.

## Validation

Validation uses mock CDP protocol tests plus Chrome e2e tests against local
fixtures.

## Evidence

Expected evidence is JUnit plus e2e screenshots for browser-visible journeys.

## Known gaps

None for the local fixture scope.
