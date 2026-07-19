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
  network: host
  idle_timeout: 24h
  shm_size: 1g
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
```

The machine-readable contract is
[`schemas/cdpx.schema.json`](../schemas/cdpx.schema.json).

## Option reference

| Key | Default | Contract |
| --- | --- | --- |
| `schema` | `cdpx/v1` | Required schema generation when present |
| `runtime.network` | `host` | `host`, `bridge`, `network:NAME`, or `container:NAME` |
| `runtime.idle_timeout` | `24h` | 5 minutes through 7 days |
| `runtime.shm_size` | `1g` | 256 MiB through 4 GiB |
| `environment.required` | `[]` | names copied from the invocation; missing/empty fails |
| `environment.optional` | `[]` | names copied only when present and non-empty |
| `environment.set` | `{}` | literal non-secret strings |
| `mounts[].source` | none | existing path contained by the worktree |
| `mounts[].target` | none | absolute, unique, non-system container path |
| `mounts[].read_only` | `true` | explicit `false` enables writes |
| `session.ttl` | `1h` | 60 seconds through 24 hours |
| `session.origins` | `[]` | default allowlist supplied to session startup |

Project configuration cannot replace the runtime image or forward arbitrary
host environment. Image selection is release-controlled. The
`CDPX_IMAGE_REF` override exists only for development and controlled
integration tests.

Changing configuration replaces an idle runtime. If sessions are active the
launcher refuses replacement; stop them first or use
`cdpx runtime reset --force`.
