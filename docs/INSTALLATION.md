# Installation

This document covers **installed mode**: deploying the cdpx launcher on a
host, keeping it up to date, understanding its runtime constraints and
removing it. Python, Chromium and the application are supplied by a
digest-pinned Docker image; the host needs only Docker and a POSIX shell.
Linux is the supported platform, WSL2 follows the Linux contract, and macOS
launcher support is beta. To work on cdpx itself, use a development checkout
instead — see the [development portal](DEVELOPMENT.md).

## Install the launcher

```bash
curl -fsSL https://inem0o.github.io/cdpx/install | sh
cdpx --version
```

The installer downloads `SHA256SUMS`, verifies the selected release asset and
atomically installs `cdpx` under `$HOME/.local/bin`. Add that directory to
`PATH` if necessary. Select a release or destination explicitly:

```bash
curl -fsSL https://inem0o.github.io/cdpx/install -o /tmp/cdpx-install
sh /tmp/cdpx-install --version v0.1.4 --install-dir "$HOME/bin"
```

## What gets installed

The installed `cdpx` is a small POSIX launcher, not the browser-automation
CLI itself. It contains the exact GHCR image digest selected by the release
(`ghcr.io/inem0o/cdpx@sha256:…`) and never follows a mutable Docker tag. On
first use it starts one hardened runtime container per workspace — read-only
root filesystem, all capabilities dropped, `no-new-privileges`, no Docker
socket — and forwards every command into it with `docker exec`. The commands
you type are executed by the cdpx CLI inside that pinned image.

Installed-mode constraints:

- Docker Engine (or Docker Desktop) is required and must be running.
- The optional workspace configuration lives in `cdpx.yaml` at the worktree
  root; see [workspace configuration](CONFIGURATION.md).
- Private runtime state lives in `.cdpx/runtime/` inside the workspace.
- One runtime container serves one user and one worktree; the full runtime
  contract is described in the [integration guide](INTEGRATION.md).

## Manage the launcher

The launcher's management surface is intentionally small:

```bash
cdpx --version                  # launcher, runtime and image versions
cdpx update                     # self-update to the latest release
cdpx update --version vX.Y.Z    # self-update to a specific release
cdpx init                       # scaffold cdpx.yaml in the workspace
cdpx runtime status             # runtime identity and active sessions
```

`cdpx update` replays the stable installer; the updated launcher pins the
digest selected by that release. `cdpx runtime plan|status|stop|reset`
inspects and controls the per-workspace runtime container; the lifecycle
semantics are documented in the [integration guide](INTEGRATION.md). Every
other invocation is forwarded to the in-image CLI.

There is no `uninstall`, `doctor` or channel subcommand: removal is the
manual procedure below, and image selection is release-controlled.

## Experimental embedded mode

Linux/glibc integrators can copy the exact self-contained bundle from the
production image:

```dockerfile
FROM ghcr.io/inem0o/cdpx:0.1.4 AS cdpx
FROM debian:bookworm-slim
COPY --from=cdpx /opt/cdpx /opt/cdpx
RUN /opt/cdpx/install --link /usr/local/bin/cdpx
```

Or install the release archive with
`sh /tmp/cdpx-install --embedded`. Alpine/musl, Windows native and macOS
cannot execute this Linux/glibc bundle; use the Docker launcher or sidecar.

## Uninstall

Remove the installed launcher. Then remove workspace runtimes explicitly:

```bash
cdpx runtime stop --force
rm "$HOME/.local/bin/cdpx"
```

`stop` refuses active sessions unless forced; see the
[integration guide](INTEGRATION.md) for the full runtime lifecycle.
Embedded installs may additionally be removed from
`$HOME/.local/lib/cdpx-vX.Y.Z`. cdpx never deletes Docker images
automatically; use the normal Docker image-pruning policy for the host.
