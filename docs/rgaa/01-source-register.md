# 01 — Source register

| Source | Pin | Role | License / authority |
|---|---|---|---|
| `DISIC/accessibilite.numerique.gouv.fr` | commit `ca4019f95073b6cbd2482a16e9f12b52d8de678d` | RGAA 4.1.2 criteria, methods, glossary | Official DINUM content, Licence Ouverte 2.0 |
| `dequelabs/axe-core` | 4.10.3, SHA-256 `880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb` | Optional WCAG advisory observations | MPL-2.0; never normative RGAA authority |
| Chrome DevTools Protocol | Chromium pinned by the cdpx OCI image | DOM, CSS, Accessibility, Input, Runtime | Browser observation/interaction transport |
| W3C WCAG 2.1 / ACT | references embedded by DINUM and provider tags | Cross-reference and atomic-rule validation | Informative for mappings; not a replacement catalog |

The exact source hashes are recorded in
`src/cdpx/rgaa/data/4.1.2/source-manifest.json`. Runtime network fetches are
forbidden. The generated catalog hash is enforced by `cdpx.rgaa.catalog`.
