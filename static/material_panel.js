// Painel de material de apoio (templates/lc_detail.html):
//  - grade única de miniaturas (imagens e PDFs); passando de LIMIT itens,
//    mostra só as primeiras linhas e um botão "Ver todos";
//  - lightbox: clicar numa imagem amplia; setas navegam, Esc fecha.
// Sem JS, tudo continua visível e os links abrem o arquivo em nova aba.
(function () {
  var panel = document.querySelector('[data-material-panel]');
  if (!panel) return;

  // ---- ver todos / mostrar menos ----
  var LIMIT = 8;
  var grid = panel.querySelector('.lc-mat-grid');
  var total = grid ? grid.children.length : 0;
  if (total > LIMIT) {
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'lc-mat-more';
    btn.setAttribute('aria-controls', 'mat-grid');
    grid.id = 'mat-grid';
    var setCollapsed = function (c) {
      grid.classList.toggle('is-collapsed', c);
      btn.setAttribute('aria-expanded', c ? 'false' : 'true');
      btn.textContent = c ? 'Ver todos (' + total + ')' : 'Mostrar menos';
    };
    btn.addEventListener('click', function () {
      var collapse = !grid.classList.contains('is-collapsed');
      setCollapsed(collapse);
      if (collapse) panel.scrollIntoView({ block: 'nearest' });
    });
    setCollapsed(true);
    grid.insertAdjacentElement('afterend', btn);
  }

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
