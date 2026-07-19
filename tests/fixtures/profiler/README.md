# Symfony Web Profiler HTML fixtures

These files freeze the WebProfilerBundle 7.3 panel markup parsed by
`src/cdpx/primitives/profiler/`. The fixture server exposes them at
`/_profiler/<token>?panel=<name>`; the filename is the `panel` parameter.
`exception-raised.html` is used only by parser tests.

The structures are adapted from the real reference application under
`tests/symfony-app/`, exercised by `make docker-symfony-e2e`. They keep the
meaningful markers while removing interface CSS and JavaScript: `metric`
blocks, `tab-title` tabs, pool and HTTP-client badges, mixed
`<th>key</th><td>value</td>` rows, `sf-dump` blocks, response status spans
and logger filter badges.

Values are deliberately distinctive and asserted by
`tests/test_profiler_panels.py`; update the tests with any fixture change.

The adapted structures remain covered by the WebProfilerBundle MIT license.
[`LICENSE.SYMFONY`](LICENSE.SYMFONY) contains the upstream notice and license
text.

To capture the current markup:

```bash
make docker-symfony-e2e
TOKEN=$(curl -si http://localhost:8000/profiler-target | sed -n 's/^X-Debug-Token: //p' | tr -d '\r')
curl "http://localhost:8000/_profiler/$TOKEN?panel=db" > db.html
```

Trim the interface-only markup, run `make check`, and update the parsers only
when a meaningful marker changes.
