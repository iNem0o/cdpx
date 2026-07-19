# Releasing cdpx

Public releases are Docker-first and require explicit maintainer approval.
The workflow promotes an already verified image digest; it never rebuilds a
tag and does not publish to PyPI.

## Preconditions

- `master` contains the intended clean release commit.
- `pyproject.toml` and `CHANGELOG.md` contain the same `X.Y.Z` version.
- `PR Gate / Required` and the master candidate workflow are green for that
  exact commit.
- The protected `release` environment has a required reviewer.
- GHCR packages and GitHub Pages are public, and the `v*` namespace is
  protected.

## Local verification

```bash
./dev release
CDPX_IMAGE_REF=cdpx-runtime:dev ./cdpx --version
```

Inspect `.proof/shareable/` and the locally built runtime. The wheel under
`dist/` is an internal packaging check, not a public asset.

## Tag and promotion

1. Create a signed or annotated `vX.Y.Z` tag on the verified master commit.
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
