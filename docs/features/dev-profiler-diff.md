+++
id = "dev-profiler-diff"
title = "Developer diagnostics"
status = "validated"
summary = "Read Symfony profiler data and compare DOM before/after a browser action."
entrypoints = ["cdpx profiler", "cdpx dom-diff", "make docker-symfony-e2e"]
path_globs = ["src/cdpx/primitives/dev.py", "tests/fixtures/profiler*.html", "tests/fixtures/form.html", "docker-compose.symfony-e2e.yml", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**"]
test_globs = ["tests/test_primitives.py::test_profiler*", "tests/test_primitives.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*", "tests/e2e/test_e2e_symfony.py::*"]
docs = ["docs/PRIMITIVES.md", "docs/milestones/M2-boucle-symfony.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "read-profiler"
title = "Read Symfony profiler from a browser navigation"
entrypoint = "cdpx profiler"

[[journeys]]
id = "diff-dom-action"
title = "Compare DOM before and after an action"
entrypoint = "cdpx dom-diff"

[[scenarios]]
id = "read-symfony-profiler"
journey = "read-profiler"
title = "Read Symfony profiler data from navigation"
ui_text = "The agent can open a Symfony page and follow profiler evidence."
report_text = "This scenario proves that framework diagnostics are reachable from browser navigation, including the explicit Docker portal when a real Symfony app is required."
given = "A fixture or Symfony test app exposes profiler-like headers and pages."
when = "cdpx reads profiler data after navigation."
then = "The report links profiler tests and artifacts to the developer diagnostics feature."
tests = ["tests/test_primitives.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_symfony.py::*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "diff-dom-after-action"
journey = "diff-dom-action"
title = "Compare DOM before and after a browser action"
ui_text = "The report explains what changed in the DOM after an action."
report_text = "This scenario proves that DOM changes can be compared around a controlled browser action and reviewed as developer evidence."
given = "A fixture page has a stable before state and a user action that mutates DOM."
when = "cdpx records DOM before and after the action."
then = "The diff is available as structured test evidence with browser screenshots for e2e coverage."
tests = ["tests/test_primitives.py::test_dom_diff*", "tests/e2e/test_e2e_chrome.py::test_dom_diff*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Give framework-aware diagnostic feedback without requiring the agent to parse a
full browser session manually.

## User journeys

- Navigate to a Symfony route and follow profiler token headers.
- Take a stable DOM diff around a browser action.

## Validation

Unit fixtures simulate profiler headers; Docker e2e validates a real Symfony
profiler when explicitly run.

## Evidence

Expected evidence is JUnit and screenshots for Chrome fixture scenarios.

## Known gaps

The Docker Symfony portal is separate from default `make proof`.
