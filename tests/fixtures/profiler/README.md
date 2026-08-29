# Web profiler HTML contract fixtures

These files freeze meaningful profiler panel structures parsed by
`src/cdpx/primitives/profiler/`. They are parser contracts only: the fixture
server does not expose fake profiler endpoints and these files never attest
runtime compatibility.

The Symfony structures are adapted from the real reference application under
`tests/symfony-app/`, currently locked to WebProfilerBundle 7.4.14 and
exercised by `./dev check`. They keep the
meaningful markers while removing interface CSS and JavaScript: `metric`
blocks, `tab-title` tabs, pool and HTTP-client badges, mixed
`<th>key</th><td>value</td>` rows, `sf-dump` blocks, response status spans
and logger filter badges.

`shopware-db-grouped.html` is a normalized excerpt captured from the real
Shopware 6.7.13.1 gate. It retains the authentic Shopware collector metrics,
table headings and the five-execution SQL row while removing tokens,
backtraces and unrelated statements. `manifest.json` records its provenance
and exact component versions.

Values are deliberately distinctive and asserted by
`tests/test_profiler_panels.py`; update the tests with any fixture change.

The adapted structures remain covered by the WebProfilerBundle MIT license.
[`LICENSE.SYMFONY`](LICENSE.SYMFONY) contains the upstream notice and license
text.

To refresh Symfony markup, run the real Symfony gate, obtain its disposable
token and download the requested panel. To refresh Shopware markup, run the
real Shopware gate and download `app.connection_collector` with `group=true`.
Normalize the capture without changing meaningful labels or table structure.

After any refresh, run `./dev check` and update parsers only when a meaningful
marker changed.
