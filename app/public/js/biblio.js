// Carrusel de promociones del micrositio de Biblioteca (auto-avance + puntos clicables).
(function () {
  var track = document.querySelector('.bc-track');
  if (!track) return;
  var slides = Array.prototype.slice.call(track.children);
  var dotsWrap = document.querySelector('.bc-dots');
  if (!slides.length || !dotsWrap) return;
  var i = 0;
  var timer;

  function go(n) {
    i = (n + slides.length) % slides.length;
    track.style.transform = 'translateX(-' + (i * 100) + '%)';
    Array.prototype.forEach.call(dotsWrap.children, function (d, idx) {
      d.classList.toggle('is-active', idx === i);
    });
  }

  slides.forEach(function (_, idx) {
    var dot = document.createElement('button');
    dot.type = 'button';
    dot.className = 'bc-dot';
    dot.setAttribute('aria-label', 'Diapositiva ' + (idx + 1));
    dot.addEventListener('click', function () { go(idx); restart(); });
    dotsWrap.appendChild(dot);
  });

  function restart() {
    clearInterval(timer);
    timer = setInterval(function () { go(i + 1); }, 6000);
  }

  go(0);
  restart();
  track.closest('.biblio-carousel').addEventListener('mouseenter', function () { clearInterval(timer); });
  track.closest('.biblio-carousel').addEventListener('mouseleave', restart);
})();
