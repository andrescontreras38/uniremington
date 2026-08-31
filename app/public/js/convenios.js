/* Página de Convenios: filtro (categoría + búsqueda) combinado con paginación
   sobre el resultado filtrado, y un modal de pantalla completa para ver los
   beneficios de un convenio (en vez de expandir la tarjeta in-place, que
   empujaba el resto de la cuadrícula hacia abajo). */
(function () {
  var grid = document.getElementById('conv-grid');
  if (!grid) return;
  var input = document.getElementById('conv-search-input');
  var chips = document.getElementById('conv-chips');
  var countEl = document.getElementById('conv-count');
  var emptyEl = document.getElementById('conv-empty');
  var pagerEl = document.getElementById('conv-pager');
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.conv-card'));
  var PAGE_SIZE = 12;
  var activeCat = 'todos';
  var page = 1;

  function apply() {
    var q = (input.value || '').trim().toLowerCase();
    var matches = cards.filter(function (card) {
      var matchesCat = activeCat === 'todos' || card.getAttribute('data-cat') === activeCat;
      var matchesQ = !q || card.getAttribute('data-name').indexOf(q) !== -1;
      return matchesCat && matchesQ;
    });
    var totalPages = Math.max(1, Math.ceil(matches.length / PAGE_SIZE));
    if (page > totalPages) page = totalPages;
    var start = (page - 1) * PAGE_SIZE;
    var pageSet = matches.slice(start, start + PAGE_SIZE);

    cards.forEach(function (card) { card.hidden = pageSet.indexOf(card) === -1; });

    countEl.textContent = matches.length === 0
      ? 'Mostrando 0 convenios'
      : matches.length === cards.length && totalPages === 1
        ? 'Mostrando ' + cards.length + ' convenios'
        : 'Mostrando ' + (start + 1) + '–' + Math.min(start + PAGE_SIZE, matches.length) + ' de ' + matches.length + ' convenios';
    emptyEl.hidden = matches.length !== 0;

    renderPager(totalPages);
  }

  function renderPager(totalPages) {
    if (totalPages <= 1) { pagerEl.hidden = true; pagerEl.innerHTML = ''; return; }
    pagerEl.hidden = false;
    var h = '<button type="button" class="conv-pg" data-pg="' + (page - 1) + '"' + (page === 1 ? ' disabled' : '') + ' aria-label="Página anterior">‹</button>';
    for (var i = 1; i <= totalPages; i++) {
      h += '<button type="button" class="conv-pg' + (i === page ? ' is-active' : '') + '" data-pg="' + i + '">' + i + '</button>';
    }
    h += '<button type="button" class="conv-pg" data-pg="' + (page + 1) + '"' + (page === totalPages ? ' disabled' : '') + ' aria-label="Página siguiente">›</button>';
    pagerEl.innerHTML = h;
  }

  input.addEventListener('input', function () { page = 1; apply(); });

  chips.addEventListener('click', function (e) {
    var btn = e.target.closest('.conv-chip');
    if (!btn) return;
    chips.querySelectorAll('.conv-chip').forEach(function (c) { c.classList.remove('is-active'); });
    btn.classList.add('is-active');
    activeCat = btn.getAttribute('data-cat');
    page = 1;
    apply();
  });

  pagerEl.addEventListener('click', function (e) {
    var btn = e.target.closest('.conv-pg');
    if (!btn || btn.disabled) return;
    page = +btn.getAttribute('data-pg');
    apply();
    grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  apply();

  // ---- Modal de pantalla completa ----
  var modal = document.getElementById('conv-modal');
  // Se reubica como hijo directo de <body>: si se deja dentro de .dep-layout,
  // la animación de entrada de esa capa (fadeSoft, con fill-mode:both) deja un
  // transform "identidad" activo para siempre, y CUALQUIER transform en un
  // ancestro convierte a ese ancestro en el containing block de position:fixed
  // -- el modal quedaba atrapado dentro del layout de la página en vez de
  // cubrir la ventana completa.
  if (modal) document.body.appendChild(modal);
  var modalBg = document.getElementById('conv-modal-bg');
  var modalClose = document.getElementById('conv-modal-close');
  var modalTop = document.getElementById('conv-modal-top');
  var modalLogo = document.getElementById('conv-modal-logo');
  var modalCat = document.getElementById('conv-modal-cat');
  var modalName = document.getElementById('conv-modal-name');
  var modalPct = document.getElementById('conv-modal-pct');
  var modalBody = document.getElementById('conv-modal-body');
  var lastTrigger = null;

  function openModal(card, trigger) {
    var cc = card.style.getPropertyValue('--cc') || '#00457c';
    modalTop.style.setProperty('--cc', cc);
    modalLogo.style.setProperty('--cc', cc);
    modalLogo.textContent = card.querySelector('.conv-logo').textContent;
    modalCat.textContent = card.querySelector('.conv-cat-tag').textContent;
    modalCat.style.setProperty('--cc', cc);
    modalName.textContent = card.querySelector('.conv-name').textContent;
    var pctEl = card.querySelector('.conv-pct');
    modalPct.innerHTML = pctEl ? pctEl.innerHTML : '';
    modalPct.style.setProperty('--cc', cc);
    modalPct.hidden = !pctEl;
    modalBody.innerHTML = card.querySelector('.conv-card-body').innerHTML;

    lastTrigger = trigger;
    modal.hidden = false;
    document.body.classList.add('conv-modal-open');
    modalClose.focus();
    document.addEventListener('keydown', onKeydown);
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove('conv-modal-open');
    document.removeEventListener('keydown', onKeydown);
    if (lastTrigger) lastTrigger.focus();
  }

  function onKeydown(e) { if (e.key === 'Escape') closeModal(); }

  grid.addEventListener('click', function (e) {
    var btn = e.target.closest('.conv-card-head');
    if (!btn) return;
    openModal(btn.closest('.conv-card'), btn);
  });

  modalBg.addEventListener('click', closeModal);
  modalClose.addEventListener('click', closeModal);
})();
