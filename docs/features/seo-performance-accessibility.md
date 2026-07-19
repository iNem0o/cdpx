+++
id = "seo-performance-accessibility"
title = "SEO, performance, and accessibility audits"
status = "validated"
summary = "Audit the SEO contract of the rendered DOM, Web Vitals diagnostics, the accessibility tree, an automated front-end RGAA subset, and JS/CSS coverage."
entrypoints = ["cdpx seo", "cdpx vitals", "cdpx a11y", "cdpx coverage"]
path_globs = ["src/cdpx/primitives/audit.py", "src/cdpx/primitives/diagnostics.py", "src/cdpx/primitives/frames.py", "tests/fixtures/seo*.html", "tests/fixtures/vitals.html", "tests/fixtures/coverage.html", "tests/fixtures/coverage.css", "tests/fixtures/coverage.js", "tests/fixtures/iframe.html", "tests/fixtures/child.html", "tests/e2e/test_e2e_symfony.py", "tests/symfony-app/**"]
test_globs = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_vitals*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*", "tests/e2e/test_e2e_symfony.py::test_symfony_vitals*", "tests/e2e/test_e2e_symfony.py::test_symfony_rgaa*"]
docs = ["docs/PRIMITIVES.md", "docs/VALIDATION.md"]
expected_proofs = ["junit", "screenshot"]

[[journeys]]
id = "audit-seo-rendered-dom"
title = "Audit SEO on the rendered DOM"
entrypoint = "cdpx seo"

[[journeys]]
id = "measure-vitals"
title = "Measure basic Web Vitals after an optional interaction"
entrypoint = "cdpx vitals"

[[journeys]]
id = "audit-front-accessibility"
title = "Audit deterministic front-end accessibility checks"
entrypoint = "cdpx a11y"

[[scenarios]]
id = "audit-rendered-seo-and-a11y"
journey = "audit-seo-rendered-dom"
title = "Audit the SEO and accessibility contracts of the rendered DOM"
ui_text = "The report presents the SEO and accessibility checks of the rendered page as product proofs."
report_text = "This scenario proves that browser-rendered audit primitives validate SEO and accessibility signals that raw HTML would not show."
given = "The SEO, edge-case, and iframe fixtures are available in a real browser."
when = "cdpx runs the SEO audit, accessibility tree, or coverage primitives."
then = "The resulting checks are attached to the feature with JUnit and browser screenshots."
tests = ["tests/test_cli.py::test_seo*", "tests/test_primitives.py::test_seo*", "tests/test_primitives.py::test_a11y*", "tests/test_primitives.py::test_coverage*", "tests/e2e/test_e2e_chrome.py::test_seo*", "tests/e2e/test_e2e_chrome.py::test_a11y*", "tests/e2e/test_e2e_chrome.py::test_coverage*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "measure-local-vitals"
journey = "measure-vitals"
title = "Measure Web Vitals locally"
ui_text = "The user can measure basic Web Vitals after an optional interaction."
report_text = "This scenario proves that browser performance measurements are available as compact proofs on local fixtures."
given = "A vitals fixture is loaded in Chrome."
when = "cdpx vitals collects the supported browser performance signals."
then = "The result is reported with its test coverage and an e2e scenario backed by a screenshot."
tests = ["tests/test_primitives.py::test_vitals*", "tests/e2e/test_e2e_chrome.py::test_vitals*"]
expected_proofs = ["junit", "screenshot"]

[[scenarios]]
id = "compare-symfony-vitals"
journey = "measure-vitals"
title = "Compare the Symfony vitals baseline and degraded pages"
ui_text = "The report compares deterministic Symfony performance variants."
report_text = "This scenario proves that Web Vitals, Performance metrics, and screenshots can be orchestrated against the Symfony baseline/degraded pages."
given = "The Symfony scenario engine exposes `/scenario/vitals/baseline` and `/scenario/vitals/degraded`."
when = "cdpx collects the vitals, browser metrics, scenario metadata, and screenshots for both variants."
then = "The report shows the deltas between variants and links the JSON, JUnit, logs, and screenshot proofs."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_compare_baseline_degraded"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "symfony-vitals-diagnostic-attribution"
journey = "measure-vitals"
title = "Collect diagnostic attribution for Symfony Web Vitals"
ui_text = "The report keeps LCP, INP, and CLS front and center while showing deterministic attribution diagnostics."
report_text = "This scenario proves that the Symfony routes for LCP image/text, injected CLS, INP long-task, and blocking resources expose thresholds, navigation timing, resource timing buckets, source metadata, and emulation metadata as JSON proofs."
given = "The Symfony scenario engine exposes `/scenario/vitals/lcp-image`, `/scenario/vitals/lcp-text`, `/scenario/vitals/cls-injected-banner`, `/scenario/vitals/inp-long-task`, and `/scenario/vitals/resource-blocking`."
when = "cdpx collects the Web Vitals, deterministic attribution metadata, and screenshots for each route."
then = "The proof cockpit links JUnit, JSON diagnostics, Docker logs, and screenshots without turning attribution gaps into hidden successes."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_vitals_diagnostics_cover_attribution_routes"]
expected_proofs = ["junit", "json", "screenshot"]

[[scenarios]]
id = "audit-symfony-rgaa-subset"
journey = "audit-front-accessibility"
title = "Audit the deterministic Symfony RGAA subset"
ui_text = "The report distinguishes automated RGAA-inspired checks from full RGAA coverage."
report_text = "This scenario proves that automated RGAA-themed checks can be grouped by images, frames, colors, multimedia, tables, links, scripts/components, mandatory elements, structure, presentation, forms, navigation, and consultation without claiming full RGAA coverage."
given = "The Symfony scenario engine exposes accessible and regressed pages under `/scenario/rgaa/{case}`."
when = "cdpx reads the accessibility tree and deterministic DOM checks for both variants."
then = "The report includes JSON checks per theme with criteria, automated scope, status, limitations, JUnit, logs, and screenshots as proofs."
tests = ["tests/e2e/test_e2e_symfony.py::test_symfony_rgaa_subset_checks_are_deterministic"]
expected_proofs = ["junit", "json", "screenshot"]
+++

## Intent

Provide browser-rendered audit primitives for pages where raw HTML is not
the source of truth. On a JS front end, an injected canonical, JSON-LD set
by GTM, or rewritten hreflang only exist in the final DOM — and that final
DOM is what Googlebot evaluates at rendering time. Auditing the HTTP
response is therefore not enough: `cdpx seo`, `cdpx vitals`, `cdpx a11y`,
and `cdpx coverage` measure the page as the user (and the crawler in
rendering mode) actually receives it.

## Usage

Global options and exit codes: see the CLI Contract section of the README.

### `cdpx seo`

Synopsis: `cdpx seo [url]`

Extracts, in a single call, the on-page SEO contract of the **rendered**
DOM: title, metas, canonical, robots, h1, hreflang, JSON-LD blocks
(validated against a minimal `Product` schema: `sku` or `name` required),
images without `alt`, internal/external/nofollow link counts, a pixel
estimate of the SERP width of the title and meta description, and
duplicate-h1 detection. Anomalies are aggregated in `findings` (an empty
list means no issue was detected).

Specific options:

- `url` (positional, optional) — navigate to this URL first. Without
  `url`, the command audits the page currently displayed in the target
  tab: handy for auditing a state reached after interactions (open cart,
  selected variant, SPA page after a client-side route).

```bash
# Audit a product page after navigation and rendering
cdpx seo https://shop.example.test/product-42

# Audit the current page, without navigating (post-interaction state)
cdpx seo

# Human-readable output
cdpx --pretty seo https://shop.example.test/product-42
```

Output (realistic excerpt):

```json
{
  "url": "https://shop.example.test/product-42",
  "lang": "en",
  "title": "Vertex 42 trail shoes | Example Store",
  "metas": {
    "description": "Vertex 42 trail shoes with maximum grip.",
    "robots": "index,follow",
    "og:title": "Vertex 42 trail shoes"
  },
  "canonical": "https://shop.example.test/product-42",
  "robots": "index,follow",
  "h1": ["Vertex 42 trail shoes"],
  "hreflang": [
    {"lang": "en", "href": "https://shop.example.test/product-42"},
    {"lang": "de", "href": "https://shop.example.test/de/product-42"}
  ],
  "jsonld": [
    {"@type": "Product", "name": "Vertex 42", "sku": "VTX-42"}
  ],
  "images_without_alt": 2,
  "links": {"internal": 34, "external": 3, "nofollow": 1},
  "title_px_estimate": 331,
  "description_px_estimate": 353,
  "findings": ["2 image(s) without alt text"]
}
```

Pitfalls and edge cases:

- Without `url`, a page must already be loaded in the target tab; on
  `about:blank` the audit returns an almost-empty contract with many
  `findings`.
- An unparsable JSON-LD block is reported (`"invalid JSON-LD"` in
  `findings`) instead of failing the command.
- The `*_px_estimate` values are a stable approximation for agent/CI use
  (average desktop SERP width), not a pixel-perfect rendering.

### `cdpx vitals`

Synopsis: `cdpx vitals url [--click SELECTOR] [--settle S]`

Measures LCP, CLS, and INP via `PerformanceObserver` instances pre-injected
**before** navigation (`Page.addScriptToEvaluateOnNewDocument`), which
captures buffered entries from the very first paint. The optional
`--click` interaction fires a real event to feed the INP measurement.

Specific options:

- `url` (positional, required) — page to measure.
- `--click SELECTOR` — CSS selector to click after loading to measure
  INP (without a click, `inp` stays at 0).
- `--settle S` — delay in seconds left for the observers to collect
  entries after loading/interaction (default: 0.5).

```bash
# Measure loading vitals
cdpx vitals https://shop.example.test/product-42

# Measure INP by clicking the add-to-cart button
cdpx vitals https://shop.example.test/product-42 --click "#add-to-cart" --settle 1.0
```

Output:

```json
{
  "url": "https://shop.example.test/product-42",
  "lcp": 812.4,
  "cls": 0.031,
  "inp": 96
}
```

Pitfalls and edge cases:

- `inp` is 0 without `--click` (no interaction means nothing to measure).
- The `event` observer (INP) is optional depending on browser support: its
  absence is not an error, the value simply stays at 0.
- A `--settle` that is too short can underestimate CLS/LCP on pages that
  inject content late.

### `cdpx a11y`

Synopsis: `cdpx a11y`

Returns the compacted accessibility tree (AXTree) of the current page: a
low-token-cost **semantic** view of the page. Each non-ignored node
exposes its `role` and `name` in Chrome's compact AX view. It is a useful
signal to check labels, heading structure, and landmark regions without
parsing the full HTML, but it is not an exhaustive stand-in for every
screen reader.

Specific options: none (the command operates on the current page of the
target tab; navigate first with `cdpx goto` if needed).

```bash
cdpx goto https://shop.example.test/product-42
cdpx a11y

# Human-readable output
cdpx --pretty a11y
```

Output:

```json
{
  "nodes": [
    {"role": "RootWebArea", "name": "Vertex 42 trail shoes", "ignored": false},
    {"role": "banner", "name": "", "ignored": false},
    {"role": "heading", "name": "Vertex 42 trail shoes", "ignored": false},
    {"role": "button", "name": "Add to cart", "ignored": false},
    {"role": "link", "name": "Size guide", "ignored": false}
  ],
  "count": 5
}
```

Pitfalls and edge cases:

- `ignored` nodes are filtered out: an element invisible to the
  accessibility API does not appear, which is exactly the signal being
  looked for (unnamed button, icon without an alternative...).
- On large pages the list is bounded by `--limit` (50 by default); use
  `--full` to see everything.

### `cdpx coverage`

Synopsis: `cdpx coverage url`

Measures dead JS and CSS after loading a page: precise per-file JS
coverage (`Profiler.takePreciseCoverage`) and CSS rule usage
(`CSS.stopRuleUsageTracking`). Use case: quantify the weight of code that
is never executed (oversized bundles, unused theme CSS) before a
performance workstream.

Specific options:

- `url` (positional, required) — page to load under instrumentation (the
  tracking starts before navigation so nothing is missed).

```bash
cdpx coverage https://shop.example.test/product-42
```

Output:

```json
{
  "url": "https://shop.example.test/product-42",
  "files": [
    {"url": "https://shop.example.test/assets/app.js", "functions": 214, "used_ranges": 87},
    {"url": "https://shop.example.test/assets/vendor.js", "functions": 1032, "used_ranges": 240}
  ],
  "count": 2,
  "css": {"rules": 418, "used": 137, "unused": 281}
}
```

Pitfalls and edge cases:

- The measurement reflects loading alone: code executed only after an
  interaction (menus, carousels) counts as "dead" if no interaction
  happens.
- Inline scripts appear with an empty `url` or the document's own URL.

## User journeys

- Check title, metas, canonical, h1, hreflang, JSON-LD, image alt text,
  and links on the rendered DOM.
- Read the compact accessibility tree as a semantic view of the page.
- Measure Web Vitals and JS/CSS coverage.
- Compare the Symfony baseline/degraded performance variants as well as
  the LCP, CLS, INP, and resource diagnostic variants.
- Review the deterministic automated RGAA checks grouped by the 13 RGAA
  themes, with explicit limitations.

## Validation

The fixtures cover clean, broken, and edge-case SEO scenarios, plus vitals
and coverage pages. The Symfony scenario engine under Docker adds
deterministic variants for vitals, attribution diagnostics, and front-end
accessibility.

## Proofs

Expected proofs are JUnit and screenshots from the Chrome e2e audit
scenarios. The Symfony scenarios additionally attach vitals JSON,
thresholds, resource timing, attribution metadata, metrics, and checks
from the automated RGAA subset.

## Known limitations

- `seo` checks the rendered DOM of **the current page**. It does not crawl
  a site, and it does not verify actual indexing, server robots,
  backlinks, logs, or Search Console data.
- `vitals` is a local diagnostic bounded by the browser and the
  `--settle` window, not a multi-run lab methodology nor a field
  CrUX/RUM measurement. Browser support determines which INP/event
  timing signals are available.
- `a11y` compacts the AXTree and is neither a test with real assistive
  technologies nor a full RGAA audit. The Symfony RGAA coverage is an
  automated subset grouped by theme.
- `coverage` only sees code executed during the instrumented load and is
  not a full static analysis of the bundles.
