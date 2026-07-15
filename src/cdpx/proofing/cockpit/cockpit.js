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
      </article>`;
    }).join('');
    app.innerHTML = `${crumbs([{label: 'Features'}])}
      <h1>Features</h1>
      <p>Navigation produit par feature, journey et scénario. Les textes affichés viennent des docs feature.</p>
      ${renderMetrics()}<div class="grid">${cards}</div>`;
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
      <div class="muted">${(scenario.matched_tests || []).length} tests<br>${(scenario.proofs || []).length} preuves</div>
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
      <h2>Tests liés</h2>${renderScenarioRuns(scenario.matched_scenarios || [], scenario.tests || [])}
      <h2>Preuves</h2>${renderProofLinks(scenario.proofs || [])}`;
  }

  function renderScenarioRuns(runs, declaredTests) {
    const declared = list(declaredTests, (test) => `<li><code>${esc(test)}</code></li>`);
    if (!runs.length) return `<div class="two"><section class="panel"><h3>Déclarés</h3>${declared}</section><section class="panel"><h3>Exécutés</h3><div class="empty">Aucun test exécuté.</div></section></div>`;
    const rows = runs.map((run) => `<tr>
      <td>${statusPill(run.status || 'unknown')}</td>
      <td><code>${esc(run.nodeid)}</code><p>${esc(run.message || '')}</p></td>
      <td>${esc(run.duration_s || 0)}s</td>
      <td>${renderArtifacts(run.artifacts || [])}</td>
    </tr>`).join('');
    return `<div class="two"><section class="panel"><h3>Déclarés</h3>${declared}</section><section class="panel"><h3>Exécutés</h3><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Test</th><th>Durée</th><th>Artefacts</th></tr></thead><tbody>${rows}</tbody></table></div></section></div>`;
  }

  function renderArtifacts(artifacts) {
    if (!artifacts.length) return '<span class="muted">Aucun artefact</span>';
    return artifacts.map((artifact) => {
      const href = hrefFor(artifact.path);
      const label = esc(artifact.label || artifact.type || 'artefact');
      if (artifact.type === 'screenshot') {
        return `<a class="shot" href="${esc(href)}"><img src="${esc(href)}" alt="${label}"><span>${label}</span></a>`;
      }
      return `<a href="${esc(href)}">${label}</a>`;
    }).join('');
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
      <h2>Commandes</h2><div class="table-wrap"><table><thead><tr><th>Statut</th><th>Preuve</th><th>Commande</th><th>Durée</th><th>Log</th></tr></thead><tbody>${rows}</tbody></table></div>
      <h2>Suites JUnit</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Tests</th><th>Passés</th><th>Échecs</th><th>Skips</th><th>Durée</th><th>XML</th></tr></thead><tbody>${suiteRows}</tbody></table></div>
      <h2>Focus (échecs ou plus lents)</h2><div class="table-wrap"><table><thead><tr><th>Suite</th><th>Statut</th><th>Test</th><th>Durée</th></tr></thead><tbody>${focusRows}</tbody></table></div>
      <h2>Fins de logs</h2><section class="panel">${tails}</section>
      <h2>Catalogue</h2>${renderEvidenceCatalog()}`;
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
  if (!location.hash) location.hash = '#/features';
  render();
})();
