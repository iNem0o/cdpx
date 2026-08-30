# Leverage log

- Session-Key: next-release@643eea8
  - Symptom: invoking host `pytest` failed during collection on the project's
    Python 3.14 multiple-exception syntax because the host interpreter is 3.12.
  - Root cause (missing capability): the source targets the pinned Python 3.14
    toolchain and deliberately does not support an arbitrary host Python.
  - Fix encoded (doc/script/lint): `docs/DEVELOPMENT.md` already makes Docker
    the only host dependency and `./dev check-local` the canonical short loop;
    validation was rerun exclusively through that portal.
  - Verification (command/CI): `./dev check-local` passed 988 tests with
    89.97% line and 79.01% branch coverage; `./dev check` returned `ok: true`
    with the targeted Shopware Fetch profiler scenario and zero proof failure.

- Session-Key: next-release@0622b44
  - Symptom: review found that composed secret typing could outlive its action
    timeout, numeric YAML keys in frame candidates escaped as a traceback, and
    direct framework gates could leave proof artifacts owned by UID/GID 1000.
  - Root cause (missing capability): ordinary typing reset the CDP timeout for
    each event, the scenario unknown-field formatter assumed string keys, and
    the direct Compose path did not forward the invoking identity.
  - Fix encoded (doc/script/lint): one remaining-time callback now covers the
    typed action, scenario mappings reject non-string keys as usage errors, and
    both direct framework gates pass UID/GID through a tested Compose environment.
  - Verification (command/CI): `./dev check-local` passed 974 tests with
    89.96% line and 78.97% branch coverage; `./dev check` returned `ok: true`
    for 1,034 tests with zero proof failure.

- Session-Key: next-release@9172a15
  - Symptom: five review regressions exposed a Shopware access-key leak, a
    per-key-event origin race, asymmetric profiler URL comparison, unattested
    Chrome runtime claims and unbounded cache-tag callers. Enforcing the
    Chrome rule then correctly made the first full proof red for five
    documented scenarios without exact attested evidence.
  - Root cause (missing capability): sensitive-header matching omitted the
    `access-key` family; typing guards surrounded characters rather than each
    CDP event; profiler hits and the current page used different URL forms;
    runtime provenance checks covered Symfony/Shopware but not Chrome; and a
    bounded cache-tag row still retained every nested caller. The proof's
    Chrome command also omitted TLS scenarios, while wait/DOM-diff runtime
    tests lacked explicit scenario identities.
  - Fix encoded (doc/script/lint): redaction, per-event guards, symmetric URL
    normalization, runtime suite attestation and nested list metadata now have
    focused tests. The proof gate runs TLS E2E and the wait/DOM-diff Chrome
    tests publish exact scenario IDs and screenshots; public primitive docs
    describe the tightened contracts.
  - Verification (command/CI): `./dev check-local` passed 969 tests (89.96%
    line, 78.98% branch); `./dev check` returned `ok: true` with 52 attested
    Chrome tests, 7 Symfony tests, 1 Shopware test and zero proof failures.

- Session-Key: next-release@952bd0d
  - Symptom: the first real Shopware proof exposed 23 feature flags, so the
    deterministic E2E flag was absent from cdpx's intentionally bounded first
    20 rows; adding Cart work to the historical profiler route also made its
    bounded DAL-tag assertion depend on query timings.
  - Root cause (missing capability): the fixture compiler pass appended its
    flag after Shopware's production flags, and the Cart fixture shared a route
    with the existing DB/rules/cache proof despite adding many DAL queries.
  - Fix encoded (doc/script/lint): the E2E compiler pass prepends the
    deterministic flag before Shopware registers its profiler collector; the
    runtime test also proves total/truncation metadata and exact collector
    selection without relaxing the public bound. A dedicated Cart route keeps
    the real pipeline separate from the historical DB proof.
  - Verification (command/CI): `./dev test-shopware-e2e`,
    `./dev test-symfony-e2e`, `./dev check-local` (963 tests; 89.95% line and
    78.94% branch coverage) and `./dev check` (`ok: true`) completed
    successfully.

- Session-Key: next-release@106cb40
  - Symptom: Shopware 6.7 advertised 26 profiler collectors, but cdpx still
    learned its DB collector through a speculative `?panel=db` 404 and
    discarded DAL query comments, source links, active rules and cache tags.
  - Root cause (missing capability): panel selection was a static fallback
    list with no collector-menu probe or composable extension registry, and
    the shared DB parser ignored Shopware's highlighted comments/backtraces.
  - Fix encoded (doc/script/lint): one same-origin request-panel probe now
    selects advertised collectors through adapters; bounded `profile`
    metadata, tagged DB queries with best-effort sources, active rules and
    cache-tag panels share the stable profiler schema and redaction boundary.
  - Verification (command/CI): `./dev test-shopware-e2e`, `./dev check-local`
    (947 tests; 89.84% line and 78.6% branch coverage) and `./dev check`
    (`ok: true`) all completed successfully.

- Session-Key: next-release@9706c80
  - Symptom: mocked profiler scenarios stayed green while the real Symfony
    browser selected a later `/favicon` profiler token, and the Shopware test
    assumed a serial collector order although panel fetches run concurrently.
  - Root cause (missing capability): Symfony and Shopware did not share a
    dedicated supervised runtime harness, and functional profiler assertions
    still lived partly in mocks or in brittle runtime-specific setup.
  - Fix encoded (doc/script/lint): both suites use the shared pinned-Chromium
    session and framework runner; the public `cdpx profiler` command drives the
    primary assertions; passive collection selects the current document; mocks
    retain deterministic protocol, schema and redaction contracts only.
  - Verification (command/CI): `./dev check` completed with `ok: true`, 940
    unit/contract tests, 49 real Chrome tests, 7 real Symfony tests and 1 real
    Shopware 6.7 test.

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
