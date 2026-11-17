/* Seletor de capas sugeridas (Open Library, BrasilAPI, Google Books).
 *
 * Uso:  var picker = CoverPicker(rootEl, hiddenInputEl, { onChange: fn });
 *       picker.search({ isbn, title, author });   // busca e mostra as miniaturas
 *       picker.clear();                           // esconde e desmarca
 *
 * O valor do <input type="hidden"> é o token assinado da capa escolhida ('' =
 * nenhuma). O servidor é quem baixa a imagem a partir do token ao salvar o
 * formulário — o navegador nunca envia URL de imagem.
 */
(function () {
  function CoverPicker(root, input, opts) {
    var onChange = (opts && opts.onChange) || function () {};
    var seq = 0; // descarta respostas de buscas antigas

    root.classList.add('cover-picker');
    var status = document.createElement('p');
    status.className = 'hint cover-picker-status';
    var grid = document.createElement('div');
    grid.className = 'cover-picker-grid';
    grid.setAttribute('role', 'group');
    grid.setAttribute('aria-label', 'Capas sugeridas');
    root.appendChild(status);
    root.appendChild(grid);
    root.hidden = true;

    function setValue(token) {
      input.value = token || '';
      onChange(input.value);
    }

    function select(btn, token) {
      var already = btn.getAttribute('aria-pressed') === 'true';
      Array.prototype.forEach.call(grid.children, function (b) {
        b.setAttribute('aria-pressed', 'false');
        b.classList.remove('is-selected');
      });
      if (already) { setValue(''); return; }
      btn.setAttribute('aria-pressed', 'true');
      btn.classList.add('is-selected');
      setValue(token);
    }

    function render(items) {
      grid.innerHTML = '';
      if (!items || !items.length) {
        status.textContent = 'Nenhuma capa sugerida encontrada — você pode enviar uma imagem.';
        return;
      }
      status.textContent = 'Capas sugeridas (Open Library, BrasilAPI e Google Books). ' +
        'Clique para escolher; clique de novo para desmarcar.';
      items.forEach(function (it) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'cover-option secondary';
        btn.setAttribute('aria-pressed', 'false');
        btn.title = it.label;

        var img = document.createElement('img');
        img.src = it.preview;
        img.alt = 'Capa sugerida — ' + it.label;
        img.loading = 'lazy';

        var label = document.createElement('span');
        label.className = 'cover-option-label';
        label.textContent = it.label;

        btn.appendChild(img);
        btn.appendChild(label);
        if (it.width && it.height) {
          var size = document.createElement('span');
          size.className = 'cover-option-size';
          size.textContent = it.width + '×' + it.height;
          btn.appendChild(size);
        }
        btn.addEventListener('click', function () { select(btn, it.token); });
        grid.appendChild(btn);
      });
    }

    function clear() {
      seq++;
      grid.innerHTML = '';
      status.textContent = '';
      root.hidden = true;
      setValue('');
    }

    function search(q) {
      q = q || {};
      var isbn = (q.isbn || '').trim();
      var title = (q.title || '').trim();
      var author = (q.author || '').trim();
      if (!isbn && title.length < 2) { clear(); return; }
      var mine = ++seq;
      grid.innerHTML = '';
      setValue('');
      root.hidden = false;
      status.textContent = 'Buscando capas…';
      var url = '/api/cover-candidates?isbn=' + encodeURIComponent(isbn) +
        '&title=' + encodeURIComponent(title) + '&author=' + encodeURIComponent(author);
      fetch(url)
        .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
        .then(function (items) { if (mine === seq) render(items); })
        .catch(function (err) {
          if (mine !== seq) return;
          console.error('Busca de capas falhou:', err);
          status.textContent = 'Não foi possível buscar capas agora — use a do Google Books ou envie uma imagem.';
        });
    }

    return { search: search, clear: clear };
  }

  window.CoverPicker = CoverPicker;
})();
