/* Cockpit SPA (6/6) — routeur hash et bootstrap. */
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
