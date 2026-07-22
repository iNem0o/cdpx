# Development portal

This document is for people changing cdpx itself. To install and use cdpx
on a project, see [Installation](INSTALLATION.md) instead.

`./dev` is the canonical development, CI and local release-readiness portal.
Docker is its only host dependency. The pinned Python 3.14 toolchain,
Chromium, uv, linters, type checker, tests, Docker CLI and Compose plugin all
live in multi-stage targets from the same `Dockerfile`.

## Get a development checkout

Development needs only Docker:

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
./dev setup
./dev check-local
```

No host Python environment is read or modified.

## The ./dev commands

| Command | Purpose |
| --- | --- |
| `./dev setup` | build the development and production targets |
| `./dev check-local` | Ruff, format, mypy, unit, 85% line and 75% branch coverage |
| `./dev check` | blocking real Chrome, Symfony and proof gate |
| `./dev proof` | regenerate the private proof cockpit |
| `./dev release` | full gate plus internal wheel build |
| `./dev test-e2e` | real Chrome tests |
| `./dev image` | production runtime image |
| `./dev shell` | shell in the development image |
| `./dev fixtures` | reference site on port 8899 |
| `./dev mock` | protocol-faithful mock |
| `./dev site-record` | regenerate site casts |
| `./dev fmt` | Ruff formatting and safe fixes |
| `./dev clean` | remove generated workspace artifacts |

The worktree is mounted at the same absolute path, preserving Git worktree
semantics and artifact paths. `.cache/` contains BuildKit and tool caches.
The source tree is mounted over the image snapshot so a short-loop invocation
does not need to rebuild for every edit.

`Makefile` remains a thin internal compatibility facade for Compose and
proof scripts. Public instructions and CI must call `./dev`; new behavior is
registered once in `tools/harness.py`, not independently in Make and YAML.

## CI and the Docker socket

CI invokes `./dev`, exactly like a contributor. Development and CI mount the
host Docker socket only to run nested integration scenarios; this is
container-outside-of-container, not a privileged production runtime. Run
such jobs only on trusted runners and never expose the socket to untrusted
services.

## Development image override

The `CDPX_IMAGE_REF` environment variable makes the launcher run an
arbitrary image instead of the released digest. It exists only for
development and controlled integration tests — end-user image selection is
release-controlled. See [RELEASING.md](RELEASING.md) for how releases pin
the digest.

## Documentation gate

Before review, run `./dev check`. A public command, option, configuration key
or integration mode must update the appropriate user and integrator
documents plus `docs/surfaces.yaml`; the documentation gate verifies that
mapping.
