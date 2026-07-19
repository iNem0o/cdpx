# AGENTS.md — cdpx

Session anchor for any agent working on this repository. Read this file
first, then `HARNESS.md` for the execution rules and `docs/CONTEXT.md` for
the product rationale. The model acts, but the harness decides.

## Mission

cdpx exposes Chrome DevTools Protocol primitives as a CLI so an agent, or the
developer steering it, can see, act and measure inside a disposable
development Chrome. See `docs/PRIMITIVES.md` for the public catalog.

## Working commands

```text
./dev setup              # pinned Docker development/runtime images
./dev check-local        # short loop: lint + format + mypy + unit/coverage
./dev check              # GATE: Docker + Chrome + Symfony + proof
./dev test-e2e           # real Chrome — its absence is an error
./dev proof              # private proof report under .proof/
./dev release            # gate + internal wheel verification
./dev fixtures           # reference site on :8899
./dev mock               # scriptable fake Chrome, no browser
```

## Invariants

1. **`./dev check` is green before the session ends.**
2. Unit tests are deterministic, loopback-only and browser-free. Real browser
   coverage belongs in `tests/e2e/`; unavailable Chrome or Symfony blocks the
   runtime and release gates.
3. The CLI contract is stable: stdout is one JSON object, stderr carries
   diagnostics, exit codes are 0/1/2. Contract changes require tests and an
   update to `docs/PRIMITIVES.md`.
4. Every primitive includes its implementation, CLI route, mock protocol and
   output tests, an HTML fixture when relevant, and user documentation.
5. Cookie and storage values are redacted by default. Examples use only
   disposable Chrome profiles.
6. The mock follows the real protocol. Browser protocol changes require mock,
   client and test updates together.

## Repository map

```text
src/cdpx/client.py        CDP WebSocket client
src/cdpx/discovery.py     loopback HTTP discovery
src/cdpx/primitives/      browser capabilities
src/cdpx/cli.py           argparse to primitives to JSON
src/cdpx/testing/         shipped mock and fixture server
tests/                    deterministic unit and contract tests
tests/fixtures/           static reference site
tests/e2e/                blocking Chrome and Symfony scenarios
docs/                     current product, feature and operating references
```

## Work loop

1. State the concrete outcome and inspect the current contract.
2. Write or adapt the mock test first; the emitted protocol is the spec.
3. Implement the smallest coherent change.
4. Run `./dev check-local`, then the mandatory `./dev check`.
5. Update the affected canonical documentation and examples.
6. Create one atomic commit with an imperative subject and a body explaining
   why the change exists.

## Definition of done

- [ ] `./dev check` is green
- [ ] mock output and emitted protocol are covered
- [ ] user documentation matches the current behavior
- [ ] relevant fixture markers are tested
- [ ] no secret or session value appears in default outputs
- [ ] contribution follows `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`
