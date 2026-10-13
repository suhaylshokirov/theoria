"""JavaScript geometry helpers run inside the page. Kept in one place so every
test measures the same way. Each constant is a function expression for
``page.evaluate``."""

# Shared by the snippets below; prepended to each one.
_PRELUDE = """
const vis = (el) => {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  if (el.checkVisibility)
    return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true});
  const cs = getComputedStyle(el);
  return cs.display !== 'none' && cs.visibility === 'visible' && parseFloat(cs.opacity) > 0;
};
const rect = (el) => {
  const r = el.getBoundingClientRect();
  return {left: r.left, top: r.top, right: r.right, bottom: r.bottom,
          width: r.width, height: r.height};
};
const label = (el) => {
  let s = el.tagName.toLowerCase();
  const c = (typeof el.className === 'string' ? el.className : (el.className && el.className.baseVal) || '').trim();
  if (c) s += '.' + c.split(/\\s+/).join('.');
  if (el.id) s += '#' + el.id;
  return s;
};
"""


def _js(body: str) -> str:
    return "() => {" + _PRELUDE + body + "}"


# Document scroll width vs viewport, plus the culprits.
OVERFLOW = _js("""
const vw = window.innerWidth;
const doc = document.documentElement;
const clipped = (el) => {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (getComputedStyle(p).overflowX !== 'visible') return true;
  }
  return false;
};
const offenders = [];
for (const el of document.body.querySelectorAll('*')) {
  if (offenders.length >= 8) break;
  if (!vis(el) || clipped(el)) continue;
  const r = el.getBoundingClientRect();
  if (r.right > vw + 1 || r.left < -1) {
    offenders.push({el: label(el), rect: rect(el)});
  }
}
return {scrollWidth: doc.scrollWidth, innerWidth: vw, offenders};
""")

# Visible header controls (and the header box). nav links count only when
# laid out inline, i.e. the hamburger is not shown.
HEADER_CONTROLS = _js("""
const header = document.querySelector('.site-header');
const out = [];
const add = (name, el) => { if (vis(el)) out.push({name, label: label(el), rect: rect(el)}); };
add('brand', document.querySelector('.site-header .brand'));
document.querySelectorAll('.site-header .signin-btn').forEach((e, i) => add('signin-btn#' + i, e));
const toggle = document.querySelector('.site-header .nav-toggle');
add('nav-toggle', toggle);
add('search-btn', document.querySelector('.site-header .search-btn'));
add('search-field', document.querySelector('.site-header .header-search__field'));
add('lang-menu', document.querySelector('.site-header .lang-menu__trigger'));
add('theme-toggle', document.querySelector('.site-header .theme-toggle'));
if (!vis(toggle)) {
  // the Sign in link inside .nav-links is already collected above as a signin-btn
  document.querySelectorAll('.site-header .nav-links > a:not(.signin-btn)').forEach((e, i) => add('nav-link#' + i, e));
}
return {
  innerWidth: window.innerWidth,
  header: header ? rect(header) : null,
  controls: out,
};
""")

# Icon vs control containment.
HEADER_ICONS = _js("""
const pairs = [
  ['.nav-toggle svg', '.nav-toggle'],
  ['.theme-toggle svg', '.theme-toggle'],
  ['.search-btn svg', '.search-btn'],
  ['.lang-menu__chevron', '.lang-menu__trigger'],
  ['.signin-icon', '.signin-btn'],
  ['.brand .mark', '.brand'],
];
const out = [];
for (const [iconSel, ctrlSel] of pairs) {
  for (const icon of document.querySelectorAll('.site-header ' + iconSel)) {
    const ctrl = icon.closest(ctrlSel);
    if (!ctrl || !vis(ctrl)) continue;
    const r = icon.getBoundingClientRect();
    // icon opacity: svg may be a stacked pair swapped by opacity
    if (parseFloat(getComputedStyle(icon).opacity) <= 0) continue;
    if (getComputedStyle(icon).display === 'none') continue;
    out.push({icon: label(icon), control: label(ctrl), iconRect: rect(icon), ctrlRect: rect(ctrl)});
  }
}
return out;
""")

# Mobile menu state.
MENU_STATE = _js("""
const toggle = document.querySelector('.site-header .nav-toggle');
const nav = document.querySelector('#nav-links');
const links = [...nav.querySelectorAll('a')].map((a) => ({
  label: label(a), text: a.textContent.trim(), visible: vis(a), rect: rect(a),
  signin: a.classList.contains('signin-btn') || !!a.closest('.account-menu')}));
return {
  toggleVisible: vis(toggle),
  expanded: toggle && toggle.getAttribute('aria-expanded'),
  navVisible: vis(nav),
  innerWidth: window.innerWidth,
  innerHeight: window.innerHeight,
  links,
};
""")

TOGGLE_VISIBLE = _js("return vis(document.querySelector('.site-header .nav-toggle'));")

WALL_TRANSFORM = _js("""
const t = document.querySelector('.wall .wall__track');
return t ? getComputedStyle(t).transform : null;
""")

REEL_STATE = _js("""
const slides = [...document.querySelectorAll('[data-reel-slide]')];
const idx = slides.findIndex((s) => !s.hasAttribute('hidden'));
return {count: slides.length, current: idx};
""")
