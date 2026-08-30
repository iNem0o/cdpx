# ruff: noqa: E501
"""Fixed, read-only page probes used by the native RGAA engine.

Page content cannot alter these expressions or select new capabilities.  The
passive probe only observes DOM/rendered state.  The spacing probe is separate
because it deliberately and temporarily changes presentation and therefore
requires privileged authority.
"""

PASSIVE_PROBE = r"""
// __cdpx_rgaa_passive
(() => {
  const LIMIT = 200;
  const cut = (value, length = 160) => String(value || "").trim().slice(0, length);
  const cssPath = (element) => {
    if (!element || element.nodeType !== Node.ELEMENT_NODE) return null;
    if (element.id) return `#${CSS.escape(element.id)}`;
    const parts = [];
    let current = element;
    while (current && current !== document.documentElement && parts.length < 6) {
      let part = current.localName || "element";
      const parent = current.parentElement;
      if (parent) {
        const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName);
        if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      current = parent;
    }
    return `html > ${parts.join(" > ")}`;
  };
  const referencedText = (element, attribute) => {
    const ids = cut(element.getAttribute(attribute), 500).split(/\s+/).filter(Boolean);
    return cut(ids.map((id) => document.getElementById(id)?.textContent || "").join(" "));
  };
  const accessibleName = (element) => {
    const labelled = referencedText(element, "aria-labelledby");
    if (labelled) return labelled;
    const aria = cut(element.getAttribute("aria-label"));
    if (aria) return aria;
    if (element instanceof HTMLInputElement && element.type === "image") return cut(element.alt);
    if (element instanceof HTMLInputElement && ["submit", "reset", "button"].includes(element.type)) {
      return cut(element.value);
    }
    if (element instanceof HTMLImageElement) return cut(element.alt);
    const text = cut(element.textContent);
    if (text) return text;
    const descendantImages = [...element.querySelectorAll("img[alt],input[type=image][alt]")]
      .map((image) => cut(image.getAttribute("alt"))).filter(Boolean).join(" ");
    if (descendantImages) return cut(descendantImages);
    return cut(element.getAttribute("title"));
  };
  const visible = (element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const take = (values) => ({items: values.slice(0, LIMIT), total: values.length, truncated: values.length > LIMIT});

  const frames = take([...document.querySelectorAll("iframe,frame")].map((element) => ({
    selector: cssPath(element),
    title_present: element.hasAttribute("title"),
    title: cut(element.getAttribute("title")),
    aria_hidden: element.getAttribute("aria-hidden") === "true"
  })));

  const fieldSelector = "input:not([type=hidden]):not([type=submit]):not([type=reset]):not([type=button]):not([type=image]),select,textarea,[role=textbox],[role=combobox],[role=listbox],[role=slider],[role=spinbutton],[role=checkbox],[role=radio]";
  const fields = take([...document.querySelectorAll(fieldSelector)].map((element) => {
    const id = element.getAttribute("id");
    const explicit = id ? document.querySelector(`label[for=${JSON.stringify(id)}]`) : null;
    const wrapping = element.closest("label");
    const labelledby = referencedText(element, "aria-labelledby");
    const aria = cut(element.getAttribute("aria-label"));
    const title = cut(element.getAttribute("title"));
    const mechanisms = [];
    if (labelledby) mechanisms.push("aria-labelledby");
    if (aria) mechanisms.push("aria-label");
    if (explicit && cut(explicit.textContent)) mechanisms.push("label-for");
    if (wrapping && cut(wrapping.textContent)) mechanisms.push("wrapping-label");
    if (title) mechanisms.push("title");
    return {selector: cssPath(element), labelled: mechanisms.length > 0, mechanisms};
  }));

  const links = take([...document.querySelectorAll("a[href],[role=link]")].map((element) => ({
    selector: cssPath(element), name: accessibleName(element)
  })));
  const buttons = take([...document.querySelectorAll("button,input[type=submit],input[type=reset],input[type=button],input[type=image],[role=button]")].map((element) => ({
    selector: cssPath(element), name: accessibleName(element), visible_name: cut(element.textContent || element.value)
  })));

  const parseColor = (value) => {
    const match = String(value).match(/^rgba?\(\s*([\d.]+)[, ]+\s*([\d.]+)[, ]+\s*([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/i);
    if (!match) return null;
    return {r: Number(match[1]), g: Number(match[2]), b: Number(match[3]), a: match[4] === undefined ? 1 : Number(match[4])};
  };
  const luminance = (color) => {
    const channels = [color.r, color.g, color.b].map((channel) => {
      const normalized = channel / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  };
  const ratio = (first, second) => {
    const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
  };
  const solidBackground = (element) => {
    let current = element;
    while (current) {
      const style = getComputedStyle(current);
      if (style.backgroundImage && style.backgroundImage !== "none") return null;
      const color = parseColor(style.backgroundColor);
      if (!color) return null;
      if (color.a === 1) return color;
      if (color.a > 0) return null;
      current = current.parentElement;
    }
    return {r: 255, g: 255, b: 255, a: 1};
  };
  const contrastCandidates = [];
  let unresolvedContrast = 0;
  const seenTextElements = new Set();
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode() && seenTextElements.size < LIMIT * 2) {
    const text = cut(walker.currentNode.nodeValue);
    const element = walker.currentNode.parentElement;
    if (!text || !element || seenTextElements.has(element) || !visible(element)) continue;
    seenTextElements.add(element);
    const style = getComputedStyle(element);
    const foreground = parseColor(style.color);
    const background = solidBackground(element);
    if (!foreground || foreground.a !== 1 || !background) {
      unresolvedContrast += 1;
      continue;
    }
    const size = Number.parseFloat(style.fontSize) || 0;
    const weight = Number.parseInt(style.fontWeight, 10) || (style.fontWeight === "bold" ? 700 : 400);
    const bold = weight >= 700;
    const large = bold ? size >= 18.5 : size >= 24;
    const testId = bold ? (large ? "3.2.4" : "3.2.2") : (large ? "3.2.3" : "3.2.1");
    const required = large ? 3 : 4.5;
    contrastCandidates.push({
      selector: cssPath(element), test_id: testId, ratio: Math.round(ratio(foreground, background) * 100) / 100,
      required, font_size: size, font_weight: weight,
      foreground: style.color, background: `rgb(${background.r}, ${background.g}, ${background.b})`
    });
  }

  const doctype = document.doctype;
  const titleElement = document.querySelector("head > title");
  const html = document.documentElement;
  return JSON.stringify({
    url: String(location.href),
    doctype: doctype ? {present: true, name: doctype.name, public_id: doctype.publicId, system_id: doctype.systemId} : {present: false},
    language: {lang: cut(html.getAttribute("lang"), 64), xml_lang: cut(html.getAttribute("xml:lang"), 64)},
    title: {present: Boolean(titleElement), value: cut(titleElement?.textContent, 240)},
    frames, fields, links, buttons,
    meta_refresh: take([...document.querySelectorAll('meta[http-equiv="refresh" i]')].map((element) => ({
      selector: cssPath(element), content: cut(element.getAttribute("content"), 120)
    }))),
    contrast: {items: contrastCandidates.slice(0, LIMIT), total: contrastCandidates.length, unresolved: unresolvedContrast, truncated: contrastCandidates.length > LIMIT}
  });
})()
"""


FOCUS_STATE_PROBE = r"""
// __cdpx_rgaa_focus_state
(() => {
  const element = document.activeElement;
  if (!element || element === document.body || element === document.documentElement) return null;
  const path = (() => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const parts = [];
    let current = element;
    while (current && current !== document.documentElement && parts.length < 6) {
      let part = current.localName || "element";
      const parent = current.parentElement;
      if (parent) {
        const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName);
        if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      current = parent;
    }
    return `html > ${parts.join(" > ")}`;
  })();
  const style = getComputedStyle(element);
  const outlineWidth = Number.parseFloat(style.outlineWidth) || 0;
  const outlineVisible = style.outlineStyle !== "none" && outlineWidth > 0 && style.outlineColor !== "transparent";
  const shadowVisible = style.boxShadow && style.boxShadow !== "none";
  return JSON.stringify({
    selector: path,
    tag: element.localName,
    role: element.getAttribute("role"),
    outline_style: style.outlineStyle,
    outline_width: style.outlineWidth,
    outline_color: style.outlineColor,
    box_shadow: String(style.boxShadow).slice(0, 160),
    focus_visible_match: (() => { try { return element.matches(":focus-visible"); } catch (_) { return null; } })(),
    indicator_detected: Boolean(outlineVisible || shadowVisible)
  });
})()
"""


TEXT_SPACING_PROBE = r"""
// __cdpx_rgaa_text_spacing
(async () => {
  const STYLE_ID = "__cdpx-rgaa-spacing";
  document.getElementById(STYLE_ID)?.remove();
  const selector = (element) => {
    if (element.id) return `#${CSS.escape(element.id)}`;
    const parts = [];
    let current = element;
    while (current && current !== document.documentElement && parts.length < 5) {
      let part = current.localName || "element";
      const parent = current.parentElement;
      if (parent) {
        const peers = [...parent.children].filter((candidate) => candidate.localName === current.localName);
        if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      current = parent;
    }
    return `html > ${parts.join(" > ")}`;
  };
  const candidates = [...document.querySelectorAll("body *")].filter((element) => {
    const text = String(element.textContent || "").trim();
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return text && rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }).slice(0, 500);
  const baseline = new Map(candidates.map((element) => [element, {
    horizontal: element.scrollWidth > element.clientWidth + 1,
    vertical: element.scrollHeight > element.clientHeight + 1
  }]));
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = "*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important}p{margin-bottom:2em!important}";
  document.head.appendChild(style);
  try {
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const clipped = [];
    for (const element of candidates) {
      const before = baseline.get(element);
      const horizontal = element.scrollWidth > element.clientWidth + 1;
      const vertical = element.scrollHeight > element.clientHeight + 1;
      if ((horizontal && !before.horizontal) || (vertical && !before.vertical)) {
        clipped.push({selector: selector(element), horizontal, vertical});
        if (clipped.length >= 200) break;
      }
    }
    return JSON.stringify({candidates: candidates.length, clipped, clipped_total: clipped.length});
  } finally {
    style.remove();
  }
})()
"""
