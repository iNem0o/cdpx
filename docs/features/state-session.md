+++
id = "state-session"
title = "State and session controls"
status = "validated"
summary = "Inspect and prepare cookies, localStorage and sessionStorage without leaking secrets by default."
entrypoints = ["cdpx cookies", "cdpx storage"]
path_globs = ["src/cdpx/primitives/state.py", "tests/fixtures/storage.html"]
test_globs = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_set_and_clear*", "tests/test_primitives.py::test_get_storage", "tests/e2e/test_e2e_chrome.py::test_cookies*"]
docs = ["docs/PRIMITIVES.md", "HARNESS.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-session"
title = "Inspect browser session state"
entrypoint = "cdpx cookies"

[[journeys]]
id = "prepare-session"
title = "Set or clear cookies for a repeatable scenario"
entrypoint = "cdpx cookies"

[[scenarios]]
id = "read-session-state"
journey = "read-session"
title = "Read browser session state safely"
ui_text = "The user can inspect cookies and storage without exposing secret values by default."
report_text = "This scenario proves that browser session state is observable while keeping sensitive cookie values masked unless explicitly requested."
given = "A local storage fixture sets cookies and browser storage values."
when = "cdpx reads cookies, localStorage or sessionStorage."
then = "The output is structured and safe for review in the proof report."
tests = ["tests/test_cli.py::test_cookies*", "tests/test_primitives.py::test_cookies*", "tests/test_primitives.py::test_get_storage", "tests/e2e/test_e2e_chrome.py::test_cookies*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "prepare-repeatable-session"
journey = "prepare-session"
title = "Prepare repeatable browser session state"
ui_text = "The agent can set or clear session state before running a scenario."
report_text = "This scenario proves that repeatable browser workflows can prepare cookies before action while preserving the same review trail."
given = "A browser target accepts cookie mutation through CDP."
when = "cdpx sets or clears cookies for the target origin."
then = "Subsequent steps run against a controlled session state."
tests = ["tests/test_primitives.py::test_set_and_clear*"]
expected_proofs = ["junit"]
+++

## Intent

Support repeatable browser scenarios while keeping sensitive values masked
unless explicitly requested.

## User journeys

- Read cookies with values masked by default.
- Set or clear cookies before an action.
- Read localStorage or sessionStorage.

## Validation

Unit tests enforce masking and CDP fallback behavior; e2e tests verify real
fixture state.

## Evidence

Expected evidence is JUnit and e2e screenshots.

## Known gaps

Storage output must still be reviewed before sharing when pages are
authenticated.
