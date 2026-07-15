(function () {
  const data = JSON.parse(document.getElementById('report-data').textContent);
  const app = document.getElementById('app');
  const featureNav = document.getElementById('featureNav');
  const featureSearch = document.getElementById('featureSearch');
  const featureSide = document.getElementById('featureSide');
  const docsNav = document.getElementById('docsNav');
  const docsSearch = document.getElementById('docsSearch');
  const docsSide = document.getElementById('docsSide');
  const topLinks = Array.from(document.querySelectorAll('[data-route]'));

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
  const routeInfo = () => {
    const raw = location.hash.slice(1) || '/features';
    const [path, query = ''] = raw.split('?', 2);
    return {path, params: new URLSearchParams(query)};
  };
  const route = () => routeInfo().path;
  const features = () => data.feature_inventory.features || [];
  const documents = () => data.documentation?.documents || [];
  const findDocument = (path) => documents().find((document) => document.path === path);
  const docHref = (path, section = '') => {
    const base = '#/docs/view/' + String(path).split('/').map(encodeURIComponent).join('/');
    return section ? base + '?section=' + encodeURIComponent(section) : base;
  };
  const featureStatus = (feature) => {
    if ((feature.matched_scenarios || []).some((s) => ['failed', 'error'].includes(s.status))) {
      return 'failed';
    }
    if ((feature.matched_scenarios || []).some((s) => s.status === 'unavailable')) {
      return 'warning';
    }
    return (feature.gaps || []).length ? 'warning' : 'ok';
  };
  const counts = (items) => {
    const out = {passed: 0, failed: 0, skipped: 0, unavailable: 0};
    for (const item of items || []) {
      if (['failed', 'error'].includes(item.status)) out.failed += 1;
      else if (item.status === 'unavailable') out.unavailable += 1;
      else if (item.status === 'skipped') out.skipped += 1;
      else out.passed += 1;
    }
    return out;
  };
  const hrefFor = (path) => {
    if (!path) return '';
    return String(path).startsWith('.proof/') ? String(path).slice(7) : String(path);
  };
  const findFeature = (id) => features().find((feature) => feature.id === id);
  const findJourney = (feature, id) => (feature?.journeys || []).find((journey) => journey.id === id);
  const findScenario = (feature, id) => {
    for (const journey of feature?.journeys || []) {
      const scenario = (journey.scenarios || []).find((item) => item.id === id);
      if (scenario) return {journey, scenario};
    }
    return {};
  };
  const list = (items, formatter) => {
    if (!items || !items.length) return '<div class="empty">Aucune donnée.</div>';
    return '<ul class="list">' + items.map(formatter).join('') + '</ul>';
  };
  const crumbs = (items) => '<div class="crumbs">' + items.map((item, index) => {
    const sep = index ? '<span>/</span>' : '';
    return sep + (item.href ? `<a href="${item.href}">${esc(item.label)}</a>` : `<span>${esc(item.label)}</span>`);
  }).join('') + '</div>';
  const statusPill = (status) => `<span class="pill ${esc(status)}">${esc(status)}</span>`;

  /* === Visualiseurs d'artefacts ===
     Chaque type de la taxonomie (cdpx.testing.evidence.ARTIFACT_TYPES) doit
     avoir une entrée VIEWERS: le test "calculé => rendu" échoue sinon.
     Contrat payload: inline_content / excerpt / truncated / inline_skipped
     sont optionnels — tout viewer dégrade en lien de téléchargement. */

  /* feature_inventory porte des copies d'artefacts jamais inlinées (l'inliner
     Python ne visite que scenario_evidence.suites, la source unique — inliner
     chaque copie multiplierait le poids du rapport). Le modal résout donc le
     contenu embarqué par path avant de choisir le visualiseur. */
  const inlineByPath = (() => {
    const index = {};
    const suites = (data.scenario_evidence || {}).suites || {};
    for (const scenarios of Object.values(suites)) {
      for (const scenario of scenarios || []) {
        for (const artifact of scenario.artifacts || []) {
          if (artifact.path && (artifact.inline_content || artifact.excerpt || artifact.inline_skipped)) {
            index[artifact.path] = artifact;
          }
        }
      }
    }
    return index;
  })();

  function resolveInline(artifact) {
    if (!artifact || artifact.inline_content || artifact.excerpt || artifact.inline_skipped) return artifact;
    const source = inlineByPath[artifact.path];
    if (!source) return artifact;
    return {
      ...artifact,
      inline_content: source.inline_content,
      excerpt: source.excerpt,
      truncated: source.truncated,
      inline_skipped: source.inline_skipped,
      meta: artifact.meta || source.meta
    };
  }

  const VIEWER_ICONS = {
    'screenshot': '🖼', 'video': '🎬', 'console': '≡', 'network': '⇄',
    'json': '{}', 'profiler': '⏱', 'logs': '¶', 'log-excerpt': '¶', 'command': '$',
    'asciinema': '⏵', 'file': '⇩'
  };

  const fileLink = (artifact, label = 'ouvrir le fichier') =>
    artifact.path ? `<a href="${esc(hrefFor(artifact.path))}">${esc(label)}</a>` : '';

  function downloadFallback(artifact) {
    const reason = artifact.inline_skipped
      ? `Contenu non embarqué (${esc(artifact.inline_skipped)}).`
      : 'Contenu non embarqué dans le rapport.';
    return `<div class="viewer-fallback"><p>${reason}</p><p>${fileLink(artifact)}</p></div>`;
  }

  function truncationNote(artifact) {
    if (!artifact.truncated) return '';
    return `<p class="viewer-note">Extrait tronqué — ${fileLink(artifact, 'fichier complet')}</p>`;
  }

  function screenshotViewer(artifact) {
    const href = esc(hrefFor(artifact.path));
    return `<figure class="viewer-media"><img src="${href}" alt="${esc(artifact.label || 'screenshot')}" data-zoomable></figure>`;
  }

  function videoViewer(artifact) {
    const href = esc(hrefFor(artifact.path));
    return `<figure class="viewer-media"><video controls preload="metadata" src="${href}"></video></figure>`;
  }

  function basicTextViewer(artifact) {
    const body = artifact.inline_content || artifact.excerpt;
    if (!body) return downloadFallback(artifact);
    return `${truncationNote(artifact)}<pre class="viewer-text">${esc(body)}</pre>`;
  }

  function parseInline(artifact) {
    if (!artifact.inline_content) return null;
    try { return JSON.parse(artifact.inline_content); } catch (error) { return null; }
  }

  function consoleViewer(artifact) {
    const payload = parseInline(artifact);
    if (!payload || !Array.isArray(payload.entries)) return basicTextViewer(artifact);
    if (!payload.entries.length) {
      return `<div class="viewer-fallback"><p>Console vide — aucun message émis pendant la capture.</p><p>${fileLink(artifact)}</p></div>`;
    }
    const levelOf = (entry) => {
      if (entry.kind === 'exception' || entry.type === 'error' || entry.type === 'assert') return 'error';
      if (entry.type === 'warning' || entry.type === 'warn') return 'warn';
      return 'log';
    };
    const byLevel = {error: 0, warn: 0, log: 0};
    payload.entries.forEach((entry) => { byLevel[levelOf(entry)] += 1; });
    const filters = ['error', 'warn', 'log'].map((level) =>
      `<label class="console-filter level-${level}"><input type="checkbox" data-console-level="${level}" checked> ${level} (${byLevel[level]})</label>`
    ).join('');
    const rows = payload.entries.map((entry) => {
      const level = levelOf(entry);
      return `<div class="console-line" data-level="${level}"><span class="console-level level-${level}">${esc(level)}</span><span class="console-text">${esc(entry.text || '')}</span></div>`;
    }).join('');
    return `<div class="console-toolbar">${filters}</div><div class="console-view">${rows}</div>`;
  }

  function networkViewer(artifact) {
    const payload = parseInline(artifact);
    if (!payload || !Array.isArray(payload.requests)) return basicTextViewer(artifact);
    const summary = payload.summary || {};
    const statusClass = (status) => {
      if (!status) return 'muted';
      if (status >= 400) return 'bad';
      if (status >= 300) return 'warn';
      return 'ok';
    };
    const rows = payload.requests.map((request) => {
      const status = Number(request.status) || 0;
      const shownStatus = request.failed ? `échec: ${request.failed}` : (request.status ?? '—');
      return `<tr>
        <td><code>${esc(request.method || '')}</code></td>
        <td class="net-url" title="${esc(request.url || '')}"><code>${esc(request.url || '')}</code></td>
        <td><span class="net-status net-${request.failed ? 'bad' : statusClass(status)}">${esc(shownStatus)}</span></td>
        <td>${esc(request.resourceType || '')}</td>
        <td>${esc(request.encodedBytes ?? '')}</td>
      </tr>`;
    }).join('');
    const head = `<p class="viewer-summary">${esc(summary.total ?? payload.requests.length)} requêtes · ${esc(summary.errors_4xx_5xx || 0)} erreurs 4xx/5xx · ${esc(summary.failed || 0)} échecs réseau · ${esc(summary.bytes || 0)} octets</p>`;
    return `${head}<div class="table-wrap"><table><thead><tr><th>Méthode</th><th>URL</th><th>Statut</th><th>Type</th><th>Octets</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  const JSON_NODE_BUDGET = 2000;

  function jsonTree(value, depth, state) {
    if (state.nodes >= JSON_NODE_BUDGET) { state.truncated = true; return ''; }
    state.nodes += 1;
    if (value === null || typeof value !== 'object') {
      const kind = value === null ? 'null' : typeof value;
      return `<span class="json-value json-${kind}">${esc(JSON.stringify(value))}</span>`;
    }
    const isArray = Array.isArray(value);
    const entries = isArray ? value.map((item, index) => [index, item]) : Object.entries(value);
    if (!entries.length) return `<span class="json-value">${isArray ? '[]' : '{}'}</span>`;
    const body = entries.map(([key, item]) => {
      if (state.nodes >= JSON_NODE_BUDGET) { state.truncated = true; return ''; }
      return `<div class="json-entry"><span class="json-key">${esc(key)}</span>: ${jsonTree(item, depth + 1, state)}</div>`;
    }).join('');
    const label = isArray ? `[${entries.length}]` : `{${entries.length}}`;
    return `<details class="json-node"${depth < 2 ? ' open' : ''}><summary>${label}</summary>${body}</details>`;
  }

  function jsonViewer(artifact) {
    const payload = parseInline(artifact);
    if (payload === null) return basicTextViewer(artifact);
    const state = {nodes: 0, truncated: false};
    const tree = jsonTree(payload, 0, state);
    const note = state.truncated
      ? `<p class="viewer-note">Affichage tronqué (${JSON_NODE_BUDGET} nœuds) — ${fileLink(artifact, 'fichier complet')}</p>`
      : '';
    return `${note}<div class="json-view">${tree}</div>`;
  }

  function profilerViewer(artifact) {
    const payload = parseInline(artifact);
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return jsonViewer(artifact);
    const scalars = Object.entries(payload)
      .filter(([, value]) => value === null || typeof value !== 'object')
      .slice(0, 8)
      .map(([key, value]) => `<span class="chip"><span class="chip-icon">${esc(key)}</span>${esc(String(value))}</span>`)
      .join('');
    return `${scalars ? `<div class="viewer-summary">${scalars}</div>` : ''}${jsonViewer(artifact)}`;
  }

  function matchesPattern(line, pattern) {
    try { return new RegExp(pattern).test(line); } catch (error) { return false; }
  }

  function logViewer(artifact) {
    const body = artifact.inline_content || artifact.excerpt;
    if (!body) return downloadFallback(artifact);
    const meta = artifact.meta || {};
    const banner = [];
    if (meta.source) banner.push(`source <code>${esc(meta.source)}</code>`);
    if (meta.pattern) banner.push(`motif <code>${esc(meta.pattern)}</code>`);
    if (Array.isArray(meta.matched_lines) && meta.matched_lines.length) {
      banner.push(`${meta.matched_lines.length} correspondance(s)`);
    }
    const head = banner.length ? `<p class="viewer-summary">${banner.join(' · ')}</p>` : '';
    const isExcerpt = artifact.type === 'log-excerpt';
    const lines = body.split('\n').map((line, index) => {
      const hit = isExcerpt && meta.pattern && matchesPattern(line, meta.pattern);
      const number = isExcerpt ? '' : `<span class="log-num">${index + 1}</span>`;
      return `<div class="log-line${hit ? ' log-hit' : ''}">${number}<span class="log-text">${esc(line)}</span></div>`;
    }).join('');
    return `${head}${truncationNote(artifact)}<div class="log-view">${lines}</div>`;
  }

  function transcriptSection(transcript, name) {
    const marker = `--- ${name} ---`;
    const start = transcript.indexOf(marker);
    if (start === -1) return null;
    const afterStart = start + marker.length;
    const nextMarker = transcript.indexOf('\n--- ', afterStart);
    const raw = nextMarker === -1 ? transcript.slice(afterStart) : transcript.slice(afterStart, nextMarker);
    return raw.replace(/^\n/, '').replace(/\n$/, '');
  }

  function commandViewer(artifact) {
    const meta = artifact.meta || {};
    const body = artifact.inline_content || '';
    if (!body && !artifact.excerpt) return downloadFallback(artifact);
    const exitPill = meta.exit_code === undefined
      ? ''
      : `<span class="pill ${Number(meta.exit_code) === 0 ? 'ok' : 'failed'}">exit ${esc(meta.exit_code)}</span>`;
    const argv = Array.isArray(meta.argv) ? meta.argv.join(' ') : '';
    const duration = meta.duration_s === undefined ? '' : `<span class="muted">${esc(meta.duration_s)}s</span>`;
    const head = `<div class="command-head">${exitPill}${argv ? `<code>$ ${esc(argv)}</code>` : ''}${duration}</div>`;
    if (!body) return `${head}${truncationNote(artifact)}<pre class="viewer-text">${esc(artifact.excerpt)}</pre>`;
    const stdout = transcriptSection(body, 'stdout');
    const stderr = transcriptSection(body, 'stderr');
    if (stdout === null && stderr === null) return `${head}<pre class="viewer-text">${esc(body)}</pre>`;
    const streams = [
      stdout === null ? '' : `<section class="stream stream-out"><h3>stdout</h3><pre class="viewer-text">${esc(stdout || '(vide)')}</pre></section>`,
      stderr === null ? '' : `<section class="stream stream-err"><h3>stderr</h3><pre class="viewer-text">${esc(stderr || '(vide)')}</pre></section>`
    ].join('');
    return `${head}${streams}`;
  }

  /* === Player asciinema (.cast v2) sur xterm.js ===
     Émulation terminal complète via le bundle xterm.js vendoré (MIT —
     asciinema-player officiel est GPL-3.0, incompatible avec le paquet).
     La toolbar maison (lecture, scrub, vitesses) écrit dans xterm; le
     rembobinage = reset + rejeu (xterm n'a pas d'état réversible). */

  const ANSI_RE = /\x1b\[[0-9;?]*[ -\/]*[@-~]/g;

  function parseCast(text) {
    const lines = String(text || '').split('\n').filter((line) => line.trim());
    if (!lines.length) return null;
    let header;
    try { header = JSON.parse(lines[0]); } catch (error) { return null; }
    if (!header || header.version !== 2) return null;
    const events = [];
    for (const line of lines.slice(1)) {
      try {
        const event = JSON.parse(line);
        if (Array.isArray(event) && event[1] === 'o') {
          events.push({t: Number(event[0]) || 0, data: String(event[2] ?? '')});
        }
      } catch (error) { /* événement corrompu ignoré */ }
    }
    return {header, events};
  }

  function castViewer(artifact) {
    const parsed = parseCast(artifact.inline_content);
    if (!parsed || typeof globalThis.Terminal !== 'function') return basicTextViewer(artifact);
    const rawText = parsed.events.map((event) => event.data).join('').replace(ANSI_RE, '').replace(/\r/g, '');
    const duration = parsed.events.length ? parsed.events[parsed.events.length - 1].t : 0;
    return `<div class="cast" data-cast>
      <div class="cast-toolbar">
        <button type="button" data-cast-play>▶ lecture</button>
        <button type="button" data-cast-speed>×1</button>
        <button type="button" data-cast-end>⏭ fin</button>
        <input type="range" data-cast-scrub min="0" max="${Math.max(Math.ceil(duration * 1000), 1)}" value="0">
        <span class="muted" data-cast-time></span>
        <label class="muted cast-rawtoggle"><input type="checkbox" data-cast-rawtoggle> vue brute</label>
      </div>
      <div class="cast-screen" data-cast-screen></div>
      <pre class="viewer-text" data-cast-raw hidden>${esc(rawText)}</pre>
    </div>`;
  }

  let castPlayer = null;

  function stopCastPlayer() {
    if (!castPlayer) return;
    castPlayer.playing = false;
    if (castPlayer.raf) cancelAnimationFrame(castPlayer.raf);
    if (castPlayer.terminal) castPlayer.terminal.dispose();
    castPlayer = null;
  }

  function initCastPlayer(container, artifact) {
    stopCastPlayer();
    const root = container.querySelector('[data-cast]');
    const parsed = parseCast(artifact.inline_content);
    if (!root || !parsed || typeof globalThis.Terminal !== 'function') return;
    const screen = root.querySelector('[data-cast-screen]');
    const raw = root.querySelector('[data-cast-raw]');
    const scrub = root.querySelector('[data-cast-scrub]');
    const timeLabel = root.querySelector('[data-cast-time]');
    const playButton = root.querySelector('[data-cast-play]');
    const speedButton = root.querySelector('[data-cast-speed]');
    const duration = parsed.events.length ? parsed.events[parsed.events.length - 1].t : 0;
    const terminal = new globalThis.Terminal({
      cols: Number(parsed.header.width) || 100,
      rows: Number(parsed.header.height) || 30,
      disableStdin: true,
      convertEol: false,
      scrollback: 5000,
      fontSize: 13
    });
    terminal.open(screen);
    const player = {events: parsed.events, duration, clock: 0, playing: false, speed: 1, raf: 0, last: 0, written: 0, terminal};

    const renderAt = (clock) => {
      player.clock = Math.min(Math.max(clock, 0), duration);
      let target = 0;
      while (target < player.events.length && player.events[target].t <= player.clock) target += 1;
      // Avance: on n'écrit que le delta. Recul: reset + rejeu depuis zéro.
      let start = player.written;
      if (target < player.written) { terminal.reset(); start = 0; }
      for (let index = start; index < target; index += 1) terminal.write(player.events[index].data);
      player.written = target;
      scrub.value = String(Math.round(player.clock * 1000));
      timeLabel.textContent = `${player.clock.toFixed(1)}s / ${duration.toFixed(1)}s`;
      playButton.textContent = player.playing ? '⏸ pause' : '▶ lecture';
    };
    const tick = (now) => {
      if (!player.playing || castPlayer !== player) return;
      player.clock += ((now - player.last) / 1000) * player.speed;
      player.last = now;
      if (player.clock >= duration) { player.clock = duration; player.playing = false; }
      renderAt(player.clock);
      if (player.playing) player.raf = requestAnimationFrame(tick);
    };
    player.toggle = () => {
      if (player.playing) { player.playing = false; renderAt(player.clock); return; }
      if (player.clock >= duration) player.clock = 0;
      player.playing = true;
      player.last = performance.now();
      player.raf = requestAnimationFrame(tick);
    };

    playButton.addEventListener('click', player.toggle);
    speedButton.addEventListener('click', () => {
      player.speed = player.speed >= 4 ? 1 : player.speed * 2;
      speedButton.textContent = `×${player.speed}`;
    });
    root.querySelector('[data-cast-end]').addEventListener('click', () => {
      player.playing = false;
      renderAt(duration);
    });
    scrub.addEventListener('input', () => {
      player.playing = false;
      renderAt(Number(scrub.value) / 1000);
    });
    root.querySelector('[data-cast-rawtoggle]').addEventListener('change', (event) => {
      const showRaw = event.target.checked;
      raw.hidden = !showRaw;
      screen.hidden = showRaw;
      root.querySelector('.cast-toolbar').classList.toggle('cast-raw-mode', showRaw);
    });

    renderAt(duration);
    castPlayer = player;
  }

  const VIEWERS = {
    'screenshot': screenshotViewer,
    'video': videoViewer,
    'console': consoleViewer,
    'network': networkViewer,
    'json': jsonViewer,
    'profiler': profilerViewer,
    'logs': logViewer,
    'log-excerpt': logViewer,
    'command': commandViewer,
    'asciinema': castViewer,
    'file': downloadFallback
  };

  /* === Modal === */

  const modal = document.getElementById('artifact-modal');
  const modalState = {items: [], index: -1, context: null, lastFocus: null};
  let artifactGroups = [];

  function relativeCapture(artifact, run) {
    if (!artifact.created_at || !run || !run.started_at) return artifact.created_at || '';
    const offset = (new Date(artifact.created_at) - new Date(run.started_at)) / 1000;
    if (!isFinite(offset)) return artifact.created_at;
    const clamped = Math.max(offset, 0);
    return `${artifact.created_at.slice(11, 19)} (+${clamped.toFixed(1)}s)`;
  }

  function modalContext(artifact, context) {
    const rows = [];
    const scenario = context?.scenario;
    const run = context?.run;
    if (scenario) {
      rows.push(['Scénario', esc(scenario.title || scenario.id || '')]);
      const wording = scenario.report_text || scenario.ui_text;
      if (wording) rows.push(['', `<em>« ${esc(wording)} »</em>`]);
    }
    const step = artifact.step || (artifact.meta && artifact.meta.step) || '';
    if (step) rows.push(['Étape', `<code>${esc(step)}</code>`]);
    if (run && run.nodeid) rows.push(['Test', `<code>${esc(run.nodeid)}</code>`]);
    if (run && run.intent) rows.push(['Intention', esc(run.intent)]);
    if (artifact.created_at) rows.push(['Capturé', esc(relativeCapture(artifact, run))]);
    if (artifact.bytes) rows.push(['Taille', esc(`${artifact.bytes} octets`)]);
    rows.push(['', fileLink(artifact)]);
    return rows
      .filter(([, value]) => value)
      .map(([key, value]) => `<div class="ctx-row">${key ? `<span class="ctx-key">${esc(key)}</span>` : ''}<span class="ctx-value">${value}</span></div>`)
      .join('');
  }

  function renderModalCurrent() {
    const artifact = resolveInline(modalState.items[modalState.index]);
    if (!artifact) return;
    const viewer = VIEWERS[artifact.type] || downloadFallback;
    modal.querySelector('.modal-type').textContent = artifact.type || 'artefact';
    modal.querySelector('.modal-type').className = 'modal-type pill';
    modal.querySelector('.modal-title').textContent = artifact.label || artifact.type || 'artefact';
    modal.querySelector('.modal-counter').textContent =
      modalState.items.length > 1 ? `${modalState.index + 1}/${modalState.items.length}` : '';
    modal.querySelector('.modal-content').innerHTML = viewer(artifact);
    modal.querySelector('.modal-context-body').innerHTML = modalContext(artifact, modalState.context);
    if (artifact.type === 'asciinema') initCastPlayer(modal.querySelector('.modal-content'), artifact);
    else stopCastPlayer();
  }

  function openModal(items, index, context) {
    modalState.items = items;
    modalState.index = index;
    modalState.context = context || null;
    modalState.lastFocus = document.activeElement;
    renderModalCurrent();
    modal.hidden = false;
    document.body.classList.add('modal-open');
    modal.querySelector('.modal-close').focus();
  }

  function closeModal() {
    stopCastPlayer();
    modal.hidden = true;
    document.body.classList.remove('modal-open');
    modal.querySelector('.modal-content').innerHTML = '';
    if (modalState.lastFocus && modalState.lastFocus.focus) modalState.lastFocus.focus();
    modalState.items = [];
    modalState.index = -1;
  }

  function stepModal(delta) {
    if (!modalState.items.length) return;
    const next = modalState.index + delta;
    if (next < 0 || next >= modalState.items.length) return;
    modalState.index = next;
    renderModalCurrent();
  }

  document.addEventListener('keydown', (event) => {
    if (modal.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeModal(); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); stepModal(1); }
    else if (event.key === 'ArrowLeft') { event.preventDefault(); stepModal(-1); }
    else if (event.key === 'z') {
      const zoomable = modal.querySelector('[data-zoomable]');
      if (zoomable) { event.preventDefault(); zoomable.classList.toggle('zoomed'); }
    } else if (event.key === ' ' && castPlayer && event.target.tagName !== 'INPUT' && event.target.tagName !== 'BUTTON') {
      event.preventDefault();
      castPlayer.toggle();
    } else if (event.key === 'Tab') {
      const focusables = Array.from(modal.querySelectorAll('button, a[href], video'));
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  modal.addEventListener('change', (event) => {
    const box = event.target.closest('[data-console-level]');
    if (!box) return;
    modal.querySelectorAll(`.console-line[data-level="${box.dataset.consoleLevel}"]`)
      .forEach((line) => { line.hidden = !box.checked; });
  });

  modal.addEventListener('click', (event) => {
    if (event.target.closest('[data-modal-close]') || event.target === modal.querySelector('.modal-overlay')) {
      closeModal();
    }
    const zoomable = event.target.closest('[data-zoomable]');
    if (zoomable) zoomable.classList.toggle('zoomed');
  });

  app.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-modal-group]');
    if (!chip) return;
    const group = artifactGroups[Number(chip.dataset.modalGroup)];
    if (!group) return;
    event.preventDefault();
    openModal(group.artifacts, Number(chip.dataset.modalIndex), group.ctx);
  });

  if (window.mermaid) {
    window.mermaid.parseError = () => {};
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      htmlLabels: false,
      flowchart: {htmlLabels: false},
      theme: 'default'
    });
  }

  async function renderMermaid() {
    if (!window.mermaid) return;
    const nodes = Array.from(app.querySelectorAll('pre.mermaid'));
    for (const node of nodes) {
      const source = node.textContent;
      await window.mermaid.run({nodes: [node], suppressErrors: true});
      if (!node.querySelector('svg')) {
        node.textContent = source;
        node.removeAttribute('data-processed');
        const error = document.createElement('p');
        error.className = 'mermaid-error';
        error.textContent = 'Diagramme Mermaid invalide — source conservée.';
        node.insertAdjacentElement('afterend', error);
      }
    }
  }

  function renderFeatureNav() {
    const q = (featureSearch.value || '').trim().toLowerCase();
    featureNav.innerHTML = features()
      .filter((feature) => !q || [feature.id, feature.title, feature.summary].join(' ').toLowerCase().includes(q))
      .map((feature) => {
        const status = featureStatus(feature);
        return `<a href="#/features/${esc(feature.id)}" data-feature-id="${esc(feature.id)}">
          ${esc(feature.title)}<small>${esc(status)} · ${(feature.journeys || []).length} journeys</small>
        </a>`;
      }).join('');
  }

  function renderDocTree(node, query, root = false) {
    const ownDocuments = (node.documents || []).map(findDocument).filter(Boolean)
      .filter((document) => !query || [document.title, document.path, document.summary].join(' ').toLowerCase().includes(query));
    const children = (node.children || []).map((child) => renderDocTree(child, query)).filter(Boolean);
    if (!ownDocuments.length && !children.length) return '';
    const links = ownDocuments.map((document) => `<a href="${docHref(document.path)}" data-doc-path="${esc(document.path)}">${esc(document.title)}<small>${esc(document.path)}</small></a>`).join('');
    const body = links + children.join('');
    if (root) return body;
    return `<details class="doc-tree" open><summary>${esc(node.label || node.name)}</summary>${body}</details>`;
  }

  function renderDocsNav() {
    const q = (docsSearch.value || '').trim().toLowerCase();
    docsNav.innerHTML = renderDocTree(data.documentation?.tree || {}, q, true);
  }

  function setActiveNav() {
    const current = route();
    topLinks.forEach((link) => {
      link.classList.toggle('active', current === link.dataset.route || current.startsWith(link.dataset.route + '/'));
    });
    Array.from(featureNav.querySelectorAll('a')).forEach((link) => {
      link.classList.toggle('active', current.includes('/features/' + link.dataset.featureId));
    });
    Array.from(docsNav.querySelectorAll('a')).forEach((link) => {
      link.classList.toggle('active', current.startsWith('/docs/view/') && decodeURIComponent(current.slice(11)) === link.dataset.docPath);
    });
    const inDocs = current === '/docs' || current.startsWith('/docs/');
    featureSide.hidden = inDocs;
    docsSide.hidden = !inDocs;
  }

  function renderMetrics() {
    const totals = data.totals || {};
    const featureTotals = data.feature_inventory.totals || {};
    const scenarioTotals = data.scenario_totals || {};
    const okFeatures = features().filter((feature) => featureStatus(feature) === 'ok').length;
    return `<div class="metrics">
      <div class="metric"><strong>${data.ok ? 'OK' : 'ECHEC'}</strong><span>Verdict global</span></div>
      <div class="metric"><strong>${esc(totals.passed || 0)}/${esc(totals.tests || 0)}</strong><span>Tests passés</span></div>
      <div class="metric"><strong>${okFeatures}/${features().length}</strong><span>Features sans gap</span></div>
      <div class="metric"><strong>${esc(featureTotals.documented_scenarios || 0)}</strong><span>Scénarios documentés</span></div>
      <div class="metric"><strong>${esc(featureTotals.scenarios || 0)}</strong><span>Tests scénarisés</span></div>
      <div class="metric"><strong>${esc(scenarioTotals.screenshots || 0)}</strong><span>Screenshots</span></div>
      <div class="metric"><strong>${esc((featureTotals.violations || 0) + (featureTotals.warnings || 0))}</strong><span>Gaps catalogue</span></div>
      <div class="metric"><strong>${esc(featureTotals.mapped_entrypoints || 0)}/${esc(featureTotals.entrypoints || 0)}</strong><span>Entrypoints rattachés</span></div>
      <div class="metric${(totals.unavailable || 0) ? ' warning' : ''}"><strong>${esc(totals.unavailable || 0)}</strong><span>Preuves indisponibles</span></div>
    </div>`;
  }

  /* === Ordre de lecture guidé: verdict -> échecs -> features -> run === */

  const scenarioArtifacts = (runs) => (runs || []).flatMap((run) => run.artifacts || []);

  function failedRuns() {
    const out = [];
    const suites = (data.scenario_evidence || {}).suites || {};
    for (const scenarios of Object.values(suites)) {
      for (const run of scenarios) {
        if (['failed', 'error'].includes(run.status)) out.push(run);
      }
    }
    return out;
  }

  function renderReadFirst() {
    const failures = data.proof_failures || [];
    const failed = failedRuns();
    if (data.ok && !failures.length && !failed.length) return '';
    const failureItems = failures.map((item) => `<li>${esc(item)}</li>`).join('');
    const runItems = failed.map((run) => {
      const href = run.feature && run.scenario_id
        ? `#/features/${esc(run.feature)}/scenarios/${esc(run.scenario_id)}`
        : '#/gaps';
      return `<li>${statusPill(run.status)} <a href="${href}"><code>${esc(run.nodeid)}</code></a> ${esc(run.message || '')}</li>`;
    }).join('');
    return `<section class="panel read-first"><h2>À lire d'abord</h2>
      <ul class="list">${failureItems}${runItems}</ul></section>`;
  }

  function renderReadingPath() {
    return `<div class="reading-path">Parcours de lecture — <strong>1.</strong> Verdict
      · <strong>2.</strong> <a href="#/gaps">Échecs &amp; gaps</a>
      · <strong>3.</strong> Features ci-dessous
      · <strong>4.</strong> <a href="#/run">Preuves du run</a></div>`;
  }

  function decorateTopbar() {
    const inv = data.feature_inventory || {};
    const gapCount = (inv.violations || []).length + (inv.warnings || []).length
      + (data.proof_failures || []).length;
    const gapsLink = document.querySelector('[data-route="/gaps"]');
    if (gapsLink && gapCount) {
      gapsLink.innerHTML = `Gaps <sup class="${(data.proof_failures || []).length ? 'sup-bad' : 'sup'}">${gapCount}</sup>`;
    }
    const failed = (data.totals || {}).failed || 0;
    const runLink = document.querySelector('[data-route="/run"]');
    if (runLink && failed) runLink.innerHTML = `Run <sup class="sup-bad">${failed}</sup>`;
  }

  function renderFeatures() {
    const cards = features().map((feature) => {
      const status = featureStatus(feature);
      return `<article class="card">
        <div class="meta">${statusPill(status)} <code>${esc(feature.id)}</code></div>
        <h2><a href="#/features/${esc(feature.id)}">${esc(feature.title)}</a></h2>
        <p>${esc(feature.summary)}</p>
        <div class="meta">
          <span>${(feature.journeys || []).length} journeys</span>
          <span>${(feature.scenarios || []).length} scénarios docs</span>
          <span>${(feature.matched_tests || []).length} tests</span>
          <span>${(feature.proofs || []).length} preuves</span>
        </div>
        <div class="badges">${typeBadges(scenarioArtifacts(feature.matched_scenarios))}</div>
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features'}])}
      <h1>Features</h1>
      <p>Navigation produit par feature, journey et scénario. Les textes affichés viennent des docs feature.</p>
      ${renderReadFirst()}${renderMetrics()}${renderReadingPath()}<div class="grid">${cards}</div>`;
  }

  function renderDocs() {
    const cards = documents().map((document) => `<article class="card">
      <div class="meta"><span class="pill ${document.kind === 'feature' ? 'ok' : 'warning'}">${esc(document.kind)}</span><code>${esc(document.path)}</code></div>
      <h2><a href="${docHref(document.path)}">${esc(document.title)}</a></h2>
      <p>${esc(document.summary || (document.kind === 'feature' ? 'Spécification fonctionnelle liée au harness.' : 'Référence produit rendue depuis le dépôt.'))}</p>
    </article>`).join('');
    app.innerHTML = `${crumbs([{label: 'Documentation'}])}
      <h1>Documentation produit</h1>
      <p>Guides, références et spécifications fonctionnelles rendus depuis les sources Markdown du dépôt. Les fiches features restent également reliées aux tests et preuves.</p>
      <div class="grid">${cards || '<div class="empty">Aucun document publié.</div>'}</div>`;
  }

  function renderDocument(document) {
    const featureLink = document.feature_id
      ? `<a class="button" href="#/features/${esc(document.feature_id)}">Voir le harness et les preuves</a>`
      : '';
    app.innerHTML = `${crumbs([{label: 'Documentation', href: '#/docs'}, {label: document.title}])}
      <div class="meta"><code>${esc(document.path)}</code>${featureLink}</div>
      <section class="panel doc">${document.html || '<div class="empty">Document vide.</div>'}</section>`;
  }

  function renderFeature(feature) {
    const journeys = (feature.journeys || []).map((journey) => {
      const c = counts(journey.matched_scenarios || []);
      return `<article class="card">
        <h3><a href="#/features/${esc(feature.id)}/journeys/${esc(journey.id)}">${esc(journey.title || journey.id)}</a></h3>
        <p><code>${esc(journey.entrypoint || '')}</code></p>
        <div class="meta"><span>${(journey.scenarios || []).length} scénarios</span><span>${c.passed} passed</span><span>${c.failed} failed</span></div>
        <div class="badges">${typeBadges(scenarioArtifacts(journey.matched_scenarios))}</div>
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features', href: '#/features'}, {label: feature.title}])}
      <h1>${esc(feature.title)}</h1>
      <p>${esc(feature.summary)}</p>
      <div class="meta">${statusPill(featureStatus(feature))}<code>${esc(feature.source)}</code></div>
      <div class="two">
        <section class="panel"><h2>Documentation</h2>${list(feature.docs || [], (doc) => {
          const published = findDocument(doc);
          return published ? `<li><a href="${docHref(doc)}"><code>${esc(doc)}</code></a></li>` : `<li><code>${esc(doc)}</code></li>`;
        })}</section>
        <section class="panel"><h2>Gaps</h2>${list(feature.gaps || [], (gap) => `<li>${esc(gap)}</li>`)}</section>
      </div>
      <section class="panel doc"><h2>Documentation utilisateur</h2>${feature.doc_html || '<div class="empty">Aucune documentation.</div>'}</section>
      <h2>User journeys</h2><div class="grid">${journeys}</div>
      <h2>Tests et preuves</h2>
      <div class="two">
        <section class="panel"><h3>Tests</h3>${list(feature.matched_tests || [], (test) => `<li><code>${esc(test)}</code></li>`)}</section>
        <section class="panel"><h3>Preuves</h3>${renderProofLinks(feature.proofs || [])}</section>
      </div>`;
  }

  function renderJourney(feature, journey) {
    const scenarios = (journey.scenarios || []).map((scenario) => renderScenarioRow(feature, journey, scenario)).join('');
    app.innerHTML = `${crumbs([
      {label: 'Features', href: '#/features'},
      {label: feature.title, href: '#/features/' + feature.id},
      {label: journey.title || journey.id}
    ])}
      <h1>${esc(journey.title || journey.id)}</h1>
      <p><code>${esc(journey.entrypoint || '')}</code></p>
      <div class="meta"><span>${(journey.matched_tests || []).length} tests</span><span>${(journey.proofs || []).length} preuves</span></div>
      <h2>Scénarios</h2><div class="scenario-list">${scenarios || '<div class="empty">Aucun scénario documenté.</div>'}</div>
      <h2>Preuves du journey</h2>${renderProofLinks(journey.proofs || [])}`;
  }

  function renderScenarioRow(feature, journey, scenario) {
    const c = counts(scenario.matched_scenarios || []);
    const status = c.failed ? 'failed' : ((scenario.gaps || []).length ? 'warning' : 'ok');
    return `<article class="scenario-row">
      ${statusPill(status)}
      <div>
        <strong><a href="#/features/${esc(feature.id)}/scenarios/${esc(scenario.id)}">${esc(scenario.title)}</a></strong>
        <p>${esc(scenario.ui_text)}</p>
        <code>${esc(scenario.scenario_id)}</code>
      </div>
      <div class="muted">${(scenario.matched_tests || []).length} tests<br>${(scenario.proofs || []).length} preuves<div class="badges">${typeBadges(scenarioArtifacts(scenario.matched_scenarios))}</div></div>
    </article>`;
  }

  function renderScenario(feature, journey, scenario) {
    app.innerHTML = `${crumbs([
      {label: 'Features', href: '#/features'},
      {label: feature.title, href: '#/features/' + feature.id},
      {label: journey.title || journey.id, href: '#/features/' + feature.id + '/journeys/' + journey.id},
      {label: scenario.title}
    ])}
      <h1>${esc(scenario.title)}</h1>
      <p>${esc(scenario.report_text || scenario.ui_text)}</p>
      <div class="meta"><code>${esc(scenario.scenario_id)}</code>${statusPill((scenario.gaps || []).length ? 'warning' : 'ok')}</div>
      <section class="bdd">
        <div><h3>Given</h3><p>${esc(scenario.given)}</p></div>
        <div><h3>When</h3><p>${esc(scenario.when)}</p></div>
        <div><h3>Then</h3><p>${esc(scenario.then)}</p></div>
      </section>
      <h2>Tests liés</h2>${renderScenarioRuns(scenario.matched_scenarios || [], scenario.tests || [], scenario)}
      <h2>Preuves</h2>${renderProofLinks(scenario.proofs || [])}`;
  }

  /* === Cartes de test: intention -> assertions -> preuves ===
     Statuts d'assertion honnêtes: on ne peint en vert que ce que la ligne
     d'échec permet d'affirmer; sans corrélation, marqueur neutre. */

  function assertionRows(run) {
    const assertions = run.assertions || [];
    if (!assertions.length) return '';
    const failedLine = Number(run.failed_line) || 0;
    const failed = ['failed', 'error'].includes(run.status);
    const rows = assertions.map((assertion) => {
      let mark = '·';
      let cls = 'neutral';
      const checkable = assertion.kind !== 'note';
      if (run.status === 'passed' && checkable) { mark = '✔'; cls = 'ok'; }
      else if (failed && checkable) {
        if (assertion.status === 'failed') { mark = '✘'; cls = 'bad'; }
        else if (failedLine && Number(assertion.end_line) < failedLine) { mark = '✔'; cls = 'ok'; }
        else if (failedLine && Number(assertion.line) > failedLine) { mark = '—'; cls = 'unreached'; }
      }
      const hover = assertion.code_excerpt ? ` title="${esc(assertion.code_excerpt)}"` : '';
      const failNote = assertion.status === 'failed' && run.message
        ? `<div class="assertion-fail">${esc(run.message)}</div>`
        : '';
      const noteTag = assertion.kind === 'note' ? ' <em class="muted">(note)</em>' : '';
      return `<div class="assertion-row assertion-${cls}"${hover}>
        <span class="assertion-mark">${mark}</span>
        <span class="assertion-line">l.${esc(assertion.line)}</span>
        <span class="assertion-text">${esc(assertion.text)}${noteTag}${failNote}</span>
      </div>`;
    }).join('');
    return `<div class="assertion-list"><h4>Déroulé annoté (#: dans le test)</h4>${rows}</div>`;
  }

  function typeBadges(artifacts) {
    const countsByType = {};
    (artifacts || []).forEach((artifact) => {
      countsByType[artifact.type] = (countsByType[artifact.type] || 0) + 1;
    });
    return Object.entries(countsByType)
      .map(([type, count]) => `<span class="badge" title="${esc(type)}">${VIEWER_ICONS[type] || VIEWER_ICONS.file} ${count}</span>`)
      .join('');
  }

  function artifactTimeline(run, scenario) {
    const artifacts = (run.artifacts || []).slice()
      .sort((a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')));
    if (!artifacts.length) return '';
    const group = artifactGroups.push({artifacts, ctx: {scenario, run}}) - 1;
    const started = run.started_at ? new Date(run.started_at) : null;
    const rows = artifacts.map((artifact, index) => {
      let offset = '';
      if (started && artifact.created_at) {
        const seconds = (new Date(artifact.created_at) - started) / 1000;
        if (isFinite(seconds)) offset = `+${Math.max(seconds, 0).toFixed(0)}s`;
      }
      return `<div class="timeline-row"><span class="timeline-time">${esc(offset)}</span>${artifactChip(artifact, group, index)}</div>`;
    }).join('');
    return `<div class="artifact-timeline"><h4>Preuves (chronologie)</h4>${rows}</div>`;
  }

  function renderTestCard(run, scenario) {
    const failed = ['failed', 'error'].includes(run.status);
    const intent = run.intent ? `<p class="test-intent">${esc(run.intent)}</p>` : '';
    const orphanFailure = failed && run.message && !(run.assertions || []).some((assertion) => assertion.status === 'failed')
      ? `<div class="assertion-fail">${esc(run.message)}</div>`
      : '';
    const streams = (run.stdout || run.stderr)
      ? `<details class="test-streams"><summary>stdout / stderr</summary>${run.stdout ? `<pre>${esc(run.stdout)}</pre>` : ''}${run.stderr ? `<pre>${esc(run.stderr)}</pre>` : ''}</details>`
      : '';
    return `<details class="test-card"${failed ? ' open' : ''}>
      <summary>${statusPill(run.status || 'unknown')} <code>${esc(run.nodeid)}</code><span class="muted">${esc(run.duration_s || 0)}s</span><span class="badges">${typeBadges(run.artifacts)}</span></summary>
      ${intent}${orphanFailure}${assertionRows(run)}${artifactTimeline(run, scenario)}${streams}
    </details>`;
  }

  function renderScenarioRuns(runs, declaredTests, scenario) {
    const declared = list(declaredTests, (test) => `<li><code>${esc(test)}</code></li>`);
    if (!runs.length) return `<div class="two"><section class="panel"><h3>Déclarés</h3>${declared}</section><section class="panel"><h3>Exécutés</h3><div class="empty">Aucun test exécuté.</div></section></div>`;
    const cards = runs.map((run) => renderTestCard(run, scenario)).join('');
    return `<details class="panel declared-tests"><summary>Tests déclarés (${(declaredTests || []).length})</summary>${declared}</details>
      <div class="test-cards">${cards}</div>`;
  }

  function artifactChip(artifact, group, index) {
    const href = esc(hrefFor(artifact.path));
    const icon = VIEWER_ICONS[artifact.type] || VIEWER_ICONS.file;
    const label = esc(artifact.label || artifact.type || 'artefact');
    if (!VIEWERS[artifact.type]) {
      return `<a class="chip" href="${href}"><span class="chip-icon">${icon}</span>${label}</a>`;
    }
    const modalAttrs = `data-modal-group="${group}" data-modal-index="${index}"`;
    if (artifact.type === 'screenshot') {
      return `<a class="shot" href="${href}" ${modalAttrs}><img src="${href}" alt="${label}" loading="lazy"><span>${icon} ${label}</span></a>`;
    }
    return `<a class="chip" href="${href}" ${modalAttrs} title="${esc(artifact.type)}"><span class="chip-icon">${icon}</span>${label}</a>`;
  }

  function renderArtifacts(artifacts, ctx) {
    if (!artifacts.length) return '<span class="muted">Aucun artefact</span>';
    const group = artifactGroups.push({artifacts, ctx: ctx || null}) - 1;
    return artifacts.map((artifact, index) => artifactChip(artifact, group, index)).join('');
  }

  function renderProofLinks(proofs) {
    if (!proofs.length) return '<div class="empty">Aucune preuve collectée.</div>';
    return list(proofs, (proof) => `<li><a href="${esc(hrefFor(proof.path))}">${esc(proof.label || proof.type || 'preuve')}</a> <code>${esc(proof.scenario_id || proof.scenario || '')}</code></li>`);
  }

  function renderGaps() {
    const inv = data.feature_inventory || {};
    const proofFailures = data.proof_failures || [];
    app.innerHTML = `${crumbs([{label: 'Gaps'}])}<h1>Gaps et violations</h1>
      <div class="two">
        <section class="panel"><h2>Violations</h2>${list(inv.violations || [], (item) => `<li>${esc(item)}</li>`)}</section>
        <section class="panel"><h2>Warnings</h2>${list(inv.warnings || [], (item) => `<li>${esc(item)}</li>`)}</section>
      </div>
      <section class="panel"><h2>Proof failures</h2>${list(proofFailures, (item) => `<li>${esc(item)}</li>`)}</section>`;
  }

  function renderRun() {
    const commands = data.commands || [];
    const rows = commands.map((command) => `<tr><td>${statusPill(command.status)}</td><td>${esc(command.label)}</td><td><code>${esc((command.argv || []).join(' '))}</code></td><td>${esc(command.duration_s)}s</td><td><code>${esc(command.log)}</code></td></tr>`).join('');
    const junit = data.junit || {};
    const suiteRows = Object.entries(junit).map(([name, suite]) => `<tr>
      <td>${esc(name)}</td><td>${esc(suite.tests)}</td><td>${esc(suite.passed)}</td>
      <td>${esc(suite.failures + suite.errors)}</td><td>${esc(suite.skipped)}</td>
      <td>${esc(suite.time_s)}s</td><td><code>${esc(suite.path)}</code></td>
    </tr>`).join('');
    const focusRows = Object.entries(junit).flatMap(([name, suite]) =>
      (suite.focus || []).map((tc) => `<tr><td>${esc(name)}</td><td>${statusPill(tc.status)}</td><td><code>${esc(tc.classname)}.${esc(tc.name)}</code></td><td>${esc(tc.time_s)}s</td></tr>`)
    ).join('');
    const tails = commands.map((command) => `<details><summary>${esc(command.label)} — <code>${esc(command.log)}</code></summary><pre>${esc(command.log_tail || '(log vide)')}</pre></details>`).join('');
    app.innerHTML = `${crumbs([{label: 'Run'}])}<h1>Preuves du run</h1>${renderMetrics()}
      <h2>Chronologie</h2>${renderCommandTimeline(commands)}
      <h2>Commandes</h2><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Preuve</th><th>Commande</th><th>Durée</th><th>Log</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h2>Suites JUnit</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Tests</th><th>Passés</th><th>Échecs</th><th>Skips</th><th>Durée</th><th>XML</th></tr></thead><tbody>${suiteRows}</tbody></table></div>
      <details class="panel secondary-table"><summary>Focus (échecs ou plus lents)</summary><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Statut</th><th>Test</th><th>Durée</th></tr></thead><tbody>${focusRows}</tbody></table></div></details>
      ${renderCastSection()}
      <details class="panel secondary-table"><summary>Fins de logs</summary>${tails}</details>
      <details class="panel secondary-table"><summary>Catalogue des preuves</summary>${renderEvidenceCatalog()}</details>`;
  }

  function renderCastSection() {
    // Portail cast: chaque commande de démonstration doit avoir son .cast
    // "generated"; les versions inlinées du catalogue s'ouvrent dans le player.
    const casts = data.casts || [];
    if (!casts.length) return '';
    const playable = (data.evidence_catalog || [])
      .filter((item) => item.type === 'asciinema' && item.inline_content)
      .map((item) => ({...item, label: item.name}));
    const chips = playable.length
      ? renderArtifacts(playable, null)
      : '<span class="muted">Aucun cast jouable embarqué dans ce rapport.</span>';
    const rows = casts.map((cast) => `<tr>
      <td>${statusPill(cast.status === 'generated' ? 'ok' : 'failed')}</td>
      <td><code>${esc(cast.id)}</code></td>
      <td><code>${esc(cast.path || '—')}</code></td>
      <td>${esc(cast.bytes || 0)}</td>
    </tr>`).join('');
    return `<h2>Casts de démonstration</h2>
      <div class="badges">${chips}</div>
      <div class="table-wrap"><table><thead><tr><th>Statut</th><th>Cast</th><th>Fichier</th><th>Octets</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderCommandTimeline(commands) {
    const total = commands.reduce((sum, command) => sum + (Number(command.duration_s) || 0), 0);
    if (!total) return '<div class="empty">Aucune durée mesurée.</div>';
    const bars = commands.map((command) => {
      const share = Math.max(((Number(command.duration_s) || 0) / total) * 100, 1.5);
      return `<div class="tl-bar ${command.status === 'ok' ? 'tl-ok' : 'tl-bad'}" style="width:${share.toFixed(1)}%" title="${esc(command.label)} — ${esc(command.duration_s)}s"><span>${esc(command.id)}</span></div>`;
    }).join('');
    return `<div class="run-timeline">${bars}</div>
      <p class="muted">Où est passé le temps: largeur ∝ durée (total ${total.toFixed(1)}s), rouge = échec. Survoler pour le détail.</p>`;
  }

  function renderCli() {
    const inv = data.feature_inventory || {};
    const byEp = inv.feature_by_entrypoint || {};
    const rows = (inv.entrypoints || []).map((ep) => {
      const featureId = byEp[ep.id] || '';
      const link = featureId ? `<a href="#/features/${esc(featureId)}">${esc(featureId)}</a>` : '<span class="muted">non rattaché</span>';
      return `<tr><td><code>${esc(ep.id)}</code></td><td>${esc(ep.type)}</td><td>${esc(ep.label || '')}</td><td>${link}</td></tr>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'CLI'}])}<h1>Surface CLI et entrypoints</h1>
      <p>${esc((data.project || {}).cli_command_count || 0)} sous-commandes cdpx. Chaque entrypoint public est rattaché à exactement une feature (sinon la preuve échoue). Aide complète capturée: <code>${esc(data.cli_help || '')}</code></p>
      <div class="table-wrap"><table><thead><tr><th>Entrypoint</th><th>Type</th><th>Description</th><th>Feature</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderValidation() {
    const matrixRows = (data.validation_matrix || []).map((row) => `<tr><td>${esc(row.milestone)}</td><td>${esc(row.proof)}</td></tr>`).join('');
    const coverageRows = (data.coverage_groups || []).map((group) => `<tr><td>${esc(group.suite)}</td><td><code>${esc(group.module)}</code></td><td>${esc(group.tests)}</td><td>${esc(group.failed)}</td><td>${esc(group.skipped)}</td></tr>`).join('');
    const riskRows = (data.risks || []).map((risk) => `<tr><td>${esc(risk.risk)}</td><td>${esc(risk.mitigation)}</td><td>${esc(risk.rollback)}</td></tr>`).join('');
    const unknownRows = (data.unknowns || []).map((item) => `<tr><td>${esc(item.item)}</td><td>${esc(item.why)}</td><td>${esc(item.how_to_verify)}</td></tr>`).join('');
    app.innerHTML = `${crumbs([{label: 'Validation'}])}<h1>Matrice de validation</h1>
      <h2>Preuve par milestone</h2><div class="table-wrap"><table><thead><tr><th>Milestone</th><th>Preuve</th></tr></thead><tbody>${matrixRows}</tbody></table></div>
      <h2>Tests par module</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Module</th><th>Tests</th><th>Échecs</th><th>Skips</th></tr></thead><tbody>${coverageRows}</tbody></table></div>
      <h2>Risques et mitigations</h2><div class="table-wrap"><table><thead><tr><th>Risque</th><th>Mitigation</th><th>Rollback</th></tr></thead><tbody>${riskRows}</tbody></table></div>
      <h2>Inconnues assumées</h2><div class="table-wrap"><table><thead><tr><th>Sujet</th><th>Pourquoi</th><th>Comment vérifier</th></tr></thead><tbody>${unknownRows}</tbody></table></div>`;
  }

  function renderEvidenceCatalog() {
    const rows = (data.evidence_catalog || []).map((item) => `<tr><td>${esc(item.type)}</td><td>${esc(item.name)}</td><td>${statusPill(item.status)}</td><td><code>${esc(item.path || '-')}</code></td><td>${esc(item.roi)}</td></tr>`).join('');
    return `<div class="table-wrap"><table><thead><tr><th>Type</th><th>Nom</th><th>Statut</th><th>Artefact</th><th>ROI</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderProject() {
    const project = data.project || {};
    const env = data.environment || {};
    app.innerHTML = `${crumbs([{label: 'Projet'}])}<h1>Contexte projet</h1>
      <section class="panel"><h2>Mission</h2><p>${esc(project.mission || '')}</p><p>Version <code>${esc(project.version || 'unknown')}</code>, branche <code>${esc(data.git?.branch || 'unknown')}</code> @ <code>${esc(data.git?.sha || 'unknown')}</code>.</p>
      <p>Environnement du run: Python <code>${esc(env.python || '?')}</code>, <code>${esc(env.platform || '?')}</code>, Chrome/Chromium ${env.chrome_or_chromium ? 'présent' : 'absent'}.</p></section>
      <div class="two">
        <section class="panel"><h2>Docs</h2>${list(project.docs || [], (doc) => `<li><code>${esc(doc)}</code></li>`)}</section>
        <section class="panel"><h2>Fixtures</h2>${list(project.fixtures || [], (fixture) => `<li><code>${esc(fixture)}</code></li>`)}</section>
      </div>`;
  }

  function renderNotFound() {
    app.innerHTML = `${crumbs([{label: 'Introuvable'}])}<h1>Vue introuvable</h1><p>La route <code>${esc(route())}</code> ne correspond à aucune vue.</p>`;
  }

  function render() {
    artifactGroups = [];
    if (!modal.hidden) closeModal();
    renderFeatureNav();
    renderDocsNav();
    const parts = route().split('/').filter(Boolean);
    if (parts.length === 0 || parts[0] === 'features' && parts.length === 1) renderFeatures();
    else if (parts[0] === 'features' && parts.length === 2) {
      const feature = findFeature(parts[1]);
      feature ? renderFeature(feature) : renderNotFound();
    } else if (parts[0] === 'features' && parts[2] === 'journeys') {
      const feature = findFeature(parts[1]);
      const journey = findJourney(feature, parts[3]);
      feature && journey ? renderJourney(feature, journey) : renderNotFound();
    } else if (parts[0] === 'features' && parts[2] === 'scenarios') {
      const feature = findFeature(parts[1]);
      const found = findScenario(feature, parts[3]);
      feature && found.scenario ? renderScenario(feature, found.journey, found.scenario) : renderNotFound();
    } else if (parts[0] === 'docs' && parts.length === 1) renderDocs();
    else if (parts[0] === 'docs' && parts[1] === 'view' && parts.length >= 3) {
      const path = decodeURIComponent(parts.slice(2).join('/'));
      const document = findDocument(path);
      document ? renderDocument(document) : renderNotFound();
    } else if (parts[0] === 'gaps') renderGaps();
    else if (parts[0] === 'run') renderRun();
    else if (parts[0] === 'cli') renderCli();
    else if (parts[0] === 'validation') renderValidation();
    else if (parts[0] === 'project') renderProject();
    else renderNotFound();
    setActiveNav();
    renderMermaid().then(() => {
      const section = routeInfo().params.get('section');
      if (section) document.getElementById(section)?.scrollIntoView();
    }).catch((error) => {
      const message = document.createElement('p');
      message.className = 'mermaid-error';
      message.textContent = 'Rendu Mermaid indisponible: ' + String(error);
      app.prepend(message);
    });
  }

  featureSearch.addEventListener('input', renderFeatureNav);
  docsSearch.addEventListener('input', renderDocsNav);
  window.addEventListener('hashchange', render);
  decorateTopbar();
  if (!location.hash) location.hash = '#/features';
  render();
})();
