# ruff: noqa: E501
"""Integrity-isolated, bounded RGAA page probes.

Every expression is executed in a fresh isolated world. Partial observation
frontiers are explicit and can never support ``pass`` or ``not_applicable``.
"""

PASSIVE_PROBE = r"""
// __cdpx_rgaa_passive_v2
(() => {
  const ITEM_LIMIT = 200, NODE_LIMIT = 5000;
  const stringify = JSON.stringify.bind(JSON);
  const cut = (value, length = 160) => String(value || "").trim().slice(0, length);
  const structuralPath = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) {
      let part = current.localName || "element";
      const parent = current.parentElement;
      if (parent) {
        const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName);
        part += `:nth-of-type(${peers.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      const root = current.getRootNode();
      if (!parent && root instanceof ShadowRoot) { parts.unshift(">>"); current = root.host; }
      else current = parent;
    }
    return parts.join(" > ").replace(/ > >> > /g, " >> ");
  };
  const exposed = (element) => {
    if (element.closest("[hidden],[inert],[aria-hidden=true]")) return false;
    const style = getComputedStyle(element), rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      style.visibility !== "collapse" && Number(style.opacity) !== 0 &&
      rect.width > 0 && rect.height > 0;
  };
  const walkElements = (predicate) => {
    const matches = [], roots = [document.documentElement];
    let examined = 0;
    while (roots.length && examined < NODE_LIMIT) {
      const root = roots.pop();
      if (!root) continue;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
      let node = root;
      while (node && examined < NODE_LIMIT) {
        examined += 1;
        if (predicate(node)) matches.push(node);
        if (node.shadowRoot) roots.push(node.shadowRoot);
        node = walker.nextNode();
      }
    }
    return {matches, examined, node_limit_reached: examined >= NODE_LIMIT};
  };
  const group = (walked, project) => ({
    items: walked.matches.slice(0, ITEM_LIMIT).map(project), total: walked.matches.length,
    examined: walked.examined,
    truncated: walked.node_limit_reached || walked.matches.length > ITEM_LIMIT,
    evidence_complete: false,
    coverage_scope: "document-and-open-shadow-dom; frames-and-closed-shadow-roots-unobserved"
  });
  const referencedText = (element, attribute) => {
    const ids = cut(element.getAttribute(attribute), 500).split(/\s+/).filter(Boolean);
    return cut(ids.map((id) => document.getElementById(id)?.textContent || "").join(" "));
  };
  const nameSources = (element) => {
    const sources = [];
    if (referencedText(element, "aria-labelledby")) sources.push("aria-labelledby");
    if (cut(element.getAttribute("aria-label"))) sources.push("aria-label");
    if (cut(element.getAttribute("title"))) sources.push("title");
    if (cut(element.textContent)) sources.push("descendant-text");
    if ([...element.querySelectorAll("img[alt],input[type=image][alt]")].some((item) => cut(item.getAttribute("alt")))) sources.push("descendant-image-alt");
    if (element instanceof HTMLInputElement && cut(element.value)) sources.push("input-value");
    return sources;
  };

  const framesWalk = walkElements((element) => element.matches("iframe,frame"));
  const frames = group(framesWalk, (element) => ({target: structuralPath(element), title_present: element.hasAttribute("title"), title: cut(element.getAttribute("title")), exposed: exposed(element)}));
  const fieldSelector = "input:not([type=hidden]):not([type=submit]):not([type=reset]):not([type=button]):not([type=image]),select,textarea,[role=textbox],[role=searchbox],[role=combobox],[role=listbox],[role=slider],[role=spinbutton],[role=checkbox],[role=radio],[role=switch]";
  const fieldsWalk = walkElements((element) => element.matches(fieldSelector) && exposed(element));
  const fields = group(fieldsWalk, (element) => {
    const id = element.getAttribute("id");
    const explicit = id ? document.querySelector(`label[for=${stringify(id)}]`) : null;
    return {target: structuralPath(element), tag: element.localName, role: element.getAttribute("role"),
      explicit_label: Boolean(explicit && cut(explicit.textContent)),
      implicit_label: Boolean(element.closest("label") && cut(element.closest("label").textContent)),
      aria_labelledby: Boolean(referencedText(element, "aria-labelledby")),
      aria_label: Boolean(cut(element.getAttribute("aria-label"))), title: Boolean(cut(element.getAttribute("title")))};
  });
  const linksWalk = walkElements((element) => element.matches("a[href],area[href],[role=link]") && exposed(element));
  const links = group(linksWalk, (element) => ({target: structuralPath(element), name_sources: nameSources(element)}));
  const buttonsWalk = walkElements((element) => element.matches("form button,form input[type=submit],form input[type=reset],form input[type=button],form input[type=image],form [role=button]") && exposed(element));
  const buttons = group(buttonsWalk, (element) => ({target: structuralPath(element), name_sources: nameSources(element)}));

  const parseColor = (value) => {
    const match = String(value).match(/^rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/i);
    return match ? {r: Number(match[1]), g: Number(match[2]), b: Number(match[3]), a: match[4] === undefined ? 1 : Number(match[4])} : null;
  };
  const luminance = (color) => {
    const channels = [color.r, color.g, color.b].map((channel) => { const normalized = channel / 255; return normalized <= 0.04045 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4); });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const ratio = (first, second) => { const values = [luminance(first), luminance(second)].sort((a, b) => b - a); return (values[0] + 0.05) / (values[1] + 0.05); };
  const solidBackground = (element) => {
    let current = element;
    while (current) {
      const style = getComputedStyle(current);
      if (style.backgroundImage && style.backgroundImage !== "none") return null;
      const color = parseColor(style.backgroundColor);
      if (!color || color.a > 0 && color.a < 1) return null;
      if (color.a === 1) return color;
      current = current.parentElement;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const contrastCandidates = [], textRoots = [document.documentElement], seenTextElements = new Set();
  let textExamined = 0, unresolvedContrast = 0;
  while (textRoots.length && textExamined < NODE_LIMIT) {
    const root = textRoots.pop(), walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
    let node = walker.nextNode();
    while (node && textExamined < NODE_LIMIT) {
      textExamined += 1;
      if (node.nodeType === Node.ELEMENT_NODE && node.shadowRoot) textRoots.push(node.shadowRoot);
      if (node.nodeType === Node.TEXT_NODE) {
        const text = cut(node.nodeValue), element = node.parentElement;
        if (text && element && !seenTextElements.has(element) && exposed(element)) {
          seenTextElements.add(element);
          const style = getComputedStyle(element), foreground = parseColor(style.color), background = solidBackground(element);
          if (!foreground || foreground.a !== 1 || !background) unresolvedContrast += 1;
          else {
            const size = Number.parseFloat(style.fontSize) || 0, weight = Number.parseInt(style.fontWeight, 10) || (style.fontWeight === "bold" ? 700 : 400), bold = weight >= 700, large = bold ? size >= 18.5 : size >= 24;
            const testId = bold ? (large ? "3.2.4" : "3.2.2") : (large ? "3.2.3" : "3.2.1");
            contrastCandidates.push({target: structuralPath(element), test_id: testId, ratio: Math.round(ratio(foreground, background) * 100) / 100, required: large ? 3 : 4.5, font_size: size, font_weight: weight, foreground: style.color, background: `rgb(${background.r}, ${background.g}, ${background.b})`});
          }
        }
      }
      node = walker.nextNode();
    }
  }
  const refreshWalk = walkElements((element) => element.matches('meta[http-equiv="refresh" i],object,embed,svg,canvas'));
  const doctype = document.doctype, titleElement = document.querySelector("head > title"), html = document.documentElement;
  return stringify({
    doctype: doctype ? {present: true, name: doctype.name, public_id: doctype.publicId, system_id: doctype.systemId, evidence_complete: true} : {present: false, evidence_complete: true},
    language: {lang: cut(html.getAttribute("lang"), 64), xml_lang: cut(html.getAttribute("xml:lang"), 64), evidence_complete: true},
    title: {present: Boolean(titleElement), value: cut(titleElement?.textContent, 240), evidence_complete: true},
    frames, fields, links, buttons,
    refresh_mechanisms: group(refreshWalk, (element) => ({target: structuralPath(element), kind: element.localName, content: cut(element.getAttribute("content"), 120)})),
    contrast: {items: contrastCandidates.slice(0, ITEM_LIMIT), total: contrastCandidates.length, examined: textExamined, unresolved: unresolvedContrast, truncated: textExamined >= NODE_LIMIT || contrastCandidates.length > ITEM_LIMIT, evidence_complete: false, coverage_scope: "rendered DOM text on opaque solid backgrounds; images, generated content, frames, closed shadow roots and alternate mechanisms unobserved"}
  });
})()
"""

FOCUS_RESET_PROBE = r"""
// __cdpx_rgaa_focus_reset_v2
(() => { const active = document.activeElement; const token = active && active !== document.body && active !== document.documentElement ? {id: active.id || null, tag: active.localName || null} : null; if (active && typeof active.blur === "function") active.blur(); return JSON.stringify(token); })()
"""

FOCUS_STATE_PROBE = r"""
// __cdpx_rgaa_focus_state_v2
(async () => {
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  let element = document.activeElement;
  while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
  if (!element || element === document.body || element === document.documentElement) return null;
  const parts = []; let current = element;
  while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) {
    let part = current.localName || "element"; const parent = current.parentElement;
    if (parent) { const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName); part += `:nth-of-type(${peers.indexOf(current) + 1})`; }
    parts.unshift(part); const root = current.getRootNode();
    if (!parent && root instanceof ShadowRoot) { parts.unshift(">>"); current = root.host; } else current = parent;
  }
  const style = getComputedStyle(element), before = getComputedStyle(element, "::before"), after = getComputedStyle(element, "::after");
  return JSON.stringify({target: parts.join(" > ").replace(/ > >> > /g, " >> "), tag: element.localName, role: element.getAttribute("role"), outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`, box_shadow: String(style.boxShadow).slice(0, 160), border: `${style.borderStyle} ${style.borderWidth} ${style.borderColor}`, background: style.backgroundColor, transform: style.transform, before_content: String(before.content).slice(0, 80), after_content: String(after.content).slice(0, 80), focus_visible_match: (() => { try { return element.matches(":focus-visible"); } catch (_) { return null; } })()});
})()
"""

TEXT_SPACING_PROBE = r"""
// __cdpx_rgaa_text_spacing_v2
(async () => {
  const TOKEN = __CDPX_SPACING_TOKEN__, NODE_LIMIT = 3000, CANDIDATE_LIMIT = 500, FINDING_LIMIT = 200;
  const candidates = [], seen = new Set(), walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let examined = 0;
  while (walker.nextNode() && examined < NODE_LIMIT && candidates.length < CANDIDATE_LIMIT) {
    examined += 1; const element = walker.currentNode.parentElement;
    if (!String(walker.currentNode.nodeValue || "").trim() || !element || seen.has(element)) continue;
    const rect = element.getBoundingClientRect(), computed = getComputedStyle(element);
    if (rect.width <= 0 || rect.height <= 0 || computed.display === "none" || computed.visibility === "hidden") continue;
    seen.add(element); candidates.push(element);
  }
  const target = (element) => { const parts = []; let current = element; while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) { let part = current.localName || "element"; const parent = current.parentElement; if (parent) { const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName); part += `:nth-of-type(${peers.indexOf(current) + 1})`; } parts.unshift(part); current = parent; } return parts.join(" > "); };
  const state = (element) => { const style = getComputedStyle(element); return {horizontal: element.scrollWidth > element.clientWidth + 1, vertical: element.scrollHeight > element.clientHeight + 1, overflow_x: style.overflowX, overflow_y: style.overflowY}; };
  const baseline = new Map(candidates.map((element) => [element, state(element)]));
  const style = document.createElement("style"); style.setAttribute("data-cdpx-rgaa-spacing", TOKEN); style.textContent = "*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}p{margin-bottom:2em!important}"; globalThis.__cdpxRgaaSpacingStyle = style; document.head.appendChild(style);
  try {
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const clipped = [], potential_overflow = [];
    for (const element of candidates) {
      const prior = baseline.get(element), after = state(element), newHorizontal = after.horizontal && !prior.horizontal, newVertical = after.vertical && !prior.vertical;
      if (!newHorizontal && !newVertical) continue;
      const hidden = (newHorizontal && ["hidden", "clip"].includes(after.overflow_x)) || (newVertical && ["hidden", "clip"].includes(after.overflow_y));
      const finding = {target: target(element), horizontal: newHorizontal, vertical: newVertical, overflow_x: after.overflow_x, overflow_y: after.overflow_y};
      (hidden ? clipped : potential_overflow).push(finding); if (clipped.length + potential_overflow.length >= FINDING_LIMIT) break;
    }
    return JSON.stringify({candidates: candidates.length, examined, clipped, clipped_total: clipped.length, potential_overflow, potential_overflow_total: potential_overflow.length, truncated: examined >= NODE_LIMIT || candidates.length >= CANDIDATE_LIMIT || clipped.length + potential_overflow.length >= FINDING_LIMIT, evidence_complete: false, coverage_scope: "bounded light-DOM text nodes; readability, frames, shadow DOM and application side effects require review"});
  } finally { style.remove(); if (globalThis.__cdpxRgaaSpacingStyle === style) delete globalThis.__cdpxRgaaSpacingStyle; }
})()
"""

TEXT_SPACING_CLEANUP = r"""
// __cdpx_rgaa_text_spacing_cleanup_v2
(() => { const style = globalThis.__cdpxRgaaSpacingStyle; if (style && typeof style.remove === "function") style.remove(); delete globalThis.__cdpxRgaaSpacingStyle; return true; })()
"""
