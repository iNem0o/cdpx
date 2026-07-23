# Workspace configuration

`cdpx.yaml` is an optional, strict project configuration. The launcher finds
the Git worktree root (or the current directory outside Git), validates the
file inside the pinned cdpx image and creates one persistent runtime for that
worktree. Unknown keys, unsafe mounts and invalid values fail with exit 2.

Generate the commented template:

```bash
cdpx init
cdpx runtime plan
```

## Complete schema

```yaml
schema: cdpx/v1
runtime:
  network: "network:${STACK_NET:-stack_default}"
  extra_hosts:
    - "${STACK_APP_HOST:-app.local}:host-gateway"
    - "api.stack.local:172.20.0.10"
  idle_timeout: 24h
  shm_size: 1g
  trust_ca: []
environment:
  required: [APP_URL]
  optional: [HTTP_PROXY]
  set:
    FEATURE_MODE: browser
mounts:
  - source: tests/fixtures
    target: /fixtures
    read_only: true
session:
  ttl: 1h
  origins:
    - http://127.0.0.1:*
  ignore_tls_errors: false
```

The machine-readable contract is
[`schemas/cdpx.schema.json`](../schemas/cdpx.schema.json).

## Option reference

| Key | Default | Contract |
| --- | --- | --- |
| `schema` | `cdpx/v1` | Required schema generation when present |
| `runtime.network` | `host` | `host`, `bridge`, `network:NAME`, or `container:NAME` |
| `runtime.extra_hosts` | `[]` | `hostname:target` entries; target is an IP or `host-gateway`; rejected with `container:` mode |
| `runtime.idle_timeout` | `24h` | 5 minutes through 7 days |
| `runtime.shm_size` | `1g` | 256 MiB through 4 GiB |
| `runtime.trust_ca` | `[]` | workspace PEM CA files bind-mounted read-only at `/etc/cdpx/trust/<basename>` and imported into a per-session trust store; a file containing a private key fails compilation |
| `environment.required` | `[]` | names copied from the invocation; missing/empty fails |
| `environment.optional` | `[]` | names copied only when present and non-empty |
| `environment.set` | `{}` | non-secret strings, resolved at plan compilation |
| `mounts[].source` | none | existing path contained by the worktree |
| `mounts[].target` | none | absolute, unique, non-system container path |
| `mounts[].read_only` | `true` | explicit `false` enables writes |
| `session.ttl` | `1h` | 60 seconds through 24 hours |
| `session.origins` | `[]` | default allowlist supplied to session startup |
| `session.ignore_tls_errors` | `false` | dev-only fallback; launches Chrome with `--ignore-certificate-errors` |

## Local HTTPS (mkcert, traefik)

Development stacks that terminate TLS with a local authority (`mkcert`,
traefik's internal CA) make Chrome raise `ERR_CERT_AUTHORITY_INVALID`.
The supported fix is to teach the session to trust that CA. Copy the
public root certificate into the workspace and list it under
`runtime.trust_ca`:

```bash
mkdir -p certs
cp "$(mkcert -CAROOT)/rootCA.pem" certs/
```

```yaml
runtime:
  trust_ca:
    - certs/rootCA.pem
```

Copy only `rootCA.pem`. Never copy or mount `rootCA-key.pem` (the private
key that lives beside it in `mkcert -CAROOT`); configuration compilation
rejects any file containing a `PRIVATE KEY` block. Each listed file is
bind-mounted read-only and imported into a per-session trust store at
`session start`. `trust_ca` enters the plan fingerprint, so changing it
replaces an idle runtime on the next invocation.

For a throwaway diagnostic where trusting the CA is not warranted,
`session.ignore_tls_errors: true` disables certificate validation for the
whole session instead. It is a dev-only fallback; prefer `runtime.trust_ca`.

## Environment interpolation

String values may reference variables from the calling environment:
`${NAME}` requires the variable (an empty value resolves to an empty
string, an unset one fails compilation), `${NAME:-default}` substitutes
the default when the variable is unset or empty, and `$$` writes a
literal `$`. Any other `$` is rejected. Only values are interpolated,
never keys, and every resolved value passes the same strict validation
as a literal one. Files written before interpolation existed keep their
meaning except for a literal `$`, which must now be escaped as `$$`.

Resolution happens once, when the execution plan is compiled. Resolved
values enter the plan fingerprint — changing a referenced variable
replaces an idle runtime on the next invocation — and appear in
`cdpx runtime plan` output. Never interpolate secrets; forward those
per invocation with `environment.required` or `environment.optional`.

The typical use is a development stack whose tooling exports its Docker
network name and service addresses: `network: "network:${STACK_NET}"`
joins the stack, and `extra_hosts` re-creates hostnames the tooling
would otherwise only register in the host's `/etc/hosts`, either with a
stack IP or with the special `host-gateway` target.

For parallel Git worktrees, resolve `STACK_NET` to a different application
network per worktree and isolate writable services such as databases, queues
and caches. cdpx isolates its runtime, Chrome profile and artifacts; joining
two runtimes to the same application network intentionally does not isolate
the application's state.

Project configuration cannot replace the runtime image or forward
arbitrary host environment at execution time. Interpolation resolves
only the variables the file explicitly references, at compilation;
per-invocation forwarding remains limited to the declared `environment`
names. Image selection is release-controlled; a development-only image
override exists for contributors — see
[DEVELOPMENT.md](DEVELOPMENT.md#development-image-override).

Changing configuration replaces an idle runtime. If sessions are active the
launcher refuses replacement; stop them first or use
`cdpx runtime reset --force`.
