# Leverage log

- Session-Key: next-release@f2d013c
  - Symptom: the first proof aggregation treated every test in an attested
    suite, and then every broad scenario glob match, as if it carried the
    documented scenario's proof kind; auxiliary tests could therefore create
    false runtime evidence or fail the provenance gate noisily.
  - Root cause (missing capability): suite environment attestation, feature
    classification and scenario proof identity were not distinct concepts in
    the evidence pipeline.
  - Fix encoded (doc/script/lint): suite attestations remain at the file root;
    auxiliary cases stay unattested; only an explicit `scenario_id` can attach
    proof to a documented scenario; contract and runtime scenarios are split
    in the feature sheets and covered by reader/writer/inventory tests.
  - Verification (command/CI): `./dev check-local` passed 926 unit/contract
    tests, then `./dev check` passed all 983 tests (49 real Chrome, 7 real
    Symfony and 1 real Shopware) with zero proof failure.

- Session-Key: next-release@7d74508
  - Symptom: a repeated full gate passed 893 unit tests and 49 Chrome E2E
    tests, then Docker Compose reported `No such container` while starting the
    freshly created cdpx service for the Symfony E2E stage.
  - Root cause (missing capability): a concurrent purge of unused Docker
    resources removed the container between the Compose create and start
    phases; the gate requires exclusive access to those resources while it
    runs.
  - Fix encoded (doc/script/lint): this operational sequencing constraint is
    recorded here; no product-code retry hides destructive Docker maintenance.
  - Verification (command/CI): `./dev check` completed after the purge ended,
    with `ok: true`, no proof failures and the Symfony E2E JUnit report present.

- Session-Key: agent/intercept-click@934ee63
  - Symptom: invoking host `pytest` failed while importing Python 3.14 syntax,
    before collecting the targeted scenario regression tests.
  - Root cause (missing capability): the host interpreter was used instead of
    the repository's pinned Docker toolchain.
  - Fix encoded (doc/script/lint): no new mechanism was needed;
    `docs/DEVELOPMENT.md` and `CONTRIBUTING.md` already make Docker the only
    host prerequisite and `./dev check-local` the canonical short loop.
  - Verification (command/CI): `./dev check-local` and the mandatory
    `./dev check` both completed successfully in the pinned Python 3.14 image.

- Session-Key: master@944204e
  - Symptom: releasing 0.1.2 required rediscovering the whole procedure —
    the tag alone looked sufficient, while the version is actually pinned in
    thirteen files and the publication gate is the protected environment
    approval, not the tag.
  - Root cause (missing capability): the release order lived only in prose
    and the version fan-out had no mechanical guard, so a bump could silently
    miss a pinned surface.
  - Fix encoded (doc/script/lint): `docs/RELEASING.md` opens with a numbered
    runbook, and `test_release_version_pins_move_together` fails the unit
    gate naming any file whose pin lags the package version.
  - Verification (command/CI): `./dev check-local` and `./dev check` green on
    the 0.1.2 preparation commit; the test names the offending file when any
    single pin is reverted.

- Session-Key: master@3418047
  - Symptom: the first containerized proof runs could not start Chromium's
    sandbox, and the next unit run inherited a container marker in a test that
    intended to model a normal host user.
  - Root cause (missing capability): the proof environment allowlist omitted
    `CDPX_CONTAINERIZED`, while the sandbox unit contract did not isolate that
    variable. Newly added tooling tests were also absent from the proof
    cockpit's scenario mapping.
  - Fix encoded (doc/script/lint): the proof runner preserves the container
    marker; the sandbox test covers host, CI, root and container cases; and the
    harness feature sheet maps coverage, runtime configuration and OCI tooling
    tests to explicit scenarios.
  - Verification (command/CI): `./dev check` completed with 767 passing tests,
    real Chromium, real Symfony and a green proof inventory.
