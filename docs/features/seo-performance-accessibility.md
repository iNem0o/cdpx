+++
id = "seo-performance-accessibility"
title = "SEO, performance and accessibility audits"
status = "validated"
summary = "Audit rendered SEO contracts, Web Vitals, accessibility tree and JS/CSS coverage."
entrypoints = ["cdpx seo", "cdpx vitals", "cdpx a11y", "cdpx coverage"]
path_globs = ["src/cdpx/primitives/audit.py", "src/cdpx/primitives/advanced.py", "tests/fixtures/seo*.html", "tests/fixtures/vitals.html", "tests/fixtures/coverage.html", "tests/fixtures/coverage.css", "tests/fixtures/coverage.js", "tests/fixtures/iframe.html", "tests/fixtures/child.html"]
test_globs = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_vitals*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md", "docs/milestones/M4-seo-perf.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "audit-seo-rendered-dom"
title = "Audit SEO on the rendered DOM"
entrypoint = "cdpx seo"

[[journeys]]
id = "measure-vitals"
title = "Measure basic Web Vitals after optional interaction"
entrypoint = "cdpx vitals"

[[scenarios]]
id = "audit-rendered-seo-and-a11y"
journey = "audit-seo-rendered-dom"
title = "Audit rendered SEO and accessibility contracts"
ui_text = "The report shows rendered-page SEO and accessibility checks as product evidence."
report_text = "This scenario proves that browser-rendered audit primitives can validate SEO and accessibility signals that raw HTML would miss."
given = "SEO, edge-case and iframe fixtures are available in a real browser."
when = "cdpx runs SEO, accessibility tree or coverage-oriented audit primitives."
then = "The resulting checks are attached to the feature with JUnit and browser screenshots."
tests = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "measure-local-vitals"
journey = "measure-vitals"
title = "Measure local Web Vitals"
ui_text = "The user can measure basic Web Vitals after optional interaction."
report_text = "This scenario proves that performance-oriented browser measurements are available as compact local-fixture evidence."
given = "A vitals fixture is loaded in Chrome."
when = "cdpx vitals collects the supported browser performance signals."
then = "The result is reported with test coverage and a screenshot-backed e2e scenario."
tests = ["tests/test_primitives.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_vitals*"]
expected_proofs = ["junit", "screenshot"]
+++

## Intent

Provide browser-rendered audit primitives for pages where raw HTML is not the
source of truth.

## User journeys

- Check title, metas, canonical, h1, hreflang, JSON-LD, image alt and links.
- Read compact accessibility tree information.
- Measure Web Vitals and JS/CSS coverage.

## Validation

Fixtures include clean, broken and edge SEO cases plus vitals and coverage
pages.

## Evidence

Expected evidence is JUnit and screenshots from Chrome e2e audit scenarios.

## Known gaps

Vitals are intentionally basic and local-fixture oriented.
