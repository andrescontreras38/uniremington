// Escrapea las noticias/eventos NUEVOS del WordPress de producción (uniremington.edu.co)
// vía su API REST y los añade a data/post.json y data/tribe_events.json con la MISMA forma
// que el resto del contenido migrado. "Nuevos" = con fecha posterior a la más reciente que
// ya tenemos, y cuyo slug aún no exista (idempotente: re-correrlo no duplica).
//
// El WordPress tiene un bug de tema que antepone warnings PHP a las respuestas, así que se
// limpia todo lo anterior al primer '['/'{' antes de parsear. La imagen destacada se
// embebe al inicio del content_html (como los posts ya migrados) para que la foto aparezca
// tanto en local (SQLite, que no guarda cover_image) como en producción (JSON).
//
// Uso:  node scripts/scrape-new-content.mjs         (dry-run: solo informa)
//       node scripts/scrape-new-content.mjs --apply (escribe los JSON)
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA = join(__dirname, '..', 'data');
const BASE = 'https://www.uniremington.edu.co/wp-json';
const APPLY = process.argv.includes('--apply');

const load = (f) => JSON.parse(readFileSync(join(DATA, f), 'utf-8'));
const stripToJson = (t) => { const i = t.search(/[[{]/); return i >= 0 ? t.slice(i) : t; };
async function getJson(url) {
  const r = await fetch(url, { headers: { Accept: 'application/json' } });
  const t = await r.text();
  return JSON.parse(stripToJson(t));
}

const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', '#039': "'", '#8217': '’', '#8216': '‘', '#8220': '“', '#8221': '”', '#8211': '–', '#8212': '—', '#8230': '…', hellip: '…', nbsp: ' ', laquo: '«', raquo: '»', ntilde: 'ñ', aacute: 'á', eacute: 'é', iacute: 'í', oacute: 'ó', uacute: 'ú' };
function decode(s) {
  return (s || '').replace(/&(#?\w+);/g, (m, e) => (e in ENT ? ENT[e] : (/^#\d+$/.test(e) ? String.fromCharCode(+e.slice(1)) : m))).trim();
}
function cleanExcerpt(html) {
  return decode((html || '')
    .replace(/<a[^>]*class="[^"]*more-link[^"]*"[^>]*>.*?<\/a>/gis, '')
    .replace(/\[&hellip;\]|\[…\]|\[\.\.\.\]/g, '…')
    .replace(/\s+/g, ' ')).trim();
}
function cleanContent(html) {
  return (html || '')
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .trim();
}

async function imgFromPage(url) {
  // Fallback fiable: la imagen destacada aparece en la página del artículo como
  // <img class="... wp-post-image"> (el REST de media a veces devuelve error).
  try {
    const html = await (await fetch(url)).text();
    const tag = (html.match(/<img[^>]*\bwp-post-image\b[^>]*>/i) || [])[0] || '';
    if (!tag) return '';
    const src = (tag.match(/\bsrc=["']([^"']+)["']/i) || [])[1]
      || (tag.match(/\bdata-(?:lazy-)?src=["']([^"']+)["']/i) || [])[1]
      || (tag.match(/\bsrcset=["']([^"'\s]+)/i) || [])[1] || '';
    return /^https?:\/\//.test(src) ? src : '';
  } catch { return ''; }
}
async function featuredUrl(wp, pageUrl) {
  // 1) embed, 2) featured_media -> /media/{id}, 3) HTML de la página (wp-post-image)
  const emb = wp._embedded && wp._embedded['wp:featuredmedia'] && wp._embedded['wp:featuredmedia'][0];
  if (emb && emb.source_url) return emb.source_url;
  if (wp.featured_media && wp.featured_media > 0) {
    try { const m = await getJson(`${BASE}/wp/v2/media/${wp.featured_media}?_fields=source_url`); if (m && m.source_url) return m.source_url; } catch {}
  }
  return await imgFromPage(pageUrl);
}
function categoriesOf(wp) {
  const terms = wp._embedded && wp._embedded['wp:term'];
  if (!terms) return [];
  const names = [];
  for (const group of terms) for (const t of (group || [])) if (t && t.name && !/^uncategorized$/i.test(t.name)) names.push(decode(t.name));
  return [...new Set(names)];
}

async function buildItem(wp, kind) {
  const type = kind === 'post' ? 'post' : 'tribe_events';
  const slug = wp.slug;
  const title = decode((wp.title && wp.title.rendered) || '');
  let content = cleanContent((wp.content && wp.content.rendered) || '');
  const pageUrl = (wp.link && /^https?:/.test(wp.link)) ? wp.link : `https://www.uniremington.edu.co${kind === 'post' ? '' : '/evento'}/${slug}/`;
  const img = await featuredUrl(wp, pageUrl);
  // Embeber la imagen destacada al inicio si no está ya en el contenido (para que sea la
  // portada de la tarjeta y el hero del artículo, igual que los posts ya migrados).
  if (img && !content.includes(img)) {
    content = `<figure><img src="${img}" alt="${title.replace(/"/g, '&quot;')}"></figure>\n` + content;
  }
  const date = String(wp.date || '').replace('T', ' ').slice(0, 19);
  const orig_path = kind === 'post' ? `/${slug}/` : `/evento/${slug}/`;
  const categories = kind === 'post' ? (categoriesOf(wp).length ? categoriesOf(wp) : ['Noticias']) : [];
  return {
    id: String(wp.id), type, status: 'publish', title, slug, orig_path, date,
    author: '', parent: '0', menu_order: 0, is_program: false, facultad_slug: '',
    modalidad: '', nivel: '', sedes: [], ficha: {}, categories,
    raw_chars: content.length, content_html: content, excerpt: cleanExcerpt(wp.excerpt && wp.excerpt.rendered),
    clean_chars: content.length,
  };
}

async function fetchNew(kind, restBase, file) {
  const existing = load(file);
  const slugs = new Set(existing.map((x) => x.slug));
  const maxDate = existing.filter((x) => x.status === 'publish').map((x) => x.date).filter(Boolean).sort().pop() || '2000-01-01 00:00:00';
  const after = maxDate.replace(' ', 'T');
  const raw = await getJson(`${BASE}/wp/v2/${restBase}?after=${encodeURIComponent(after)}&per_page=100&_embed=1&orderby=date&order=asc`);
  const news = [];
  for (const wp of raw) {
    if (wp.status && wp.status !== 'publish') continue;
    if (slugs.has(wp.slug)) continue;
    news.push(await buildItem(wp, kind));
  }
  return { existing, news, maxDate };
}

(async () => {
  console.log(`--- Escrapeando contenido NUEVO de ${BASE} ---  (${APPLY ? 'APLICAR' : 'dry-run'})\n`);
  for (const [kind, restBase, file] of [['post', 'posts', 'post.json'], ['event', 'tribe_events', 'tribe_events.json']]) {
    const { existing, news, maxDate } = await fetchNew(kind, restBase, file);
    console.log(`${file}: máx actual ${maxDate} -> ${news.length} nuevos`);
    for (const n of news) console.log(`   + ${n.date}  ${n.slug}  (img:${/<img/.test(n.content_html) ? 'sí' : 'no'}, cats:${JSON.stringify(n.categories)})`);
    if (APPLY && news.length) {
      writeFileSync(join(DATA, file), JSON.stringify(existing.concat(news), null, 1));
      console.log(`   -> escritos ${news.length} en ${file}`);
    }
    console.log('');
  }
  console.log(APPLY ? 'Listo. Corre `npm run admin:migrate` y reinicia el server para verlos en local.' : 'Dry-run. Revisa y vuelve a correr con --apply.');
})().catch((e) => { console.error('ERROR', e); process.exit(1); });
