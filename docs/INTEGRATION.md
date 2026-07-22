# Integration guide

cdpx exposes the same CLI contract in three integration modes. Prefer the
launcher for a developer worktree, a sidecar for Compose/Kubernetes-style
topologies, and the embedded bundle only when a Linux/glibc image must carry
the binary directly.

## Persistent worktree runtime

The launcher names the container from the canonical worktree and user ID,
bind-mounts the worktree at the same absolute path and keeps private state in
`.cdpx/runtime/`. It runs read-only, drops all capabilities, sets
`no-new-privileges`, and never mounts the Docker socket into the production
runtime.

```bash
cdpx runtime plan
cdpx runtime status
cdpx runtime stop
cdpx runtime reset --force
```

`plan` shows normalized configuration. `status` reports the runtime identity
and active sessions. `stop` refuses active sessions unless `--force`;
`reset --force` also removes generated runtime state.

## Environment and paths

Every `docker exec` uses the caller's current directory and a short-lived
`0600` environment file. Only the core identity variables plus names declared
in `cdpx.yaml` are forwarded. The environment file is deleted after the
command. Mount sources must remain within the worktree, so the configuration
is portable and cannot silently expose `$HOME` or `/`.

A development stack running on the same host is reached by joining its
network (`runtime.network: network:<stack>`) and, when the stack registers
hostnames in the host's `/etc/hosts`, by declaring them as
`runtime.extra_hosts` — the runtime container never reads the host file.
Stack tooling that exports its network name and addresses as environment
variables can drive both values through `${VAR}` interpolation; see
[CONFIGURATION.md](CONFIGURATION.md).

```yaml
runtime:
  network: "network:${STACK_NET}"
  extra_hosts:
    - "${STACK_APP_HOST:-app.local}:host-gateway"
```

The session manifest carries a runtime attestation. A manifest created in one
runtime is rejected in another. Public callers select Chrome and supervisor
ownership through the runtime; there are no public `--chrome` or
`--owner-pid` escape hatches.

## Sidecar

[`packaging/compose.sidecar.yml`](../packaging/compose.sidecar.yml) shows the
minimum hardened service. Put the application and cdpx in the same Docker
network namespace when loopback is required, and execute CLI commands with
`docker compose exec cdpx cdpx ...`. Do not mount the host Docker socket.

## Embedded bundle

[`packaging/Dockerfile.embedded`](../packaging/Dockerfile.embedded) copies
`/opt/cdpx` from the official image. `/opt/cdpx/install --link PATH` creates a
link to the native entry point. Debian 12/13 and Ubuntu 24.04/26.04 on amd64
or arm64 are the intended validation matrix. The bundle is not a stable ABI;
pin the source image digest and update it as a unit.
