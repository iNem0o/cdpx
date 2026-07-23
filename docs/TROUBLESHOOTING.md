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

## ERR_CERT_AUTHORITY_INVALID on local HTTPS

A development stack that terminates TLS with a local authority (`mkcert`,
traefik's internal CA) is not trusted by the session's fresh Chrome, so
navigation fails with `ERR_CERT_AUTHORITY_INVALID`.

Prefer teaching the session to trust that CA. Copy the public root
certificate into the workspace and list it under `runtime.trust_ca`:

```bash
mkdir -p certs
cp "$(mkcert -CAROOT)/rootCA.pem" certs/
```

```yaml
runtime:
  trust_ca:
    - certs/rootCA.pem
```

Copy only `rootCA.pem`, never the `rootCA-key.pem` private key; a file
containing a `PRIVATE KEY` block is rejected at compilation. Changing
`trust_ca` replaces the idle runtime on the next invocation. Full
guidance is in
[configuration](CONFIGURATION.md#local-https-mkcert-traefik).

For a one-off diagnostic, `session.ignore_tls_errors: true` (or
`cdpx session start --ignore-tls-errors`) disables certificate
validation for the whole session. It is a dev-only fallback.

Importing a CA needs `certutil`. When trust is requested and no
`certutil` is found, `session start` fails with a `PolicyError`. Install
`libnss3-tools` (which provides `certutil`) or point `CDPX_CERTUTIL` at
an existing binary. The bundled runtime image already ships it.

## Cleanup

`cdpx runtime stop --force` removes the runtime container.
`cdpx runtime reset --force` also removes `.cdpx/runtime`. Development caches
are cleared with `./dev clean`; Docker image cleanup remains under the host's
normal retention policy.
