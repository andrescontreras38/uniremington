/* Visor de investigadores de los grupos de investigación (GrupLAC).
   Repone el widget cuyo <script> elimina el pipeline, reproduciendo EXACTAMENTE el
   render de producción (misma marcación .rejilla-tarjetas / .tarjeta-persona /
   .circulo-iniciales / .etiqueta-estado y paginación .boton-pag) para que se vea igual
   que en la página original — el CSS scoped del micrositio es el mismo de producción.
   La config (archivo JSON, enlaces, acento, sufijo) llega en los data-* que inyecta el
   pipeline en #contenedor-grupo-*. */
(function () {
  var BASE = 'https://raw.githubusercontent.com/webmasteruniremington-oss/datos-gruplac/main/';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function iniciales(nom) {
    return nom.split(' ').filter(function (n) { return n.length > 2; }).map(function (n) { return n[0]; }).join('').substring(0, 2).toUpperCase();
  }

  function initGrupo(cont) {
    var archivo = cont.getAttribute('data-archivo');
    if (!archivo || cont.getAttribute('data-gl-init')) return;
    cont.setAttribute('data-gl-init', '1');
    var suffix = cont.getAttribute('data-gruplac') || 'grupo';
    var LINK_MIN = cont.getAttribute('data-min') || '#';
    var LINK_REPO = cont.getAttribute('data-repo') || '#';
    var accent = cont.getAttribute('data-accent') || '#00457c';
    var url = BASE + archivo;
    // Casi todos los grupos usan #visor-contenido/#visor-paginacion sin sufijo, pero al
    // menos 2 (Asimétrico, GESHE) los generaron con el sufijo del grupo en el id
    // (#visor-contenido-asimetrico) — sin este fallback el querySelector no encontraba
    // nada, `if (!contenido) return` cortaba en seco y el widget quedaba inerte (ni
    // pintaba "Cargando…" ni el error real: simplemente no hacía nada).
    var contenido = cont.querySelector('#visor-contenido-' + suffix) || cont.querySelector('#visor-contenido');
    var pag = cont.querySelector('#visor-paginacion-' + suffix) || cont.querySelector('#visor-paginacion');
    if (!contenido) return;
    var datos = [], actual = 1, limite = 6;   // menos nombres por página, se lee mejor

    // Marcación IDÉNTICA a la de producción (renderCard). El color blanco de las
    // iniciales se fija en línea porque el pipeline retira los `color:#fff` del CSS.
    function renderCard(it) {
      var nom = String(it.nombre || '').replace(/^-?\s*/, '').trim();
      return '' +
        '<a href="' + esc(it.link || '#') + '" target="_blank" rel="noopener" class="tarjeta-persona">' +
          '<div class="circulo-iniciales" style="color:#fff">' + esc(iniciales(nom)) + '</div>' +
          '<div style="min-width:0; flex-grow:1;">' +
            '<span class="nombre-persona" title="' + esc(nom) + '">' + esc(nom) + '</span>' +
            '<span style="color:#64748b; font-size:12px; display:block;">' + esc(it.vinculacion || 'Integrante') + '</span>' +
            '<div class="etiqueta-estado">' +
              '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
              ' Activo' +
            '</div>' +
          '</div>' +
        '</a>';
    }
    function mostrarPagina(n) {
      actual = n;
      var s = (n - 1) * limite, sub = datos.slice(s, s + limite);
      contenido.innerHTML = '<div class="rejilla-tarjetas" style="animation: entradaSuave 0.4s ease;">' + sub.map(renderCard).join('') + '</div>';
      renderNav();
    }
    function renderNav() {
      if (!pag) return;
      var total = Math.ceil(datos.length / limite);
      if (total <= 1) { pag.innerHTML = ''; return; }
      var h = '<button class="boton-pag" data-n="' + (actual - 1) + '"' + (actual === 1 ? ' disabled' : '') + '>Ant</button>';
      for (var i = 1; i <= total; i++) h += '<button class="boton-pag' + (i === actual ? ' activo' : '') + '" data-n="' + i + '">' + i + '</button>';
      h += '<button class="boton-pag" data-n="' + (actual + 1) + '"' + (actual === total ? ' disabled' : '') + '>Sig</button>';
      pag.innerHTML = h;
      pag.querySelectorAll('.boton-pag').forEach(function (b) {
        b.addEventListener('click', function () { if (!b.disabled) { mostrarPagina(+b.getAttribute('data-n')); cont.scrollIntoView({ behavior: 'smooth', block: 'start' }); } });
      });
    }
    function cargarIntegrantes() {
      contenido.innerHTML = 'Cargando integrantes...';
      fetch(url + '?v=' + Date.now(), { cache: 'no-cache' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (j) { datos = Array.isArray(j) ? j : []; mostrarPagina(1); })
        .catch(function () { contenido.innerHTML = 'No se pudo cargar la información de los integrantes.'; });
    }
    var ENLACES = {
      'enlace-oficial': { t: 'GrupLAC Oficial', d: 'Información pública del grupo en la plataforma de MinCiencias.', u: LINK_MIN },
      'enlace-repositorio': { t: 'Repositorio Institucional', d: 'Consulta la productividad académica y producción del grupo.', u: LINK_REPO }
    };
    cont.querySelectorAll('.opcion-menu').forEach(function (t) {
      t.addEventListener('click', function () {
        cont.querySelectorAll('.opcion-menu').forEach(function (i) { i.classList.remove('seleccionada'); });
        t.classList.add('seleccionada');
        var sec = t.dataset.seccion;
        if (sec === 'investigadores') { if (pag) pag.style.display = 'flex'; cargarIntegrantes(); return; }
        if (pag) pag.style.display = 'none';
        var cfg = ENLACES[sec];
        if (!cfg) return;
        contenido.innerHTML = '<div style="padding:10px; animation: entradaSuave 0.4s ease;"><h2 style="color:' + esc(accent) + ';margin-top:0;">' + esc(cfg.t) +
          '</h2><p>' + esc(cfg.d) + '</p><a href="' + esc(cfg.u) + '" target="_blank" rel="noopener" class="boton-accion-' + esc(suffix) + '" style="color:#fff">Ver Plataforma</a></div>';
      });
    });
    cargarIntegrantes();
  }

  document.querySelectorAll('[data-archivo][data-gruplac]').forEach(initGrupo);
})();
