// Mapa mundi 3D interactivo (/donde-estamos/): globo terráqueo real en WebGL (three.js,
// auto-alojado — sin CDN externo, consistente con el resto del sitio) con las 19 sedes de
// Uniremington marcadas sobre Colombia. Arrastra para rotar, rueda para acercar, clic en un
// pin para ir a esa sede. Reemplaza el mapa plano en CSS/SVG anterior (demasiado "feo" según
// feedback directo) por algo que de verdad se sienta premium.
import * as THREE from '/vendor/three.module.min.js';
import { OrbitControls } from '/vendor/OrbitControls.js';

(function () {
  const mount = document.getElementById('co-globe');
  const sedes = window.__CO_SEDES__;
  if (!mount || !sedes || !sedes.length) return;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const RADIUS = 2;

  function latLonToVector3(lat, lon, r) {
    const phi = (90 - lat) * (Math.PI / 180);
    const theta = (lon + 180) * (Math.PI / 180);
    return new THREE.Vector3(
      -r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta)
    );
  }

  // --- Escena base ---------------------------------------------------------
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
  camera.position.set(0, 0, 4.2);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch (e) {
    return; // sin WebGL disponible: se deja solo el listado de sedes (ya en el HTML) como fallback
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  mount.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.enablePan = false;
  controls.minDistance = 3;
  controls.maxDistance = 8;
  controls.zoomSpeed = 1.1;
  controls.autoRotate = !reduceMotion;
  controls.autoRotateSpeed = 1.3;
  controls.rotateSpeed = 0.55;
  controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_ROTATE };

  // --- Tierra ----------------------------------------------------------------
  const earthGroup = new THREE.Group();
  // Colombia (lon ~ -74°) mirando de frente desde el arranque, en vez de dejarlo al azar.
  // Con latLonToVector3 tal cual está abajo, lon=-90° queda centrada sin rotación —
  // así que para centrar cualquier otra longitud se rota el grupo -(lon+90)°.
  earthGroup.rotation.y = -THREE.MathUtils.degToRad(-74 + 90);
  scene.add(earthGroup);

  const loader = new THREE.TextureLoader();
  const earthTexture = loader.load('/textures/earth-day.webp');
  earthTexture.colorSpace = THREE.SRGBColorSpace;
  earthTexture.anisotropy = 4;

  const earth = new THREE.Mesh(
    new THREE.SphereGeometry(RADIUS, 64, 64),
    new THREE.MeshPhongMaterial({ map: earthTexture, shininess: 10, specular: 0x333333 })
  );
  earthGroup.add(earth);

  // Halo/atmósfera: rim glow con Fresnel, técnica estándar de three.js.
  const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(RADIUS * 1.16, 64, 64),
    new THREE.ShaderMaterial({
      vertexShader: `varying vec3 vNormal; void main(){ vNormal = normalize(normalMatrix*normal); gl_Position = projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
      fragmentShader: `varying vec3 vNormal; void main(){ float i = pow(0.62 - dot(vNormal, vec3(0.0,0.0,1.0)), 2.8); gl_FragColor = vec4(0.35,0.66,1.0,1.0) * i; }`,
      blending: THREE.AdditiveBlending,
      side: THREE.BackSide,
      transparent: true,
    })
  );
  scene.add(atmosphere);

  // Estrellas de fondo (procedural, sin textura).
  const starCount = 700;
  const starPos = new Float32Array(starCount * 3);
  for (let i = 0; i < starCount; i++) {
    const r = 40 + Math.random() * 160;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(Math.random() * 2 - 1);
    starPos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    starPos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    starPos[i * 3 + 2] = r * Math.cos(phi);
  }
  const starGeo = new THREE.BufferGeometry();
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xbfd6ff, size: 0.55, sizeAttenuation: true, transparent: true, opacity: 0.75 })));

  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.4);
  sun.position.set(5, 2, 4);
  scene.add(sun);

  // --- Pines de las sedes: isologo institucional (no un punto genérico) sobre un halo
  // suave pulsante. Escala pequeña a propósito: 19 sedes caen en un área muy chica del
  // globo (Colombia), un pin grande las hace ver amontonadas en una sola mancha.
  function glowTexture() {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const ctx = c.getContext('2d');
    const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    g.addColorStop(0, 'rgba(227,6,19,.35)');
    g.addColorStop(0.55, 'rgba(227,6,19,.1)');
    g.addColorStop(1, 'rgba(227,6,19,0)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
    return new THREE.CanvasTexture(c);
  }
  const pinMaterial = new THREE.SpriteMaterial({ map: new THREE.TextureLoader().load('/img/isologo-pin.png'), depthTest: true, transparent: true });
  const glowTex = glowTexture();

  const PIN_SCALE = 0.088;
  const glows = [];
  const pins = sedes.map((s, i) => {
    const pos = latLonToVector3(s.lat, s.lon, RADIUS * 1.012);

    // Material propio por pin (misma textura, instancia de material distinta) para que
    // cada halo pulse con su propia opacidad — si comparten material, todos parpadean a la vez.
    const glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
    glow.position.copy(pos);
    glow.scale.set(PIN_SCALE * 1.9, PIN_SCALE * 1.9, 1);
    glow.userData = { pulseOffset: (i / sedes.length) * Math.PI * 2 };
    earthGroup.add(glow);
    glows.push(glow);

    const sprite = new THREE.Sprite(pinMaterial);
    sprite.position.copy(pos);
    sprite.scale.set(PIN_SCALE, PIN_SCALE, 1);
    sprite.userData = s;
    earthGroup.add(sprite);
    return sprite;
  });

  // --- Tooltip + interacción ------------------------------------------------
  const tip = document.createElement('div');
  tip.className = 'co-globe-tip';
  mount.appendChild(tip);

  const raycaster = new THREE.Raycaster();
  const mouseNdc = new THREE.Vector2();
  let hovered = null;

  function pointerToNdc(e) {
    const r = renderer.domElement.getBoundingClientRect();
    mouseNdc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    mouseNdc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  }

  function pickPin(e) {
    pointerToNdc(e);
    raycaster.setFromCamera(mouseNdc, camera);
    const hit = raycaster.intersectObjects(pins);
    return hit.length ? hit[0].object : null;
  }

  renderer.domElement.addEventListener('pointermove', (e) => {
    hovered = pickPin(e);
    renderer.domElement.style.cursor = hovered ? 'pointer' : 'grab';
    tip.classList.toggle('is-on', !!hovered);
    if (hovered) tip.textContent = hovered.userData.n;
  });
  renderer.domElement.addEventListener('pointerleave', () => { hovered = null; tip.classList.remove('is-on'); });
  renderer.domElement.addEventListener('click', (e) => {
    const pin = pickPin(e);
    if (pin) window.location.href = '/' + pin.userData.s + '/';
  });

  // --- Tamaño / resize ------------------------------------------------------
  function resize() {
    const w = mount.clientWidth, h = mount.clientHeight;
    if (!w || !h) return;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
  }
  new ResizeObserver(resize).observe(mount);
  resize();

  // --- Loop -------------------------------------------------------------
  function animate(t) {
    requestAnimationFrame(animate);
    controls.update();
    if (!reduceMotion) {
      const s = (t || 0) / 1000;
      glows.forEach((g) => {
        const p = 0.7 + 0.3 * Math.sin(s * 1.8 + g.userData.pulseOffset);
        g.scale.set(PIN_SCALE * (1.5 + p * 0.6), PIN_SCALE * (1.5 + p * 0.6), 1);
        g.material.opacity = 0.4 + p * 0.3;
      });
    }
    if (hovered) {
      const p = hovered.position.clone().applyMatrix4(earthGroup.matrixWorld).project(camera);
      const r = renderer.domElement.getBoundingClientRect();
      tip.style.left = (r.left - mount.getBoundingClientRect().left + (p.x * 0.5 + 0.5) * r.width) + 'px';
      tip.style.top = (r.top - mount.getBoundingClientRect().top + (-p.y * 0.5 + 0.5) * r.height - 16) + 'px';
    }
    renderer.render(scene, camera);
  }
  animate();

  mount.classList.add('is-ready');
})();
