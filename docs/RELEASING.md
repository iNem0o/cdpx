# Releasing cdpx

This procedure prepares and publishes a cdpx release. Publishing requires
explicit maintainer authorization.

## Preconditions

- `master` contains the intended release commit and the worktree is clean.
- `src/cdpx/__init__.py` and `CHANGELOG.md` contain the same `X.Y.Z` version.
- The changelog heading uses the release date in `YYYY-MM-DD` form.
- `PR Gate / Required` is green for the release commit.
- The `pypi` environment, approval rule and PyPI Trusted Publisher are
  verified.
- The `v*` tag namespace is protected.

## Local verification

```bash
make release
```

The command must complete the deterministic checks, Docker validation, real
Chrome and Symfony scenarios, proof cockpit, `twine check --strict`,
distribution-content inspection and isolated wheel installation. Inspect:

```bash
ls -l dist/
python scripts/verify_dist.py dist/*
```

The wheel and source archive must report the intended version and contain
only current documentation.

## Tag and publication

1. Create the signed or annotated tag `vX.Y.Z` on the verified commit.
2. Push only that tag.
3. Confirm that the Release workflow verifies:
   - the tag matches the package version;
   - the tagged commit belongs to `master`;
   - `make release` succeeds;
   - the distributions and proof match the tagged commit.
4. Approve the protected `pypi` environment.
5. Confirm the Trusted Publishing upload and the GitHub Release assets.
6. Install the published wheel in a clean environment and run:

   ```bash
   python -m pip install "cdpx==X.Y.Z"
   cdpx --version
   cdpx --help
   ```

The workflow uses OIDC; no long-lived PyPI token belongs in repository
secrets. A failed or partial publication stops the procedure and is diagnosed
from the workflow run before any retry.
