# M9 — Generalized secondary proofs

## Why

Intent annotation (430/430 docstrings, `#:` walkthroughs) had made the
cockpit readable, but many tests proved without showing: the piece of
evidence (redacted journal, allowlist manifest, CLI transcript, capture)
stayed in the run's sandbox. This milestone works through the backlog of 61
opportunities identified during annotation (`attach-backlog.json`) so that
every strong claim of the harness is visible in the cockpit modal.

## Delivered state

### Attachments across all suites

69 secondary proof calls added (12 batches, one commit per batch):
`attach_text`/`attach_json` for redacted outputs and manifests, `attach_file`
for ndjson journals and binaries, `attach_command_output` for in-process CLI
executions, `attach_cli_run` for e2e subprocesses, `attach_log_excerpt` and
`attach_cast` on the proof pipeline side. All attachments are guarded by
`if evidence_case is not None:` — the suites remain deterministic without
`--cdpx-evidence-dir`.

### Inlineable `.ndjson`

Attached record/eval journals used to fall into the opaque `file` type, so
were invisible in the cockpit. `.ndjson` is now typed `logs`, textual, copied
redacted and classified `internal`: the modal shows the journal that proves
only the `@env:` reference is persisted.

### Reattachment to features

27 `@pytest.mark.scenario` markers added (23 → 50), favoring the existing
scenarios from the sheets; 4 new scenarios documented in `state-session.md`
(untrusted page content, supervisor cycle without Chrome, redacted startup
diagnostics, public manifest without control levers). Features inventory
and legacy ratchet stayed at zero violations.

### Proof security

Each batch grep-checked the absence of canaries/secrets in the produced
evidence tree. `--show-values` outputs are never attached raw: the derived
proof demonstrates the redacted/revealed contrast without exposing a value.
Binaries (captures, PDF) remain `opaque-restricted`, backed by a readable
JSON (permissions, signatures, sizes) for the cockpit trace.

## Proofs

`attach-backlog.json` backlog emptied (61/61, removed as batches progressed).
`make check-local` green after each batch; `make test-e2e` green on the
Chrome batches; `make docker-symfony-e2e` green on the Symfony batch (7/7);
`make check` and `make proof` green at closing, new proofs visible on the
feature pages (content inlined in the modal, no "Content not embedded"
fallback for text).

## Definition of Done

- [x] 61 backlog entries processed or reclassified, backlog at `[]`;
- [x] tests green with and without an evidence folder, intent 430/430
      preserved;
- [x] scenario_ids all resolved by the features inventory (ratchet 0);
- [x] no canary/secret in the attached evidence artifacts;
- [x] `make check` + `make proof` green at the end of the run.
