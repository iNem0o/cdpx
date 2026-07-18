# Homepage demo casts

These asciicast v2 files are replayed by asciinema-player in
`site/index.html`. They are not mockups: every JSON output and every
duration comes from commands actually executed against a real Chrome
and the repository's reference site (`tests/fixtures/`). Only the
keystrokes are synthesized (deterministic pacing) for readability.

## Scripted generation

The full protocol lives in `scripts/site_casts/`:

```bash
# from the repository root, with Chrome/Chromium + Docker installed
make site-casts                                  # everything, Symfony app included

# or by hand, without the profiler scenario:
python3 scripts/site_casts/generate.py list      # scenario catalog
python3 scripts/site_casts/generate.py record    # (re)record on :8899
python3 scripts/site_casts/generate.py check     # validate format + forbidden values
```

`record` starts, for each scenario, the fixtures server and a disposable
supervised session (`authority privileged`, loopback origins), runs the
scenario's commands, checks their expectations (`expect`, exit code) and
writes the cast only if everything is green. `--only id,id` and `--port N`
let you record a subset or avoid a busy port.

Each scenario is a module in `scripts/site_casts/scenarios/`: `Comment`
steps (a pedagogical `#` line), `Cmd` (a cdpx command) or `Shell` (a bash
pipeline, e.g. `jq -e`). Adding a cast means adding a module, registering
it, then wiring it into `site/index.html`.

## Published casts

| Cast | Group | Commands covered |
|---|---|---|
| `session.cast` | Session | session start --export / status / stop, version, tabs list |
| `nav.cast` | Navigation | goto, wait, count, text |
| `read.cast` | Reading | text, html, count, eval, frame |
| `act.cast` | Interaction | type --secret-env, click, key, dom-diff |
| `observe.cast` | Observability | console --duration, network, metrics |
| `capture.cast` | Capture | screenshot, pdf, a11y |
| `state.cast` | State | storage, cookies get/set/clear --value-env |
| `seo.cast` | SEO | seo (+ jq on findings, compliant vs broken page) |
| `perf.cast` | Performance | vitals --click, coverage, budget jq -e |
| `journey.cast` | Journey | record ×3, replay, scenario run |
| `resilience.cast` | Resilience | intercept "*api* => 503", text, emulate |
| `profiler.cast` | Symfony Profiler | profiler --panels db,cache (healthy and N+1 variants, jq -e gate) |

The `profiler` scenario is recorded against the real reference Symfony app
(`tests/symfony-app`): `make site-casts` starts it via the
`docker-compose.site-casts.yml` overlay (loopback :8025) and passes
`--symfony-base` to the generator. Without a base supplied, it is skipped
cleanly (`skipped`), and `check` only treats it as an error if its cast is
present but invalid.

The session values visible in the outputs (identifiers, `/run/user/...`
paths) belong to disposable sessions destroyed after recording. Scenarios
declare their forbidden values (`forbidden`): a leaked secret fails the
generation.
