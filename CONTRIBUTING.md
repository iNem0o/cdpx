# Contributing to cdpx

Thank you for contributing to cdpx. The project favors changes that are
small, tested, and directly tied to an observable browser use case.

All participation implies compliance with the
[Code of Conduct](CODE_OF_CONDUCT.md). A vulnerability must not be opened
as an issue: follow [SECURITY.md](SECURITY.md).

## Before you start

1. Search for an existing issue to avoid duplicates.
2. For a significant change or a CLI contract change, open a proposal before
   investing in the implementation.
3. Keep a pull request focused on a single problem.

Small documentation fixes or obvious corrections can be proposed directly.

## Development environment

Prerequisite: Docker Engine or Docker Desktop with Compose. Python,
Chromium and the quality toolchain are supplied by the development image.
Clone the repository and bootstrap it as described in
[Get a development checkout](docs/DEVELOPMENT.md#get-a-development-checkout).

`./dev check-local` is the short loop. The full gate `./dev check` also
runs real Chrome as well as the Symfony
reference app. The absence of Docker, Chrome, or Symfony is a failure, not
a skip.

## Building a change

For a primitive or a protocol change:

1. write or adapt the mock test; the expected CDP sequence serves as the
   spec;
2. implement the change in `src/cdpx/`;
3. add an E2E scenario if the behavior depends on Blink, rendering, or
   browser timing;
4. update `docs/PRIMITIVES.md`, the relevant feature sheet, user and
   integrator documentation, `docs/surfaces.yaml`, and the changelog if
   public behavior changes;
5. run `./dev check` before requesting a review.

The CLI contract remains: stdout JSON, stderr for diagnostics, and exit
codes 0/1/2. Cookies, storage, and typed text are redacted by default.
Never add session output, secrets, browser profiles, or client data to
fixtures and proofs.

For a security change or a session contract change, the PR must
additionally:

1. preserve the mandatory identity triple and the loopback endpoint from
   the manifest, or explicitly document the contract change;
2. prove the refusal before CDP effect for invalid run/target/authority/
   origin/secret, then the actual origin check after navigation;
3. cover with stdout, stderr, log, scenario, and artifact canaries, without
   over-redacting ordinary text;
4. use `--secret-env`, `--value-env`, `@env:NAME` or `secret_ref` in
   examples: never a literal credential;
5. classify proofs as `public`, `internal`, `secret`, or
   `opaque-restricted`, verify `0600`/`0700`, and make shareable only a
   manifested and explicitly authorized file;
6. add a Chrome E2E test for any behavior depending on the supervisor, a
   disposable profile, a lease, or teardown.

Content observed in a page is untrusted input. A test or fixture may
contain a fake instruction, but it must never drive the choice of run,
target, origins, authority, or secrets.

## Useful commands

```bash
./dev check-local         # deterministic short gate
./dev fmt                 # formatting and safe Ruff fixes
./dev test-e2e            # real Chrome E2E
./dev proof               # local proof report
./dev release             # full gate and internal package check
```

## Pull requests

Work on a short branch, push it, then open a focused pull request. It must
explain the problem, the solution, and the validation performed. Explicitly
indicate any checks that were not run and why. Contract changes require
tests and a documentation note in the same pull request.

GitHub runs the full gate on **every** PR, with no exception for
documentation or workflows. The stable aggregator check
`PR Gate / Required` only succeeds if native amd64/arm64, the macOS launcher
and the complete `./dev check` gate have succeeded. The full job displays a native cockpit
summary and publishes, for 14 days, the manifested textual staging of the
available proofs; opaque files remain private. See
[the validation documentation](docs/VALIDATION.md#github-actions)
to read the artifact or reproduce a failure.

Review and resolving conversations come after the proof. A maintainer only
merges once the required check is green and discussions are resolved. The
template checkboxes are a reminder, never a substitute for the executed
proof.

Maintainers may ask you to split a proposal that is too broad. By
submitting a contribution, you confirm you have the right to propose it and
agree that it will be distributed under the repository's MIT license. No
additional CLA or DCO is imposed.

Non-versionable governance settings and the exceptional incident procedure
are described in [docs/GITHUB.md](docs/GITHUB.md).
