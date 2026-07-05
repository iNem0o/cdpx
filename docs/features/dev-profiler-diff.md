+++
id = "dev-profiler-diff"
title = "Developer diagnostics"
status = "validated"
summary = "Read and compare Symfony profiler data, then compare DOM before/after a browser action."
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
id = "compare-profiler-variants"
title = "Compare deterministic Symfony profiler variants"
entrypoint = "make docker-symfony-e2e"

[[journeys]]
id = "diff-dom-action"
title = "Compare DOM before and after an action"
entrypoint = "cdpx dom-diff"

[[scenarios]]
id = "read-symfony-profiler"
journey = "read-profiler"
title = "Read Symfony profiler data from navigation"
ui_text = "The agent can open a Symfony page and follow profiler evidence."
report_text = "This scenario proves that framework diagnostics are reachable from browser navigation. `make proof` automatically attempts the real Docker Symfony portal, records unavailable Docker as an explicit non-blocking status, and blocks the verdict when Docker is available but the Symfony scenario fails."
given = "A fixture or Symfony test app exposes profiler-like headers and pages."
when = "cdpx reads profiler data after navigation during Chrome e2e and, when Docker is available, the Symfony e2e portal."
then = "The report links profiler tests, Docker status, JUnit, logs, JSON profiler output and screenshots to the developer diagnostics feature."
tests = ["tests/test_primitives.py::test_profiler*", "tests/e2e/test_e2e_chrome.py::test_profiler*", "tests/e2e/test_e2e_symfony.py::*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compare-symfony-profiler-variants"
journey = "compare-profiler-variants"
title = "Compare Symfony profiler variants"
ui_text = "The report compares deterministic Symfony profiler variants."
report_text = "This scenario proves baseline/degraded, Doctrine-like N+1, cache hit/miss and controlled payload/time signals are available as structured Symfony evidence."
given = "The Symfony scenario engine exposes profiler cases under `/scenario/profiler/{case}`."
when = "cdpx navigates each case, follows the real WebProfiler token and extracts scenario signals from response headers."
then = "The report links JSON comparison evidence, profiler tokens, Docker logs, JUnit and screenshots to the developer diagnostics feature."
tests = ["tests/e2e/test_e2e_symfony.py::test_profiler_compares_deterministic_symfony_variants"]
expected_proofs = ["junit", "json", "screenshot"]

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

[[scenarios]]
id = "symfony-front-state-regression"
journey = "diff-dom-action"
title = "Compare Symfony front state before and after action"
ui_text = "The report shows a deterministic Symfony front state transition."
report_text = "This scenario proves a Symfony route can expose a controlled front state and cdpx can capture the DOM diff after a browser action."
given = "The Symfony scenario engine exposes `/scenario/front/states`."
when = "cdpx snapshots DOM, clicks the state transition button and snapshots DOM again."
then = "The DOM diff and screenshot are attached as Symfony proof evidence."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_front_state_dom_diff"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intent

Give framework-aware diagnostic feedback without requiring the agent to parse a
full browser session manually.

## User journeys

- Navigate to a Symfony route and follow profiler token headers.
- Compare baseline/degraded, Doctrine-like N+1, cache hit/miss and controlled payload/time signals.
- Take a stable DOM diff around a browser action.

## Validation

Unit fixtures simulate profiler headers, including `X-CDPX-*` scenario signals.
`make proof` also attempts the Docker Symfony portal automatically: Docker
unavailable is reported as `unavailable` and non-blocking for local portability,
while a failed Symfony run with Docker available blocks the global proof
verdict.

## Evidence

Expected evidence is JUnit and screenshots for Chrome fixture scenarios. The
Symfony portal adds `.proof/symfony-e2e.log`,
`.proof/symfony-e2e-junit.xml`, profiler comparison JSON, DOM diff JSON and
browser screenshots when Docker is available.

## Known gaps

Docker availability itself is environment-dependent; absence is visible in the
report and can be resolved by installing Docker, then rerunning `make proof` or
`make docker-symfony-e2e`.
