# M5 — Orchestration and guardrails

## Why

Compose primitives into bounded, replayable and observable recipe flows,
without creating an unlimited macro language.

## Delivered state

### `record` / `replay`

`record` executes one action and writes a private `cdpx.record/v2` line.
Actions, results and errors are redacted. A literal input is refused,
`type ... @env:NAME` persists only the reference and allows a replay, while
`eval` remains non-replayable. A missing reference is refused before any CDP
effect.

`replay` validates the whole journal, its replayability, the secrets and
`--max-actions` before the first action. It compares recorded results outside
volatile fields, re-reads the real URL after navigation and blocks an
off-origin redirect before the next mutation. A green comparison does not
replace an explicit business assertion.

### YAML scenarios

The runner composes `goto`, `wait_visible`, `wait_text`, `click`, `type`, `key`
and `eval`, then assertions and captures. `wait_visible` checks rendering/a
non-null box; an input requires `secret_ref`; the final console/network drain
precedes the verdict. Artifacts are private and classified.

### `frame`

The read walks the `contentDocument` of same-origin iframes and returns the
first match. It does not use a CDP contextId and does not cross the
cross-origin boundary.

### Guardrails

- Manifest/run/target and the allowlist are mandatory; every origin consulted
  is fail-closed and the file's maximum authority is preflighted. Full browser
  isolation belongs to M8.
- `--max-actions` bounds a given replay, not a cumulative session counter.

## Proofs

Mock CDP: parsing, protocol, v2 journal, secret references, divergences,
origins and drain. Real Chrome: record/replay, pass/fail scenarios,
interactions and proofs. Symfony Docker: scenarios against the reference app.

## Definition of Done

- [x] full record/replay flow on mock fixtures and real Chrome;
- [x] mandatory allowlist and redirect controls tested;
- [x] YAML scenarios, assertions and proofs documented;
- [x] executable guardrails described in HARNESS.md.
