# M6 — Technical distribution

## Why

Make cdpx installable, verifiable and reproducible independently of the
development machine and the CI platform.

## Delivered content

- single version exposed by `cdpx --version`;
- wheel and sdist built by `python -m build` and checked by Twine;
- `cdpx-ci` image containing Chromium and the validation tooling;
- reference Symfony app orchestrated by Docker Compose;
- `make proof` for JUnit, logs, scenarios and screenshots in a private local
  tree, then a manifested shareable text staging;
- reusable browser snippet in `docs/CLAUDE-browser-snippet.md`.

Hosting and public publication are handled by M7. GitHub Actions calls the
same Make targets: CI does not redefine the quality gate.

## Validation

```bash
make docker-check
make docker-e2e
make docker-symfony-e2e
make release
```

Docker, Chrome and Symfony are mandatory for the release. The wheel must also
be installed in a clean environment before publication.

## Distributed proofs policy

- `0700` directories, `0600` files/manifests, atomic write;
- manifest with SHA-256, classification, upload authorization, redaction and
  TTL;
- screenshots, PDF and `opaque-restricted` binaries, kept out of
  `.proof/shareable/`;
- fail-closed canary scan before upload;
- retention: PR proof 14 days, release proof 30 days, verified distributions
  90 days.

## Definition of Done

- [x] versioned package, wheel and sdist verified;
- [x] Docker image and real Chrome green;
- [x] distinct and blocking Symfony suite;
- [x] consolidated proof available as an artifact;
- [x] first green run on the public GitHub runner — attested in
      `docs/leverage-log.md` (runs `29161949162` and `29162518918` green with
      `PR Gate / Required`).
