setTimeout(() => {
  const host = document.getElementById('widget-host');
  host.dataset.widgetHost = 'expanded';
  host.style.height = '467px';
  host.textContent = 'Third-party widget content';
}, 650);
