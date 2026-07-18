# Open source publication plan

This document tracks the repository's transition to GitHub and MIT. It replaces the
historical proprietary release plan, now obsolete. No step by itself grants
authorization to push, tag or publish.

## Baseline

- 31 CLI commands grouped into eight documented features;
- stdout JSON, stderr diagnostics and exit 0/1/2 contract;
- deterministic mock tests, real Chrome and Dockerized Symfony;
- `make check-local`, `make check`, `make proof` and `make release` as sources
  of truth;
- wheel and sdist built by `python -m build` then checked by Twine.

## 1. License and metadata

- [x] Confirm that the holder named in the license has the rights
      necessary for relicensing.
- [x] Install the MIT text without inventing a name or a year.
- [x] Align `pyproject.toml`, README, changelog and packaging tests.
- [x] Verify the license in the rebuilt wheel and sdist.

## 2. Public repository

- [x] README for source installation and reproducible loopback quickstart.
- [x] `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md` and `SUPPORT.md`.
- [x] Documentation stripped of client references and of the active GitLab
      status.
- [x] Remove generated proofs from the index and ignore `.proof/`.
- [x] Scan the current state and the entire history with a dedicated tool
      before the first public push.

## 3. GitHub Actions

- [x] Pull requests: call the Make targets without duplicating their logic.
- [x] Keep Docker, Chrome and Symfony as mandatory gates.
- [x] Use minimal permissions and pin third-party actions.
- [x] Publish only the manifested textual staging `.proof/shareable/`
      as a temporary artifact; keep captures/PDFs/opaque binaries private.
- [x] Validate workflow syntax locally with `actionlint`.
- [x] Run the workflows on a real GitHub runner.

## 4. Distribution

- [x] Build wheel and sdist from the integrated state and check their content.
- [x] Install the wheel in a clean environment and verify
      `cdpx --help`, `cdpx --version` and the 31 commands.
- [x] Prepare a GitHub Release on tag without triggering it.
- [x] Prepare PyPI Trusted Publishing via OIDC, without a long-lived token.
- [x] Prepare version `0.2.0`, suited to pre-1.0 changes.
- [ ] Create the tag only after explicit validation from the owner.

## Final gate

Before any opening or publication:

1. `make release` green in the integrated state;
2. Docker built from a clean context;
3. real Chrome and Symfony scenarios without skip;
4. proof cockpit green and not versioned;
5. wheel/sdist inspected and checked by Twine;
6. GitHub Actions green on the remote repository;
7. GitHub private reporting settings and Trusted Publishing enabled.

The lab limitations of the vitals and the pre-1.0 beta status remain
documented; they do not block an honest publication.
