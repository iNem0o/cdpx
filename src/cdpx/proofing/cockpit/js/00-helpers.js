/* Cockpit SPA (1/6) — helpers partagés: payload du rapport, routes, formatage.
   Les six parties js/ sont concaténées dans une IIFE unique par proof.py
   (COCKPIT_JS_PARTS): elles partagent la même portée de closure. */
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
