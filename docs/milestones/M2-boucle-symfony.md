# M2 — Symfony/Shopware dev loop

## Why
This is the milestone with the highest business value: turning cdpx into a
complete feedback loop for Symfony dev. An agent that reads the profiler after
each action detects the N+1, the 2s query, the swallowed exception — without
the human opening anything.

## Primitives
### cdpx profiler
- How: enable Network, navigate/act, read the `x-debug-token-link` header
  of the main response (Network.responseReceived -> response.headers,
  redirectResponse for 302s), then fetch
  `http://app.test/_profiler/{token}?panel=db` and parse the panels' HTML.
- Evolution note (post-M2): the profiler JSON API originally mentioned
  does not exist on the Symfony side — the real adapter parses the panels'
  HTML (see `src/cdpx/primitives/profiler/`) and the fetch happens FROM
  the page (same-origin fetch), not from cdpx: browser cookies and host
  resolution, indispensable behind Docker.
- Output: {token, url, panels: {db: {queries, statements, duplicates, ...},
  twig, cache, exception, http_client, messenger, router, time, logger}}.
- Fixture: the fixture server gains an `/api/profiler-sim` endpoint that
  emits the header and serves frozen HTML panels (real WebProfilerBundle
  markup trimmed down) -> testable without Symfony.
- Mock test: script Network.responseReceived with the header; verify the
  page-context fetch of the panels and their parsing.

### cdpx console --follow
- How: collect_events loop with no fixed duration, NDJSON output (1 line =
  1 entry), stop via Ctrl-C or --max n. Contract: NDJSON on stdout is a
  documented EXCEPTION to "one JSON object".

### cdpx dom-diff
- How: `snapshot before` (normalized serialization: tag, id, sorted classes,
  data-* attributes), action, `snapshot after`, unified diff.
- Usecase: "what did this click change in the DOM?" — exact answer.
- Fixture: form.html suffices (data-state goes from idle to submitted).

## Definition of Done
- [x] profiler tested against a fixture simulating the header + against a real
      Symfony demo (`make docker-symfony-e2e`: real Doctrine, cache, HTTP
      client, Messenger collectors — panels parsed, no more fabricated
      X-CDPX signal)
- [x] follow: NDJSON documented in PRIMITIVES.md (exception to the "one
      JSON object" contract made explicit, tested in `tests/test_cli.py`)
- [x] dom-diff: stable diff (2 runs = same diff — sorted snapshot
      normalization, proven by a mock test in `tests/test_primitives.py`)
