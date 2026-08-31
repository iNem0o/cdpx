+++
id = "rgaa-audit"
title = "RGAA catalog, page scans, and declared samples"
status = "validated"
summary = "Keep all 258 official RGAA 4.1.2 tests visible, resolve only proven subsets, collect bounded evidence, and preserve expert review work."
entrypoints = ["cdpx rgaa"]
path_globs = ["src/cdpx/rgaa/**", "src/cdpx/commands/rgaa.py", "schemas/rgaa-*.json", "tests/fixtures/rgaa*.html", "tests/test_rgaa.py", "tests/e2e/test_e2e_chrome.py"]
test_globs = ["tests/test_rgaa.py::test_*", "tests/e2e/test_e2e_chrome.py::test_rgaa_*"]
docs = ["docs/PRIMITIVES.md", "docs/rgaa/", "docs/architecture/decisions/0003-catalog-first-hybrid-rgaa.md"]
expected_proofs = ["junit", "json"]

[[journeys]]
id = "scan-rendered-page"
title = "Resolve a prudent RGAA subset on a rendered page"
entrypoint = "cdpx rgaa scan"

[[journeys]]
id = "audit-declared-sample"
title = "Compile and run a declared multi-page sample"
entrypoint = "cdpx rgaa sample"

[[scenarios]]
id = "resolve-deterministic-subset"
journey = "scan-rendered-page"
title = "Resolve deterministic RGAA observations without claiming certification"
ui_text = "The report keeps 258 official tests visible and highlights failures, unresolved review, and manual-only work."
report_text = "This scenario proves in real Chromium that the native engine passes a controlled baseline, fails controlled structural regressions, and never turns excluded or unresolved tests into passes."
given = "The pinned DINUM catalog and accessible/regressed RGAA fixtures are available."
when = "cdpx runs its fixed DOM/CSS collector against both fixtures."
then = "The result contains 258 test records, local evidence for resolved tests, and certification_claim=false."
target = "chrome"
proof_level = "runtime"
tests = ["tests/test_rgaa.py::test_*", "tests/e2e/test_e2e_chrome.py::test_rgaa_native_scan_real", "tests/e2e/test_e2e_chrome.py::test_rgaa_native_probe_isolated_from_hostile_main_world", "tests/e2e/test_e2e_chrome.py::test_rgaa_native_probe_walks_nested_open_shadow_roots", "tests/e2e/test_e2e_chrome.py::test_rgaa_environment_hash_works_on_non_secure_http_origin", "tests/e2e/test_e2e_chrome.py::test_rgaa_probe_timeout_does_not_interrupt_main_world_javascript", "tests/e2e/test_e2e_chrome.py::test_rgaa_focus_restores_idless_control_in_nested_shadow_root", "tests/e2e/test_e2e_chrome.py::test_rgaa_cli_navigation_error_text_keeps_full_report", "tests/e2e/test_e2e_chrome.py::test_rgaa_installed_cli_covers_catalog_scopes_and_samples", "tests/e2e/test_e2e_chrome.py::test_rgaa_interactive_privileged_and_hybrid_scopes_real", "tests/e2e/test_e2e_chrome.py::test_rgaa_declared_sample_runs_multiple_real_pages"]
expected_proofs = ["junit", "json"]
+++

## Intent

`cdpx rgaa` is an evidence and supervision primitive, not a certification
machine. It vendors the official RGAA 4.1.2 data at one exact DINUM commit,
checks source and generated hashes at runtime, and always models the full
inventory of 13 themes, 106 criteria, and 258 tests. A scan may prove a local
pass, fail, or non-applicability only when the modeled method has sufficient
evidence. Everything else remains `needs_review`, `manual_only`, `error`, or
`not_tested`.

The pre-existing `cdpx a11y` remains a compact AXTree view. It does not share
the RGAA result contract and is not silently upgraded into an audit.

## Usage

### cdpx rgaa

#### Inspect the catalog

`cdpx rgaa catalog [--tests IDS]` is browser-free and needs no session. It
returns provenance, cardinalities, automation profiles, statements, and the
catalog hash. `IDS` is a comma-separated list such as `2.1.1,8.3.1`.

#### Scan one page

```bash
cdpx rgaa scan [URL] \
  [--scope passive|interactive|privileged] \
  [--engine native|hybrid] \
  [--tests IDS]
```

When `URL` is omitted, the current assigned page is scanned. The complete
session/run/target identity and origin allowlist remain mandatory.
Navigation and all following collection share one deadline. If navigation or
a required collector fails, the command still emits the full normative
skeleton, publishes `execution_status: partial|error`, marks the affected tests
`error`, and exits `1`; the trustworthy final page URL remains unknown rather
than being copied from the requested URL. A proven RGAA failure after a
completed execution remains exit `0`.

The environment probe returns at most 256 KiB of DOM fingerprint material;
SHA-256 is computed by cdpx, so plain HTTP development hosts such as
`http://app.test` do not depend on Web Crypto or secure-context eligibility.
Probe timeouts abandon the isolated world without sending target-wide
`Runtime.terminateExecution`.

| Scope/engine | Required authority | Additional effect |
|---|---|---|
| passive collectors only | `observation` | fixed DOM/CSS rendered-state probe |
| a selected focus test | `interaction` | trusted Tab input and focus evidence |
| a selected text-spacing test | `privileged` | temporary official text-spacing transformation, removed in `finally` |
| a selected hybrid mapping | `privileged` | integrity-pinned axe-core in a fresh isolated world |

Authority is derived from the selected execution plan, not from unused scope
or engine capabilities.

The native rules cover deterministic or useful partial observations for
frame titles, simple solid-background contrast, link names, doctype,
default language, page title, form labels, button names, focus indicators,
tab order, text spacing, and meta refresh. Each mapped test declares whether
automatic pass/fail/NA is authorized. Complex backgrounds, semantic
relevance, actual assistive-technology restitution, and external documents
stay unresolved.

Test 8.1.1 resolves only doctype presence; validity and placement belong to
8.1.2/8.1.3. Test 8.3.1 can pass from a root language, but absence on `<html>`
remains `needs_review` until the official per-text-element branch is inspected.
Test 8.6.1 is also conditional: a missing `<title>` fails 8.5.1 but leaves
8.6.1 under review, while a present but empty title can fail 8.6.1.

axe-core is advisory only. Provider rule IDs are never public RGAA IDs, and
a provider violation cannot by itself change an RGAA verdict. Results omit
HTML snippets, bound rule/node counts, record the local bundle hash, and run
without a network fetch.

#### Validate and run a sample

`cdpx rgaa sample validate FILE` is browser-free. It rejects remote or
credential-bearing URLs, unknown fields/tests, duplicate page IDs, more than
50 pages, manifests above 1 MiB, symlinks, and unsupported schemas/versions.
Each YAML `tests` item must contain exactly one non-blank normalized ID;
comma-injected IDs and duplicates that differ only by whitespace are rejected.
It returns the complete plan, maximum required authority, and a deterministic
composition digest before any browser effect. Normative page and test-ID arrays
are preserved even with a small global `--limit`.

```yaml
schema: cdpx.rgaa.sample/v1
rgaa_version: 4.1.2
scope: interactive
engine: native
base_url: http://app.test
pages:
  - id: home
    url: /
  - id: checkout
    url: /checkout
    tests: [2.1.1, 8.3.1, 11.1.1]
```

`cdpx rgaa sample run FILE` preflights every destination and the maximum
authority before connecting, then navigates only to declared pages. It keeps
per-page reports and aggregates each official test conservatively: `fail`
dominates, followed by `error`, unresolved review/manual work, pass, NA, and
not-tested. A sample-level `pass` or `not_applicable` requires complete page
coverage; otherwise the aggregate remains `needs_review`.
Page and sample results expose `audit_findings_present`. Per-page
`actions_used` is local to that page, sample `actions_used` is cumulative, and
each page plan separates planned navigations from interactions.

## User journeys

For one rendered state, inspect the catalog, select the useful official tests,
then choose the least powerful scope that can collect the required evidence.
For a service audit, author and review the page sample first, run
`sample validate` in CI, then execute that exact digest in a sufficiently
authorized disposable session. Findings drive remediation; unresolved tests
form the explicit expert/AT review backlog.

## Verdicts

| Verdict | Meaning |
|---|---|
| `pass` | Applicability and every modeled branch were proven with sufficient evidence. |
| `fail` | A sufficient non-conformity was proven. |
| `not_applicable` | Absence of applicability was proven; no finding is not enough. |
| `needs_review` | Useful evidence exists but human, semantic, or visual judgment remains. |
| `manual_only` | The method requires absent capabilities such as real AT or document tooling. |
| `error` | A requested capable collector/provider did not complete. |
| `not_tested` | The caller excluded the test; it remains visible. |

Every report states `certification_claim: false`. A `pass` is local to one
official test on one observed page/state, never to the service.

The scanner binds evidence to the exact top-level `frameId`, `loaderId`, and
URL. Any document drift stops further collection, invalidates automatic
rollups, and produces a non-success execution status. Origin-policy violations
are fatal and stop an entire sample at the first breach.

## Validation

The official JSON files, source manifest, generated catalog, exhaustive
matrix and axe bundle are shipped inside the image. Runtime network fetching
is forbidden. `tools/generate_rgaa_catalog.py` fails unless the joins contain
exactly 13 themes, 106 criteria, and 258 unique test IDs. Updating RGAA or a
provider requires new provenance, hashes, generated outputs, mappings, tests,
and an explicit catalog/version change.

The deterministic mock proves the exact Runtime/Input/isolated-world protocol.
Real Chromium and the installed CLI prove accessible and deliberately regressed
fixtures, nested open-shadow traversal, hostile main-world isolation, focus
traversal/restoration, temporary text-spacing mutation and cleanup, the
isolated axe provider, non-secure HTTP fingerprinting, navigation `errorText`
reports, timeout isolation, shadow-root focus restoration, and multi-page
aggregation. The repository-wide gate
still requires Chrome, Symfony, Shopware, packaging, documentation, coverage,
and the proof inventory.

## Proofs

The proof cockpit owns scenario `rgaa-audit.resolve-deterministic-subset`.
JUnit establishes execution; its bounded JSON attachment compares full-count
summaries for the baseline and controlled regression. Normative source hashes,
the 258-row matrix, public schemas, and ADR are durable design evidence.

## Known limitations

Native coverage is deliberately partial. Environment fingerprinting examines
at most 5,000 light-DOM nodes and 256 KiB and publishes its frontier; it is not
a complete rendered-document identity. Complex color compositing, semantic
relevance, assistive-technology restitution, external documents and sample
representativeness are never inferred as passes. Focus and spacing collectors
produce bounded review evidence and never infer failure from a missing visual
signal or geometry change alone. Cross-origin frame
content is not deeply inspected. RGAA 5 is not aliased to 4.1.2.

Full design records and the generated 258-row matrix live in
[`docs/rgaa/`](../rgaa/).
