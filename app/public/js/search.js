// Autocompletado de la barra de búsqueda: al escribir, muestra un desplegable con
// sugerencias de TODO el contenido (páginas, programas, noticias/entradas y eventos)
// desde /buscar/sugerencias. Enter o "Ver todos" abre la página completa de resultados.
// Se engancha a la barra del navbar (.navbar-search) y a la del cajón móvil (.m-search).
(function () {
  function esc(s) { return (s || '').replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function setup(form) {
    var input = form.querySelector('input[name="q"]');
    if (!input || form.dataset.sugReady) return;
    form.dataset.sugReady = '1';

    var dd = document.createElement('div');
    dd.className = 'search-sug';
    dd.hidden = true;
    dd.setAttribute('role', 'listbox');
    form.appendChild(dd);

    var items = [], active = -1, timer = null, lastReq = '';

    function hide() { dd.hidden = true; active = -1; }
    function show() { dd.hidden = false; }
    function setActive(i) {
      active = i;
      var els = dd.querySelectorAll('.search-sug-item');
      for (var k = 0; k < els.length; k++) els[k].classList.toggle('on', k === i);
    }
    function move(d) { if (items.length) setActive((active + d + items.length) % items.length); }

    function render(data) {
      items = data.results || [];
      active = -1;
      if (!items.length) { dd.innerHTML = '<div class="search-sug-empty">Sin coincidencias</div>'; show(); return; }
      var html = '';
      for (var i = 0; i < items.length; i++) {
        var r = items[i];
        html += '<a class="search-sug-item" role="option" data-i="' + i + '" href="' + esc(r.url) + '">' +
          '<span class="search-sug-tag st-' + esc((r.tag || '').toLowerCase()) + '">' + esc(r.tag) + '</span>' +
          '<span class="search-sug-tx">' + esc(r.title) + '</span></a>';
      }
      if (data.total > items.length) {
        html += '<button type="submit" class="search-sug-all">Ver los ' + data.total + ' resultados de “' + esc(data.q) + '”</button>';
      }
      dd.innerHTML = html;
      var els = dd.querySelectorAll('.search-sug-item');
      for (var j = 0; j < els.length; j++) {
        (function (el) { el.addEventListener('mousemove', function () { setActive(+el.dataset.i); }); })(els[j]);
      }
      show();
    }

    function fetchSug(q) {
      if (q === lastReq) { if (items.length) show(); return; }
      lastReq = q;
      fetch('/buscar/sugerencias?q=' + encodeURIComponent(q), { headers: { Accept: 'application/json' } })
        .then(function (r) { return r.json(); })
        .then(function (data) { if (input.value.trim() === q) render(data); })
        .catch(function () {});
    }

    input.setAttribute('autocomplete', 'off');
    input.addEventListener('input', function () {
      var q = input.value.trim();
      clearTimeout(timer);
      if (q.length < 2) { hide(); lastReq = ''; return; }
      timer = setTimeout(function () { fetchSug(q); }, 160);
    });
    input.addEventListener('keydown', function (e) {
      if (dd.hidden) return;
      if (e.key === 'ArrowDown') { e.preventDefault(); move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
      else if (e.key === 'Enter') { if (active >= 0 && items[active]) { e.preventDefault(); window.location.href = items[active].url; } }
      else if (e.key === 'Escape') { hide(); }
    });
    input.addEventListener('focus', function () { if (input.value.trim().length >= 2 && items.length) show(); });
    document.addEventListener('click', function (e) { if (!form.contains(e.target)) hide(); });
  }

  function init() {
    var forms = document.querySelectorAll('.navbar-search, .m-search');
    for (var i = 0; i < forms.length; i++) setup(forms[i]);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
