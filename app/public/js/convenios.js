/* Filtro de la página de Convenios: combina la búsqueda por texto con el chip de
   categoría activo. Las tarjetas son <details> nativos (no dependen de JS para
   abrir/cerrar); este script solo decide qué tarjetas quedan visibles. */
(function () {
  var grid = document.getElementById('conv-grid');
  if (!grid) return;
  var input = document.getElementById('conv-search-input');
  var chips = document.getElementById('conv-chips');
  var countEl = document.getElementById('conv-count');
  var emptyEl = document.getElementById('conv-empty');
  var cards = Array.prototype.slice.call(grid.querySelectorAll('.conv-card'));
  var activeCat = 'todos';

  function apply() {
    var q = (input.value || '').trim().toLowerCase();
    var visible = 0;
    cards.forEach(function (card) {
      var matchesCat = activeCat === 'todos' || card.getAttribute('data-cat') === activeCat;
      var matchesQ = !q || card.getAttribute('data-name').indexOf(q) !== -1;
      var show = matchesCat && matchesQ;
      card.hidden = !show;
      if (show) visible++;
    });
    countEl.textContent = visible === cards.length
      ? 'Mostrando ' + cards.length + ' convenios'
      : 'Mostrando ' + visible + ' de ' + cards.length + ' convenios';
    emptyEl.hidden = visible !== 0;
  }

  input.addEventListener('input', apply);

  chips.addEventListener('click', function (e) {
    var btn = e.target.closest('.conv-chip');
    if (!btn) return;
    chips.querySelectorAll('.conv-chip').forEach(function (c) { c.classList.remove('is-active'); });
    btn.classList.add('is-active');
    activeCat = btn.getAttribute('data-cat');
    apply();
  });
})();
