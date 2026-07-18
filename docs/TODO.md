# Work in progress

The M0-M6 functional foundation has shipped. The current priority is the
first open source release on GitHub under the MIT license. The detailed
plan is [docs/RELEASE-PLAN.md](RELEASE-PLAN.md).

## Supervised session contract — validated

The mechanisms below are implemented and validated by the targeted gates,
the integrated gate, and the installed package:

- [x] Validate together the `session start/status/stop` lifecycle, three
      simultaneous profiles, explicit target/run, exclusive lease, and
      TTL/owner teardown.
- [x] Validate the `observation` / `interaction` / `privileged` matrix, the
      mandatory loopback, and the fail-closed origin allowlist.
- [x] Make the supervised session the sole contract: identity triple before
      discovery, no implicit endpoint/target, and lifecycle reserved to the
      supervisor.
- [x] Route `make mock` through the same supervised manifest as real
      Chrome.
- [x] Align HARNESS, the catalog, feature sheets, the validation matrix,
      and the proof cockpit with this single contract.
- [x] Validate the end-to-end canaries: stdout/stderr, URL/headers,
      console, storage, profiler, journal v2, scenarios, and proof
      staging.
- [x] Validate the `.proof/shareable/` staging, the `0600`/`0700` modes,
      the classification/retention manifest, and the exclusion of opaque
      binaries.
- [x] Validate, in the installed wheel, the public surface of 31 commands
      after adding `cdpx session` (`make dist` within the integrated
      gate).
- [x] Fully document Chrome launch and lifecycle, then expose an offline
      CommonMark/Mermaid gate in the cockpit without separating feature
      sheets from their role as harness specification.

`SecureArtifactWriter` automatically redacts text, JSON, and saved text
files; the canary scanner remains the last publication lock for known
secrets. Expired local proofs are automatically purged at the start of
every `make proof`, according to the retention manifest's TTLs.

## Open source preparation

- [x] Rewrite the README for an outside user with a local quickstart,
      pre-1.0 status, security, and a catalog of the CLI surface.
- [x] Add the contribution, security, conduct, and support policies.
- [x] Remove private or client references from the product documentation.
- [x] Finalize the MIT relicensing and package metadata after validation
      by the copyright holder.
- [x] Replace GitLab CI with GitHub Actions workflows with minimal
      permissions and pinned actions.
- [x] Stop versioning `.proof/`; publish reports as CI artifacts.
- [x] Verify the exact contents of the wheel and the sdist, including the
      license and the absence of internal files.
- [x] Install the wheel in a clean environment and recount the 31
      commands from the artifact.
- [x] Run `make release` on the integrated state, then confirm the same
      gates on a real GitHub runner.
- [x] Prepare release version `0.2.0`, consistent with the pre-1.0
      contract changes; no tag is authorized at this stage.

## Proof cockpit v2 — shipped

UX/UI/DX overhaul of the proof system (collection, storage, cockpit):

- [x] Extract the cockpit presentation to `src/cdpx/proofing/cockpit/`
      (shell.html, cockpit.css, js/ in ordered parts — shipped in the
      wheel).
- [x] Link each test to its intention written in the code: docstring =
      the method's intention, `#:` comments = an annotated walkthrough
      per assertion, correlated to the failing line and rendered
      hierarchically.
- [x] Close the artifact taxonomy and give each type a dedicated viewer
      in a contextual modal (zoomable screenshot, console filtered by
      level, network table, JSON tree, highlighted logs, command
      transcript, xterm.js cast player).
- [x] Add the secondary proofs: `attach_command_output`,
      `attach_log_excerpt`, `attach_cast`.
- [x] Make the cast proof mandatory: native stdlib recorder (pty →
      asciicast v2, neither asciinema nor agg), blocking gate in
      `make proof`, casts inlined and played in a vendored xterm.js
      (MIT, SHA-256 verified).
- [x] Guide the reading of the pack: "Read first" when the verdict is
      red, a command timeline, badges per proof type, counters.
- [x] Generalize docstrings + `#:` to all suites: 430/430 tests have an
      intention, 428/428 tests with assertions have an annotated
      walkthrough (`tests/test_intent.py` remains excluded — frozen-line
      witnesses). Diff proven purely additive (identical AST outside
      docstrings).
- [x] Phase 2 secondary proofs: work through the
      `docs/milestones/attach-backlog.json` backlog (61 opportunities
      identified during annotation — `attach_cli_run`, `attach_json`,
      `attach_cast`, candidate `scenario` markers), in small batches with
      `make check-local` run systematically since these additions change
      the executed code. Delivered in 12 batches: 69 attachments, 27
      `scenario` markers (4 new sheet scenarios), inlinable `.ndjson`,
      backlog emptied — see `docs/milestones/M9-preuves-secondaires.md`.

## Migration to English (open source)

Strategy: glossary first ([docs/GLOSSARY.md](GLOSSARY.md)), pure
translation commits (never mixed with a refactor), exact ratchet
(`scripts/language_ratchet.py`, committed baseline, blocking test).

- [x] Phase 0 — FR→EN glossary + language ratchet with a per-zone
      baseline.
- [x] Phase 1 — docs with no code coupling (CONTEXT, ROADMAP,
      RELEASE-PLAN, GITHUB, leverage-log, milestones, CONTRIBUTING,
      SECURITY, SUPPORT, CODE_OF_CONDUCT, CHANGELOG). LICENSE and
      THIRD_PARTY_NOTICES were already in English.
- [x] Phase 2 — docs coupled to the guards (PRIMITIVES, feature sheets,
      TODO, VALIDATION, SESSION-LIFECYCLE) then HARNESS/AGENTS
      (reinforced review: operational rules), with the tandem code/test
      switches (REQUIRED_SECTIONS, cockpit labels, asserted anchors and
      titles).
- [x] Phase 3 — code strings (CLI help, error messages, docstrings,
      cockpit SPA) in tandem with the tests that assert them; `@env:NOM`
      switched to `@env:NAME` everywhere; the cockpit UI strings ("Read
      first", "Content not embedded", "FAILED" verdict) aligned with the
      e2e and unit assertions.
- [ ] Phase 4 — test intention docstrings, in waves.
- [ ] Phase 5 — regenerate the artifacts (casts via `make site-casts`,
      proof, homepage) and aim for a ratchet at 0.

## Ongoing technical debt

- [x] Pin the validation images by digest and hand their monthly update
      off to Dependabot.
- [ ] Introduce an ExecPlan mechanism (execution plans tracked in the
      repository, cf. [docs/leverage-log.md](leverage-log.md)): harness
      evolution deliberately deferred, to be handled in a dedicated
      session.

### Standing rules

Permanent rules, with no checkbox state — they apply to every session:

- `KEY_MAP` only extends beyond the tested set (editing/navigation,
  Space, and the four arrow keys) given a real need, a mock test, and a
  browser scenario.
- A use of `eval` that recurs at least three times is promoted to a
  named primitive.

Checking an item off means: code and documentation aligned, tests
proportionate to the risk, and `make check` green.
