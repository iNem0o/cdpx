# Installation

The supported distribution is a small POSIX launcher backed by a
digest-pinned Docker image. Python, Chromium and the application are supplied
by that image; the host needs only Docker and a POSIX shell. Linux is the
supported platform, WSL2 follows the Linux contract, and macOS launcher
support is beta.

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
sh /tmp/cdpx-install --version v0.1.0 --install-dir "$HOME/bin"
```

The launcher contains the exact GHCR digest selected by the release. It does
not follow a mutable Docker tag. `cdpx update` replays the stable installer;
`cdpx update --version v0.1.0` selects a specific release.

## Development checkout

Development also needs only Docker:

```bash
git clone https://github.com/inem0o/cdpx.git
cd cdpx
./dev setup
./dev check-local
```

No host Python environment is read or modified. See
[Development](DEVELOPMENT.md) for the complete portal.

## Experimental embedded mode

Linux/glibc integrators can copy the exact self-contained bundle from the
production image:

```dockerfile
FROM ghcr.io/inem0o/cdpx:0.1.0 AS cdpx
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

Embedded installs may additionally be removed from
`$HOME/.local/lib/cdpx-vX.Y.Z`. cdpx never deletes Docker images
automatically; use the normal Docker image-pruning policy for the host.
