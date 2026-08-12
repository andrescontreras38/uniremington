// Estado compartido de contenido "direccionable por URL": páginas, programas, noticias
// y eventos. Noticias/eventos ahora viven en SQLite (content_items) en vez de los JSON
// estáticos de la migración de WordPress — este módulo es la ÚNICA fuente de verdad para
// ellos, y expone `reloadPostsAndEvents()` para que el panel de administración pueda
// refrescarlos en caliente (sin reiniciar el servidor) después de crear/editar contenido.
//
// Los arrays/objetos exportados (`posts`, `events`, `postIdx`, `eventIdx`, `postsByDate`,
// `eventsSorted`, `catIndex`, `catList`, `contentIndex`) se MUTAN in place (nunca se
// reasignan) — así todo lo que ya los importó en server.js sigue viendo los datos nuevos
// sin necesidad de volver a importar nada.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { db } from './db.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA = join(__dirname, '..', 'data');
// Respaldo de solo lectura para entornos sin disco persistente (ej. Vercel): si SQLite
// no está disponible, se sirve el último contenido migrado de WordPress tal cual —
// el sitio público sigue funcionando, solo que el panel de administración no aplica ahí.
function loadPublishedFromJson(file) {
  return JSON.parse(readFileSync(join(DATA, file), 'utf-8')).filter((p) => p.status === 'publish');
}

// ---------- URLs originales de WordPress (compartido con páginas/programas) ----------
export function normPath(p) {
  if (!p) return '/';
  p = decodeURIComponent(p.split('?')[0].split('#')[0]);
  if (!p.startsWith('/')) p = '/' + p;
  if (p.length > 1 && !p.endsWith('/')) p += '/';
  return p;
}
export const contentIndex = {};
export function assignUrl(kind, item, fallback) {
  const p = item.orig_path ? normPath(item.orig_path) : '';
  if (p && p !== '/' && !contentIndex[p]) {
    item.url = p;
    contentIndex[p] = { kind, item };
  } else {
    const fb = normPath(fallback);
    item.url = fb;
    if (!contentIndex[fb]) contentIndex[fb] = { kind, item };
  }
}

const bySlug = (arr) => Object.fromEntries(arr.map((x) => [x.slug, x]));
function parseDate(s) { const d = new Date((s || '').replace(' ', 'T')); return isNaN(d) ? null : d; }

// Eventos (The Events Calendar) recuperados del backup WXR: el pipeline de migración nunca
// extrajo la imagen destacada (_thumbnail_id) ni los metadatos propios del calendario (sede,
// organizador, costo, URL de inscripción externa) — solo el content_html. Reconstruido cruzando
// tribe_events con sus adjuntos/venues/organizadores en el XML; las imágenes solo se incluyen
// si su URL en producción todavía respondía 200 al verificar (~19% de los adjuntos de 2020-2021
// ya no existen en el servidor de WordPress). Clave: slug del evento. Exportado para que
// server.js pueda leer venue/organizadores/costo/URL externa al armar la tarjeta/ficha.
export const EVENTOS_REC = (() => {
  try { return JSON.parse(readFileSync(join(DATA, 'eventos-recuperados.json'), 'utf-8')); }
  catch { return {}; }
})();

// Categoría principal legible (descarta las genéricas/internas) — igual que antes.
export function primaryCat(p) {
  const cats = (p.categories || []).filter((c) => !/^(uncategorized|nobuscar|blog|sin categor)/i.test(c));
  let c = cats[0] || 'Noticias';
  c = c.replace(/^noticias?\s+/i, '').trim();
  return c ? c.charAt(0).toUpperCase() + c.slice(1) : 'Noticias';
}
export function catSlug(c) {
  return (c || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

// Mismo fix que server.js aplica a `pages`: encabezados de "Noticias Uniremington"
// duplicados y etiquetas de pestaña huérfanas — se reaplica en cada reload para que
// también cubra contenido recién editado a mano en el panel.
const DUPE_HEADING_RE = /(<h([1-3])>([^<]{1,80})<\/h\2>)(?:\s*<[^>]+>\s*)*<h\2>\3<\/h\2>/gi;
const STRAY_TAB_LABEL_RE = /<p>\s*(?:Noticias|Eventos)\s*(?:<br\s*\/?>|\s)\s*Uniremington\s*<\/p>\s*/gi;
// Títulos con etiquetas HTML embebidas (ej. "<i>Open Day</i> - Maestría en...") vienen de
// WordPress así, sin escapar — EJS los escapa al mostrarlos (<%= %>), así que se ven como
// texto literal ("&lt;i&gt;...") en vez de cursiva. Solo 4 casos en todo el sitio: se limpia
// la etiqueta en vez de cambiar el renderizado, porque el título debe seguir siendo texto
// plano en <title>/meta/JSON-LD.
function cleanTitle(item) {
  if (item.title && /<[a-z][^>]*>/i.test(item.title)) {
    item.title = item.title.replace(/<\/?[a-z][^>]*>/gi, '').trim();
  }
}
function cleanContentHtml(item) {
  cleanTitle(item);
  if (item.type === 'tribe_events' && !item.cover_image) {
    const rec = EVENTOS_REC[item.slug];
    if (rec && rec.img) item.cover_image = rec.img;
  }
  if (!item.content_html) return;
  if (DUPE_HEADING_RE.test(item.content_html)) {
    DUPE_HEADING_RE.lastIndex = 0;
    item.content_html = item.content_html.replace(DUPE_HEADING_RE, '$1');
  }
  if (STRAY_TAB_LABEL_RE.test(item.content_html)) {
    STRAY_TAB_LABEL_RE.lastIndex = 0;
    item.content_html = item.content_html.replace(STRAY_TAB_LABEL_RE, '');
  }
}

// Reconstruye desde una fila de `content_items` el MISMO shape que ya esperan
// leadAndBody/toNews/toEvent/renderArticle/dedupeMeta/etc. (título, slug, orig_path,
// fecha, categorías, content_html, excerpt) — así ninguna de esas funciones cambia.
function rowToItem(row) {
  return {
    id: row.legacy_id || String(row.id),
    type: row.kind === 'post' ? 'post' : 'tribe_events',
    status: row.status,
    title: row.title,
    slug: row.slug,
    orig_path: row.orig_path || '',
    date: row.date,
    author: '', parent: '0', menu_order: 0,
    is_program: false, facultad_slug: '', modalidad: '', nivel: '', sedes: [], ficha: {},
    categories: JSON.parse(row.categories || '[]'),
    raw_chars: 0,
    content_html: row.content_html || '',
    excerpt: row.excerpt || '',
    clean_chars: 0,
    cover_image: row.cover_image || '',
    // Campos internos (no forman parte del shape original de WordPress) que usa el
    // panel de administración para saber qué fila editar y si el slug debe bloquearse.
    _dbId: row.id,
    _legacySource: !!row.legacy_source,
  };
}

export const posts = [];
export const events = [];
export const postIdx = {};
export const eventIdx = {};
export const postsByDate = [];
export const eventsSorted = [];
export const catIndex = {};
export const catList = [];

export function reloadPostsAndEvents() {
  let newPosts = [];
  let newEvents = [];
  try {
    const rows = db.prepare("SELECT * FROM content_items WHERE status = 'publish' ORDER BY id").all();
    for (const row of rows) {
      const item = rowToItem(row);
      cleanContentHtml(item);
      (row.kind === 'post' ? newPosts : newEvents).push(item);
    }
  } catch (e) {
    console.error('[cms] SQLite no disponible, sirviendo noticias/eventos desde el JSON estático:', e.message);
    newPosts = loadPublishedFromJson('post.json');
    newEvents = loadPublishedFromJson('tribe_events.json');
    [newPosts, newEvents].forEach((arr) => arr.forEach(cleanContentHtml));
  }

  // Limpia del índice compartido solo las entradas de posts/eventos (páginas/programas
  // los administra server.js aparte y no deben tocarse aquí).
  for (const key of Object.keys(contentIndex)) {
    if (contentIndex[key].kind === 'post' || contentIndex[key].kind === 'event') delete contentIndex[key];
  }

  posts.length = 0; posts.push(...newPosts);
  events.length = 0; events.push(...newEvents);

  for (const k of Object.keys(postIdx)) delete postIdx[k];
  Object.assign(postIdx, bySlug(posts));
  for (const k of Object.keys(eventIdx)) delete eventIdx[k];
  Object.assign(eventIdx, bySlug(events));

  posts.forEach((p) => assignUrl('post', p, '/noticias/' + p.slug));
  events.forEach((e) => assignUrl('event', e, '/eventos/' + e.slug));

  postsByDate.length = 0;
  postsByDate.push(...[...posts].sort((a, b) => (parseDate(b.date) || 0) - (parseDate(a.date) || 0)));
  eventsSorted.length = 0;
  eventsSorted.push(...[...events].sort((a, b) => (parseDate(a.date) || 0) - (parseDate(b.date) || 0)));

  for (const k of Object.keys(catIndex)) delete catIndex[k];
  postsByDate.forEach((p) => {
    const name = primaryCat(p), slug = catSlug(name);
    (catIndex[slug] = catIndex[slug] || { name, slug, count: 0 }).count++;
  });
  catList.length = 0;
  catList.push(...Object.values(catIndex).sort((a, b) => b.count - a.count));
}
