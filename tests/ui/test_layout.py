"""Responsive-layout invariants across a device-viewport matrix.

Opt-in: pytest tests/ui --base-url http://127.0.0.1:8000 [--browser webkit]
"""

import time

import pytest

pytest.importorskip("playwright", reason="pip install -r requirements-ui.txt")
pytest.importorskip("pytest_playwright")

from . import geometry as g  # noqa: E402
from .conftest import AUTH_PAGES, PAGES, REEL_DURATION_MS, RESULTS_DIR, load, new_context, session_cookie  # noqa: E402

PAGE_PARAMS = [pytest.param(p, id=pid) for pid, p in PAGES.items()]
MOTION_VIEWPORTS = {"phone", "laptop", "tv"}


def fmt(r):
    return f"[l={r['left']:.1f} t={r['top']:.1f} r={r['right']:.1f} b={r['bottom']:.1f} ({r['width']:.1f}x{r['height']:.1f})]"


def overlap_area(a, b):
    w = min(a["right"], b["right"]) - max(a["left"], b["left"])
    h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return max(w, 0) * max(h, 0)


@pytest.fixture(scope="module", params=PAGE_PARAMS)
def path(request):
    return request.param


@pytest.fixture(scope="module")
def loaded(browser, base, viewport, path):
    pid = next(k for k, v in PAGES.items() if v == path)
    if pid in AUTH_PAGES and not session_cookie():
        pytest.skip("behind login: set UI_SESSION=sessionid=<key> to run")
    """The page under test, loaded once per viewport and shared by every
    read-only check below. Checks must leave it as they found it."""
    ctx = new_context(browser, viewport)
    yield load(ctx, base + path)
    ctx.close()


# --- a. horizontal overflow -------------------------------------------------

def test_no_horizontal_overflow(loaded):
    d = loaded.evaluate(g.OVERFLOW)
    if d["scrollWidth"] > d["innerWidth"] + 1:
        lines = [f"  {o['el']} {fmt(o['rect'])}" for o in d["offenders"]] or ["  (no unclipped offender found)"]
        pytest.fail(
            f"scrollWidth {d['scrollWidth']} > innerWidth {d['innerWidth']}; offenders:\n" + "\n".join(lines)
        )


# --- b. header controls do not overlap --------------------------------------

def test_header_controls_do_not_overlap(loaded):
    d = loaded.evaluate(g.HEADER_CONTROLS)
    assert d["header"], "no .site-header on page"
    ctrls, hdr, vw = d["controls"], d["header"], d["innerWidth"]
    assert ctrls, "no visible header controls found"
    problems = []
    for i, a in enumerate(ctrls):
        for b in ctrls[i + 1:]:
            area = overlap_area(a["rect"], b["rect"])
            if area > 1:
                problems.append(f"{a['name']} {fmt(a['rect'])} overlaps {b['name']} {fmt(b['rect'])} by {area:.0f}px2")
        r = a["rect"]
        if r["left"] < -1 or r["right"] > vw + 1:
            problems.append(f"{a['name']} {fmt(r)} outside viewport width {vw}")
        if r["top"] < hdr["top"] - 1 or r["bottom"] > hdr["bottom"] + 1:
            problems.append(f"{a['name']} {fmt(r)} outside header {fmt(hdr)} vertically")
    assert not problems, "\n".join(problems)


# --- c. icons sit inside their controls -------------------------------------

def test_header_icons_sit_in_their_controls(loaded):
    icons = loaded.evaluate(g.HEADER_ICONS)
    problems = []
    for it in icons:
        i, c = it["iconRect"], it["ctrlRect"]
        who = f"{it['icon']} in {it['control']}"
        if i["width"] <= 0 or i["height"] <= 0:
            problems.append(f"{who}: zero size {fmt(i)}")
            continue
        if (i["left"] < c["left"] - 1 or i["right"] > c["right"] + 1
                or i["top"] < c["top"] - 1 or i["bottom"] > c["bottom"] + 1):
            problems.append(f"{who}: icon {fmt(i)} not inside control {fmt(c)}")
        dy = abs((i["top"] + i["bottom"]) / 2 - (c["top"] + c["bottom"]) / 2)
        if dy > 3:
            problems.append(f"{who}: vertical centre off by {dy:.1f}px (icon {fmt(i)}, control {fmt(c)})")
    assert not problems, "\n".join(problems)


# --- d. touch targets -------------------------------------------------------

@pytest.mark.xfail(
    strict=True,
    reason="known: every header control is 34-35px on touch screens; enlarging them is a "
    "design change. strict, so this turns red the day they all reach 44px -- then drop the mark",
)
def test_touch_targets(loaded, viewport):
    if not viewport["touch"]:
        pytest.skip("touch viewports only")
    d = loaded.evaluate(g.HEADER_CONTROLS)
    small = [
        f"{c['name']} ({c['label']}) {c['rect']['width']:.1f}x{c['rect']['height']:.1f}"
        for c in d["controls"]
        if c["rect"]["width"] < 44 - 0.01 or c["rect"]["height"] < 44 - 0.01
    ]
    assert not small, "controls under 44x44 CSS px:\n  " + "\n  ".join(small)


# --- e. mobile menu ---------------------------------------------------------

def test_mobile_menu_opens_and_closes(open_page):
    page = open_page("/")
    if not page.evaluate(g.TOGGLE_VISIBLE):
        pytest.skip("nav-toggle not shown at this width (links are inline)")
    toggle = page.locator(".site-header .nav-toggle")
    toggle.click()
    page.wait_for_timeout(600)
    s = page.evaluate(g.MENU_STATE)
    problems = []
    if s["expanded"] != "true":
        problems.append(f"aria-expanded={s['expanded']!r} after open")
    if not s["navVisible"]:
        problems.append("#nav-links not visible after open")
    links = s["links"]
    for l in links:
        r = l["rect"]
        if not l["visible"] and l["signin"]:
            continue  # on phones the header's own Sign in button stands in for this one
        if not l["visible"]:
            problems.append(f"link {l['text']!r} not visible when open")
        if r["left"] < -1 or r["right"] > s["innerWidth"] + 1 or r["top"] < -1 or r["bottom"] > s["innerHeight"] + 1:
            problems.append(f"link {l['text']!r} {fmt(r)} outside viewport {s['innerWidth']}x{s['innerHeight']}")
    shown_links = [l for l in links if l["visible"]]
    for i, a in enumerate(shown_links):
        for b in shown_links[i + 1:]:
            if overlap_area(a["rect"], b["rect"]) > 1:
                problems.append(f"links {a['text']!r} {fmt(a['rect'])} and {b['text']!r} {fmt(b['rect'])} overlap")
    toggle.click()
    page.wait_for_timeout(600)
    s2 = page.evaluate(g.MENU_STATE)
    if s2["expanded"] != "false":
        problems.append(f"aria-expanded={s2['expanded']!r} after close")
    shown = [l["text"] for l in s2["links"] if l["visible"]]
    if shown:
        problems.append(f"links still visible after close: {shown}")
    assert not problems, "\n".join(problems)


# --- f/g. homepage motion ---------------------------------------------------

@pytest.fixture
def motion_viewport(viewport_id):
    if viewport_id not in MOTION_VIEWPORTS:
        pytest.skip("motion checked at phone, laptop, tv only")


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_wall_moves(open_page, motion_viewport, motion):
    page = open_page("/", reduced_motion=motion)
    a = page.evaluate(g.WALL_TRANSFORM)
    assert a is not None, "no .wall .wall__track on the homepage"
    page.wait_for_timeout(800)
    b = page.evaluate(g.WALL_TRANSFORM)
    if motion == "no-preference":
        assert a != b, f"wall track transform did not change in 800ms ({a!r})"
    else:
        assert a == b, f"wall animates under prefers-reduced-motion ({a!r} -> {b!r})"


@pytest.mark.slow
@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_reel_advances(open_page, motion_viewport, motion):
    page = open_page("/", reduced_motion=motion)
    s0 = page.evaluate(g.REEL_STATE)
    if s0["count"] < 2:
        pytest.skip(f"reel has {s0['count']} slide(s); nothing to advance")
    assert s0["current"] >= 0, "no reel slide without `hidden`"
    deadline = time.monotonic() + (REEL_DURATION_MS + 3000) / 1000
    advanced = False
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        if page.evaluate(g.REEL_STATE)["current"] != s0["current"]:
            advanced = True
            break
    if motion == "no-preference":
        assert advanced, f"reel stayed on slide {s0['current']} for {REEL_DURATION_MS + 3000}ms"
    else:
        assert not advanced, "reel advanced under prefers-reduced-motion"


# --- h. TV focus ------------------------------------------------------------

TV_VIEWPORTS = {"tv", "tv-1080", "tv-4k"}

TV_FOCUS = """() => {
  const e = document.activeElement, cs = getComputedStyle(e);
  const ring = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2;
  const img = e.closest('.poster-card') && e.querySelector('.poster,.headshot,.logo,.placeholder');
  const lift = !!img && getComputedStyle(img).boxShadow !== 'none';
  // A text field shows focus by lighting its frame (.field / header search).
  const field = e.matches('input, select') && !!e.closest('.field, .header-search__field');
  // The home page's own tiles (wall) lift and frame themselves.
  const own = getComputedStyle(e).boxShadow !== 'none' || getComputedStyle(e).transform !== 'none';
  return {tv: document.documentElement.classList.contains('is-tv'), ok: ring || lift || field || own,
          el: e.tagName + '.' + String(e.className).slice(0, 30)};
}"""


def test_tv_focus_is_visible(open_page, viewport_id, path):
    if viewport_id not in TV_VIEWPORTS:
        pytest.skip("TV viewports only")
    pid = next(k for k, v in PAGES.items() if v == path)
    if pid in AUTH_PAGES and not session_cookie():
        pytest.skip("behind login: set UI_SESSION=sessionid=<key> to run")
    page = open_page(path)
    bad = []
    for i in range(30):
        page.keyboard.press("Tab")
        page.wait_for_timeout(80)
        d = page.evaluate(TV_FOCUS)
        assert d["tv"], "html.is-tv is not set"
        # Skipped: the skip link, the wrap past the last control (BODY) and
        # the Film guide, which is off-limits to this task.
        if not d["ok"] and not any(k in d["el"] for k in ("skip-link", "BODY", "ai-assistant")):
            bad.append(f"tab {i + 1}: {d['el']}")
    assert not bad, "focus not visible on:\n  " + "\n  ".join(bad)


# --- screenshots ------------------------------------------------------------
# Last in the file on purpose: it scrolls the shared page (and scrolls it back).

def test_screenshot(loaded, path, viewport, browser_name_slug):
    pid = next(k for k, v in PAGES.items() if v == path)
    page = loaded
    height = page.evaluate("document.documentElement.scrollHeight")
    step = max(page.viewport_size["height"] // 2, 200)
    y = 0
    while y < min(height, 20000):
        page.evaluate("y => window.scrollTo(0, y)", y)
        page.wait_for_timeout(120)
        y += step
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    out = RESULTS_DIR / browser_name_slug / viewport["id"] / f"{pid}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out), full_page=True, animations="disabled")
    assert out.exists() and out.stat().st_size > 0
