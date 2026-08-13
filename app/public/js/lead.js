// Envío AJAX de formularios de captación (sin recargar) + validación + feedback.
(function () {
  function onReady(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  onReady(function () {
    var forms = document.querySelectorAll('form.js-lead');
    Array.prototype.forEach.call(forms, function (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        if (!form.checkValidity()) { form.reportValidity(); return; }

        var btn = form.querySelector('button[type="submit"]');
        var orig = btn ? btn.innerHTML : '';
        if (btn) { btn.disabled = true; btn.classList.add('loading'); btn.innerHTML = '<span class="spin" aria-hidden="true"></span> Enviando…'; }

        fetch(form.action, {
          method: 'POST',
          headers: { 'Accept': 'application/json', 'X-Requested-With': 'fetch' },
          body: new URLSearchParams(new FormData(form)),
        })
          .then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
              return { okHttp: r.ok, data: data };
            });
          })
          .then(function (result) {
            if (btn) { btn.disabled = false; btn.classList.remove('loading'); btn.innerHTML = orig; }
            var data = result.data;
            if (!result.okHttp || data.ok === false) {
              var err = form.querySelector('.lead-err') || document.createElement('p');
              err.className = 'lead-err';
              err.textContent = (data && data.message) || 'No pudimos enviar el formulario. Intenta de nuevo en unos minutos.';
              if (!err.parentNode) form.appendChild(err);
              return;
            }
            var ok = document.createElement('div');
            ok.className = 'lead-ok';
            ok.setAttribute('role', 'status');
            ok.innerHTML =
              '<svg class="msi ico" viewBox="0 0 960 960" aria-hidden="true"><path transform="matrix(1 0 0 -1 0 960)" d="M424 296 706 578 650 634 424 408 310 522 254 466ZM480 80Q397 80 324 111.5Q251 143 197 197Q143 251 111.5 324Q80 397 80 480Q80 563 111.5 636Q143 709 197 763Q251 817 324 848.5Q397 880 480 880Q563 880 636 848.5Q709 817 763 763Q817 709 848.5 636Q880 563 880 480Q880 397 848.5 324Q817 251 763 197Q709 143 636 111.5Q563 80 480 80ZM480 160Q614 160 707 253Q800 346 800 480Q800 614 707 707Q614 800 480 800Q346 800 253 707Q160 614 160 480Q160 346 253 253Q346 160 480 160Z"/></svg>' +
              '<div><strong>¡Solicitud enviada!</strong><p>' +
              ((data && data.message) || 'Un asesor académico te contactará muy pronto.') +
              '</p></div>';
            form.replaceWith(ok);
          })
          .catch(function () {
            if (btn) { btn.disabled = false; btn.classList.remove('loading'); btn.innerHTML = orig; }
            var err = form.querySelector('.lead-err') || document.createElement('p');
            err.className = 'lead-err';
            err.textContent = 'No pudimos enviar el formulario. Revisa tu conexión e inténtalo de nuevo.';
            if (!err.parentNode) form.appendChild(err);
          });
      });
    });
  });
})();
