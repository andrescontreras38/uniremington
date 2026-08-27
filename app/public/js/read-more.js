// "Ver más / Ver menos" automático para secciones largas del contenido de un programa
// (Perfil Profesional, Perfil Ocupacional). En vez de acortar el texto de cada programa a
// mano, esto mide la altura real de cada sección en el navegador y solo la recorta con un
// botón si de verdad es larga — funciona igual para cualquier programa presente o futuro.
(function () {
  var MAX_HEIGHT = 260; // px visibles antes de recortar
  var TARGET_TEXT = /perfil profesional|perfil ocupacional/i;

  function wrapSection(heading) {
    var wrap = document.createElement('div');
    wrap.className = 'ver-mas-wrap';
    heading.parentNode.insertBefore(wrap, heading.nextSibling);
    var node = wrap.nextSibling;
    while (node && !(node.nodeType === 1 && /^(H1|H2|H3|H4)$/.test(node.tagName))) {
      var next = node.nextSibling;
      wrap.appendChild(node);
      node = next;
    }
    return wrap;
  }

  function process(heading) {
    if (heading.dataset.verMasDone) return;
    // Dentro de un <details> aún cerrado (acordeones por sede) el contenido mide 0 —
    // se procesa cuando se abra (ver el listener de 'toggle' más abajo).
    if (heading.closest('details:not([open])')) return;
    heading.dataset.verMasDone = '1';

    var wrap = wrapSection(heading);
    if (wrap.scrollHeight <= MAX_HEIGHT + 30) return; // ya es corto, no hace falta el botón

    wrap.classList.add('ver-mas-clamped');
    wrap.style.setProperty('--vm-max', MAX_HEIGHT + 'px');

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ver-mas-btn';
    btn.textContent = 'Ver más';
    btn.setAttribute('aria-expanded', 'false');
    btn.addEventListener('click', function () {
      var expanded = wrap.classList.toggle('ver-mas-open');
      btn.textContent = expanded ? 'Ver menos' : 'Ver más';
      btn.setAttribute('aria-expanded', String(expanded));
      if (!expanded) wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    wrap.parentNode.insertBefore(btn, wrap.nextSibling);
  }

  function processAll() {
    document.querySelectorAll('.article h2, .article h3').forEach(function (h) {
      if (TARGET_TEXT.test(h.textContent)) process(h);
    });
  }

  // Los acordeones por sede abren después de cargada la página — el evento nativo
  // 'toggle' no burbujea en todos los navegadores, así que se escucha en captura.
  document.addEventListener('toggle', function (e) {
    if (e.target.tagName === 'DETAILS' && e.target.open) processAll();
  }, true);

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', processAll);
  else processAll();
})();
