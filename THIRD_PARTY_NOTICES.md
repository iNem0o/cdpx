# Third-party notices

## Mermaid

The proof cockpit embeds Mermaid 11.16.0 for offline rendering of diagrams.
Mermaid is distributed under the MIT License. Its unmodified browser bundle
and license are stored under `src/cdpx/proofing/vendor/`.

- Package: `mermaid@11.16.0`
- Source archive: `https://registry.npmjs.org/mermaid/-/mermaid-11.16.0.tgz`
- npm integrity: `sha512-Zvm3kbstgdpvIJPPItlL7fppIZ3kibvc1oZIGxdvk9t6UFz6flv+Jw7FtRGKwfcI8OckmH04LqG6LlS6X4B1pA==`
- Vendored bundle SHA-256: `74d7c46dabca328c2294733910a8aa1ed0c37451776e8d5295da38a2b758fb9b`

See `src/cdpx/proofing/vendor/LICENSE.mermaid` for the complete license text.

## xterm.js

The proof cockpit embeds xterm.js 5.5.0 to replay asciicast v2 terminal
recordings offline with full ANSI fidelity. xterm.js is distributed under the
MIT License. Its unmodified browser bundle, stylesheet and license are stored
under `src/cdpx/proofing/vendor/`.

- Package: `@xterm/xterm@5.5.0`
- Source archive: `https://registry.npmjs.org/@xterm/xterm/-/xterm-5.5.0.tgz`
- npm integrity: `sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A==`
- Vendored bundle SHA-256: `4196e242ef1cf4c2adead8d97f4a772a69576076f70b095e004b4abbb049e7bf`
- Vendored stylesheet SHA-256: `f7f724aea2bb620a6482bfb8e4bdecfae1152b0c7facef55fbda61f3b6cfedb2`

See `src/cdpx/proofing/vendor/LICENSE.xterm` for the complete license text.

## RGAA 4.1.2 official content

The RGAA criteria, methodologies and glossary are reproduced from the DINUM
repository `DISIC/accessibilite.numerique.gouv.fr` at commit
`ca4019f95073b6cbd2482a16e9f12b52d8de678d`. The repository states that its
content, except identified third-party material, is published under
**Licence Ouverte 2.0**. Exact file hashes and roles are stored in
`src/cdpx/rgaa/data/4.1.2/source-manifest.json`.

- Source: `https://github.com/DISIC/accessibilite.numerique.gouv.fr`
- License: `https://www.etalab.gouv.fr/licence-ouverte-open-licence/`

## axe-core

The optional RGAA hybrid engine embeds the unmodified axe-core 4.10.3 browser
bundle for offline advisory WCAG observations. axe-core is distributed under
the Mozilla Public License 2.0.

- Package: `axe-core@4.10.3`
- Source archive: `https://registry.npmjs.org/axe-core/-/axe-core-4.10.3.tgz`
- Vendored bundle SHA-256: `880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb`

See `src/cdpx/rgaa/vendor/LICENSE.axe-core` and
`src/cdpx/rgaa/vendor/LICENSE-3RD-PARTY.axe-core` for the complete notices.
