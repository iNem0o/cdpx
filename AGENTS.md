# AGENTS.md — cdpx

Session anchor for any agent working on this repository. Read this file
first, then `HARNESS.md` (rules), then `docs/CONTEXT.md` (why this project
exists). The model acts, but the harness decides.

## Mission

cdpx = Chrome DevTools Protocol primitives exposed as a CLI, so that an agent
(or the dev driving it) can **see, act and measure** inside a dev Chrome
while building Symfony / e-commerce apps, and during SEO audits. See
`docs/PRIMITIVES.md` for the implemented catalog.

## Working commands

```
make setup               # editable install + development tools
make check-local         # short loop: lint + format + mypy + unit tests
make check               # GATE: local + Docker + Chrome + Symfony
make test                # deterministic unit tests, loopback only
make test-e2e            # real local Chrome — its absence is an error
make docker-symfony-e2e  # scenarios against a real Dockerized Symfony app
make proof               # proof report generated into .proof/
make release             # check + proof + verified wheel/sdist
make fixtures            # reference site on :8899
make mock                # scriptable fake Chrome, no browser
```

Quick try without Chrome:

```
make mock &                    # prints the supervised session exports
# copy the displayed CDPX_SESSION/CDPX_RUN_ID/CDPX_TARGET exports
cdpx goto http://demo.test/
cdpx tabs list
```

## Invariants (non-negotiable)

1. **`make check` green before any end of session.** No exception.
2. **Unit tests = deterministic.** Loopback only, no external network, no
   unbounded sleep, no Chrome required. Whatever needs a real browser goes
   into `tests/e2e/`; Chrome being unavailable is blocking for the runtime
   gates and the release.
3. **Stable CLI contract**: stdout = one JSON object, stderr = diagnostics,
   exit 0/1/2. Any contract change = test changes + a note in
   `docs/PRIMITIVES.md`.
4. **Every new primitive ships with**: its function in
   `src/cdpx/primitives/`, its CLI subcommand, its mock tests (output AND
   emitted protocol), its HTML fixture when an e2e scenario makes sense, its
   entry in `docs/PRIMITIVES.md` (use case, why, example).
5. **Security**: cookie values redacted by default in every output; never
   connect to the user's personal Chrome in docs or examples (always a
   disposable `--user-data-dir`). See `HARNESS.md`.
6. **The mock follows the real protocol.** If Chrome changes behavior
   (e.g. /json/new via PUT), the mock AND the client align, tests attached.

## Where things are

```
src/cdpx/client.py        CDP WS client (commands, events, timeouts)
src/cdpx/discovery.py     HTTP /json API (tabs)
src/cdpx/primitives/      nav, js, inputs, capture, net, state, audit
src/cdpx/cli.py           argparse -> primitives -> JSON
src/cdpx/testing/         CDP mock + fixture server (shipped with the package)
tests/                    unit tests (mock) — this is where check is decided
tests/fixtures/           deterministic static reference site
tests/e2e/                real Chrome + Symfony application, blocking gates
docs/                     CONTEXT, PRIMITIVES, ROADMAP, TODO, milestones/
```

## Expected work loop

1. Read `docs/TODO.md`, pick an item, announce the intent.
2. Write/adapt the mock test first (the expected protocol IS the spec).
3. Implement the primitive + the subcommand.
4. `make check-local` during the loop, then `make check`. Iterate to green.
5. Update `docs/PRIMITIVES.md` + tick `docs/TODO.md`.
6. Atomic commit, imperative message, body explaining the why.

## Definition of Done

- [ ] `make check` green
- [ ] mock test covering output + emitted protocol
- [ ] primitive doc up to date (use case + CLI example)
- [ ] HTML fixture added when an e2e scenario is relevant (+ markers tested
      in `test_fixture_server.py`)
- [ ] no secret/session value in default outputs
- [ ] contribution compliant with `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md`
