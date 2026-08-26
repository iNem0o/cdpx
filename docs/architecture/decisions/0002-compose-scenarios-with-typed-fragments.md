# ADR 0002: Compose scenarios with typed step fragments

- Status: Accepted
- Date: 2026-08-27

## Context

An executable YAML scenario could only define one inline `steps` list. Teams
therefore had to duplicate common journeys such as authentication, cart setup
or form submission. YAML anchors reduced repetition inside one document but
could not make a flow portable between scenarios or bundles.

Composition must preserve cdpx's defining safety property: the complete
journey, including origins, secrets and required authority, is known before
the first browser effect. It must also remain deterministic and diagnosable
when the same reusable behavior appears more than once.

## Decision

An executable `cdpx.scenario/v1` document remains the sole owner of `context`,
final `assertions` and final `artifacts`. Its ordered `steps` list may contain
an explicit `include` object that references a local
`cdpx.scenario-fragment/v1` document. A fragment owns steps only and inherits
the executable scenario's context.

The browser-free compiler resolves paths relative to each including file,
confines canonical paths to the scenario root, expands nested fragments
depth-first at the include site and qualifies labels with an explicit or
default alias. It rejects remote, absolute and glob paths, duplicate aliases,
cycles and graph/action budgets. It reads every unique file once, retains
portable source provenance and hashes the dependency graph. Only the compiled
flat scenario reaches secret materialization, policy preflight and the CDP
client.

`cdpx scenario validate` exposes the same compilation plan without requiring
a browser session or resolving secret values. The `include` value is an object
so typed parameters can be added later without changing its shape; version 1
deliberately has no `with` field or template language.

## Consequences

Reusable flows have explicit execution order and cannot override session
policy or silently merge contexts and final assertions. Findings and proof
steps identify their source file and include chain, while dependency hashes
make the exact composition reviewable. Authors must keep fragments inside the
scenario root and assign distinct aliases when including the same fragment
twice.

The rejected alternatives are YAML `!include` tags, cross-file anchors,
top-level textual inclusion and whole-scenario inheritance. They either evade
JSON Schema/editor support, hide ordering, or require ambiguous merge rules
for context, assertions and artifacts.

## Enforcement and evidence

- `schemas/scenario-v1.json` and `schemas/scenario-fragment-v1.json` publish
  the versioned authoring contracts.
- `tests/test_scenarios.py` covers expansion, provenance, path confinement,
  cycles, aliases, hard limits, action budgets, CLI validation and the mock
  protocol.
- `tests/e2e/test_e2e_chrome.py` executes a composed form scenario in real
  Chromium and checks its qualified label and fragment dependency.
- `./dev check` blocks on static checks, unit coverage, real Chromium, real
  Symfony and the proof inventory.
