# ruff: noqa: E501
"""Integrity-isolated, bounded RGAA page probes.

Every expression is executed in a fresh isolated world. Partial observation
frontiers are explicit and can never support ``pass`` or ``not_applicable``.
"""

PASSIVE_PROBE = r"""
// __cdpx_rgaa_passive_v2
(() => {
  const ITEM_LIMIT = 200, NODE_LIMIT = 5000, BYTE_LIMIT = 262144;
  const stringify = JSON.stringify.bind(JSON);
  const budget = {nodes_examined: 0, bytes_examined: 0, subtree_truncated: false};
  const encoder = new TextEncoder();
  const takeNode = () => {
    if (budget.nodes_examined >= NODE_LIMIT) { budget.subtree_truncated = true; return false; }
    budget.nodes_examined += 1; return true;
  };
  const cut = (value, length = 160) => {
    const remaining = BYTE_LIMIT - budget.bytes_examined;
    if (remaining <= 0) { budget.subtree_truncated = true; return ""; }
    const raw = String(value || "");
    const charLimit = Math.min(length, Math.floor(remaining / 3));
    const bounded = raw.slice(0, charLimit);
    const bytes = encoder.encode(bounded).byteLength;
    budget.bytes_examined += bytes;
    if (raw.length > charLimit) budget.subtree_truncated = true;
    return bounded.slice(0, length).trim();
  };
  const structuralPath = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) {
      let part = current.localName || "element";
      const parent = current.parentElement;
      if (parent) {
        let index = 1, sibling = current.previousElementSibling;
        while (sibling) {
          if (!takeNode()) break;
          if (sibling.localName === current.localName) index += 1;
          sibling = sibling.previousElementSibling;
        }
        part += `:nth-of-type(${index})`;
      }
      parts.unshift(part);
      const root = current.getRootNode();
      if (!parent && root instanceof ShadowRoot) { parts.unshift(">>"); current = root.host; }
      else current = parent;
    }
    return parts.join(" > ").replace(/ > >> > /g, " >> ");
  };
  const exposed = (element) => {
    let ancestor = element, depth = 0;
    while (ancestor && depth < 32) {
      if (!takeNode()) return false;
      if (ancestor.hidden || ancestor.inert || ancestor.getAttribute("aria-hidden") === "true") return false;
      ancestor = ancestor.parentElement; depth += 1;
    }
    if (ancestor) budget.subtree_truncated = true;
    const style = getComputedStyle(element), rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      style.visibility !== "collapse" && Number(style.opacity) !== 0 &&
      rect.width > 0 && rect.height > 0;
  };
  const elements = [], textNodes = [], roots = [document.documentElement];
  while (roots.length && budget.nodes_examined < NODE_LIMIT) {
      const root = roots.pop();
      if (!root) continue;
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      let node = root.nodeType === Node.ELEMENT_NODE ? root : walker.nextNode();
      while (node && takeNode()) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          elements.push(node);
          if (node.shadowRoot) roots.push(node.shadowRoot);
        } else if (node.nodeType === Node.TEXT_NODE) textNodes.push(node);
        node = walker.nextNode();
      }
  }
  if (roots.length) budget.subtree_truncated = true;
  const walkSubtree = (root, visitor, localLimit = 256) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = walker.nextNode(), local = 0;
    while (node && local < localLimit) {
      if (!takeNode()) return;
      local += 1;
      if (visitor(node) === false) return;
      node = walker.nextNode();
    }
    if (node) budget.subtree_truncated = true;
  };
  const boundedText = (element, length = 160) => {
    const chunks = [];
    let characters = 0;
    walkSubtree(element, (node) => {
      if (node.nodeType !== Node.TEXT_NODE) return true;
      const text = cut(node.nodeValue, length - characters);
      if (text) { chunks.push(text); characters += text.length + 1; }
      return characters < length;
    });
    return chunks.join(" ").slice(0, length).trim();
  };
  const group = (matches, project) => ({
    items: matches.slice(0, ITEM_LIMIT).map(project), total: matches.length,
    examined: budget.nodes_examined,
    bytes_examined: budget.bytes_examined,
    truncated: budget.subtree_truncated || matches.length > ITEM_LIMIT,
    evidence_complete: false,
    coverage_scope: "document-and-open-shadow-dom; frames-and-closed-shadow-roots-unobserved"
  });
  const referencedText = (element, attribute) => {
    const ids = cut(element.getAttribute(attribute), 500).split(/\s+/).filter(Boolean).slice(0, 16);
    return cut(ids.map((id) => { const target = document.getElementById(id); return target ? boundedText(target) : ""; }).join(" "));
  };
  const descendantImageAlt = (element) => {
    let found = false;
    walkSubtree(element, (node) => {
      if (node.nodeType !== Node.ELEMENT_NODE) return true;
      if ((node.matches("img[alt]") || node.matches("input[type=image][alt]")) && cut(node.getAttribute("alt"))) { found = true; return false; }
      return true;
    });
    return found;
  };
  const nameSources = (element) => {
    const sources = [];
    if (referencedText(element, "aria-labelledby")) sources.push("aria-labelledby");
    if (cut(element.getAttribute("aria-label"))) sources.push("aria-label");
    if (cut(element.getAttribute("title"))) sources.push("title");
    if (boundedText(element)) sources.push("descendant-text");
    if (descendantImageAlt(element)) sources.push("descendant-image-alt");
    if (element instanceof HTMLInputElement && cut(element.value)) sources.push("input-value");
    return sources;
  };

  const framesFound = elements.filter((element) => element.matches("iframe,frame"));
  const frames = group(framesFound, (element) => ({target: structuralPath(element), title_present: element.hasAttribute("title"), title: cut(element.getAttribute("title")), exposed: exposed(element)}));
  const fieldSelector = "input:not([type=hidden]):not([type=submit]):not([type=reset]):not([type=button]):not([type=image]),select,textarea,[role=textbox],[role=searchbox],[role=combobox],[role=listbox],[role=slider],[role=spinbutton],[role=checkbox],[role=radio],[role=switch]";
  const labelIndex = new Map();
  for (const label of elements) {
    if (!label.matches("label[for]")) continue;
    const id = cut(label.getAttribute("for"), 256);
    if (id && !labelIndex.has(id)) labelIndex.set(id, label);
  }
  const implicitLabel = (element) => {
    let current = element.parentElement, depth = 0;
    while (current && depth < 32) {
      if (!takeNode()) return null;
      if (current.localName === "label") return current;
      current = current.parentElement; depth += 1;
    }
    if (current) budget.subtree_truncated = true;
    return null;
  };
  const fieldsFound = elements.filter((element) => element.matches(fieldSelector) && exposed(element));
  const fields = group(fieldsFound, (element) => {
    const id = element.getAttribute("id");
    const explicit = id ? labelIndex.get(id) : null, implicit = implicitLabel(element);
    return {target: structuralPath(element), tag: element.localName, role: element.getAttribute("role"),
      explicit_label: Boolean(explicit && boundedText(explicit)),
      implicit_label: Boolean(implicit && boundedText(implicit)),
      aria_labelledby: Boolean(referencedText(element, "aria-labelledby")),
      aria_label: Boolean(cut(element.getAttribute("aria-label"))), title: Boolean(cut(element.getAttribute("title")))};
  });
  const linksFound = elements.filter((element) => element.matches("a[href],area[href],[role=link]") && exposed(element));
  const links = group(linksFound, (element) => ({target: structuralPath(element), name_sources: nameSources(element)}));
  const buttonsFound = elements.filter((element) => element.matches("form button,form input[type=submit],form input[type=reset],form input[type=button],form input[type=image],form [role=button]") && exposed(element));
  const buttons = group(buttonsFound, (element) => ({target: structuralPath(element), name_sources: nameSources(element)}));

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
  const contrastCandidates = [], seenTextElements = new Set();
  let unresolvedContrast = 0;
  for (const node of textNodes) {
      if (!takeNode()) break;
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
  const refreshFound = elements.filter((element) => element.matches('meta[http-equiv="refresh" i]'));
  const doctype = document.doctype, titleElement = document.querySelector("head > title"), html = document.documentElement;
  return stringify({
    doctype: doctype ? {present: true, name: doctype.name, public_id: doctype.publicId, system_id: doctype.systemId, evidence_complete: true} : {present: false, evidence_complete: true},
    language: {lang: cut(html.getAttribute("lang"), 64), xml_lang: cut(html.getAttribute("xml:lang"), 64), evidence_complete: true},
    title: {present: Boolean(titleElement), value: titleElement ? boundedText(titleElement, 240) : "", evidence_complete: true},
    frames, fields, links, buttons,
    refresh_mechanisms: group(refreshFound, (element) => ({target: structuralPath(element), kind: element.localName, content: cut(element.getAttribute("content"), 120)})),
    contrast: {items: contrastCandidates.slice(0, ITEM_LIMIT), total: contrastCandidates.length, examined: budget.nodes_examined, bytes_examined: budget.bytes_examined, unresolved: unresolvedContrast, truncated: budget.subtree_truncated || contrastCandidates.length > ITEM_LIMIT, evidence_complete: false, coverage_scope: "rendered DOM text on opaque solid backgrounds; images, generated content, frames, closed shadow roots and alternate mechanisms unobserved"},
    nodes_examined: budget.nodes_examined,
    bytes_examined: budget.bytes_examined,
    subtree_truncated: budget.subtree_truncated,
    execution_timed_out: false
  });
})()
"""

FOCUS_RESET_PROBE = r"""
// __cdpx_rgaa_focus_reset_v2
(() => {
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  const token = active && active !== document.body && active !== document.documentElement ? {stored: true} : null;
  globalThis.__cdpxRgaaFocusTarget = token ? active : null;
  if (active && typeof active.blur === "function") active.blur();
  return JSON.stringify(token);
})()
"""

FOCUS_RESTORE_PROBE = r"""
// __cdpx_rgaa_focus_restore_v2
(() => {
  const target = globalThis.__cdpxRgaaFocusTarget;
  delete globalThis.__cdpxRgaaFocusTarget;
  if (!target) {
    let active = document.activeElement;
    while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
    if (active && typeof active.blur === "function") active.blur();
    return document.activeElement === document.body || document.activeElement === document.documentElement;
  }
  if (!target.isConnected || typeof target.focus !== "function") return false;
  target.focus({preventScroll: true});
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  return active === target;
})()
"""

FOCUS_STATE_PROBE = r"""
// __cdpx_rgaa_focus_state_v2
(async () => {
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  let element = document.activeElement;
  while (element?.shadowRoot?.activeElement) element = element.shadowRoot.activeElement;
  if (!element || element === document.body || element === document.documentElement) return null;
  const PATH_NODE_LIMIT = 5000, parts = [];
  let current = element, pathNodesExamined = 0, pathTruncated = false;
  while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) {
    let part = current.localName || "element"; const parent = current.parentElement;
    if (parent) {
      let index = 1, sibling = current.previousElementSibling;
      while (sibling && pathNodesExamined < PATH_NODE_LIMIT) {
        pathNodesExamined += 1;
        if (sibling.localName === current.localName) index += 1;
        sibling = sibling.previousElementSibling;
      }
      if (sibling) pathTruncated = true;
      part += `:nth-of-type(${index})`;
    }
    parts.unshift(part); const root = current.getRootNode();
    if (!parent && root instanceof ShadowRoot) { parts.unshift(">>"); current = root.host; } else current = parent;
  }
  const style = getComputedStyle(element), before = getComputedStyle(element, "::before"), after = getComputedStyle(element, "::after");
  return JSON.stringify({target: parts.join(" > ").replace(/ > >> > /g, " >> "), tag: element.localName, role: element.getAttribute("role"), outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`, box_shadow: String(style.boxShadow).slice(0, 160), border: `${style.borderStyle} ${style.borderWidth} ${style.borderColor}`, background: style.backgroundColor, transform: style.transform, before_content: String(before.content).slice(0, 80), after_content: String(after.content).slice(0, 80), focus_visible_match: (() => { try { return element.matches(":focus-visible"); } catch (_) { return null; } })(), path_nodes_examined: pathNodesExamined, path_truncated: pathTruncated});
})()
"""

TEXT_SPACING_PROBE = r"""
// __cdpx_rgaa_text_spacing_v2
(async () => {
  const TOKEN = __CDPX_SPACING_TOKEN__, NODE_LIMIT = 3000, BYTE_LIMIT = 262144, CANDIDATE_LIMIT = 500, FINDING_LIMIT = 200, encoder = new TextEncoder();
  const candidates = [], seen = new Set(), walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let examined = 0, bytesExamined = 0, subtreeTruncated = false;
  while (walker.nextNode() && examined < NODE_LIMIT && candidates.length < CANDIDATE_LIMIT) {
    examined += 1; const element = walker.currentNode.parentElement;
    const remaining = BYTE_LIMIT - bytesExamined;
    if (remaining <= 0) { subtreeTruncated = true; break; }
    const raw = String(walker.currentNode.nodeValue || ""), bounded = raw.slice(0, Math.min(1024, Math.floor(remaining / 3)));
    bytesExamined += encoder.encode(bounded).byteLength;
    if (raw.length > bounded.length) subtreeTruncated = true;
    if (!bounded.trim() || !element || seen.has(element)) continue;
    const rect = element.getBoundingClientRect(), computed = getComputedStyle(element);
    if (rect.width <= 0 || rect.height <= 0 || computed.display === "none" || computed.visibility === "hidden") continue;
    seen.add(element); candidates.push(element);
  }
  const target = (element) => { const parts = []; let current = element; while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 10) { let part = current.localName || "element"; const parent = current.parentElement; if (parent) { let index = 1, sibling = current.previousElementSibling; while (sibling && examined < NODE_LIMIT) { examined += 1; if (sibling.localName === current.localName) index += 1; sibling = sibling.previousElementSibling; } if (sibling) subtreeTruncated = true; part += `:nth-of-type(${index})`; } parts.unshift(part); current = parent; } return parts.join(" > "); };
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
    return JSON.stringify({candidates: candidates.length, examined, nodes_examined: examined, bytes_examined: bytesExamined, subtree_truncated: subtreeTruncated, execution_timed_out: false, clipped, clipped_total: clipped.length, potential_overflow, potential_overflow_total: potential_overflow.length, truncated: subtreeTruncated || examined >= NODE_LIMIT || candidates.length >= CANDIDATE_LIMIT || clipped.length + potential_overflow.length >= FINDING_LIMIT, evidence_complete: false, coverage_scope: "bounded light-DOM text nodes; readability, frames, shadow DOM and application side effects require review"});
  } finally { style.remove(); if (globalThis.__cdpxRgaaSpacingStyle === style) delete globalThis.__cdpxRgaaSpacingStyle; }
})()
"""

TEXT_SPACING_CLEANUP = r"""
// __cdpx_rgaa_text_spacing_cleanup_v2
(() => { const style = globalThis.__cdpxRgaaSpacingStyle; if (style && typeof style.remove === "function") style.remove(); delete globalThis.__cdpxRgaaSpacingStyle; return true; })()
"""
