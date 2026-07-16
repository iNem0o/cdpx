/* Cockpit SPA (3/6) — modal artefact: contexte, navigation clavier, zoom. */
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
