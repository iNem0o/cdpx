/* Cockpit SPA (5/6) — views: features, docs, scenarios, gaps, run, CLI, validation. */
  function renderMetrics() {
    const totals = data.totals || {};
    const featureTotals = data.feature_inventory.totals || {};
    const scenarioTotals = data.scenario_totals || {};
    const okFeatures = features().filter((feature) => featureStatus(feature) === 'ok').length;
    return `<div class="metrics">
      <div class="metric"><strong>${data.ok ? 'OK' : 'FAILED'}</strong><span>Overall verdict</span></div>
      <div class="metric"><strong>${esc(totals.passed || 0)}/${esc(totals.tests || 0)}</strong><span>Tests passed</span></div>
      <div class="metric"><strong>${okFeatures}/${features().length}</strong><span>Features without gaps</span></div>
      <div class="metric"><strong>${esc(featureTotals.documented_scenarios || 0)}</strong><span>Documented scenarios</span></div>
      <div class="metric"><strong>${esc(featureTotals.scenarios || 0)}</strong><span>Scenario-mapped tests</span></div>
      <div class="metric"><strong>${esc(scenarioTotals.screenshots || 0)}</strong><span>Screenshots</span></div>
      <div class="metric"><strong>${esc((featureTotals.violations || 0) + (featureTotals.warnings || 0))}</strong><span>Gaps catalogue</span></div>
      <div class="metric"><strong>${esc(featureTotals.mapped_entrypoints || 0)}/${esc(featureTotals.entrypoints || 0)}</strong><span>Attached entrypoints</span></div>
      <div class="metric${(totals.unavailable || 0) ? ' warning' : ''}"><strong>${esc(totals.unavailable || 0)}</strong><span>Unavailable proofs</span></div>
    </div>`;
  }

  /* === Guided reading order: verdict -> failures -> features -> run === */

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
      /* run.scenario_id is the full form "<feature>.<short id>"; the
         scenario routes (findScenario) match the nodes' short id — so the
         feature prefix is stripped before building the link. */
      const scenarioId = String(run.scenario_id || '');
      const prefix = `${run.feature}.`;
      const shortId = scenarioId.startsWith(prefix) ? scenarioId.slice(prefix.length) : scenarioId;
      const href = run.feature && run.scenario_id
        ? `#/features/${esc(run.feature)}/scenarios/${esc(shortId)}`
        : '#/gaps';
      return `<li>${statusPill(run.status)} <a href="${href}"><code>${esc(run.nodeid)}</code></a> ${esc(run.message || '')}</li>`;
    }).join('');
    return `<section class="panel read-first"><h2>Read first</h2>
      <ul class="list">${failureItems}${runItems}</ul></section>`;
  }

  function renderReadingPath() {
    return `<div class="reading-path">Parcours de lecture — <strong>1.</strong> Verdict
      · <strong>2.</strong> <a href="#/gaps">Failures &amp; gaps</a>
      · <strong>3.</strong> Features ci-dessous
      · <strong>4.</strong> <a href="#/run">Run proofs</a></div>`;
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
          <span>${(feature.scenarios || []).length} documented scenarios</span>
          <span>${(feature.matched_tests || []).length} tests</span>
          <span>${(feature.proofs || []).length} proofs</span>
        </div>
        <div class="badges">${typeBadges(scenarioArtifacts(feature.matched_scenarios))}</div>
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features'}])}
      <h1>Features</h1>
      <p>Product navigation by feature, journey and scenario. The displayed texts come from the feature docs.</p>
      ${renderReadFirst()}${renderMetrics()}${renderReadingPath()}<div class="grid">${cards}</div>`;
  }

  function renderDocs() {
    const cards = documents().map((document) => `<article class="card">
      <div class="meta"><span class="pill ${document.kind === 'feature' ? 'ok' : 'warning'}">${esc(document.kind)}</span><code>${esc(document.path)}</code></div>
      <h2><a href="${docHref(document.path)}">${esc(document.title)}</a></h2>
      <p>${esc(document.summary || (document.kind === 'feature' ? 'Feature specification tied to the harness.' : 'Product reference rendered from the repository.'))}</p>
    </article>`).join('');
    app.innerHTML = `${crumbs([{label: 'Documentation'}])}
      <h1>Product documentation</h1>
      <p>Guides, references and feature specifications rendered from the repository's Markdown sources. The feature sheets also stay linked to tests and proofs.</p>
      <div class="grid">${cards || '<div class="empty">No published document.</div>'}</div>`;
  }

  function renderDocument(document) {
    const featureLink = document.feature_id
      ? `<a class="button" href="#/features/${esc(document.feature_id)}">See the harness and proofs</a>`
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
        <div class="meta"><span>${(journey.scenarios || []).length} scenarios</span><span>${c.passed} passed</span><span>${c.failed} failed</span></div>
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
      <h2>Tests and proofs</h2>
      <div class="two">
        <section class="panel"><h3>Tests</h3>${list(feature.matched_tests || [], (test) => `<li><code>${esc(test)}</code></li>`)}</section>
        <section class="panel"><h3>Proofs</h3>${renderProofLinks(feature.proofs || [])}</section>
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
      <div class="meta"><span>${(journey.matched_tests || []).length} tests</span><span>${(journey.proofs || []).length} proofs</span></div>
      <h2>Scenarios</h2><div class="scenario-list">${scenarios || '<div class="empty">No documented scenario.</div>'}</div>
      <h2>Journey proofs</h2>${renderProofLinks(journey.proofs || [])}`;
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
      <div class="muted">${(scenario.matched_tests || []).length} tests<br>${(scenario.proofs || []).length} proofs<div class="badges">${typeBadges(scenarioArtifacts(scenario.matched_scenarios))}</div></div>
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
      <h2>Linked tests</h2>${renderScenarioRuns(scenario.matched_scenarios || [], scenario.tests || [], scenario)}
      <h2>Proofs</h2>${renderProofLinks(scenario.proofs || [])}`;
  }

  /* === Test cards: intent -> assertions -> proofs ===
     Honest assertion statuses: only what the failure line allows to
     assert is painted green; without correlation, a neutral marker. */

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
    return `<div class="assertion-list"><h4>Annotated walkthrough (#: in the test)</h4>${rows}</div>`;
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
    return `<div class="artifact-timeline"><h4>Proofs (timeline)</h4>${rows}</div>`;
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
    if (!runs.length) return `<div class="two"><section class="panel"><h3>Declared</h3>${declared}</section><section class="panel"><h3>Executed</h3><div class="empty">No executed test.</div></section></div>`;
    const cards = runs.map((run) => renderTestCard(run, scenario)).join('');
    return `<details class="panel declared-tests"><summary>Declared tests (${(declaredTests || []).length})</summary>${declared}</details>
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
    if (!artifacts.length) return '<span class="muted">No artifact</span>';
    const group = artifactGroups.push({artifacts, ctx: ctx || null}) - 1;
    return artifacts.map((artifact, index) => artifactChip(artifact, group, index)).join('');
  }

  function renderProofLinks(proofs) {
    if (!proofs.length) return '<div class="empty">No collected proof.</div>';
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
    app.innerHTML = `${crumbs([{label: 'Run'}])}<h1>Run proofs</h1>${renderMetrics()}
      <h2>Chronologie</h2>${renderCommandTimeline(commands)}
      <h2>Commands</h2><div class="table-wrap"><table><thead><tr><th>Status</th><th>Proof</th><th>Command</th><th>Duration</th><th>Log</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h2>JUnit suites</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Tests</th><th>Passed</th><th>Failures</th><th>Skips</th><th>Duration</th><th>XML</th></tr></thead><tbody>${suiteRows}</tbody></table></div>
      <details class="panel secondary-table"><summary>Focus (failures or slowest)</summary><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Status</th><th>Test</th><th>Duration</th></tr></thead><tbody>${focusRows}</tbody></table></div></details>
      ${renderCastSection()}
      <details class="panel secondary-table"><summary>Fins de logs</summary>${tails}</details>
      <details class="panel secondary-table"><summary>Proof catalog</summary>${renderEvidenceCatalog()}</details>`;
  }

  function renderCastSection() {
    // Cast gate: every demo command must have its .cast
    // "generated"; the inlined catalog versions open in the player.
    const casts = data.casts || [];
    if (!casts.length) return '';
    const playable = (data.evidence_catalog || [])
      .filter((item) => item.type === 'asciinema' && item.inline_content)
      .map((item) => ({...item, label: item.name}));
    const chips = playable.length
      ? renderArtifacts(playable, null)
      : '<span class="muted">No playable cast embedded in this report.</span>';
    const rows = casts.map((cast) => `<tr>
      <td>${statusPill(cast.status === 'generated' ? 'ok' : 'failed')}</td>
      <td><code>${esc(cast.id)}</code></td>
      <td><code>${esc(cast.path || '—')}</code></td>
      <td>${esc(cast.bytes || 0)}</td>
    </tr>`).join('');
    return `<h2>Demo casts</h2>
      <div class="badges">${chips}</div>
      <div class="table-wrap"><table><thead><tr><th>Statut</th><th>Cast</th><th>Fichier</th><th>Octets</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderCommandTimeline(commands) {
    const total = commands.reduce((sum, command) => sum + (Number(command.duration_s) || 0), 0);
    if (!total) return '<div class="empty">No measured duration.</div>';
    const bars = commands.map((command) => {
      const share = Math.max(((Number(command.duration_s) || 0) / total) * 100, 1.5);
      return `<div class="tl-bar ${command.status === 'ok' ? 'tl-ok' : 'tl-bad'}" style="width:${share.toFixed(1)}%" title="${esc(command.label)} — ${esc(command.duration_s)}s"><span>${esc(command.id)}</span></div>`;
    }).join('');
    return `<div class="run-timeline">${bars}</div>
      <p class="muted">Where the time went: width ∝ duration (total ${total.toFixed(1)}s), red = failure. Hover for details.</p>`;
  }

  function renderCli() {
    const inv = data.feature_inventory || {};
    const byEp = inv.feature_by_entrypoint || {};
    const rows = (inv.entrypoints || []).map((ep) => {
      const featureId = byEp[ep.id] || '';
      const link = featureId ? `<a href="#/features/${esc(featureId)}">${esc(featureId)}</a>` : '<span class="muted">unattached</span>';
      return `<tr><td><code>${esc(ep.id)}</code></td><td>${esc(ep.type)}</td><td>${esc(ep.label || '')}</td><td>${link}</td></tr>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'CLI'}])}<h1>Surface CLI et entrypoints</h1>
      <p>${esc((data.project || {}).cli_command_count || 0)} cdpx subcommands. Every public entrypoint is attached to exactly one feature (otherwise the proof fails). Full help captured: <code>${esc(data.cli_help || '')}</code></p>
      <div class="table-wrap"><table><thead><tr><th>Entrypoint</th><th>Type</th><th>Description</th><th>Feature</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderValidation() {
    const matrixRows = (data.validation_matrix || []).map((row) => `<tr><td>${esc(row.milestone)}</td><td>${esc(row.proof)}</td></tr>`).join('');
    const coverageRows = (data.coverage_groups || []).map((group) => `<tr><td>${esc(group.suite)}</td><td><code>${esc(group.module)}</code></td><td>${esc(group.tests)}</td><td>${esc(group.failed)}</td><td>${esc(group.skipped)}</td></tr>`).join('');
    const riskRows = (data.risks || []).map((risk) => `<tr><td>${esc(risk.risk)}</td><td>${esc(risk.mitigation)}</td><td>${esc(risk.rollback)}</td></tr>`).join('');
    const unknownRows = (data.unknowns || []).map((item) => `<tr><td>${esc(item.item)}</td><td>${esc(item.why)}</td><td>${esc(item.how_to_verify)}</td></tr>`).join('');
    app.innerHTML = `${crumbs([{label: 'Validation'}])}<h1>Matrice de validation</h1>
      <h2>Proof by milestone</h2><div class="table-wrap"><table><thead><tr><th>Milestone</th><th>Proof</th></tr></thead><tbody>${matrixRows}</tbody></table></div>
      <h2>Tests by module</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Module</th><th>Tests</th><th>Failures</th><th>Skips</th></tr></thead><tbody>${coverageRows}</tbody></table></div>
      <h2>Risks and mitigations</h2><div class="table-wrap"><table><thead><tr><th>Risk</th><th>Mitigation</th><th>Rollback</th></tr></thead><tbody>${riskRows}</tbody></table></div>
      <h2>Accepted unknowns</h2><div class="table-wrap"><table><thead><tr><th>Subject</th><th>Why</th><th>How to verify</th></tr></thead><tbody>${unknownRows}</tbody></table></div>`;
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
      <p>Run environment: Python <code>${esc(env.python || '?')}</code>, <code>${esc(env.platform || '?')}</code>, Chrome/Chromium ${env.chrome_or_chromium ? 'present' : 'absent'}.</p></section>
      <div class="two">
        <section class="panel"><h2>Docs</h2>${list(project.docs || [], (doc) => `<li><code>${esc(doc)}</code></li>`)}</section>
        <section class="panel"><h2>Fixtures</h2>${list(project.fixtures || [], (fixture) => `<li><code>${esc(fixture)}</code></li>`)}</section>
      </div>`;
  }

  function renderNotFound() {
    app.innerHTML = `${crumbs([{label: 'Not found'}])}<h1>View not found</h1><p>La route <code>${esc(route())}</code> matches no view.</p>`;
  }
