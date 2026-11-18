// Painel de material de apoio (templates/lc_detail.html):
//  - abas Imagens / Documentos PDF (com setas do teclado);
//  - lightbox: clicar num thumbnail amplia a imagem; setas navegam, Esc fecha.
// Sem JS, os thumbnails e links continuam abrindo o arquivo em nova aba.
(function () {
  var panel = document.querySelector('[data-material-panel]');
  if (!panel) return;

  // ---- abas ----
  var tabs = Array.prototype.slice.call(panel.querySelectorAll('[role="tab"]'));
  function selectTab(tab, focus) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var pane = panel.querySelector('[data-pane="' + t.getAttribute('data-tab') + '"]');
      if (pane) pane.hidden = !on;
    });
    if (focus) tab.focus();
  }
  tabs.forEach(function (t, i) {
    t.addEventListener('click', function () { selectTab(t, false); });
    t.addEventListener('keydown', function (ev) {
      var n = tabs.length, j = null;
      if (ev.key === 'ArrowRight') j = (i + 1) % n;
      else if (ev.key === 'ArrowLeft') j = (i - 1 + n) % n;
      else if (ev.key === 'Home') j = 0;
      else if (ev.key === 'End') j = n - 1;
      if (j !== null) { ev.preventDefault(); selectTab(tabs[j], true); }
    });
  });

  // ---- lightbox ----
  var dlg = document.getElementById('lc-lightbox');
  var links = Array.prototype.slice.call(panel.querySelectorAll('[data-lightbox]'));
  if (!dlg || !links.length || typeof dlg.showModal !== 'function') return;

  var img = dlg.querySelector('figure img');
  var cap = dlg.querySelector('figcaption');
  var openLink = dlg.querySelector('.lc-lb-open');
  var cur = 0, opener = null;
  dlg.classList.toggle('is-single', links.length < 2);

  function show(i) {
    cur = (i + links.length) % links.length;
    var a = links[cur];
    var title = a.getAttribute('data-title') || '';
    img.src = a.getAttribute('href');
    img.alt = title;
    cap.textContent = links.length > 1 ? title + '  (' + (cur + 1) + '/' + links.length + ')' : title;
    openLink.href = a.getAttribute('href');
  }
  links.forEach(function (a, i) {
    a.addEventListener('click', function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return; // deixa abrir em nova aba
      ev.preventDefault();
      opener = a;
      show(i);
      dlg.showModal();
    });
  });
  dlg.querySelector('.lc-lb-close').addEventListener('click', function () { dlg.close(); });
  dlg.querySelector('.lc-lb-prev').addEventListener('click', function () { show(cur - 1); });
  dlg.querySelector('.lc-lb-next').addEventListener('click', function () { show(cur + 1); });
  dlg.addEventListener('keydown', function (ev) {
    if (ev.key === 'ArrowLeft') { ev.preventDefault(); show(cur - 1); }
    else if (ev.key === 'ArrowRight') { ev.preventDefault(); show(cur + 1); }
  });
  // clicar fora da imagem (no fundo) fecha
  dlg.addEventListener('click', function (ev) {
    if (ev.target === dlg || ev.target.tagName === 'FIGURE') dlg.close();
  });
  dlg.addEventListener('close', function () {
    img.removeAttribute('src');
    if (opener) opener.focus();
  });
})();
