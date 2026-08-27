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
  camera.position.set(0, 0, 5.4);

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
  controls.minDistance = 3.1;
  controls.maxDistance = 9;
  controls.autoRotate = !reduceMotion;
  controls.autoRotateSpeed = 0.55;
  controls.rotateSpeed = 0.55;

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

  // --- Pines de las sedes ------------------------------------------------
  function pinTexture() {
    const c = document.createElement('canvas');
    c.width = 64; c.height = 64;
    const ctx = c.getContext('2d');
    ctx.beginPath();
    ctx.arc(32, 26, 15, 0, Math.PI * 2);
    ctx.fillStyle = '#e30613';
    ctx.fill();
    ctx.lineWidth = 4;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(32, 26, 6, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
    return new THREE.CanvasTexture(c);
  }
  const pinMap = pinTexture();
  const pinMaterial = new THREE.SpriteMaterial({ map: pinMap, depthTest: true, transparent: true });

  const pins = sedes.map((s) => {
    const pos = latLonToVector3(s.lat, s.lon, RADIUS * 1.012);
    const sprite = new THREE.Sprite(pinMaterial);
    sprite.position.copy(pos);
    sprite.scale.set(0.26, 0.26, 1);
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
  function animate() {
    requestAnimationFrame(animate);
    controls.update();
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
