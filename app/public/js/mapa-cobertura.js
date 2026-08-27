// Parallax del mapa 3D de cobertura nacional (/donde-estamos/): al mover el mouse sobre
// el mapa, inclina el plano un poco hacia el cursor. El tilt fijo ya está en el CSS
// (.co-map-plate), así que sin este script (o en touch / prefiere-menos-movimiento) el
// mapa se ve completo e igual de terminado, solo sin el seguimiento del cursor.
(function () {
  var stage = document.querySelector('.co-map-stage');
  var plate = document.querySelector('.co-map-plate');
  if (!stage || !plate) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (window.matchMedia('(pointer: coarse)').matches) return;

  var raf = null;
  stage.addEventListener('mousemove', function (e) {
    var r = stage.getBoundingClientRect();
    var mx = (e.clientX - r.left) / r.width - 0.5;
    var my = (e.clientY - r.top) / r.height - 0.5;
    if (raf) return;
    raf = requestAnimationFrame(function () {
      plate.style.transform = 'rotateX(' + (50 - my * 16).toFixed(2) + 'deg) rotateZ(' + (-8 + mx * 10).toFixed(2) + 'deg)';
      raf = null;
    });
  });
  stage.addEventListener('mouseleave', function () {
    plate.style.transform = '';
  });
})();
