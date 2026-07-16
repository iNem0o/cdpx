/* Cockpit SPA (2/6) — visualiseurs d'artefacts et registre VIEWERS. */
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
