# ADR 0003: Own RGAA semantics with a catalog-first hybrid engine

- Status: Accepted
- Date: 2026-08-30

## Context

The former `a11y` primitive exposes a compact Chromium accessibility tree. It
cannot represent all RGAA 4.1.2 methods, applicability, special cases,
assistive-technology checks, multi-state behavior, or declared page samples.
WCAG/ACT engines add useful observations but do not implement the French
test-level methodology and cannot be treated as RGAA authorities.

## Decision

cdpx owns the normative layer. The three official DINUM JSON files are
vendored at commit `ca4019f95073b6cbd2482a16e9f12b52d8de678d`, hashed, and
joined into a generated catalog with exactly 13 themes, 106 criteria, and 258
tests. An exhaustive automation profile exists for every test. Runtime fetch
is forbidden.

The native engine collects bounded rendered DOM/CSS evidence, trusted input
evidence for interactive scopes, and temporary layout evidence only under
privileged authority. A conservative resolver owns the seven public verdicts.
Native page probes execute in a dedicated isolated world and use protocol-owned
frame URLs for origin checks. Truncation is explicit and prevents pass or
non-applicability; contrast, focus, spacing and accessible-name observations
remain assisted review evidence unless a complete official branch is proven.
The optional axe-core 4.10.3 provider runs from a hash-pinned local bundle in
an isolated world and remains advisory: provider outcomes can enrich evidence
but never become RGAA verdicts by identity or implication.

Samples are explicit versioned YAML manifests read through a bounded,
no-symlink, duplicate-key-rejecting path. Compilation validates all
pages/tests, calculates the maximum authority and a digest before the first
browser effect. Runs share one monotonic deadline and action budget and preserve
an error report for every declared page. There is no implicit crawler.

## Consequences

The system can automate new test families incrementally without losing the
unautomated inventory or overstating compliance. Outputs are larger but remain
boundable through the existing list contract, while summaries always use the
complete counts. Maintaining an automated rule requires test-level mapping,
evidence, limitations, mock protocol coverage, and real Chromium coverage.
Default output bounding applies only to variable evidence; the normative 258
tests, 106 criteria, 13 themes and declared pages retain their schema shape.

The rejected designs are engine-first translation of axe/ACT into RGAA IDs,
runtime downloads of the “current” catalog, an automatic global score, and a
crawler-chosen sample. All erase official methodology or provenance and make
false compliance claims likely.

## Enforcement and evidence

- `tools/generate_rgaa_catalog.py` closes cardinality and join drift.
- `src/cdpx/rgaa/catalog.py` verifies source, catalog, and matrix hashes.
- `tests/test_rgaa.py` covers generation, verdict truth tables, authority,
  provider isolation, protocol calls, bounding, and sample compilation.
- `tests/e2e/test_e2e_chrome.py::test_rgaa_native_scan_real` proves the
  baseline/regression contract in the pinned Chromium runtime.
- `schemas/rgaa-result-v1.json` and `schemas/rgaa-sample-v1.json` publish the
  stable data contracts.
