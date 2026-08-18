// Escrapea noticias/eventos NUEVOS del WordPress de producción (uniremington.edu.co) y los
// añade a data/post.json y data/tribe_events.json con la misma forma que el resto del
// contenido migrado. También limpia contenido sucio ya existente (modo --fix).
//
// IMPORTANTE: la API REST de WordPress devuelve los shortcodes CRUDOS de WPBakery/Visual
// Composer ([vc_row], [vc_column_text]…) en `content.rendered`, porque ese constructor solo
// registra sus shortcodes en el frontend. Por eso el contenido se toma de la PÁGINA HTML ya
// renderizada (el bloque .wpb-content-wrapper / .entry-content) y se limpia con sanitize-html
// a HTML semántico (figuras, títulos, párrafos, enlaces). El tema de producción antepone
// warnings PHP a las respuestas de la API, así que se limpia todo lo previo al primer [/{.
//
// Uso:
//   node scripts/scrape-new-content.mjs          dry-run de lo NUEVO
//   node scripts/scrape-new-content.mjs --apply   añade lo NUEVO
//   node scripts/scrape-new-content.mjs --fix      re-limpia el contenido sucio existente (dry-run)
//   node scripts/scrape-new-content.mjs --fix --apply
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import sanitizeHtml from 'sanitize-html';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA = join(__dirname, '..', 'data');
const ORIGIN = 'https://www.uniremington.edu.co';
const BASE = ORIGIN + '/wp-json';
const APPLY = process.argv.includes('--apply');
const FIX = process.argv.includes('--fix');

const load = (f) => JSON.parse(readFileSync(join(DATA, f), 'utf-8'));
const stripToJson = (t) => { const i = t.search(/[[{]/); return i >= 0 ? t.slice(i) : t; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function fetchText(url, opts, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(url, opts);
      const t = await r.text();
      if (r.ok && t && t.length > 500) return t;   // respuesta válida
      if (r.status === 404) return t;               // 404 real: no reintentar
    } catch { /* red: reintentar */ }
    await sleep(700 * (i + 1));
  }
  return '';
}
async function getJson(url) {
  const t = await fetchText(url, { headers: { Accept: 'application/json' } });
  return JSON.parse(stripToJson(t));
}
async function getHtml(url) { return await fetchText(url); }

const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', '#039': "'", '#8217': '’', '#8216': '‘', '#8220': '“', '#8221': '”', '#8211': '–', '#8212': '—', '#8230': '…', hellip: '…', nbsp: ' ', ntilde: 'ñ', aacute: 'á', eacute: 'é', iacute: 'í', oacute: 'ó', uacute: 'ú' };
const decode = (s) => (s || '').replace(/&(#?\w+);/g, (m, e) => (e in ENT ? ENT[e] : (/^#\d+$/.test(e) ? String.fromCharCode(+e.slice(1)) : m))).trim();

// --- Extracción del bloque de contenido renderizado de la página HTML ---
function extractBalancedDiv(html, markerRe) {
  const start = html.search(markerRe);
  if (start < 0) return '';
  const gt = html.indexOf('>', start);
  if (gt < 0) return '';
  let depth = 1;
  const re = /<\/?div\b/gi; re.lastIndex = gt + 1;
  let m;
  while ((m = re.exec(html))) {
    depth += m[0].toLowerCase() === '<div' ? 1 : -1;
    if (depth === 0) return html.slice(gt + 1, m.index);
  }
  return html.slice(gt + 1);
}
function extractContainer(html) {
  for (const re of [
    /<div[^>]*class="[^"]*\bwpb-content-wrapper\b[^"]*"/i,
    /<div[^>]*class="[^"]*\btribe-events-single-event-description\b[^"]*"/i,
    /<div[^>]*class="[^"]*\bentry-content\b[^"]*"/i,
  ]) {
    const inner = extractBalancedDiv(html, re);
    if (inner && inner.replace(/<[^>]+>/g, '').trim().length > 120) return inner;
  }
  return '';
}
// Normaliza <img> perezosos (data-src / srcset) a un src real de mayor tamaño.
function normImgs(h) {
  return h.replace(/<img\b[^>]*>/gi, (tag) => {
    let src = (tag.match(/\bsrc=["']([^"']+)["']/i) || [])[1] || '';
    const dsrc = (tag.match(/\bdata-(?:lazy-)?src=["']([^"']+)["']/i) || [])[1];
    const sset = (tag.match(/\bdata-(?:lazy-)?srcset=["']([^"']+)["']/i) || [])[1] || (tag.match(/\bsrcset=["']([^"']+)["']/i) || [])[1];
    if ((!src || /data:image|placeholder|blank\.|lazy/i.test(src)) && dsrc) src = dsrc;
    if ((!src || /data:image|placeholder|blank\.|lazy/i.test(src)) && sset) src = sset.split(',').pop().trim().split(/\s+/)[0];
    const alt = (tag.match(/\balt=["']([^"']*)["']/i) || [])[1] || '';
    return src ? `<img src="${src}" alt="${alt}">` : '';
  });
}
function cleanContentHtml(inner) {
  let c = sanitizeHtml(normImgs(inner), {
    allowedTags: ['p', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'a', 'img', 'figure', 'figcaption', 'strong', 'b', 'em', 'i', 'u', 'br', 'blockquote', 'iframe'],
    allowedAttributes: { a: ['href', 'target', 'rel'], img: ['src', 'alt'], iframe: ['src', 'width', 'height', 'allowfullscreen', 'frameborder'] },
    allowedSchemes: ['http', 'https', 'mailto', 'tel'],
    allowedIframeHostnames: ['www.youtube.com', 'youtube.com', 'www.youtube-nocookie.com', 'player.vimeo.com', 'view.genial.ly', 'view.genially.com'],
    transformTags: { h1: 'h2' },
  });
  c = c.replace(/<a\b[^>]*>\s*(<img\b[^>]*>)\s*<\/a>/gi, '$1');   // desenvuelve imágenes enlazadas
  c = c.replace(/<p>\s*(?:&nbsp;|\s)*<\/p>/gi, '');               // párrafos vacíos
  c = c.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').replace(/\s{2,}/g, ' ').trim();
  return c;
}
function featuredFromPage(html) {
  const tag = (html.match(/<img[^>]*\bwp-post-image\b[^>]*>/i) || [])[0] || '';
  if (!tag) return '';
  const src = (tag.match(/\bsrc=["']([^"']+)["']/i) || [])[1] || (tag.match(/\bdata-(?:lazy-)?src=["']([^"']+)["']/i) || [])[1] || '';
  return /^https?:/.test(src) ? src : '';
}
const imgBase = (u) => (u.split('/').pop() || '').replace(/-\d+x\d+(?=\.\w+$)/, '').replace(/\.\w+$/, '').toLowerCase();
async function cleanContentFromPage(pageUrl, title = '') {
  try {
    const html = await getHtml(pageUrl);
    const inner = extractContainer(html);
    if (!inner) return '';
    let clean = cleanContentHtml(inner);
    if (/\[vc_/.test(clean)) return '';   // si aún quedan shortcodes, se considera fallo
    // Asegura la imagen destacada como portada/hero: si el contenido no la incluye ya
    // (comparando por nombre base, ignorando el sufijo de tamaño), se antepone.
    const feat = featuredFromPage(html);
    if (feat) {
      const base = imgBase(feat);
      const has = [...clean.matchAll(/<img[^>]+src="([^"]+)"/gi)].some((m) => imgBase(m[1]) === base);
      if (!has) clean = `<figure><img src="${feat}" alt="${(title || '').replace(/"/g, '&quot;')}"></figure>\n` + clean;
    }
    return clean;
  } catch { return ''; }
}

function pageUrlFor(kind, slug, link) {
  if (link && /^https?:/.test(link)) return link;
  return `${ORIGIN}${kind === 'post' ? '' : '/evento'}/${slug}/`;
}
function categoriesOf(wp) {
  const terms = wp._embedded && wp._embedded['wp:term'];
  if (!terms) return [];
  const names = [];
  for (const g of terms) for (const t of (g || [])) if (t && t.name && !/^uncategorized$/i.test(t.name)) names.push(decode(t.name));
  return [...new Set(names)];
}
function cleanExcerpt(html) {
  return decode((html || '').replace(/<a[^>]*more-link[^>]*>.*?<\/a>/gis, '').replace(/\[[^\]]*\]/g, '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')).trim();
}

async function buildItem(wp, kind) {
  const slug = wp.slug;
  const title = decode((wp.title && wp.title.rendered) || wp.title || '');
  const pageUrl = pageUrlFor(kind, slug, wp.link);
  let content = await cleanContentFromPage(pageUrl);
  if (!content) content = cleanContentHtml((wp.content && wp.content.rendered) || ''); // respaldo
  const date = String(wp.date || '').replace('T', ' ').slice(0, 19);
  const categories = kind === 'post' ? (categoriesOf(wp).length ? categoriesOf(wp) : ['Noticias']) : [];
  return {
    id: String(wp.id), type: kind === 'post' ? 'post' : 'tribe_events', status: 'publish',
    title, slug, orig_path: kind === 'post' ? `/${slug}/` : `/evento/${slug}/`, date,
    author: '', parent: '0', menu_order: 0, is_program: false, facultad_slug: '',
    modalidad: '', nivel: '', sedes: [], ficha: {}, categories,
    raw_chars: content.length, content_html: content, excerpt: cleanExcerpt(wp.excerpt && wp.excerpt.rendered), clean_chars: content.length,
  };
}

async function addNew() {
  console.log(`--- Escrapeando contenido NUEVO de ${ORIGIN} ---  (${APPLY ? 'APLICAR' : 'dry-run'})\n`);
  for (const [kind, restBase, file] of [['post', 'posts', 'post.json'], ['event', 'tribe_events', 'tribe_events.json']]) {
    const existing = load(file);
    const slugs = new Set(existing.map((x) => x.slug));
    const maxDate = existing.filter((x) => x.status === 'publish').map((x) => x.date).filter(Boolean).sort().pop() || '2000-01-01 00:00:00';
    const raw = await getJson(`${BASE}/wp/v2/${restBase}?after=${encodeURIComponent(maxDate.replace(' ', 'T'))}&per_page=100&_embed=1&orderby=date&order=asc`);
    const news = [];
    for (const wp of raw) { if (wp.status && wp.status !== 'publish') continue; if (slugs.has(wp.slug)) continue; news.push(await buildItem(wp, kind)); }
    console.log(`${file}: máx ${maxDate} -> ${news.length} nuevos`);
    for (const n of news) console.log(`   + ${n.date}  ${n.slug}  (${(n.content_html.match(/<img/g) || []).length} imgs, ${n.content_html.length} chars)`);
    if (APPLY && news.length) { writeFileSync(join(DATA, file), JSON.stringify(existing.concat(news), null, 1)); console.log(`   -> escritos ${news.length}`); }
    console.log('');
  }
}

async function fixDirty() {
  console.log(`--- Re-limpiando contenido con shortcodes crudos ---  (${APPLY ? 'APLICAR' : 'dry-run'})\n`);
  for (const [kind, file] of [['post', 'post.json'], ['event', 'tribe_events.json']]) {
    const arr = load(file); let fixed = 0, failed = 0;
    for (const item of arr) {
      // Solo shortcodes crudos de VC ([vc_…]); NO los guillemets »«, que son comillas
      // españolas legítimas del texto y no deben marcar el contenido como "sucio".
      if (item.status !== 'publish' || !/\[vc_/.test(item.content_html || '')) continue;
      const url = ORIGIN + (item.orig_path || `/${item.slug}/`);
      await sleep(500);
      const clean = await cleanContentFromPage(url);
      if (clean && clean.length > 150) {
        item.content_html = clean; item.raw_chars = clean.length; item.clean_chars = clean.length; fixed++;
        console.log(`   fixed ${item.slug}  (${(clean.match(/<img/g) || []).length} imgs, ${clean.length} chars)`);
      } else { failed++; console.log(`   SKIP  ${item.slug}  (no se pudo extraer/limpiar; se deja igual)`); }
    }
    if (APPLY && fixed) writeFileSync(join(DATA, file), JSON.stringify(arr, null, 1));
    console.log(`${file}: ${fixed} arreglados, ${failed} omitidos${APPLY ? ' (escrito)' : ' (dry-run)'}\n`);
  }
}

(async () => {
  if (FIX) await fixDirty(); else await addNew();
  console.log(APPLY ? 'Listo. Corre `npm run admin:migrate` y reinicia el server para verlo en local.' : 'Dry-run. Vuelve a correr con --apply.');
})().catch((e) => { console.error('ERROR', e); process.exit(1); });
