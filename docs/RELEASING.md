# Releasing cdpx

Public releases are Docker-first and require explicit maintainer approval.
The workflow promotes an already verified image digest; it never rebuilds a
tag and does not publish to PyPI.

## Runbook

The tag is the trigger, not the release: prepare, prove, tag, approve.
The sections below detail each step; this is the complete order.

1. Start from the merged, green tip: `git checkout main && git pull`.
2. Run `./dev bump X.Y.Z`: it moves every version pin from the shared
   registry (`tools/release_pins.py`) and renames the `[Unreleased]`
   changelog section to `[X.Y.Z] — DATE`, refusing to run while that
   section is empty. Review the diff and commit it as `Prepare cdpx
   X.Y.Z`.
   `tests/test_packaging.py::test_release_version_pins_move_together`
   reads the same registry and fails on any laggard.
3. Prove the prep commit locally: `./dev check-local`, `./dev check`,
   then `./dev release` and the pinned-image smoke test below.
4. Push `main` and wait for the main CI run to be green through
   `Candidate / manifest`: promotion needs the immutable `sha-COMMIT`
   candidate that this run publishes.
5. Tag that exact commit: `git tag -a vX.Y.Z -m "cdpx X.Y.Z"`, then push
   only the tag.
6. Approve the protected `release` environment when the workflow pauses.
   Nothing publishes before this approval.
7. Verify the promotion as described in "Tag and promotion" steps 5–7.

## Preconditions

- `main` contains the intended clean release commit.
- `pyproject.toml` and `CHANGELOG.md` contain the same `X.Y.Z` version.
- `PR Gate / Required` and the main candidate workflow are green for that
  exact commit.
- The protected `release` environment has a required reviewer.
- GHCR packages and GitHub Pages are public, and the `v*` namespace is
  protected.

## Local verification

```bash
./dev release
CDPX_RUNTIME_IMAGE=cdpx-runtime:release-check ./dev image
CDPX_IMAGE_REF=cdpx-runtime:release-check ./cdpx --version
```

Inspect `.proof/shareable/` and the locally built runtime. The wheel under
`dist/` is an internal packaging check, not a public asset.

## Tag and promotion

1. Create a signed or annotated `vX.Y.Z` tag on the verified main commit.
2. Push only that tag.
3. Confirm the workflow finds the successful `sha-COMMIT` candidate.
4. Review and approve the protected `release` environment.
5. Confirm promotion of the same digest to `X.Y.Z`, `X.Y`, `X` and `latest`.
6. Confirm the GitHub Release contains `cdpx`, both embedded archives,
   `install`, and `SHA256SUMS`.
7. Verify the stable installer and pinned image:

   ```bash
   curl -fsSL https://inem0o.github.io/cdpx/install -o /tmp/cdpx-install
   sh /tmp/cdpx-install --version vX.Y.Z --install-dir /tmp/cdpx-bin
   /tmp/cdpx-bin/cdpx --version
   ```

The launcher embeds the promoted `sha256` digest. See
[Release architecture](RELEASE-ARCHITECTURE.md) for candidate construction,
multi-architecture identity and rollback.

## Rollback

Do not rebuild an older version. Select the last known-good digest,
approve a retag of that digest, update release notes and record why the
mutable channel moved. Immutable `sha-COMMIT` candidates remain the audit
trail.
