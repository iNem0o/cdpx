+++
id = "browser-capture-observability"
title = "Capture and browser observability"
status = "validated"
summary = "Capture screenshots/PDFs and observe console, network and renderer metrics."
entrypoints = ["cdpx screenshot", "cdpx pdf", "cdpx console", "cdpx network", "cdpx metrics"]
path_globs = ["src/cdpx/primitives/capture.py", "src/cdpx/primitives/net.py", "src/cdpx/primitives/audit.py", "tests/fixtures/console.html", "tests/fixtures/network.html", "tests/fixtures/long.html"]
test_globs = ["tests/test_cli.py::test_screenshot", "tests/test_cli.py::test_console*", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "capture-page"
title = "Persist a screenshot proof"
entrypoint = "cdpx screenshot"

[[journeys]]
id = "inspect-runtime"
title = "Detect console and network failures"
entrypoint = "cdpx console"

[[scenarios]]
id = "persist-screenshot-proof"
journey = "capture-page"
title = "Persist a screenshot proof"
ui_text = "The proof run keeps browser pixels attached to the scenario that produced them."
report_text = "This scenario proves that visual evidence is not just a detached file: the report links the screenshot to the user journey, test and review explanation."
given = "A browser page is rendered from local fixtures."
when = "cdpx captures normal, full-page or printable output from that page."
then = "The report exposes the generated artifact next to the scenario and test result."
tests = ["tests/test_cli.py::test_screenshot", "tests/test_primitives.py::test_screenshot*", "tests/test_primitives.py::test_pdf*", "tests/e2e/test_e2e_chrome.py::test_screenshot*", "tests/e2e/test_e2e_chrome.py::test_full_page*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "inspect-runtime-failures"
journey = "inspect-runtime"
title = "Inspect runtime console, network and metrics"
ui_text = "The report shows runtime signals that explain what happened in the browser."
report_text = "This scenario proves that console entries, network observations and browser metrics can be collected in a compact form for human review."
given = "Fixture pages emit deterministic console, network and metric signals."
when = "cdpx collects console, network or metric data around the browser state."
then = "The run produces structured evidence that can be linked back to the feature."
tests = ["tests/test_cli.py::test_console*", "tests/test_primitives.py::test_console*", "tests/test_primitives.py::test_network*", "tests/test_primitives.py::test_metrics", "tests/e2e/test_e2e_chrome.py::test_console*", "tests/e2e/test_e2e_chrome.py::test_network*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Make browser state observable beyond DOM text: pixels, JavaScript errors,
network failures and performance counters.

## User journeys

- Capture a normal or full-page screenshot.
- Print the page as PDF.
- Collect console errors and network requests around a navigation.

## Validation

Mock tests cover protocol shape; e2e tests attach PNG artifacts to the proof
catalog.

## Evidence

Expected evidence is JUnit and screenshot artifacts.

## Known gaps

Video or terminal replay remains optional and is not required by the harness.
