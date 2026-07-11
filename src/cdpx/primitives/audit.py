"""Primitives d'audit: SEO on-page et métriques de performance.

Usecase direct (audits e-commerce et SEO): extraire en un appel le
contrat SEO d'une page TELLE QUE RENDUE par le navigateur — pas telle que
servie en HTML brut. C'est toute la différence sur les fronts JS: canonical
injecté, JSON-LD posé par GTM, hreflang réécrits... seul le DOM final fait foi
pour Googlebot rendering.
"""

from __future__ import annotations

import json

from cdpx.client import CDPClient
from cdpx.primitives.js import evaluate

# Marqueur __cdpx_seo: identifie l'expression (utile aux tests et au debug).
SEO_JS = r"""
(() => { const __cdpx_seo = 1;
  const attr = (sel, a) => {
    const el = document.querySelector(sel); return el ? el.getAttribute(a) : null;
  };
  const metas = {};
  document.querySelectorAll('meta[name], meta[property]').forEach(m => {
    metas[m.getAttribute('name') || m.getAttribute('property')] = m.getAttribute('content');
  });
  const jsonld = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach(s => {
    try { jsonld.push(JSON.parse(s.textContent)); }
    catch (e) { jsonld.push({__parse_error: String(e)}); }
  });
  const hreflang = [];
  document.querySelectorAll('link[rel="alternate"][hreflang]').forEach(l => {
    hreflang.push({lang: l.getAttribute('hreflang'), href: l.getAttribute('href')});
  });
  const links = {internal: 0, external: 0, nofollow: 0};
  document.querySelectorAll('a[href]').forEach(a => {
    try {
      const u = new URL(a.href, location.href);
      if (u.origin === location.origin) links.internal++; else links.external++;
      if ((a.getAttribute('rel') || '').includes('nofollow')) links.nofollow++;
    } catch (e) {}
  });
  return JSON.stringify({
    url: location.href,
    lang: document.documentElement.getAttribute('lang'),
    title: document.title,
    metas,
    canonical: attr('link[rel="canonical"]', 'href'),
    robots: metas['robots'] || null,
    h1: Array.from(document.querySelectorAll('h1')).map(h => h.innerText.trim()),
    hreflang,
    jsonld,
    images_without_alt: document.querySelectorAll('img:not([alt])').length,
    links,
  });
})()
"""


def seo(client: CDPClient) -> dict:
    raw = evaluate(client, SEO_JS)
    data = json.loads(raw)
    findings = []
    title = data.get("title") or ""
    description = data.get("metas", {}).get("description") or ""
    data["title_px_estimate"] = _px_estimate(title)
    data["description_px_estimate"] = _px_estimate(description)
    if not data.get("title"):
        findings.append("title manquant")
    if not data.get("metas", {}).get("description"):
        findings.append("meta description manquante")
    if not data.get("canonical"):
        findings.append("canonical manquant")
    if len(data.get("h1", [])) != 1:
        findings.append(f"{len(data.get('h1', []))} h1 (attendu: 1)")
    if data.get("images_without_alt"):
        findings.append(f"{data['images_without_alt']} image(s) sans alt")
    h1_norm = [_norm(h) for h in data.get("h1", [])]
    duplicated_h1 = sorted({h for h in h1_norm if h and h1_norm.count(h) > 1})
    if duplicated_h1:
        findings.append(f"h1 dupliqué: {', '.join(duplicated_h1)}")
    for item in _jsonld_items(data.get("jsonld", [])):
        if not isinstance(item, dict):
            findings.append("JSON-LD scalaire non supporté")
            continue
        if item.get("__parse_error"):
            findings.append("JSON-LD invalide")
        if item.get("@type") == "Product" and not (item.get("sku") or item.get("name")):
            findings.append("Product JSON-LD incomplet (sku ou name requis)")
    data["findings"] = findings
    return data


def _jsonld_items(value):
    """Aplatit les tableaux JSON-LD valides sans supposer que chaque racine est un objet."""
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_items(item)
    else:
        yield value


def _px_estimate(text: str) -> int:
    # Approximation stable pour agent/CI: largeur moyenne SERP desktop.
    return round(len(text) * 7.2)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def metrics(client: CDPClient) -> dict:
    """Performance.getMetrics: heap, nodes, layout count, timings navigateur."""
    client.send("Performance.enable")
    res = client.send("Performance.getMetrics")
    return {m["name"]: m["value"] for m in res.get("metrics", [])}
