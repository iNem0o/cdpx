+++
id = "dom-interaction"
title = "DOM inspection and user actions"
status = "validated"
summary = "Read rendered text/HTML, evaluate JavaScript, count elements, and perform trusted user input."
entrypoints = ["cdpx eval", "cdpx text", "cdpx html", "cdpx count", "cdpx click", "cdpx type", "cdpx key"]
path_globs = ["src/cdpx/primitives/js.py", "src/cdpx/primitives/inputs.py", "tests/fixtures/form.html"]
test_globs = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*"]
docs = ["docs/PRIMITIVES.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "inspect-dom"
title = "Read a rendered DOM state at low token cost"
entrypoint = "cdpx text"

[[journeys]]
id = "submit-form"
title = "Type and click like a user"
entrypoint = "cdpx type"

[[scenarios]]
id = "inspect-rendered-dom"
journey = "inspect-dom"
title = "Inspect rendered DOM state"
ui_text = "The user can read rendered text, HTML, counts or JavaScript results without a screenshot."
report_text = "This scenario proves that the agent can inspect the browser-rendered state with low-token primitives before deciding the next action."
given = "A fixture page exposes deterministic DOM and JavaScript state."
when = "cdpx evaluates, reads text or counts elements in the rendered page."
then = "The command output gives a compact, reviewable representation of the browser state."
tests = ["tests/test_cli.py::test_eval", "tests/test_cli.py::test_error_path*", "tests/test_primitives.py::test_evaluate*", "tests/test_primitives.py::test_get_text*", "tests/e2e/test_e2e_chrome.py::test_json_endpoint*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "submit-form-like-user"
journey = "submit-form"
title = "Submit a form like a user"
ui_text = "The browser receives trusted click, type and keyboard events."
report_text = "This scenario proves that the CLI can perform user-like DOM interactions and that the resulting state is visible in the proof report."
given = "A local form fixture is loaded in Chrome."
when = "cdpx clicks, types or presses keys through Chrome input domains."
then = "The fixture state changes and the e2e proof keeps a screenshot of the final browser state."
tests = ["tests/test_primitives.py::test_click*", "tests/test_primitives.py::test_type*", "tests/test_primitives.py::test_press_key*", "tests/e2e/test_e2e_chrome.py::test_form*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Expose the browser's rendered state and trusted input primitives in a compact,
repeatable CLI contract.

## User journeys

- Read body or selector text without taking a screenshot.
- Inspect HTML or count elements for cheap assertions.
- Click, type and press keys through Chrome input domains.

## Validation

Mock tests assert emitted CDP protocol; Chrome e2e validates real fixture
interaction.

## Evidence

Expected evidence is JUnit plus screenshots for real form interactions.

## Known gaps

`eval` remains an escape hatch and should be promoted to named primitives when
usage repeats.
