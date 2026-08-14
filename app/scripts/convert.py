#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline WXR -> JSON de alta fidelidad para la app Express.

Convierte el contenido WPBakery de WordPress a HTML semántico reproduciendo
la estructura original al pie de la letra:
  - vc_tta_section / vc_toggle  -> acordeones <details>
  - vc_single_image             -> <img> (resuelve el ID contra los adjuntos)
  - vc_btn                      -> botón <a class="btn">
  - TablePress [table id=X]     -> tabla HTML (pensum)
  - vc_column_text / rows / cols-> se aplanan conservando el HTML interior
Identifica los programas por su URL bajo /facultades/.
"""
import json, os, re, html, base64, unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from urllib.parse import urlparse, unquote

# Sedes OFICIALES vigentes (las únicas válidas). Cualquier otra ciudad del
# backup (Bogotá, Duitama, "Otras ciudades", "Virtualidad"...) se descarta.
SEDES_CANON = ['Apartadó', 'Armenia', 'Bucaramanga', 'Cali', 'Caucasia', 'Cúcuta',
               'Ibagué', 'Ipiales', 'Manizales', 'Medellín', 'Montería', 'Palmira',
               'Pasto', 'Pereira', 'Rionegro', 'Sahagún', 'Sincelejo', 'Tuluá', 'Yopal']

def _strip_ac(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn').upper().strip()

SEDE_LOOKUP = {_strip_ac(v): v for v in SEDES_CANON}   # 'MEDELLIN' -> 'Medellín'

def sedes_validas(raw_list):
    """Filtra y canoniza la lista de sedes a las oficiales, sin duplicados."""
    out = []
    for s in raw_list:
        canon = SEDE_LOOKUP.get(_strip_ac(s))
        if canon and canon not in out:
            out.append(canon)
    return sorted(out)

BASE = r"d:\Remington\OneDrive\OneDrive - Corporacion Universitaria Remington\Documents\website"
XML = os.path.join(BASE, "corporacinuniversitariaremington.WordPress.2026-07-08.xml")
OUT = os.path.join(BASE, "app", "data")
os.makedirs(OUT, exist_ok=True)

NS = {
    'wp': 'http://wordpress.org/export/1.2/',
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'excerpt': 'http://wordpress.org/export/1.2/excerpt/',
    'dc': 'http://purl.org/dc/elements/1.1/',
}
SITE = "uniremington.edu.co"

def txt(el, tag):
    x = el.find(tag, NS)
    return (x.text or '') if x is not None and x.text else ''

def attr(s, name):
    # el lookbehind evita matchear un atributo que TERMINA en `name` (p.ej. buscar `title`
    # y matchear `line_after_title="…"`, o `link` dentro de `read_more_link="…"`).
    m = re.search(r'(?<![\w-])' + name + r'="([^"]*)"', s or '')
    return m.group(1) if m else ''

# ---------------------------------------------------------------- TablePress
def cell_html(c):
    return str(c).replace('\r', '').strip().replace('\n', '<br>')

def render_table(rows, head=True, foot=False):
    if not rows:
        return ''
    body = list(rows)
    thead = tfoot = ''
    if head and body:
        hr = body.pop(0)
        thead = '<thead><tr>' + ''.join(f'<th>{cell_html(c)}</th>' for c in hr) + '</tr></thead>'
    if foot and body:
        fr = body.pop()
        tfoot = '<tfoot><tr>' + ''.join(f'<td>{cell_html(c)}</td>' for c in fr) + '</tr></tfoot>'
    tbody = '<tbody>' + ''.join(
        '<tr>' + ''.join(f'<td>{cell_html(c)}</td>' for c in row) + '</tr>' for row in body
    ) + '</tbody>'
    return '<div class="tabla-wrap"><table>' + thead + tbody + tfoot + '</table></div>'

TABLES = {}          # export_id -> html
TABLES_BY_TITLE = {} # título normalizado -> html (para pénsums que el tema arma por sede)
ATTACH = {}          # post_id   -> attachment_url
AOC_POPUPS = {}      # post_id   -> contenido crudo del popup (fichas de adopción, etc.)
FLIPBOOKS = {}       # book_id   -> URL del PDF (plugin dflip: [3d-flip-book id="X"])
TIMELINE = []        # entradas de la línea de tiempo institucional (plugin cool_timeline)

# [popup_anything id=X] referencia un post `aoc_popup` cuyo contenido (p.ej. la ficha
# de un animal en adopción: nombre, raza, edad...) se pierde al barrer el shortcode.
# Se resuelve inline con el contenido real del popup.
POPUP_SC = re.compile(r'\[popup_anything\s+id=["\']?(\d+)["\']?[^\]]*\]', re.I)

def _resolve_popup(m):
    raw = AOC_POPUPS.get(m.group(1), '')
    return ('\n\n' + raw + '\n\n') if raw.strip() else ''

def _norm_title(s):
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', s.lower()).strip()

# ------------------------------------------------------------- WPBakery -> HTML
SHORTCODE   = re.compile(r'\[/?[a-zA-Z0-9_]+[^\]]*\]')
MULTINL     = re.compile(r'\n{3,}')
TABLE_SC    = re.compile(r'\[table\s+id=["\']?(\d+)["\']?[^\]]*\]')
PLACEHOLDER = re.compile('\x00(\\d+)\x00')
# vc_raw_html / vc_raw_js: su contenido está codificado en base64(urlencode(html)).
# Son "micrositios" autocontenidos (tarjetas de sedes, servicios, contacto...) con su
# propio HTML y CSS. Antes se descartaban por completo cuando eran documentos HTML
# completos, perdiendo contenido real de las páginas internas de facultad. Ahora se
# recuperan: se extrae el cuerpo y se AÍSLA su CSS bajo el contenedor `.ms` para que
# nunca afecte el layout global del sitio (que es la razón por la que se descartaban).
RAW_HTML = re.compile(r'\[vc_raw_html[^\]]*\](.*?)\[/vc_raw_html\]', re.S)
RAW_JS = re.compile(r'\[vc_raw_js[^\]]*\].*?\[/vc_raw_js\]', re.S)

def _scope_selectors(header, scope):
    """Antepone `scope` a cada selector de una regla, neutralizando los globales
    (html/body/:root/*) para que el CSS del micrositio quede confinado a `.ms`."""
    res = []
    for p in (x.strip() for x in header.split(',')):
        if not p:
            continue
        if re.match(r'^(html|body|:root)\b', p, re.I):
            p = re.sub(r'^(html|body|:root)\b', scope, p, count=1, flags=re.I)
        elif p == '*':
            p = scope
        else:
            p = scope + ' ' + p
        res.append(p)
    return ', '.join(res) if res else scope

def _unhide_reveal(decls):
    """Neutraliza los estados iniciales de animaciones "reveal on scroll" (opacity:0
    junto a un transform) que un <script> —ya eliminado— activaría al hacer scroll.
    Sin esto el contenido quedaría invisible. Los elementos ocultos de verdad usan
    display:none / visibility:hidden, que NO se tocan."""
    if re.search(r'opacity\s*:\s*0(?![.\d])', decls):
        decls = re.sub(r'opacity\s*:\s*0(?![.\d])', 'opacity:1', decls)
        decls = re.sub(r'transform\s*:\s*[^;}]+', 'transform:none', decls)
    return decls

def _fluid_grid(decls):
    """Convierte las cuadrículas de ancho fijo de los micrositios en fluidas
    (auto-fit + minmax): se adaptan al ANCHO DEL CONTENEDOR y hacen wrap, en vez de
    forzar N columnas fijas con media-queries de viewport. Clave para responsividad.
    No toca cuadrículas icono+texto (columnas < 140px, p.ej. '28px 1fr')."""
    def repl(m):
        val = m.group(1).strip()
        low = val.lower()
        if 'auto-fit' in low or 'auto-fill' in low or 'minmax' in low:
            return m.group(0)
        base = None
        rm = re.match(r'repeat\(\s*(\d+)\s*,(.+)\)$', val)
        if rm and int(rm.group(1)) >= 2:
            pxs = re.findall(r'(\d+)px', rm.group(2))
            base = min(int(x) for x in pxs) if pxs else 220
        else:
            toks = val.split()
            if len(toks) >= 3 and all(re.fullmatch(r'\d+px', t) for t in toks):
                base = min(int(re.match(r'(\d+)', t).group(1)) for t in toks)
        if base is None or base < 140:
            return m.group(0)
        base = min(base, 280)
        return f'grid-template-columns:repeat(auto-fit,minmax(min(100%,{base}px),1fr))'
    return re.sub(r'grid-template-columns\s*:\s*([^;}]+)', repl, decls)

def _scope_css(css, scope='.ms'):
    """Reescribe un bloque CSS para confinarlo a `scope`. Descarta comentarios,
    @import y @font-face; recorre @media/@supports; deja @keyframes intactos."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    css = re.sub(r'@import[^;]+;', '', css, flags=re.I)
    css = re.sub(r'@font-face\s*\{.*?\}', '', css, flags=re.S | re.I)
    out, i, n = [], 0, len(css)
    while i < n:
        j = css.find('{', i)
        if j < 0:
            break
        header = css[i:j].strip()
        depth, k = 1, j + 1
        while k < n and depth:
            if css[k] == '{':
                depth += 1
            elif css[k] == '}':
                depth -= 1
            k += 1
        body = css[j + 1:k - 1]
        hl = header.lower()
        if hl.startswith('@media'):
            # Los micrositios traen @media (max-width/min-width) pensados para el ancho
            # del VIEWPORT completo, pero aquí viven en la columna de contenido, más
            # angosta que el viewport por la barra lateral -> esas reglas "de móvil"
            # nunca se disparaban aunque el espacio real disponible fuera estrecho.
            # Si la condición es puramente de ancho, se reescribe como @container
            # (respondiendo al ancho real de `scope`, que declara container-type).
            cond = header[len('@media'):].strip()
            width_q = r'\(\s*(?:max|min)-width\s*:\s*([\d.]+)(px|em|rem)\s*\)'
            is_width_only = bool(re.fullmatch(
                r'(?:' + width_q + r'\s*(?:and|,)?\s*)+', cond, re.I))
            # Sólo se convierte si TODOS los quiebres son de rango claramente móvil
            # (<=700px): un breakpoint mayor (900px, 1024px…) suele estar calibrado
            # para el ancho COMPLETO del viewport original del micrositio (bastante
            # más ancho que nuestra columna de contenido), y traducido tal cual se
            # dispararía SIEMPRE en el embed aunque el layout quepa perfecto -p.ej. una
            # línea de tiempo horizontal de 3 pasos que cabe cómoda en ~776px se
            # apilaba igual solo porque 776 < 900-. Con >700px se deja como @media
            # normal (viewport), que es el comportamiento original (no se dispara aquí).
            widths = re.findall(width_q, cond, re.I) if is_width_only else []
            mobile_range = bool(widths) and all(
                unit.lower() == 'px' and float(val) <= 700 for val, unit in widths)
            if is_width_only and mobile_range:
                out.append(f'@container {scope.lstrip(".")} {cond}{{' + _scope_css(body, scope) + '}')
            else:
                out.append(header + '{' + _scope_css(body, scope) + '}')
        elif hl.startswith('@supports'):
            out.append(header + '{' + _scope_css(body, scope) + '}')
        elif header.startswith('@'):          # @keyframes u otros: se conservan tal cual
            out.append(header + '{' + body + '}')
        elif header:
            out.append(_scope_selectors(header, scope) + '{' + _fluid_grid(_unhide_reveal(body)) + '}')
        i = k
    return ''.join(out)

def _cut_balanced_div(s, start):
    """Dado el índice del '<' de un <div ...>, devuelve el índice tras su </div>
    de cierre (contando anidamiento). Sirve para recortar bloques completos."""
    i = s.find('>', start) + 1
    depth = 1
    for m in re.finditer(r'<(/?)div\b[^>]*>', s[i:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return i + m.end()
    return len(s)

def _sede_icon(label):
    t = (label or '').lower()
    if 'direcc' in t or 'ubicaci' in t: return 'location_on'
    if 'horar' in t: return 'schedule'
    if 'tel' in t or 'pbx' in t or 'fono' in t or 'línea' in t or 'celular' in t or 'whats' in t: return 'call'
    if 'correo' in t or 'mail' in t or 'email' in t: return 'mail'
    return 'chevron_right'

def _transform_ugis_sedes(body):
    """Micrositio de sedes (tarjetas + modales JS): la dirección/horarios/contacto de
    cada sede (datos NAP, clave para SEO local) vive oculta en un modal que abre con
    JavaScript. Se reconstruye como un acordeón semántico propio (<details>): diseño
    limpio con el color de la facultad, sin JS, accesible y con TODO el contenido en el
    DOM (SEO/GEO: Google indexa el contenido colapsado, los LLMs lo leen igual)."""
    if 'data-ugis-modal' not in body:
        return body
    sedes = []
    for m in re.finditer(r'<div[^>]*class="[^"]*\bugis-modal\b[^"]*"[^>]*>', body):
        region = body[m.end():_cut_balanced_div(body, m.start())]
        cm = re.search(r'<h[1-6][^>]*>(.*?)</h[1-6]>', region, re.S)
        city = re.sub(r'^\s*Sede\s+', '', re.sub(r'<[^>]+>', '', cm.group(1)).strip(), flags=re.I) if cm else ''
        tm = re.search(r'<p[^>]*class="[^"]*\bugis-tag\b[^"]*"[^>]*>(.*?)</p>', region, re.S)
        tag = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', tm.group(1))).strip() if tm else ''
        rows = re.findall(r'<div><strong>([^<]+)</strong>\s*(?:<br\s*/?>)?\s*([\s\S]*?)</div>', region)
        if city and rows:
            sedes.append((city, tag, rows))
    if not sedes:
        return body
    items = []
    for i, (city, tag, rows) in enumerate(sedes):
        datos = ''.join(
            f'<div class="sede-row"><span class="msi" aria-hidden="true">{_sede_icon(l)}</span>'
            f'<div><b>{l.strip().rstrip(":")}</b><span>{v.strip()}</span></div></div>'
            for l, v in rows)
        head = f'<span class="sede-name">{city}' + (f'<small>{tag}</small>' if tag else '') + '</span>'
        items.append(
            f'<details class="sede-item"{" open" if i == 0 else ""}>'
            f'<summary class="sede-sum"><span class="sede-pin msi" aria-hidden="true">location_on</span>'
            f'{head}</summary>'
            f'<div class="sede-panel">{datos}</div></details>')
    acc = '<div class="sede-acc">' + ''.join(items) + '</div>'
    # sustituir la cuadrícula de tarjetas por el acordeón
    gm = re.search(r'<div[^>]*class="[^"]*\bugis-grid\b[^"]*"[^>]*>', body)
    if gm:
        body = body[:gm.start()] + acc + body[_cut_balanced_div(body, gm.start()):]
    # eliminar los modales (su contenido ya está en el acordeón)
    while True:
        m = re.search(r'<div[^>]*class="[^"]*\bugis-modal\b[^"]*"[^>]*>', body)
        if not m:
            break
        body = body[:m.start()] + body[_cut_balanced_div(body, m.start()):]
    if not gm:                 # sin ugis-grid: añade el acordeón y quita las tarjetas sueltas
        while True:
            m = re.search(r'<article[^>]*class="[^"]*\bugis-card\b[^"]*"[^>]*>', body)
            if not m:
                break
            end = re.search(r'</article>', body[m.start():])
            body = body[:m.start()] + body[m.start() + end.end():]
        body += acc
    return body

def _transform_org_chart(body):
    """Organigrama (.ogm-box): la descripción de cada instancia vive dentro del
    onclick de JavaScript (ogmShowInfo('titulo','desc')) → invisible para buscadores
    y LLMs, y con modal que no abre sin JS. Se pasa la descripción a un title (tooltip)
    y a una lista de definición <dl> visible bajo el organigrama. Se quita el modal."""
    if 'ogm-box' not in body or 'ogmShowInfo' not in body:
        return body
    roles = []
    def box(m):
        pre, args, post, inner = m.group(1), m.group(2), m.group(3), m.group(4)
        strs = re.findall(r"'((?:[^'\\]|\\.)*)'", args)
        desc = (strs[1] if len(strs) > 1 else '').replace('\\n', ' ').replace("\\'", "'").strip()
        label = re.sub(r'<[^>]+>', '', inner).strip()
        title = f' title="{html.escape(desc, quote=True)}"' if desc else ''
        if desc and label:
            roles.append((label, desc))
        return f'<div{pre}{post}{title}>{inner}</div>'
    body = re.sub(r'<div([^>]*?)\sonclick="ogmShowInfo\(([\s\S]*?)\)"([^>]*)>([\s\S]*?)</div>',
                  box, body)
    # quitar el modal vacío (lo poblaba el JS eliminado)
    m = re.search(r'<div[^>]*\bid="ogmModal"[^>]*>', body)
    if m:
        body = body[:m.start()] + body[_cut_balanced_div(body, m.start()):]
    if roles:
        dl = ''.join(f'<dt>{html.escape(l)}</dt><dd>{html.escape(d)}</dd>' for l, d in roles)
        body += ('<div class="ogm-roles"><h4>Funciones de cada instancia</h4>'
                 f'<dl>{dl}</dl></div>')
    return body

def _gruplac_config(dec):
    """Visor de investigadores de un grupo (pestañas Integrantes/GrupLAC/Productividad):
    su <script> hace fetch a un JSON en GitHub y se elimina en la extracción. Devuelve la
    config para reponerla con un JS compartido: {suffix, archivo, min, repo, accent}."""
    if 'visor-contenido' not in dec or 'datos-gruplac' not in dec:
        return None
    def g(name):
        m = re.search(name + r'\s*=\s*"([^"]*)"', dec)
        return m.group(1) if m else ''
    archivo = g('ARCHIVO_DATOS')
    if not archivo:
        return None
    # sufijo del grupo = el de la clase CSS `boton-accion-<suffix>` (lo usa el JS para el
    # botón de cada pestaña). No siempre coincide con el id del contenedor (p.ej. el grupo
    # "salud familiar" usa contenedor-salud-familiar pero boton-accion-salud).
    sm = (re.search(r'boton-accion-([\w-]+)', dec)
          or re.search(r'contenedor-grupo-([\w-]+)', dec)
          or re.search(r'contenedor-([\w-]+)', dec))
    ac = (re.search(r'\.circulo-iniciales\s*\{[^}]*?background[^;}]*?#([0-9a-fA-F]{6})', dec)
          or re.search(r'\.boton-accion-[\w-]+\s*\{[^}]*?background[^;}]*?#([0-9a-fA-F]{6})', dec)
          or re.search(r'<h2 style="color:#([0-9a-fA-F]{6})', dec)
          or re.search(r'\.opcion-menu\.seleccionada[^}]*?#([0-9a-fA-F]{6})', dec))
    return {'suffix': sm.group(1) if sm else 'grupo', 'archivo': archivo,
            'min': g('LINK_MINCIENCIAS'), 'repo': g('LINK_REPOSITORIO'),
            'accent': '#' + ac.group(1) if ac else '#00457c'}

def _transform_grupo_cards(body):
    """Tarjetas de grupos de investigación (micrositio .uni-srv con "Clasificación en
    Minciencias"): se rediseñan como un componente limpio y moderno (.grupo-card) en una
    grilla responsiva, en vez de las tarjetas viejas con barra roja pulsante."""
    if 'uni-srv-card' not in body or 'inciencias' not in body:
        return body
    cards = []
    for m in re.finditer(r'<div[^>]*class="[^"]*\buni-srv-card\b[^"]*"[^>]*>', body):
        region = body[m.end():_cut_balanced_div(body, m.start())]
        tm = re.search(r'class="[^"]*uni-srv-title[^"]*"[^>]*>([\s\S]*?)</', region)
        title = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', tm.group(1))).strip() if tm else ''
        cat = re.sub(r'<[^>]+>', '', (re.search(r'<mark[^>]*>([\s\S]*?)</mark>', region) or [None, ''])[1]).strip()
        img = (re.search(r'<img[^>]+src="([^"]+)"', region) or [None, ''])[1]
        link = (re.search(r'uni-srv-footer[\s\S]*?<a[^>]+href="([^"]+)"', region) or [None, '#'])[1]
        info = re.search(r'uni-srv-info[^>]*>([\s\S]*?)$', region)
        lider = correo = ''
        if info:
            lm = re.search(r'<strong[^>]*>([\s\S]*?)</strong>', info.group(1))
            lider = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', lm.group(1))).strip() if lm else ''
            lider = re.sub(r'^\s*l[ií]der\s*:?\s*', '', lider, flags=re.I)   # el CSS ya antepone "Líder:"
            correo = (re.search(r'mailto:([^"\'>\s]+)', info.group(1)) or [None, ''])[1]
        if title:
            cards.append((img, title, cat, link, lider, correo))
    if not cards:
        return body
    out = ['<div class="grupo-grid">']
    for img, title, cat, link, lider, correo in cards:
        s = [f'<a class="grupo-card" href="{link}">']
        if img:
            s.append(f'<span class="grupo-card-img"><img src="{img}" alt="{html.escape(title, quote=True)}" loading="lazy"></span>')
        s.append('<span class="grupo-card-bd">')
        if cat:
            s.append(f'<span class="grupo-card-cat">{cat}</span>')
        s.append(f'<span class="grupo-card-t">{title}</span>')
        if lider:
            s.append(f'<span class="grupo-card-lider">{lider}</span>')
        if correo:
            s.append(f'<span class="grupo-card-mail">{correo}</span>')
        s.append('<span class="grupo-card-go">Ver información del grupo <span aria-hidden="true">→</span></span>')
        s.append('</span></a>')
        out.append(''.join(s))
    out.append('</div>')
    return ''.join(out)

def _transform_grupo_stats(body):
    """Tabla "Estadísticas de los últimos 10 años" de un grupo (una fila: nombre + 4
    categorías + total) → banda de indicadores (.stat-band) con el total como cifra
    protagonista y las categorías como tarjetas. Mucho más legible que la tabla plana."""
    if 'stad' not in body or not re.search(r'>\s*total', body, re.I):
        return body
    tm = re.search(r'<table[\s\S]*?</table>', body)
    if not tm:
        return body
    table = tm.group(0)
    labels = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t)).strip()
              for t in re.findall(r'<th[^>]*>([\s\S]*?)</th>', table)]
    datarow = next((r for r in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', table) if '<td' in r), None)
    if not datarow or len(labels) < 3:
        return body
    vals = [re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', t)).strip()
            for t in re.findall(r'<td[^>]*>([\s\S]*?)</td>', datarow)]
    if len(vals) != len(labels):
        return body
    group, pairs = vals[0], list(zip(labels[1:], vals[1:]))
    if len(pairs) < 2:
        return body
    tlabel, tval = pairs[-1]
    out = [f'<div class="stat-band">',
           '<div class="stat-total">',
           f'<span class="stat-total-n">{html.escape(tval)}</span>',
           f'<span class="stat-total-l">{html.escape(tlabel)}</span>',
           f'<span class="stat-total-g">Grupo {html.escape(group)} · últimos 10 años</span>',
           '</div><div class="stat-cards">']
    for lab, val in pairs[:-1]:
        out.append(f'<div class="stat-card"><span class="stat-n">{html.escape(val)}</span>'
                   f'<span class="stat-l">{html.escape(lab)}</span></div>')
    out.append('</div></div>')
    grid = ''.join(out)
    wm = re.search(r'<div[^>]*border-radius:\s*15px[^>]*>', body)
    if wm:
        return body[:wm.start()] + grid + body[_cut_balanced_div(body, wm.start()):]
    return body[:tm.start()] + grid + body[tm.end():]

_SEMILLEROS_ITEM = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bacordeon-item\b[^"]*"[^>]*\bdata-facultad="([^"]+)"[^>]*>\s*'
    r'<button([^>]*)>([\s\S]*?)</button>\s*'
    r'<div(?:[^>]*\bclass="[^"]*\bacordeon-contenido\b[^"]*"[^>]*)>([\s\S]*?)</div>\s*</div>')

def _transform_semilleros_cifras(body):
    """'Semilleros de investigación en cifras': el conteo real (por Facultad y el total)
    lo calculaba un <script> contando los .semillero-tag dentro de cada acordeón, y el
    propio acordeón (abrir/cerrar) dependía de otro <script> (toggleAcordeon); TODO
    <script> se elimina en clean_content, así que las cifras quedaban en 0 y el acordeón
    solo mostraba abierto el primer ítem (el único con estilo inline), sin forma de abrir
    los demás. Se recalculan los conteos en frío y el acordeón se reescribe con <details>/
    <summary> nativos (mismas clases -> conserva el CSS del propio micrositio) para que
    funcione sin JS."""
    if 'unr-cifra-card' not in body or 'acordeon-item' not in body:
        return body
    counts = {}

    def _item(m):
        fac, btn_attrs, btn_inner, tags = m.groups()
        n = tags.count('semillero-tag')
        counts[fac] = n
        title = re.sub(r'<span[^>]*\bacordeon-boton__count\b[^>]*>[\s\S]*?</span>', '', btn_inner)
        title = re.sub(r'<[^>]+>', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()
        is_open = 'activo' in btn_attrs.lower()
        return ('<details class="acordeon-item"' + (' open' if is_open else '') +
                f' data-facultad="{fac}"><summary class="acordeon-boton">'
                f'<span class="acordeon-boton__left"><span class="acordeon-boton__count" data-count>{n}</span> '
                f'{title}</span><span class="acordeon-icono"></span></summary>'
                f'<div class="acordeon-contenido">{tags}</div></details>')
    body = _SEMILLEROS_ITEM.sub(_item, body)
    total = sum(counts.values())

    def _card(m):
        return m.group(0).replace('data-count>0<', f'data-count>{counts.get(m.group(1), 0)}<', 1)
    body = re.sub(r'<div\b[^>]*\bclass="unr-cifra-card"[^>]*\bdata-facultad="([^"]+)"[^>]*>'
                  r'[\s\S]*?</div>\s*</div>', _card, body)
    body = re.sub(r'(class="unr-total__num"[^>]*data-total>)0(<)', rf'\g<1>{total}\g<2>', body)

    # con <details> nativo el estado abierto lo da [open]; solo faltan el giro del icono
    # y el acento lateral de color que antes disparaba la clase .activo puesta por JS.
    # El propio CSS del micrositio trae ".acordeon-contenido{display:none}" INCONDICIONAL
    # (esperaba que el JS eliminado pusiera "style=display:block" al abrir) -> sin JS,
    # esa regla gana siempre sobre el "mostrar hijos si [open]" nativo de <details>, y el
    # contenido queda oculto aunque el acordeón esté abierto. Se fuerza a visible con [open].
    return ('<style data-ms>summary.acordeon-boton{list-style:none;cursor:pointer}'
            'summary.acordeon-boton::-webkit-details-marker{display:none}'
            'details.acordeon-item[open]>summary.acordeon-boton .acordeon-icono{transform:rotate(-135deg)}'
            'details.acordeon-item[open]::before{opacity:1}'
            'details.acordeon-item[open]>.acordeon-contenido{display:block!important}</style>') + body

def _extract_micrositio(dec):
    """De un HTML de micrositio (documento completo o fragmento) devuelve un bloque
    `<div class="ms">` con el cuerpo y su CSS ya aislado en `<style data-ms>`."""
    scoped = ''.join(_scope_css(st) for st in
                     re.findall(r'<style[^>]*>(.*?)</style>', dec, re.S | re.I))
    gl = _gruplac_config(dec)                     # visor de grupo: extraer config antes de quitar el <script>
    bm = re.search(r'<body[^>]*>(.*?)</body>', dec, re.S | re.I)
    body = bm.group(1) if bm else dec
    body = re.sub(r'<!doctype[^>]*>', '', body, flags=re.I)
    body = re.sub(r'<head[^>]*>.*?</head>', '', body, flags=re.S | re.I)
    body = re.sub(r'</?(html|head|body)[^>]*>', '', body, flags=re.I)
    body = re.sub(r'<style[^>]*>.*?</style>', '', body, flags=re.S | re.I)
    body = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.S | re.I)
    body = re.sub(r'<(?:link|meta)[^>]*>', '', body, flags=re.I)
    body = re.sub(r'<title[^>]*>.*?</title>', '', body, flags=re.S | re.I)
    if gl:                                          # reponer la config como data-* en el contenedor
        # el contenedor del visor puede llamarse contenedor-grupo-X o contenedor-<nombre>
        body = re.sub(r'(<div\s+id="contenedor-[\w-]+")',
                      lambda m: (m.group(1) + f' data-gruplac="{gl["suffix"]}" data-archivo="{gl["archivo"]}"'
                                 f' data-min="{gl["min"]}" data-repo="{gl["repo"]}" data-accent="{gl["accent"]}"'),
                      body, count=1)
    # micrositios interactivos -> HTML estático, semántico y accesible (sin JS)
    body = _transform_ugis_sedes(body)
    body = _transform_org_chart(body)
    body = _transform_grupo_cards(body)
    body = _transform_grupo_stats(body)
    body = _transform_semilleros_cifras(body)
    micro = ('<style data-ms>' + scoped + '</style>' if scoped.strip() else '') + body
    # sin saltos de línea: es un único bloque, y así las etiquetas multilínea
    # (atributos de <path>, <a>...) no se rompen con <br> ni se parten en el auto-<p>
    micro = re.sub(r'\s*[\r\n]+\s*', ' ', micro)
    micro = re.sub(r' {2,}', ' ', micro).strip()
    return '\n\n<div class="ms">' + micro + '</div>\n\n' if micro else '\n\n'

def _rawhtml(m):
    try:
        dec = unquote(base64.b64decode(m.group(1)).decode('utf-8', 'ignore'))
    except Exception:
        return ''
    return _extract_micrositio(dec)

# shortcodes de par (con contenido interior)
PAIRED = re.compile(
    r'\[(vc_column_text|vc_tta_section|vc_toggle|vc_tta_accordion|vc_tta_tabs|vc_tta_tour|'
    r'vc_tta_pageable|vc_row|vc_row_inner|vc_column|vc_column_inner|vc_cta|vc_message|vc_hoverbox)'
    r'([^\]]*)\](.*?)\[/\1\]', re.S)

def _btn(m):
    title = attr(m.group(0), 'title') or 'Ver más'
    link = attr(m.group(0), 'link')
    href = '#'
    mm = re.search(r'url:([^|]+)', link)
    if mm:
        href = unquote(mm.group(1))
    return f'\n\n<p><a class="btn btn-oro" href="{href}">{html.unescape(title)}</a></p>\n\n'

def _img(m):
    s = m.group(0)
    url = ATTACH.get(attr(s, 'image'), '')
    if not url:
        return '\n\n'
    img = f'<img src="{url}" alt="" loading="lazy">'
    # vc_single_image con onclick=custom_link envuelve la imagen en su enlace (p.ej. cada
    # portada del Periódico Entorno enlaza a su edición). Se preserva el destino del backup.
    raw_link = attr(s, 'link')
    mm = re.search(r'url:([^|]+)', raw_link)
    href = unquote(mm.group(1)) if mm else (unquote(raw_link) if raw_link else '')
    if href:
        img = f'<a href="{href}">{img}</a>'
    return f'\n\n<figure>{img}</figure>\n\n'

# Icono PDF (SVG, no depende de la fuente) + visor de PDF uniforme. Se definen aquí arriba
# porque clean_content (PASO 2) los usa vía _flipbook antes de las funciones de más abajo.
PDF_ICON = ('<svg class="doc-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="M14 3v4a1 1 0 0 0 1 1h4"/>'
            '<path d="M16.5 21h-9A1.5 1.5 0 0 1 6 19.5v-15A1.5 1.5 0 0 1 7.5 3H14l4 4v12.5A1.5 1.5 0 0 1 16.5 21z"/>'
            '<path d="M9 13h6M9 16.5h4"/></svg>')

def pdf_viewer(url):
    """Visor de PDF uniforme (mismo diseño para flipbooks dflip e iframes de PDF de WP):
    iframe embebido con altura usable + enlace de respaldo 'Abrir el documento'."""
    return (f'\n\n<div class="pdf-embed"><iframe class="pdf-frame" src="{url}" loading="lazy" '
            f'title="Documento PDF"></iframe></div>'
            f'<p><a class="doc-pdf" href="{url}" target="_blank" rel="noopener">'
            f'{PDF_ICON}<span class="doc-txt">Abrir el documento en una pestaña nueva</span></a></p>\n\n')

def _flipbook(m):
    """[3d-flip-book id="X"] (plugin dflip) -> visor de PDF. El PDF se resuelve por id contra
    FLIPBOOKS (recolectado de los posts 3d-flip-book en PASO 1). Sin esto, las ediciones del
    Periódico "En Torno" y varias cartillas quedaban en blanco."""
    bid = attr(m.group(0), 'id')
    url = FLIPBOOKS.get(bid, '')
    return pdf_viewer(url) if url else '\n\n'

def _cooltimeline(m):
    """[cool-timeline …] -> línea de tiempo institucional a partir de los posts cool_timeline
    (recolectados en PASO 1). Ordena por fecha ascendente y muestra año + evento."""
    if not TIMELINE:
        return '\n\n'
    rows = sorted(TIMELINE, key=lambda t: t[0])
    cards = []
    for pd, ev, thumb in rows:
        img = ATTACH.get(thumb, '')
        fig = f'<figure><img src="{img}" alt="" loading="lazy"></figure>' if img else ''
        cards.append(f'<div class="tl-item"><div class="tl-year">{pd[:4]}</div>'
                     f'<div class="tl-body">{fig}<div class="tl-ev">{ev}</div></div></div>')
    return '\n\n<div class="timeline">' + ''.join(cards) + '</div>\n\n'

def _iconbox(m):
    """[thim-icon-box title=… read_more_link=… desc_content=…] (tema THIM) -> tarjeta .callout
    con título + descripción + botón. Sin esto quedaban en blanco páginas como Comités
    reglamentarios, Reglamentos orgánicos y Tutoriales SIM."""
    s = m.group(0)
    title = html.unescape(attr(s, 'title')).strip()
    link = attr(s, 'read_more_link').strip()
    desc = html.unescape(attr(s, 'desc_content')).strip()
    if not title and not link:
        return '\n\n'
    parts = []
    if title:
        parts.append(f'<strong>{title}</strong>')
    if desc:
        parts.append(f'<p>{desc}</p>')
    if link:
        tgt = ' target="_blank" rel="noopener"'
        if re.search(r'\.pdf($|[?#])', link, re.I):     # PDF -> chip documento (icono + buen contraste)
            parts.append(f'<p><a class="doc-pdf" href="{link}"{tgt}>{PDF_ICON}'
                         f'<span class="doc-txt">Ver documento</span></a></p>')
        else:                                            # otro enlace (p.ej. video replay) -> botón sólido
            parts.append(f'<p><a class="btn btn-oro" href="{link}"{tgt}>Abrir ›</a></p>')
    return '\n\n<div class="callout">' + ''.join(parts) + '</div>\n\n'

def _heading(m):
    """vc_custom_heading -> <hN> real (encabezado del tema, muy usado; sin esto se perdían
    encabezados en casi todo el sitio -> mala estructura y SEO)."""
    s = m.group(0)
    text = attr(s, 'text')
    if '%' in text and '<' not in text:
        try: text = unquote(text)
        except Exception: pass
    text = html.unescape(text).strip()
    if not text:
        return '\n\n'
    # El editor original a veces mete párrafos completos (con saltos de línea) en este
    # shortcode pensado para títulos cortos; si se renderiza como <hN> hereda el color/
    # tamaño de encabezado y el texto queda todo azul. Un título real no trae párrafos.
    if len(text) > 180 or '\n' in text:
        paras = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
        return '\n\n' + ''.join(f'<p>{p}</p>\n\n' for p in paras)
    tm = re.search(r'tag:(h[1-6])', attr(s, 'font_container'), re.I)
    tag = (tm.group(1).lower() if tm else 'h3')
    return f'\n\n<{tag}>{text}</{tag}>\n\n'

def _video(m):
    """vc_video -> iframe de YouTube (todos los videos del sitio son de YouTube)."""
    link = attr(m.group(0), 'link')
    yt = re.search(r'(?:youtu\.be/|youtube\.com/(?:embed/|watch\?v=|v/|shorts/))([\w-]{6,})', link)
    if not yt:
        return '\n\n'
    return (f'\n\n<div class="video-embed"><iframe src="https://www.youtube-nocookie.com/embed/{yt.group(1)}" '
            f'title="Video Uniremington" loading="lazy" allowfullscreen '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"></iframe></div>\n\n')

def _gmaps(m):
    """vc_gmaps -> iframe de Google Maps (el link va en base64(urlencode(iframe)) tras un
    prefijo #E-N_). Es el contenido principal de páginas como 'Cobertura Nacional'."""
    mm = re.search(r'#E-\d+_(.+)$', attr(m.group(0), 'link'))
    if not mm:
        return '\n\n'
    try:
        dec = unquote(base64.b64decode(mm.group(1)).decode('utf-8', 'ignore'))
    except Exception:
        return '\n\n'
    src = attr(dec, 'src')
    return f'\n\n<div class="map-embed"><iframe src="{src}" loading="lazy" title="Mapa"></iframe></div>\n\n' if src else '\n\n'

def _paired(m):
    tag, attrs, inner = m.group(1), m.group(2), m.group(3)
    if tag in ('vc_tta_section', 'vc_toggle'):
        title = html.unescape(attr(attrs, 'title'))
        return (f'\n\n<details class="acc"><summary>{title}</summary>'
                f'<div class="acc-body">{inner}</div></details>\n\n')
    if tag in ('vc_tta_accordion', 'vc_tta_tabs', 'vc_tta_tour', 'vc_tta_pageable'):
        return f'\n\n<div class="acc-group">{inner}</div>\n\n'
    if tag == 'vc_hoverbox':
        # tarjeta: imagen (frente) + título + descripción (reverso) y/o botón. Sin esto quedaban
        # en blanco páginas como Investigadores y Soy aspirante (el inner suele venir vacío).
        img = ATTACH.get(attr(attrs, 'image'), '')
        title = html.unescape(attr(attrs, 'primary_title')).strip()
        btn_t = html.unescape(attr(attrs, 'hover_btn_title')).strip()
        lm = re.search(r'url:([^|]+)', attr(attrs, 'hover_btn_link'))
        href = unquote(lm.group(1)) if lm else ''
        back = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', inner))).strip()
        parts = []
        if img:
            parts.append(f'<figure><img src="{img}" alt="{title}" loading="lazy"></figure>')
        if title:
            parts.append(f'<strong>{title}</strong>')
        if back:
            parts.append(f'<p>{back}</p>')
        if href and btn_t:
            parts.append(f'<p><a class="btn btn-oro" href="{href}" target="_blank" rel="noopener">{btn_t}</a></p>')
        return '\n\n<div class="hb-card">' + ''.join(parts) + '</div>\n\n' if parts else '\n\n'
    if tag in ('vc_cta', 'vc_message'):
        return f'\n\n<div class="callout">{inner}</div>\n\n'
    # vc_column_text, vc_row, vc_column, *_inner -> aplanar
    return '\n\n' + inner + '\n\n'

# Shortcodes con el_class="nomostrar" (o cualquier valor que lo contenga): el editor de
# WordPress los oculta con CSS sin borrarlos del contenido. Se descartan por completo, tanto
# los de PAR (con su contenido interno: filas/columnas/cajas ocultas) como los de una sola
# etiqueta (p.ej. [vc_btn ... el_class="nomostrar"], sin cierre).
# Confirmado contra producción en /investigacion/: un [vc_row_inner el_class="nomostrar"]
# envuelve 3 botones de "eventos" que NO aparecen en el sitio real (se ocultan en cascada
# igual que un [vc_btn] individual con la misma marca) -> el alcance amplio es correcto.
_NOMOSTRAR_PAIRED = re.compile(
    r'\[(\w[\w-]*)\b(?:[^\]])*?el_class="[^"]*nomostrar[^"]*"(?:[^\]])*?\]'
    r'(?:(?!\[/?\1\b)[\s\S])*?'
    r'\[/\1\]', re.I)
_NOMOSTRAR_SELF = re.compile(r'\[\w[\w-]*\b(?:[^\]])*?el_class="[^"]*nomostrar[^"]*"(?:[^\]])*?\]', re.I)
def strip_nomostrar(s):
    prev = None
    while prev != s:          # por si hay varios bloques ocultos consecutivos o anidados
        prev = s
        s = _NOMOSTRAR_PAIRED.sub('', s)
    return _NOMOSTRAR_SELF.sub('', s)

def clean_content(raw):
    if not raw:
        return ''
    s = raw
    _PENDING_NEWS.clear()   # una página no arrastra las noticias pendientes de otra
    # -1) Contenido marcado como oculto en el editor de WordPress (el_class contiene
    #     "nomostrar"): el tema lo esconde con CSS sin borrarlo del origen, así que el WXR
    #     lo trae completo aunque en producción NUNCA se vea. Se descarta ENTERO (shortcode
    #     de par: se quita también su contenido interno) para reflejar lo que de verdad se
    #     ve en el sitio real. Debe correr ANTES de cualquier otra conversión de shortcodes,
    #     mientras el atributo el_class todavía es visible como texto.
    s = strip_nomostrar(s)
    # 0) resolver popups (fichas de adopción, etc.) y decodificar los embeds base64
    #    de WPBakery (vc_raw_html/js -> micrositios aislados)
    s = POPUP_SC.sub(_resolve_popup, s)
    s = RAW_HTML.sub(_rawhtml, s)
    s = RAW_JS.sub('', s)
    # proteger el <style data-ms> de los micrositios (RAW_HTML ya lo dejó embebido en el
    # HTML) del resto de la limpieza: si el CSS trae selectores de atributo, p.ej.
    # [data-facultad="x"], el barrido genérico de shortcodes de más abajo (que busca
    # cualquier "[...]" suelto) los confunde con shortcodes y los destruye, dejando
    # "\n\n" sueltos que el auto-párrafo convierte en </p><p> DENTRO del CSS.
    _style_store = []
    def _protect_style(m):
        _style_store.append(m.group(0))
        return f'\x01{len(_style_store) - 1}\x01'
    s = re.sub(r'<style[^>]*data-ms[^>]*>.*?</style>', _protect_style, s, flags=re.S | re.I)
    # normalizar botones crudos del contenido a nuestro estilo (texto blanco, etc.)
    s = s.replace('class="unr-button"', 'class="btn btn-oro"')
    # quitar bloques <style>/<script> incrustados en el contenido (afectan el layout
    # global); se preservan los <style data-ms> ya aislados de los micrositios.
    s = re.sub(r'<style(?![^>]*data-ms)[^>]*>.*?</style>', '', s, flags=re.S | re.I)
    s = re.sub(r'<script[^>]*>.*?</script>', '', s, flags=re.S | re.I)
    # 1) proteger tablas (marcador sin corchetes)
    s = TABLE_SC.sub(lambda m: f'\x00{m.group(1)}\x00', s)
    # 2) shortcodes autocontenidos -> HTML
    s = re.sub(r'\[vc_single_image[^\]]*\]', _img, s)
    s = re.sub(r'\[vc_custom_heading[^\]]*\]', _heading, s)
    s = re.sub(r'\[vc_gmaps[^\]]*\]', _gmaps, s)
    s = re.sub(r'\[vc_video[^\]]*\]', _video, s)
    s = re.sub(r'\[vc_btn[^\]]*\]', _btn, s)
    s = re.sub(r'\[3d-flip-book[^\]]*\]', _flipbook, s)
    s = re.sub(r'\[cool-timeline[^\]]*\]', _cooltimeline, s)
    s = re.sub(r'\[/thim-icon-box\]', '', s, flags=re.I)          # cierre suelto del icon-box
    s = re.sub(r'\[thim-icon-box[^\]]*\]', _iconbox, s, flags=re.I)
    s = re.sub(r'\[vc_separator[^\]]*\]', '\n\n<hr>\n\n', s)
    s = re.sub(r'\[vc_empty_space[^\]]*\]', '\n\n', s)
    s = re.sub(r'\[vc_icon[^\]]*\]', '', s)
    # (opcional) <hr> y el <hN>Noticias Uniremington</hN> que suele preceder al grid ya
    # están convertidos a HTML normal a esta altura -se consumen aquí junto con el
    # shortcode para poder moverlos juntos al final; _basic_grid añade su propio
    # encabezado siempre, así que no importa si el original no traía uno (pasa en varias
    # facultades) ni si lo consume aquí (evita que quede duplicado).
    s = re.sub(r'(?:<hr>\s*)?(?:<(h[1-6])>\s*Noticias\s+Uniremington\s*</\1>\s*)?\[vc_basic_grid[^\]]*\]',
               _basic_grid, s, flags=re.I)
    # 3) shortcodes de par (de dentro hacia fuera) -> HTML semántico
    prev = None
    while prev != s:
        prev = s
        s = PAIRED.sub(_paired, s)
    # encabezados huérfanos de grids dinámicos (vc_basic_grid) que no se pueden reproducir
    s = re.sub(r'<(h[1-6])>[^<]*</\1>\s*(?=\[vc_basic_grid)', '', s)
    # 4) barrer cualquier shortcode restante
    s = SHORTCODE.sub('\n\n', s)
    # 5) restaurar las tablas de TablePress
    s = PLACEHOLDER.sub(lambda m: '\n\n' + TABLES.get(m.group(1), '') + '\n\n', s)
    # quitar colores blancos inline (venían de hoverboxes sobre fondo oscuro; en el
    # callout/fondo claro serían texto invisible)
    s = re.sub(r'color:\s*(?:#f{3}(?:f{3})?|white)\s*;?', '', s, flags=re.I)
    # quitar estilos inline de color en los encabezados (los controla el CSS del sitio)
    s = re.sub(r'(<h[1-6])\s+style="[^"]*"', r'\1', s)
    s = MULTINL.sub('\n\n', s)
    # restaurar el <style data-ms> protegido arriba: debe ir DESPUÉS de las limpiezas de
    # texto de este paso (si no, "color: white" del CSS cae en el barrido de arriba) y
    # ANTES del auto-párrafo (si no, el marcador -que no empieza por "<"- se envuelve en
    # <p></p> como si fuera texto suelto).
    s = re.sub(r'\x01(\d+)\x01', lambda m: _style_store[int(m.group(1))], s)
    # 6) auto-párrafo para bloques de texto plano; respeta el HTML existente
    out = []
    for block in s.split('\n\n'):
        b = block.strip()
        if not b:
            continue
        out.append(b if b[0] == '<' else '<p>' + b.replace('\n', '<br>') + '</p>')
    result = '\n'.join(out).strip()
    # 7) barrer párrafos vacíos residuales (p.ej. envoltorios de popups resueltos)
    result = re.sub(r'<p[^>]*>\s*(?:&nbsp;|<br\s*/?>|\s)*</p>', '', result, flags=re.I)
    # 8) mover "Noticias Uniremington" (si la hay) al final de la página: el marcador de
    # _basic_grid pudo quedar envuelto en <p> por el auto-párrafo (paso 6) al no empezar
    # por "<".
    news_html = []
    def _collect_news(mm):
        news_html.append(_PENDING_NEWS[int(mm.group(1))])
        return ''
    result = re.sub(r'(?:<p[^>]*>\s*)?\x03(\d+)\x03(?:\s*</p>)?', _collect_news, result)
    if news_html:
        result = result.strip() + '\n' + ''.join(news_html)
    return result.strip()

# ------------------------------------------------------------------ utilidades
def segs_of(link):
    return [x for x in urlparse(link).path.strip('/').split('/') if x]

def slug_from_link(link, pid):
    s = segs_of(link)
    return s[-1] if s else f"item-{pid}"

def path_from_link(link):
    if not link:
        return ''
    p = urlparse(link)
    return p.path if SITE in p.netloc or not p.netloc else link

def modalidad(title):
    t = title.lower()
    if 'virtual' in t: return 'Virtual'
    if 'distancia' in t: return 'Distancia'
    if 'presencial' in t: return 'Presencial'
    return ''

def nivel(title):
    t = title.lower()
    if re.search(r'especializaci|maestr[ií]a|doctorado|posgrado', t): return 'Posgrado'
    if re.search(r'tecnolog|t[eé]cnic', t): return 'Tecnología'
    return 'Pregrado'

def first_php_string(s):
    """Extrae el primer string de un array PHP serializado de ACF."""
    m = re.search(r's:\d+:"([^"]*)"', s or '')
    return m.group(1) if m else ''

def all_php_strings(s):
    """Extrae todos los strings de un array PHP serializado de ACF."""
    return [html.unescape(x) for x in re.findall(r's:\d+:"([^"]*)"', s or '')]

def build_ficha(meta):
    """Ficha técnica del programa desde los campos ACF."""
    snies_raw = (meta.get('snies') or '').strip()
    mnum = re.search(r'(\d{4,})', snies_raw)
    pdf = (meta.get('enlace_pdf_resolucion') or '').strip()
    insc = (meta.get('url_inscripciones') or '').strip()
    reso = html.unescape((meta.get('resolucion') or '').strip())
    reso = re.sub(r'(Resoluci[oó]n)\s+(Resoluci[oó]n)', r'\1', reso, flags=re.I)  # quita duplicado
    return {
        'snies': mnum.group(1) if mnum else '',
        'resolucion': reso,
        'resolucion_pdf': pdf if pdf.startswith('http') else '',
        'registro_unico': html.unescape((meta.get('registro_unico') or '').strip()),
        'duracion': html.unescape((meta.get('cant_de_semestres') or '').strip()),
        'inscripcion': insc if insc.startswith('http') else '',
        'banner_id': (meta.get('banner-general') or '').strip(),
        'banner': '',
    }

WANT = {'page', 'post', 'tribe_events'}
buckets = {k: [] for k in WANT}
slugs = Counter()
menu_items = []
CAT_NAME_BY_ID = {}   # wp:term_id -> nombre de categoría (para resolver [vc_basic_grid taxonomies="N"])

# ----------------------------------------------- PASO 1: recorrido único
_fh = open(XML, 'rb')
_fh.seek(_fh.read(16).index(b'<?xml'))
for _ev, el in ET.iterparse(_fh, events=('end',)):
    if el.tag == '{%s}category' % NS['wp']:
        tid = txt(el, 'wp:term_id').strip()
        name = txt(el, 'wp:cat_name').strip()
        if tid and name:
            CAT_NAME_BY_ID[tid] = html.unescape(name)
        el.clear(); continue
    if el.tag != 'item':
        continue
    ptype = txt(el, 'wp:post_type').strip()

    if ptype == 'attachment':
        ATTACH[txt(el, 'wp:post_id').strip()] = txt(el, 'wp:attachment_url').strip()
        el.clear(); continue

    if ptype == 'aoc_popup':
        AOC_POPUPS[txt(el, 'wp:post_id').strip()] = txt(el, 'content:encoded') or ''
        el.clear(); continue

    if ptype == 'cool_timeline':
        # cada entrada = año (post_date) + evento (título); el contenido suele estar vacío
        pd = txt(el, 'wp:post_date').strip()
        ev = html.unescape(txt(el, 'title').strip())
        thumb = ''
        for pm in el.findall('wp:postmeta', NS):
            if txt(pm, 'wp:meta_key') == '_thumbnail_id':
                thumb = txt(pm, 'wp:meta_value').strip()
                break
        if ev and pd[:4].isdigit():
            TIMELINE.append((pd, ev, thumb))
        el.clear(); continue

    if ptype == '3d-flip-book':
        # el PDF vive en el meta serializado 3dfb_data como s:4:"guid";s:NN:"URL"
        data = ''
        for pm in el.findall('wp:postmeta', NS):
            if txt(pm, 'wp:meta_key') == '3dfb_data':
                data = txt(pm, 'wp:meta_value') or ''
                break
        g = re.search(r's:4:"guid";s:\d+:"(.*?)"', data)
        if g:
            FLIPBOOKS[txt(el, 'wp:post_id').strip()] = html.unescape(g.group(1))
        el.clear(); continue

    if ptype == 'tablepress_table':
        meta = {txt(pm, 'wp:meta_key'): txt(pm, 'wp:meta_value')
                for pm in el.findall('wp:postmeta', NS)}
        tid = meta.get('_tablepress_export_table_id')
        try:
            rows = json.loads(txt(el, 'content:encoded') or '[]')
        except Exception:
            rows = []
        opts = {}
        try:
            opts = json.loads(meta.get('_tablepress_table_options', '{}'))
        except Exception:
            pass
        html_tbl = render_table(rows, opts.get('table_head', True),
                                opts.get('table_foot', False)) if rows else ''
        if tid and html_tbl:
            TABLES[str(tid)] = html_tbl
        ttl = _norm_title(txt(el, 'title'))
        if ttl and html_tbl:
            TABLES_BY_TITLE.setdefault(ttl, html_tbl)
        el.clear(); continue

    if ptype == 'nav_menu_item':
        meta = {txt(pm, 'wp:meta_key'): txt(pm, 'wp:meta_value')
                for pm in el.findall('wp:postmeta', NS)}
        nav_menu = ''
        for c in el.findall('category'):
            if c.get('domain') == 'nav_menu':
                nav_menu = c.get('nicename') or ''
        menu_items.append({
            'id': txt(el, 'wp:post_id'), 'title': html.unescape(txt(el, 'title').strip()),
            'order': int(txt(el, 'wp:menu_order') or 0),
            'parent': meta.get('_menu_item_menu_item_parent', '0'),
            'object_id': meta.get('_menu_item_object_id', ''),
            'url': meta.get('_menu_item_url', ''), 'type': meta.get('_menu_item_object', ''),
            'menu': nav_menu,
        })
        el.clear(); continue

    if ptype not in WANT:
        el.clear(); continue

    link = txt(el, 'link').strip()
    pid = txt(el, 'wp:post_id').strip()
    sg = segs_of(link)
    # Está bajo una facultad, pero eso NO basta: hay boletines, consultorios,
    # clínicas, observatorios, etc. Un programa REAL tiene el grupo de campos
    # ACF (snies/ciudad/resolucion/modalidad). Esa es la señal definitiva.
    under_fac = (ptype == 'page' and len(sg) >= 3 and sg[0] == 'facultades')
    slug = slug_from_link(link, pid)
    slugs[slug] += 1
    title = html.unescape(txt(el, 'title').strip())
    cats = [c.text for c in el.findall('category', NS)
            if c.get('domain') == 'category' and c.text]
    ficha, mod, sedes, is_prog = {}, '', [], False
    if under_fac:
        pmeta = {txt(pm, 'wp:meta_key'): txt(pm, 'wp:meta_value')
                 for pm in el.findall('wp:postmeta', NS)}
        PROG_KEYS = ('snies', 'resolucion', 'ciudad', 'modalidad')
        is_prog = any(k in pmeta for k in PROG_KEYS)
        if is_prog:
            ficha = build_ficha(pmeta)
            mod = first_php_string(pmeta.get('modalidad', '')) or modalidad(title)
            sedes = sedes_validas(all_php_strings(pmeta.get('ciudad', '')))
    buckets[ptype].append({
        'id': pid, 'type': ptype, 'status': txt(el, 'wp:status').strip(),
        'title': title, 'slug': slug, 'orig_path': path_from_link(link),
        'date': txt(el, 'wp:post_date').strip(), 'author': txt(el, 'dc:creator').strip(),
        'parent': txt(el, 'wp:post_parent').strip(),
        'menu_order': int(txt(el, 'wp:menu_order') or 0),
        'is_program': is_prog,
        'facultad_slug': sg[1] if is_prog else '',
        'modalidad': mod,
        'nivel': nivel(title) if is_prog else '',
        'sedes': sedes,
        'ficha': ficha,
        '_raw': txt(el, 'content:encoded'),
        '_raw_excerpt': txt(el, 'excerpt:encoded'),
        'categories': cats,
    })
    el.clear()

# Índice liviano de posts (título/url/fecha/categorías/imagen/resumen) para poder
# reproducir en frío los grids dinámicos [vc_basic_grid post_type="post" ...]
# ("Noticias Uniremington" y similares). Se construye ANTES del paso 2 porque este
# usa r['_raw'], que el paso 2 va consumiendo (r.pop) a medida que limpia cada item.
POST_INDEX = []
_PENDING_NEWS = []   # bloques de "Noticias Uniremington" pendientes de mover al final (por página)
for _r in buckets.get('post', []):
    if _r.get('status') != 'publish':
        continue
    _raw = _r.get('_raw') or ''
    _img_url = ''
    _img_m = re.search(r'<img[^>]+src="([^"]+)"', _raw)
    if _img_m:
        _img_url = _img_m.group(1)
    else:
        _vc_m = re.search(r'\[vc_single_image\s+image="(\d+)"', _raw)
        if _vc_m:
            _img_url = ATTACH.get(_vc_m.group(1), '')
    _exc = html.unescape((_r.get('_raw_excerpt') or '').strip())
    _exc = re.sub(r'<[^>]+>', ' ', _exc)
    _exc = re.sub(r'\s+', ' ', _exc).strip()
    if not _exc:
        _body = html.unescape(re.sub(r'<[^>]+>', ' ', _raw))
        _body = re.sub(r'\s+', ' ', _body).strip()
        _exc = (_body[:160].rsplit(' ', 1)[0] + '…') if len(_body) > 160 else _body
    POST_INDEX.append({
        'title': _r['title'], 'url': _r['orig_path'], 'date': _r['date'],
        'categories': _r.get('categories') or [],
        'img': _img_url, 'excerpt': _exc,
    })
POST_INDEX.sort(key=lambda p: p['date'], reverse=True)

def _basic_grid(m):
    tag = m.group(0)
    pt = attr(tag, 'post_type')
    if pt != 'post':
        return tag                      # tribe_events u otros: sin soporte, se barre después
    try:
        n = int(attr(tag, 'max_items') or '4')
    except ValueError:
        n = 4
    taxo = attr(tag, 'taxonomies')
    names = None
    if taxo:
        names = {CAT_NAME_BY_ID[t.strip()] for t in taxo.split(',')
                 if t.strip() in CAT_NAME_BY_ID}
    items = [p for p in POST_INDEX if not names or (set(p['categories']) & names)][:n]
    if not items:
        return ''
    cards = []
    for p in items:
        media = (f'<img src="{p["img"]}" alt="{p["title"]}" loading="lazy" decoding="async">'
                  if p['img'] else '<span class="ph"><span class="msi">newspaper</span></span>')
        cards.append(f'<a class="post" href="{p["url"]}">'
                     f'<span class="post-media">{media}</span>'
                     f'<span class="bd"><h3>{p["title"]}</h3><p>{p["excerpt"]}</p>'
                     f'<span class="go">Leer más <span class="msi">arrow_forward</span></span>'
                     f'</span></a>')
    grid = ('<hr>\n<h2>Noticias Uniremington</h2>\n<div class="news">' + ''.join(cards) + '</div>')
    # "Noticias Uniremington" debe quedar siempre al FINAL de la página, sin importar en
    # qué punto del HTML de WordPress cayera el shortcode originalmente (a veces aparecía
    # en medio del contenido, entre videos institucionales y la oferta de programas). Se
    # devuelve un marcador y se traslada al final de clean_content() (ver más abajo).
    _PENDING_NEWS.append(grid)
    return f'\x03{len(_PENDING_NEWS) - 1}\x03'

# ----------------------------------------------- PASO 2: limpiar contenido
for typ, items in buckets.items():
    for r in items:
        raw = r.pop('_raw')
        r['raw_chars'] = len(raw)
        r['content_html'] = clean_content(raw)
        r['excerpt'] = html.unescape(clean_content(r.pop('_raw_excerpt')))
        r['clean_chars'] = len(r['content_html'])
        # resolver el banner del programa (ID de adjunto -> URL) ahora que ATTACH está completo
        f = r.get('ficha')
        if f and f.get('banner_id'):
            f['banner'] = ATTACH.get(f['banner_id'], '')
        # extraer el "Título otorgado" del contenido para destacarlo en la ficha
        if f and r.get('is_program'):
            m = re.search(r'[Tt][íi]tulo\s+[Oo]torgado\s*</h[1-6]>\s*(?:<p>)?\s*([^<\n]{2,90})',
                          r['content_html'])
            if m:
                f['titulo'] = m.group(1).strip().strip('.').strip()

# desambiguar slugs duplicados (los programas conservan prioridad de slug limpio)
# desambiguar slugs: programas primero, luego los PUBLICADOS (nunca un borrador se
# queda con el slug limpio de una página publicada).
buckets['page'].sort(key=lambda r: (not r['is_program'], r['status'] != 'publish'))
seen = {}
for typ in ('page', 'post', 'tribe_events'):
    for r in buckets[typ]:
        s = r['slug']
        if s in seen:
            seen[s] += 1
            r['slug'] = f"{s}-{r['id']}"
        else:
            seen[s] = 1

# ---- PASO 3: reescribir enlaces internos rotos (a borradores/privados) ----
def _norm_href_path(href):
    p = urlparse(href).path
    try:
        p = unquote(p)
    except Exception:
        pass
    if not p:
        return ''
    if not p.startswith('/'):
        p = '/' + p
    if len(p) > 1 and not p.endswith('/'):
        p += '/'
    return p

valid_paths = set()
for typ, items in buckets.items():
    for r in items:
        if r['status'] == 'publish' and r['orig_path']:
            valid_paths.add(_norm_href_path(r['orig_path']))
fac_slugs = {r['facultad_slug'] for r in buckets['page']
             if r.get('is_program') and r['status'] == 'publish' and r['facultad_slug']}

# ---------------- Menú Principal (del backup) como árbol anidado ----------------
# Reproduce el menú "menu-principal" del WordPress original. Cada ítem enlaza a la
# página LOCAL si ya está migrada; si no, al dominio de producción (según lo pedido).
PROD = 'https://www.uniremington.edu.co'
_post_by_id = {r['id']: r for typ, its in buckets.items() for r in its if r['status'] == 'publish'}

def _menu_target(it):
    raw = (it.get('url') or '').strip()
    if not raw or raw == '#':
        p = _post_by_id.get(it.get('object_id'))
        raw = (PROD + p['orig_path']) if (p and p.get('orig_path')) else ''
    return raw

# Renombrados de etiquetas del menú: el título original del WordPress no siempre es
# el deseado (p.ej. "EMPLEADO UNIREMINGTON" se muestra como "ADMINISTRATIVO").
_MENU_LABEL_OVERRIDE = {
    'EMPLEADO UNIREMINGTON': 'ADMINISTRATIVO',
}

def _menu_label(it):
    if it['title']:
        raw = it['title']
    else:
        p = _post_by_id.get(it.get('object_id'))
        raw = (p['title'] if p else '').strip()
    return _MENU_LABEL_OVERRIDE.get(raw.strip().upper(), raw)

def _resolve_href(raw):
    """(href, external). Local si la página existe; si no, producción/externo."""
    if not raw:
        return PROD + '/', True
    m = re.match(r'^https?://([^/]+)(/.*)?$', raw, re.I)
    if m:
        host, path = m.group(1).lower(), (m.group(2) or '/')
        if not re.match(r'^(www\.)?uniremington\.edu\.co$', host):
            return raw, True                    # otro host (virtual., class., servicios...)
        if '?' in raw or '#' in path:           # ?page_id=, anclas -> producción tal cual
            return raw, True
    else:
        path = raw if raw.startswith('/') else '/' + raw
    path = _norm_href_path(path)
    segs = [s for s in path.strip('/').split('/') if s]
    if len(segs) == 2 and segs[0] == 'facultades' and segs[1] in fac_slugs:
        return '/facultad/' + segs[1], False    # facultad -> página local propia
    if path in valid_paths:
        return path, False                      # página migrada -> enlace local
    return (raw if m else PROD + path), True     # no migrada -> producción

_COLUMNA = re.compile(r'^columna\s*\d+$', re.I)

def build_menu(slug):
    its = [i for i in menu_items if i.get('menu') == slug]
    by_parent = {}
    for i in its:
        by_parent.setdefault(i['parent'], []).append(i)
    for lst in by_parent.values():
        lst.sort(key=lambda x: x['order'])

    def kids(it):
        out = []
        for ch in by_parent.get(it['id'], []):
            if _COLUMNA.match(ch['title'] or ''):        # aplanar columnas del mega-menú
                out.extend(node(g) for g in by_parent.get(ch['id'], []))
            else:
                out.append(node(ch))
        return out

    def node(it):
        href, ext = _resolve_href(_menu_target(it))
        return {'label': _menu_label(it), 'href': href, 'external': ext, 'children': kids(it)}

    top, seen = [], set()
    for it in by_parent.get('0', []):
        key = (it['title'] or '').strip().upper()
        if key in seen:                          # descarta duplicados de 1er nivel (2ª SEDES/ÁREAS)
            continue
        seen.add(key)
        top.append(node(it))
    return top

menu_principal = build_menu('menu-principal')

# --- auto-enlazar menciones "nuestro programa de {X}" a programas publicados ---
def _norm_name(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]', ' ', s)).strip()

def _core_term(title):
    t = re.sub(r'\s*[-–]\s*(Presencial|Virtual|Distancia).*$', '', title or '', flags=re.I)
    t = re.sub(r'^(Especializaci[oó]n|Maestr[ií]a|Doctorado|Tecnolog[ií]a|T[eé]cnico(?:\s+laboral)?|T[eé]cnica)\s+(en\s+|profesional\s+en\s+)?',
               '', t, flags=re.I)
    return t.strip()

PROG_BY_NAME = {}
for r in buckets['page']:
    if r.get('is_program') and r['status'] == 'publish' and r['orig_path']:
        url = _norm_href_path(r['orig_path'])
        for name in (r['title'], _core_term(r['title'])):
            k = _norm_name(name)
            if len(k) >= 6:
                PROG_BY_NAME.setdefault(k, url)

CONOCE = re.compile(
    r'(nuestr[oa]\s+(?:programa|tecnolog[ií]a|especializaci[oó]n|maestr[ií]a)\s+(?:de|en)\s+)'
    r'([A-ZÁÉÍÓÚÑ][^.<]{2,70})', re.I)
_conoce_count = [0]

def _link_conoce(m):
    lead, name = m.group(1), m.group(2).rstrip()
    url = PROG_BY_NAME.get(_norm_name(name))
    if url:
        _conoce_count[0] += 1
        return f'{lead}<a href="{url}">{name}</a>'
    return m.group(0)

# Convierte líneas sueltas del tipo "<strong>Etiqueta:</strong> texto" (perfil
# ocupacional, roles...) en una lista, para que no se fundan en un párrafo corrido.
ROLES = re.compile(r'(?:^|\n)((?:[ \t]*<strong>[^<]{2,55}:</strong>[^\n]*(?:\n|$)){2,})', re.M)

def group_roles(html_text):
    def repl(m):
        items = [ln.strip() for ln in m.group(1).split('\n') if ln.strip()]
        return '\n<ul>' + ''.join(f'<li>{it}</li>' for it in items) + '</ul>\n'
    return ROLES.sub(repl, html_text)

def _bullets_conv_p(m):
    inner = m.group(1)
    if not re.search(r'[•▪●]', inner):
        return m.group(0)
    parts = re.split(r'<br\s*/?>', inner)
    lis, pre = [], []
    for part in parts:
        t = part.strip()
        if not t:
            continue
        if re.match(r'^[•▪●]', t):
            lis.append(re.sub(r'^[•▪●]\s*', '', t).strip())
        elif not lis:
            pre.append(t)
        else:
            lis.append(t)                          # continuación de la viñeta anterior
    if not lis:
        return m.group(0)
    out = ('<p>' + ' '.join(pre) + '</p>') if pre else ''
    return out + '<ul>' + ''.join(f'<li>{x}</li>' for x in lis if x) + '</ul>'

# Familia de acordeones personalizados del sitio (todos con onclick a un JS que se
# elimina): acordeon-item/-boton/-contenido y funcionalidad-item/-boton/-contenido.
# Se admiten clases extra (p.ej. "acordeon-boton activo") y atributos (style, aria...).
ACORDEON = re.compile(
    r'<div[^>]*class="[^"]*\b(?:acordeon|funcionalidad)-item\b[^"]*"[^>]*>\s*'
    r'<button([^>]*\bclass="[^"]*\b(?:acordeon|funcionalidad)-boton\b[^"]*"[^>]*)>([\s\S]*?)</button>\s*'
    r'<div([^>]*\bclass="[^"]*\b(?:acordeon|funcionalidad)-contenido\b[^"]*"[^>]*)>([\s\S]*?)</div>\s*</div>',
    re.I)

# Imágenes decorativas "separador" genéricas del tema de WordPress (barras vacías, sin
# información). Se eliminan para un diseño limpio. Se conservan las "separador_nombre"
# (banners temáticos que pueden llevar texto). También limpia párrafos/figuras vacías.
_SEP = r'separador(?:es)?(?:-generico)?-?\d*\.(?:jpe?g|png|webp|gif)'
DECOR_FIG = re.compile(r'<figure[^>]*>\s*(?:<a[^>]*>\s*)?<img[^>]*\bsrc="[^"]*' + _SEP +
                       r'[^"]*"[^>]*>\s*(?:</a>\s*)?(?:<figcaption[^>]*>[\s\S]*?</figcaption>\s*)?</figure>', re.I)
DECOR_IMG = re.compile(r'<img[^>]*\bsrc="[^"]*' + _SEP + r'[^"]*"[^>]*>', re.I)

_BLOCK_RE = re.compile(r'(<(?:div|details|section|article|figure|span|table)\b[^>]*?>|</(?:div|details|section|article|figure|span|table)>)', re.I)
def balance_html(html_text):
    """Rebalancea el anidamiento de contenedores de bloque para que HTML mal formado
    (por el aplanado de WPBakery) NO cierre los contenedores del layout de la página.
    Descarta cierres huérfanos, autocierra mal-anidamientos (como el navegador) y cierra
    los que queden abiertos. No-op en páginas bien formadas."""
    stack, out = [], []
    for part in _BLOCK_RE.split(html_text):
        if not part:
            continue
        # Solo los DELIMITADORES (etiquetas de bloque de _BLOCK_RE) se tratan como apertura/cierre.
        # Los demás segmentos son TEXTO (pueden empezar con <h2>, </h2>… o texto suelto tras un
        # heading) y deben pasar intactos: antes se empujaba cualquier <tag> a la pila y un
        # segmento "</h2>texto" se descartaba como cierre huérfano, borrando el texto que seguía.
        if not _BLOCK_RE.fullmatch(part):
            out.append(part)
            continue
        if part.startswith('</'):
            tag = part[2:-1].lower()
            if tag in stack:
                while stack:
                    t = stack.pop()
                    out.append('</%s>' % t)
                    if t == tag:
                        break
            # cierre huérfano de bloque: se descarta
        else:
            stack.append(re.match(r'<(\w+)\b', part).group(1).lower())
            out.append(part)
    while stack:
        out.append('</%s>' % stack.pop())
    return ''.join(out)

# Componente global para enlaces a PDF: chip con icono. PDF_ICON y pdf_viewer se definen
# más arriba (los usa clean_content vía _flipbook). Aquí solo el componente de enlaces.
def pdf_component(html_text):
    # 1) quitar el icono-imagen viejo de descarga y las anclas/figuras que queden vacías
    html_text = re.sub(r'<img[^>]*descargar-archivo[^>]*>', '', html_text, flags=re.I)
    html_text = re.sub(r'<a\b[^>]*>\s*</a>', '', html_text, flags=re.I)
    html_text = re.sub(r'<figure[^>]*>\s*(?:</a>\s*)*</figure>', '', html_text, flags=re.I)
    # 2) enlaces a PDF con texto -> chip .doc-pdf con icono
    def mark(m):
        href, inner = m.group(1), m.group(2)
        open_tag = m.group(0).split('>', 1)[0]
        if re.search(r'class="[^"]*\b(btn|ql-link)\b', open_tag):
            return m.group(0)                    # ya es botón o quick-link: dejar igual
        if '<img' in inner.lower():
            return m.group(0)                    # enlaces-imagen (miniaturas): no tocar
        label = re.sub(r'<[^>]+>', ' ', inner)
        label = re.sub(r'\s+', ' ', label).strip()
        if not label:
            return m.group(0)
        return (f'<a class="doc-pdf" href="{href}" target="_blank" rel="noopener">'
                f'{PDF_ICON}<span class="doc-txt">{label}</span></a>')
    html_text = re.sub(r'<a\s+[^>]*?href="([^"]*\.pdf[^"]*)"[^>]*>([\s\S]*?)</a>', mark, html_text, flags=re.I)
    # 3) desenvolver encabezados que solo contenían el enlace (eran heading solo por el PDF)
    html_text = re.sub(r'<(h[1-6])>\s*(<a class="doc-pdf"[\s\S]*?</a>)\s*</\1>', r'\2', html_text, flags=re.I)
    return html_text

def fix_pdf_iframe(html_text):
    """Los PDF embebidos de WordPress quedan mangleados al aplanarse: el <iframe src="...pdf">
    se parte con un </p>, el </iframe> queda huérfano más abajo, y en medio aparece el texto de
    respaldo 'ERROR: An iframe should be displayed here…' como texto VISIBLE. Además el wrapper
    no trae altura → el visor colapsa a ~150px. Se recompone en un visor limpio con altura usable
    y un enlace 'Abrir/Descargar PDF' de respaldo."""
    if 'An iframe should be displayed' not in html_text and '.pdf"' not in html_text:
        return html_text
    # 1) quitar el párrafo de respaldo 'ERROR: An iframe…try again.'
    html_text = re.sub(r'<p[^>]*>\s*<em>\s*<strong>\s*ERROR:[\s\S]*?</p>', '', html_text, flags=re.I)
    # 1b) red de seguridad: cualquier resto del texto de respaldo suelto
    html_text = re.sub(r'(?:<em>|<strong>|<br\s*/?>|\s)*ERROR:\s*(?:</strong>)?\s*(?:<br\s*/?>)?\s*'
                       r'An iframe should be displayed here[\s\S]*?(?:try again\.?|support iframes\.?)(?:\s*</em>)?'
                       r'(?:\s*Please update your browser[^<]*)?', '', html_text, flags=re.I)
    # 2) recomponer el iframe de PDF partido: <iframe ...pdf...></p> … <p></iframe>  →  visor limpio.
    #    El lookahead (?![^>]*pdf-frame) evita re-procesar visores ya limpios (p.ej. de _flipbook).
    _viewer = lambda m: pdf_viewer(m.group(1)).strip()
    # captura desde <iframe src="X.pdf"> hasta el </iframe> (que quedó huérfano tras el texto)
    html_text = re.sub(r'<iframe\b(?![^>]*pdf-frame)[^>]*\bsrc="([^"]+\.pdf[^"]*)"[^>]*>[\s\S]*?</iframe>', _viewer, html_text, flags=re.I)
    # 2b) iframe de PDF que quedó SIN </iframe> (solo apertura): cerrarlo como visor
    html_text = re.sub(r'<iframe\b(?![^>]*pdf-frame)[^>]*\bsrc="([^"]+\.pdf[^"]*)"[^>]*>(?![\s\S]*?</iframe>)', _viewer, html_text, flags=re.I)
    return html_text

# Colapsa callouts consecutivos con el mismo texto (p.ej. grids de hoverboxes que
# repiten el mismo enlace/slogan al aplanarse). Compara por texto normalizado.
CALLOUT_SPLIT = re.compile(r'(<div class="callout">(?:(?!<div)[\s\S])*?</div>)')
def _plain(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', s)).strip().lower()
def dedupe_callouts(html_text):
    out, last = [], None
    for part in CALLOUT_SPLIT.split(html_text):
        if part.startswith('<div class="callout">'):
            t = _plain(part)
            if t and t == last:
                continue                         # callout duplicado consecutivo
            last = t
            out.append(part)
        else:
            if _plain(part):                     # texto real entre medias -> corta la racha
                last = None
            out.append(part)
    return ''.join(out)

def strip_grupo_semilleros(html_text):
    """En las páginas de GRUPO de investigación (visor GrupLAC), elimina el bloque de la
    ficha del semillero SERES avalado (logo + coordinador/asesores): es información antigua
    y desactualizada, duplicada en varios grupos. El bloque puede estar al inicio, en medio
    o al final, con o sin encabezado "Semilleros", así que se identifica por la figura del
    logo SERES (o el `<strong>Seres. Semillero…`) y un encabezado "Semilleros" opcional, y se
    corta SOLO hasta el siguiente micrositio o sección (nunca hasta el final: preserva el visor)."""
    fig = (re.search(r'<figure>\s*<img[^>]*logo-seres[\s\S]*?</figure>', html_text, flags=re.I)
           or re.search(r'<strong>\s*Seres\.\s*Semillero', html_text, flags=re.I))
    hd = re.search(r'<h[1-3][^>]*>(?:\s|&nbsp;)*Semilleros?(?:\s|&nbsp;)*</h[1-3]>', html_text, flags=re.I)
    if not fig and not hd:
        return html_text
    start = hd.start() if hd else fig.start()
    if not hd and fig:                                       # absorber un <p> de apertura anterior a la figura
        pm = re.search(r'<p>\s*$', html_text[:start])
        if pm:
            start = pm.start()
    after = fig.end() if fig else hd.end()
    nxt = re.search(r'<div class="ms"|<h[1-3]\b', html_text[after:])
    end = after + nxt.start() if nxt else len(html_text)
    cleaned = html_text[:start] + html_text[end:]
    return re.sub(r'<p>\s*</p>', '', cleaned)                # limpia un <p> vacío que quede

def strip_decor(html_text):
    html_text = re.sub(r'<!--[\s\S]*?-->', '', html_text)       # comentarios HTML (instrucciones WP, etc.)
    # texto de relleno por defecto de WPBakery que nunca reemplazaron (Lorem ipsum)
    html_text = re.sub(r'<p[^>]*>\s*(?:Soy un bloque de texto|Lorem ipsum)[\s\S]*?</p>', '', html_text, flags=re.I)
    # <br> incrustado a mano dentro de un encabezado (salto de línea fijo del editor de WP):
    # se quita para que el título fluya como texto normal y el navegador ajuste el salto según
    # el ancho real (evita cortes feos como "Nuestra oferta" / "académica - Ipiales").
    def _dehyphen_heading(m):
        return re.sub(r'\s*<br\s*/?>\s*', ' ', m.group(0))
    html_text = re.sub(r'<h[1-6]\b[^>]*>(?:(?!</h[1-6]>)[\s\S])*?</h[1-6]>', _dehyphen_heading, html_text, flags=re.I)
    html_text = re.sub(r'</iframe>\s*</iframe>', '</iframe>', html_text)   # </iframe> duplicado
    # quitar estilos inline de las tablas (colores/bordes de WP): que las controle el CSS del sitio
    html_text = re.sub(r'(<(?:table|thead|tbody|tfoot|tr|th|td)\b[^>]*?)\s+style="[^"]*"',
                       r'\1', html_text, flags=re.I)
    html_text = DECOR_FIG.sub('', html_text)
    html_text = DECOR_IMG.sub('', html_text)
    html_text = re.sub(r'<p>\s*(?:&nbsp;|<br\s*/?>|\s)*</p>', '', html_text, flags=re.I)
    html_text = re.sub(r'<(h[1-6])>\s*</\1>', '', html_text)      # encabezados vacíos
    # callouts vacíos: cajas con borde izquierdo sin contenido (artefactos de la extracción)
    html_text = re.sub(r'<div class="callout"[^>]*>(?:\s|&nbsp;|<br\s*/?>)*</div>', '', html_text, flags=re.I)
    # cierres dobles malformados del backup: </strong></strong> -> </strong> (idem </em>, </b>)
    for _ in range(3):
        new = re.sub(r'</(strong|em|b|i)>\s*</\1>', r'</\1>', html_text, flags=re.I)
        if new == html_text:
            break
        html_text = new
    return html_text

# Videos -> cuadrícula responsiva .video-grid (diseño profesional cuando hay varios).
# Dos casos: (a) parejas "encabezado + video" repetidas -> tarjetas con título; (b) varios
# videos pelados seguidos -> grilla simple. Un video suelto se queda centrado.
# un "capítulo": encabezado (sin cruzar otros bloques) + video + (opcional) botón PDF
_VHEAD = r'<h[1-6][^>]*>(?:(?!</?h[1-6]|<div|<p\b)[\s\S])*?</h[1-6]>'
_VCHAP = _VHEAD + r'\s*<div class="video-embed">[\s\S]*?</div>\s*(?:<p>\s*<a[^>]*\bbtn\b[^>]*>[\s\S]*?</a>\s*</p>\s*)?'
VIDEO_TITLED = re.compile(r'(?:\s*' + _VCHAP + r'){2,}')
VIDEO_GROUP = re.compile(r'(?:\s*<div class="video-embed">[\s\S]*?</div>\s*){2,}')
def group_videos(html_text):
    def repl_titled(m):
        pairs = re.findall(r'<h[1-6][^>]*>((?:(?!</?h[1-6]|<div|<p\b)[\s\S])*?)</h[1-6]>\s*'
                           r'(<div class="video-embed">[\s\S]*?</div>)'
                           r'\s*(<p>\s*<a[^>]*\bbtn\b[^>]*>[\s\S]*?</a>\s*</p>)?', m.group(0))
        cards = ''.join(f'<div class="video-card"><h4 class="video-title">{re.sub(r"<[^>]+>","",t).strip()}'
                        f'</h4>{v}{btn or ""}</div>' for t, v, btn in pairs)
        return '\n<div class="video-grid">' + cards + '</div>\n'
    html_text = VIDEO_TITLED.sub(repl_titled, html_text)
    def repl_bare(m):
        vids = re.findall(r'<div class="video-embed">[\s\S]*?</div>', m.group(0))
        return '\n<div class="video-grid">' + ''.join(vids) + '</div>\n'
    html_text = VIDEO_GROUP.sub(repl_bare, html_text)
    return html_text

# Tarjetas hoverbox consecutivas (imagen+título+botón) -> cuadrícula .hb-grid.
HB_GROUP = re.compile(r'(?:\s*<div class="hb-card">(?:(?!</div>)[\s\S])*?</div>\s*){2,}')
def group_hoverboxes(html_text):
    return HB_GROUP.sub(lambda m: '\n<div class="hb-grid">' + m.group(0).strip() + '</div>\n', html_text)

# Icono temático (Material Symbols) para el enlace de un botón, según su texto. Réplica de
# recIcon() en server.js, para que el mismo tipo de enlace use el mismo icono en todo el sitio.
def _rec_icon(label):
    t = (label or '').lower()
    if re.search(r'equipo|docente|profesor|decan', t): return 'groups'
    if re.search(r'biblioteca', t): return 'local_library'
    if re.search(r'portafolio', t): return 'description'
    if re.search(r'inscrip|matr[ií]cul|admis', t): return 'how_to_reg'
    if re.search(r'graduaci|postulaci|grados', t): return 'school'
    if re.search(r'educaci[oó]n continua', t): return 'cast_for_education'
    if re.search(r'revista|bolet[ií]n', t): return 'menu_book'
    if re.search(r'circular|comunicado', t): return 'campaign'
    if re.search(r'convocatoria', t): return 'campaign'
    if re.search(r'software', t): return 'computer'
    if re.search(r'[ée]tica|bio[ée]tica', t): return 'balance'
    if re.search(r'requisici[oó]n|solicitud', t): return 'assignment'
    if re.search(r'investigaci', t): return 'science'
    if re.search(r'trabajo de grado|tesis', t): return 'assignment'
    if re.search(r'reglament|normativ', t): return 'gavel'
    if re.search(r'cl[ií]nica|consultorio', t): return 'medical_services'
    if re.search(r'emple|egresad|bolsa', t): return 'work'
    return 'arrow_forward'

# Botones sueltos consecutivos (cada uno en su <p>) -> lista de índice (ícono circular sutil +
# texto + flecha que aparece al pasar el mouse, separados por una línea fina). En vez de un
# muro de tarjetas o botones vistosos, se lee como un índice de referencia bien organizado.
BTN_GROUP = re.compile(r'(?:\s*<p>\s*<a\b[^>]*\bclass="[^"]*\bbtn\b[^"]*"[^>]*>[\s\S]*?</a>\s*</p>){2,}', re.I)
def group_buttons(html_text):
    def repl(m):
        btns = re.findall(r'<a\b[^>]*\bhref="([^"]*)"[^>]*\bclass="[^"]*\bbtn\b[^"]*"[^>]*>([\s\S]*?)</a>|'
                          r'<a\b[^>]*\bclass="[^"]*\bbtn\b[^"]*"[^>]*\bhref="([^"]*)"[^>]*>([\s\S]*?)</a>',
                          m.group(0), re.I)
        links = []
        for a1, l1, a2, l2 in btns:
            href = a1 or a2
            label = re.sub(r'<[^>]+>', ' ', l1 or l2)
            label = re.sub(r'\s+', ' ', label).strip()
            if not href or not label:
                continue
            ico = _rec_icon(label)
            ext = ' target="_blank" rel="noopener"' if re.match(r'^https?://', href, re.I) else ''
            links.append(f'<a class="ql-link" href="{href}"{ext}>'
                        f'<span class="ql-ic"><span class="msi">{ico}</span></span>'
                        f'<span class="ql-txt">{label}</span>'
                        f'<span class="ql-go msi">arrow_forward</span></a>')
        if not links:
            return ''
        return '\n<div class="quick-links">' + ''.join(links) + '</div>\n'
    return BTN_GROUP.sub(repl, html_text)

# Limpieza de figuras sueltas del micrositio: iconos/logos que salen gigantes y apilados.
_FIG = r'<figure[^>]*>\s*(?:<a\b[^>]*>\s*)?<img\b[^>]*>\s*(?:</a>\s*)?</figure>'
_FIG_BTN = re.compile(r'(?:\s*' + _FIG + r'\s*<p>\s*<a\b[^>]*\bclass="[^"]*\bbtn\b[^"]*"[^>]*>[\s\S]*?</a>\s*</p>){2,}', re.I)
_FIG_RUN = re.compile(r'(?:\s*' + _FIG + r'\s*){2,}', re.I)
def group_figures(html_text):
    """Compone las figuras sueltas del backup en bloques limpios:
    (a) patrón "icono + botón" repetido → cuadrícula de navegación (.nav-grid);
    (b) figuras consecutivas (logos/galería) → fila (.fig-grid; logos si son PNG)."""
    def repl_btn(m):
        block = m.group(0)
        figs = re.findall(_FIG, block, re.I)
        btns = re.findall(r'<a\b[^>]*\bclass="[^"]*\bbtn\b[^"]*"[^>]*>[\s\S]*?</a>', block, re.I)
        cards = ''.join('<div class="nav-card">' + figs[i] + (btns[i] if i < len(btns) else '') + '</div>'
                        for i in range(len(figs)))
        return '\n<div class="nav-grid">' + cards + '</div>\n'
    html_text = _FIG_BTN.sub(repl_btn, html_text)
    def repl_run(m):
        figs = re.findall(_FIG, m.group(0), re.I)
        srcs = re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', m.group(0), re.I)
        logos = bool(srcs) and all(s.split('?')[0].lower().endswith(('.png', '.svg', '.webp')) for s in srcs)
        return '\n<div class="fig-grid' + (' logos' if logos else '') + '">' + ''.join(figs) + '</div>\n'
    html_text = _FIG_RUN.sub(repl_run, html_text)
    return html_text

def custom_accordions(html_text):
    """Convierte los acordeones personalizados del sitio (div.*-item + botón con onclick,
    cuyo JS se elimina) en acordeones nativos <details class="acc"> — sin JS, accesibles
    y con el contenido siempre en el DOM (SEO/GEO). Respeta el estado inicial abierto."""
    def repl(m):
        btn_attrs, title_raw, cont_attrs, body = m.groups()
        title = re.sub(r'[▶▼►▸◄▲]', '', re.sub(r'<[^>]+>', ' ', title_raw))
        title = re.sub(r'\s+', ' ', title).strip()
        is_open = ('activo' in btn_attrs.lower()
                   or 'display:block' in (cont_attrs or '').lower().replace(' ', ''))
        return (f'<details class="acc"{" open" if is_open else ""}><summary>{title}</summary>'
                f'<div class="acc-body">{body.strip()}</div></details>')
    html_text = ACORDEON.sub(repl, html_text)
    # limpia contenedores/atributos sobrantes de los acordeones viejos
    html_text = re.sub(r'<div class="(?:acordeon|funcionalidades?[\w-]*)">', '<div class="acc-group">', html_text)
    return html_text

def bullets_to_lists(html_text):
    """Convierte viñetas de texto (• / ▪ / ●) en listas <ul><li> reales. Cubre las
    que están dentro de <p> con <br> y también las sueltas (separadas por saltos de
    línea, en celdas de tabla o tras encabezados). Mismo contenido, mejor marcado."""
    # 1) caso limpio: párrafos <p>…</p> con viñetas separadas por <br>
    html_text = re.sub(r'<p>([\s\S]*?)</p>', _bullets_conv_p, html_text)
    # 2) viñetas sueltas: se marca cada ítem y luego se envuelven los grupos contiguos
    html_text = re.sub(r'[•▪●][ \t]*([^\n<]*?)(?=\s*(?:<br\s*/?>|\n|<))',
                       lambda m: ('\x01' + m.group(1).strip() + '\x02') if m.group(1).strip() else '',
                       html_text)
    html_text = re.sub(r'\x02(?:\s|<br\s*/?>)*\x01', '\x02\x01', html_text)
    html_text = re.sub(r'(?:\x01[^\x02]*\x02)+',
                       lambda m: '<ul>' + ''.join(f'<li>{x}</li>' for x in re.findall(r'\x01([^\x02]*)\x02', m.group(0))) + '</ul>',
                       html_text)
    return html_text.replace('\x01', '').replace('\x02', '')

def slugify(text):
    t = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    t = re.sub(r'[^a-zA-Z0-9]+', '-', t).strip('-').lower()
    return t[:50] or 'seccion'

HEADING = re.compile(r'<(h[23])\b([^>]*)>(.*?)</\1>', re.S | re.I)

def add_toc(html_text):
    """Pone id a los h2/h3 del contenido y devuelve (html, tabla_de_contenidos)."""
    toc, used = [], set()
    def repl(m):
        tag, attrs, inner = m.group(1), m.group(2), m.group(3)
        text = re.sub(r'<[^>]+>', '', inner).strip()
        if not text:
            return m.group(0)
        idm = re.search(r'\bid="([^"]+)"', attrs)
        if idm:
            hid = idm.group(1)
        else:
            hid = slugify(text)
            base, n = hid, 2
            while hid in used:
                hid = f'{base}-{n}'; n += 1
        used.add(hid)
        toc.append({'id': hid, 'text': text[:70], 'level': int(tag[1])})
        newattrs = attrs if idm else f'{attrs} id="{hid}"'
        return f'<{tag}{newattrs}>{inner}</{tag}>'
    new = HEADING.sub(repl, html_text)
    return new, toc

# --- Pénsum para programas cuyo plan el tema arma dinámicamente por sede -------
# (no viene embebido en el contenido; se toma de las tablas TablePress por título).
PENSUM_OVERRIDE = {
    'derecho-laboral': ['pensum 1 - 2 - derecho laboral - presencial - sedes'],
    'derecho-penal':   ['pensum derecho penal'],
    'procedimientos-en-derecho-de-familia': ['pensum procedimientos en derecho de familia'],
    'ingenieria-en-seguridad-y-salud-en-el-trabajo': [
        'pensum ingenieria en seguridad y salud en el trabajo',
        'pensum ingenieria en seguridad y salud en el trabajo 2',
        'pensum ingenieria en seguridad y salud en el trabajo 3'],
    'ingenieria-en-seguridad-y-salud-en-el-trabajo-distancia': [
        'pensum ingenieria en seguridad y salud en el trabajo',
        'pensum ingenieria en seguridad y salud en el trabajo 2',
        'pensum ingenieria en seguridad y salud en el trabajo 3'],
    'maestria-en-ciencias-de-la-salud': [
        'cohorte 1 - 4 maestria en ciencias de la salud - presencial'],
    'tecnologia-en-regencia-de-farmacia-virtual': [
        'pensum 1 - 4 regencia de farmacia - virtual',
        'pensum 5 - 6 regencia de farmacia - virtual'],
}
ORD = {'primer':1,'primero':1,'segundo':2,'tercer':3,'tercero':3,'cuarto':4,
       'quinto':5,'sexto':6,'septimo':7,'octavo':8,'noveno':9,'decimo':10}

def _sem_range(table_html):
    """Etiqueta 'Semestres X a Y' a partir de los encabezados de la tabla."""
    nums = []
    for th in re.findall(r'<th>(.*?)</th>', table_html, re.S):
        for tok in _norm_title(re.sub(r'<[^>]+>', ' ', th)).split():
            if tok in ORD:
                nums.append(ORD[tok]); break
    if not nums:
        return ''
    a, b = min(nums), max(nums)
    return f'Semestres {a} a {b}' if a != b else f'Semestre {a}'

pensum_injected = {'ok': 0, 'faltan': []}
def build_pensum(slug):
    titles = PENSUM_OVERRIDE.get(slug)
    if not titles:
        return ''
    tabs = [(t, TABLES_BY_TITLE.get(t)) for t in titles]
    tabs = [(t, h) for t, h in tabs if h]
    if not tabs:
        pensum_injected['faltan'].append(slug)
        return ''
    pensum_injected['ok'] += 1
    out = ['<h2 id="pensum">Pénsum</h2>']
    if len(tabs) == 1:
        out.append(tabs[0][1])
    else:
        out.append('<div class="acc-group">')
        for _t, h in tabs:
            lbl = _sem_range(h) or 'Plan de estudios'
            out.append(f'<details class="acc"><summary>{lbl}</summary>'
                       f'<div class="acc-body">{h}</div></details>')
        out.append('</div>')
    return '\n'.join(out)

def add_alt(s, title):
    """Rellena el alt de las imágenes sin él y activa lazy-load (SEO + accesibilidad + CWV)."""
    safe = re.sub(r'["<>]', '', title or '').strip()[:120] or 'Corporación Universitaria Remington'
    s = s.replace('alt=""', f'alt="{safe}"').replace("alt=''", f'alt="{safe}"')
    s = re.sub(r'<img\b(?![^>]*\balt=)([^>]*?)/?>', lambda m: f'<img{m.group(1)} alt="{safe}">', s)
    # carga diferida + decodificación asíncrona en las que no lo tengan
    s = re.sub(r'<img\b(?![^>]*\bloading=)([^>]*?)>', lambda m: f'<img loading="lazy" decoding="async"{m.group(1)}>', s)
    # dimensiones deducibles del nombre (WordPress: nombre-ANCHOxALTO.ext) -> reserva espacio, evita CLS
    def _dim(m):
        tag = m.group(0)
        if re.search(r'\b(?:width|height)=', tag):
            return tag
        sm = re.search(r'src="[^"]*-(\d{2,4})x(\d{2,4})\.(?:jpe?g|png|webp|gif)', tag, re.I)
        return re.sub(r'\s*/?>$', f' width="{sm.group(1)}" height="{sm.group(2)}">', tag) if sm else tag
    s = re.sub(r'<img\b[^>]*>', _dim, s)
    return s

LINK = re.compile(r'<a\s+([^>]*?)href="([^"]+)"([^>]*)>(.*?)</a>', re.S | re.I)
MEDIA_EXT = re.compile(r'\.(pdf|jpe?g|png|webp|gif|svg|docx?|xlsx?|pptx?|zip|rar|mp4|mp3|ogg)(\?|#|$)', re.I)
SITE_HTTP = re.compile(r'^https?://(www\.)?uniremington\.edu\.co(/|$)', re.I)
links_fixed = {'facultad': 0, 'relativized': 0}

def _fix_link(m):
    pre, href, post, inner = m.groups()
    low = href.lower().strip()
    # SOLO enlaces de página del sitio: http(s) al dominio o ruta absoluta "/algo".
    # Excluye correos (mailto:), teléfonos (tel:), anclas (#), protocolo-relativo (//), etc.
    is_site = bool(SITE_HTTP.match(low))
    is_root = href.startswith('/') and not href.startswith('//')
    if not (is_site or is_root):
        return m.group(0)                       # correo/externo/otro: NO tocar
    if '/wp-content/' in low or MEDIA_EXT.search(low):
        return m.group(0)                       # medio/archivo: se resuelve aparte
    path = _norm_href_path(href)
    frag = urlparse(href).fragment              # preservar el #ancla (mismo enlace del backup -> SEO)
    frag = ('#' + frag) if frag else ''
    if not path or (path == '/' and not frag):
        return m.group(0)
    if path in valid_paths:                      # publicado -> enlace relativo (mismo destino, dominio-independiente)
        links_fixed['relativized'] += 1
        return f'<a {pre}href="{path}{frag}"{post}>{inner}</a>'
    # Roto SOLO si es un programa (bajo /facultades/) no publicado -> su facultad.
    segs = [s for s in path.strip('/').split('/') if s]
    if len(segs) >= 3 and segs[0] == 'facultades' and segs[1] in fac_slugs:
        links_fixed['facultad'] += 1
        return f'<a {pre}href="/facultad/{segs[1]}{frag}"{post}>{inner}</a>'
    return m.group(0)                            # cualquier otro caso: NO tocar

for typ, items in buckets.items():
    for r in items:
        html_c = r.get('content_html') or ''
        if html_c:
            html_c = strip_decor(html_c)
            # Un solo H1 por página (el del hero de la plantilla): los <h1> del contenido
            # del backup son duplicados que dañan la jerarquía SEO -> se degradan a <h2>.
            html_c = re.sub(r'<(/?)h1(\b[^>]*)>', r'<\1h2\2>', html_c, flags=re.I)
            # Red de seguridad: cualquier encabezado (de cualquier origen, no solo
            # vc_custom_heading) cuyo texto sea en realidad un párrafo largo hereda el
            # color/tamaño de título y se ve mal -> se degrada a <p>.
            html_c = re.sub(r'<(h[1-6])\b[^>]*>([\s\S]*?)</\1>',
                             lambda m: (f'<p>{m.group(2)}</p>' if len(re.sub(r'<[^>]+>', '', m.group(2))) > 180
                                        else m.group(0)),
                             html_c, flags=re.I)
            html_c = dedupe_callouts(html_c)
            html_c = custom_accordions(html_c)
            html_c = group_roles(html_c)
            html_c = bullets_to_lists(html_c)
            html_c = LINK.sub(_fix_link, html_c)
            html_c = group_figures(html_c)
            html_c = group_hoverboxes(html_c)
            html_c = group_buttons(html_c)
            html_c = group_videos(html_c)
            html_c = fix_pdf_iframe(html_c)
            html_c = pdf_component(html_c)
            html_c = add_alt(html_c, r['title'])
            if 'data-gruplac' in html_c:                 # grupos: quitar ficha vieja de "Semilleros"
                html_c = strip_grupo_semilleros(html_c)
        if r.get('is_program'):
            if html_c:
                html_c = CONOCE.sub(_link_conoce, html_c)
            # el pénsum de algunos programas lo arma el tema por sede: se inyecta aquí
            if '<table' not in html_c.lower():
                html_c += build_pensum(r['slug'])
            html_c, r['toc'] = add_toc(html_c)
            r['clean_chars'] = len(html_c)
        elif html_c:
            r['clean_chars'] = len(html_c)
        if html_c:
            html_c = balance_html(html_c)       # anidamiento seguro (no rompe el layout)
            html_c = re.sub(r'</iframe>(?:\s*</iframe>)+', '</iframe>', html_c)   # </iframe> duplicados
            # limpieza FINAL: callouts vacíos (cajas con borde sin contenido), incluso anidados
            for _ in range(3):
                html_c, _n = re.subn(r'<div class="callout"[^>]*>(?:\s|&nbsp;|<br\s*/?>|<p>\s*</p>)*</div>', '', html_c, flags=re.I)
                if not _n:
                    break
            # cierres dobles malformados del backup (</strong></strong> etc.) → uno solo
            for _ in range(3):
                html_c, _n = re.subn(r'</(strong|em|b|i)>\s*</\1>', r'</\1>', html_c, flags=re.I)
                if not _n:
                    break
        r['content_html'] = html_c

print(f"  pénsums inyectados por título {pensum_injected['ok']}"
      + (f"  ·  faltan {pensum_injected['faltan']}" if pensum_injected['faltan'] else ''))
meta = {'counts': {}, 'tables': len(TABLES), 'attachments': len(ATTACH),
        'links_fixed': links_fixed, 'generated_from': os.path.basename(XML)}
for typ, items in buckets.items():
    items.sort(key=lambda r: (r['menu_order'], r['title'].lower()))
    with open(os.path.join(OUT, f"{typ}.json"), 'w', encoding='utf-8') as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    meta['counts'][typ] = {'total': len(items),
                           'publish': sum(1 for r in items if r['status'] == 'publish')}

programas = [r for r in buckets['page'] if r['is_program']]
meta['programas'] = {'total': len(programas),
                     'publish': sum(1 for r in programas if r['status'] == 'publish')}

with open(os.path.join(OUT, "menu.json"), 'w', encoding='utf-8') as f:
    json.dump({'principal': menu_principal}, f, ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "meta.json"), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=1)

print("Conversión completa.")
for typ in WANT:
    c = meta['counts'][typ]
    print(f"  {typ:14} total={c['total']:5}  publish={c['publish']:5}")
print(f"  PROGRAMAS         total={meta['programas']['total']:5}  publish={meta['programas']['publish']:5}")
print(f"  tablas TablePress {len(TABLES)}   ·   adjuntos mapeados {len(ATTACH)}")
print(f"  menu_items        {len(menu_items)}")
