/* Cockpit SPA (4/6) — rendu Mermaid et navigation latérale/topbar. */
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
