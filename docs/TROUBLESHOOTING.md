# Troubleshooting

## Docker is unavailable

`cdpx` and `./dev` fail early when the Docker CLI or daemon is unavailable.
Start Docker Desktop/Engine and verify `docker info`. On macOS only a local
Unix Docker context is supported by the development portal.

## A runtime cannot be replaced

Configuration or image changes require a new worktree runtime. Inspect it:

```bash
cdpx runtime status
```

Stop the active session normally. Use `cdpx runtime reset --force` only when
intentional teardown of every active session is acceptable.

## A configured variable is missing

Names under `environment.required` must be non-empty in every invocation.
Export the value or move the name to `environment.optional`. Literal secrets
do not belong under `environment.set`; use the existing secret-reference
options for browser actions.

## Embedded mode reports an unsupported platform

The bundle requires Linux/glibc and amd64 or arm64. Alpine uses musl; Windows
native and macOS use different executable formats. Run the official Docker
image as a sidecar instead.

## Cleanup

`cdpx runtime stop --force` removes the runtime container.
`cdpx runtime reset --force` also removes `.cdpx/runtime`. Development caches
are cleared with `./dev clean`; Docker image cleanup remains under the host's
normal retention policy.
