+++
id = "seo-performance-accessibility"
title = "SEO, performance and accessibility audits"
status = "validated"
summary = "Audit rendered SEO contracts, Web Vitals diagnostics, accessibility tree, RGAA automated-subset front checks and JS/CSS coverage."
entrypoints = ["cdpx seo", "cdpx vitals", "cdpx a11y", "cdpx coverage"]
path_globs = ["src/cdpx/primitives/audit.py", "src/cdpx/primitives/advanced.py", "tests/fixtures/seo*.html", "tests/fixtures/vitals.html", "tests/fixtures/coverage.html", "tests/fixtures/coverage.css", "tests/fixtures/coverage.js", "tests/fixtures/iframe.html", "tests/fixtures/child.html", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**"]
test_globs = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_vitals*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*", "tests/e2e/test_e2e_symfony.py::test_symfony_vitals*", "tests/e2e/test_e2e_symfony.py::test_symfony_rgaa*"]
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

[[journeys]]
id = "audit-front-accessibility"
title = "Audit deterministic front accessibility checks"
entrypoint = "cdpx a11y"

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

[[scenarios]]
id = "compare-symfony-vitals"
journey = "measure-vitals"
title = "Compare Symfony baseline and degraded vitals pages"
ui_text = "The report compares deterministic Symfony performance variants."
report_text = "This scenario proves Web Vitals, Performance metrics and screenshots can be orchestrated against Symfony baseline/degraded pages."
given = "The Symfony scenario engine exposes `/scenario/vitals/baseline` and `/scenario/vitals/degraded`."
when = "cdpx collects vitals, browser metrics, scenario metadata and screenshots for both variants."
then = "The report shows the variant deltas and links the JSON evidence, JUnit, logs and screenshot."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_compare_baseline_degraded"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "symfony-vitals-diagnostic-attribution"
journey = "measure-vitals"
title = "Collect Symfony Web Vitals diagnostic attribution"
ui_text = "The report keeps LCP, INP and CLS primary while showing deterministic attribution diagnostics."
report_text = "This scenario proves Symfony routes for LCP image/text, injected CLS, long-task INP and blocking resources expose thresholds, navigation timing, resource timing buckets, source metadata and emulation metadata as JSON evidence."
given = "The Symfony scenario engine exposes `/scenario/vitals/lcp-image`, `/scenario/vitals/lcp-text`, `/scenario/vitals/cls-injected-banner`, `/scenario/vitals/inp-long-task` and `/scenario/vitals/resource-blocking`."
when = "cdpx collects Web Vitals, deterministic attribution metadata and screenshots from each route."
then = "The proof cockpit links JUnit, JSON diagnostics, Docker logs and screenshots without treating attribution gaps as hidden successes."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_diagnostics_cover_attribution_routes"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "audit-symfony-rgaa-subset"
journey = "audit-front-accessibility"
title = "Audit deterministic Symfony RGAA subset"
ui_text = "The report separates automated RGAA-like checks from full RGAA coverage."
report_text = "This scenario proves RGAA-themed automated checks can be grouped by images, frames, colors, multimedia, tables, links, scripts/components, mandatory elements, structure, presentation, forms, navigation and consultation without claiming full RGAA coverage."
given = "The Symfony scenario engine exposes accessible and regressed pages under `/scenario/rgaa/{case}`."
when = "cdpx reads the accessibility tree and deterministic DOM checks for both variants."
then = "The report includes per-theme JSON checks with criteria, automated scope, status, limitations, JUnit, logs and screenshot evidence."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_rgaa_subset_checks_are_deterministic"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intent

Provide browser-rendered audit primitives for pages where raw HTML is not the
source of truth.

## User journeys

- Check title, metas, canonical, h1, hreflang, JSON-LD, image alt and links.
- Read compact accessibility tree information.
- Measure Web Vitals and JS/CSS coverage.
- Compare Symfony baseline/degraded performance plus LCP, CLS, INP and resource diagnostic variants.
- Review deterministic RGAA automated-subset checks grouped by the 13 RGAA themes with explicit limitations.

## Validation

Fixtures include clean, broken and edge SEO cases plus vitals and coverage
pages. The Symfony Docker scenario engine adds deterministic vitals,
attribution diagnostics and front-accessibility variants.

## Evidence

Expected evidence is JUnit and screenshots from Chrome e2e audit scenarios.
Symfony scenarios also attach JSON vitals, thresholds, resource timing,
attribution metadata, metrics and RGAA automated-subset checks.

## Known gaps

Vitals are local deterministic diagnostics; browser support determines whether
all INP/event-timing and long-task attribution fields are observable. RGAA
coverage is an automated subset grouped by theme and is not presented as a
complete RGAA audit.
