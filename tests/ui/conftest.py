"""Fixtures for the opt-in browser layout suite.

Run against a live server:  pytest tests/ui --base-url http://127.0.0.1:8000
Nothing here imports Django: the suite only talks to a running site over HTTP.
"""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "test-results" / "ui"

# id -> context options. Ids appear in test names.
VIEWPORTS = {
    "phone-small": dict(width=360, height=640, dsf=3, mobile=True, touch=True),
    "phone": dict(width=390, height=844, dsf=3, mobile=True, touch=True),
    "tablet": dict(width=820, height=1180, dsf=2, mobile=False, touch=True),
    "laptop": dict(width=1366, height=768, dsf=1, mobile=False, touch=False),
    "desktop": dict(width=1920, height=1080, dsf=1, mobile=False, touch=False),
    # How many 1080p TV browsers report their CSS viewport (960x540 at 2x).
    # tv=True forces html.is-tv the way ?tv=1 does (a laptop's UA is not a
    # TV's); the 4K one needs no forcing, a 3000px+ screen counts as a TV.
    "tv": dict(width=960, height=540, dsf=2, mobile=False, touch=False, tv=True),
    # The other 1080p shape: a 1920x1080 viewport from a TV's own UA (zoom 1.5).
    "tv-1080": dict(width=1920, height=1080, dsf=1, mobile=False, touch=False, tv=True),
    # A 4K panel reporting its full 3840px (auto TV, zoom 3).
    "tv-4k": dict(width=3840, height=2160, dsf=1, mobile=False, touch=False),
    "wide": dict(width=2560, height=1440, dsf=1, mobile=False, touch=False),
}

# page id -> path (public, no login needed)
PAGES = {
    "home": "/",
    "browse": "/browse/",
    "movies": "/movies/",
    "tv": "/tv/",
    "cartoons": "/cartoons/",
    "people": "/people/",
    "studios": "/studios/",
    "login": "/accounts/login/",
    # The site's own 404 page. A DEBUG server routes this URL to the same view
    # production uses for a missing page; production doesn't route it at all,
    # so it is a real 404 there. Either way the page under test is the same.
    "not-found": "/__404__/",
    # Detail pages and analytics sit behind login. They run only when
    # UI_SESSION="sessionid=<key>" names a valid session cookie of the site
    # under test (see AUTH_PAGES); otherwise they are skipped. The slugs are
    # real rows of the local replica: a film, a show with several seasons,
    # an actor and a studio.
    "movie-detail": "/movies/inception/",
    "tv-detail": "/tv/breaking-bad/",
    "person-detail": "/people/leonardo-dicaprio/",
    "studio-detail": "/studios/warner-bros-pictures/",
    "analytics": "/analytics/",
}
AUTH_PAGES = {"movie-detail", "tv-detail", "person-detail", "studio-detail", "analytics"}


def session_cookie():
    """(name, value) from UI_SESSION, or None."""
    raw = os.environ.get("UI_SESSION", "")
    if "=" not in raw:
        return None
    name, _, value = raw.partition("=")
    return name.strip(), value.strip()

REEL_DURATION_MS = 8000


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: waits several seconds for real-time motion")


def _base_url(config):
    try:
        return config.getoption("base_url")
    except ValueError:  # pytest-playwright not installed
        return None


def pytest_collection_modifyitems(config, items):
    if _base_url(config):
        return
    skip = pytest.mark.skip(
        reason="UI layout tests are opt-in: start the site, then run "
        "`pytest tests/ui --base-url http://127.0.0.1:8000`"
    )
    for item in items:
        if "tests/ui/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(skip)


# Module-scoped so pytest orders the run viewport by viewport (and, in
# test_layout, page by page): one page load then serves every check on it.
@pytest.fixture(scope="module", params=list(VIEWPORTS), ids=list(VIEWPORTS))
def viewport_id(request):
    return request.param


@pytest.fixture(scope="module")
def viewport(viewport_id):
    return {"id": viewport_id, **VIEWPORTS[viewport_id]}


@pytest.fixture(scope="module")
def base(request):
    return _base_url(request.config).rstrip("/")


def settle(page, timeout_ms=8000):
    """Wait for load, fonts and visible lazy images, each capped."""
    page.wait_for_load_state("load", timeout=20000)
    page.evaluate("document.fonts.ready.then(() => true)")
    try:
        page.wait_for_function(
            "() => [...document.images].filter(i => {const r = i.getBoundingClientRect();"
            " return r.bottom > 0 && r.top < innerHeight && r.width > 0;})"
            ".every(i => i.complete)",
            timeout=timeout_ms,
        )
    except Exception:
        pass  # remote TMDB images may be slow or blocked; layout is what we test
    # Neutral spot: bottom-right corner, never over the homepage wall.
    vp = page.viewport_size
    page.mouse.move(vp["width"] - 2, vp["height"] - 2)


def new_context(browser, viewport, reduced_motion="no-preference"):
    ctx = browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        device_scale_factor=viewport["dsf"],
        is_mobile=viewport["mobile"],
        has_touch=viewport["touch"],
        reduced_motion=reduced_motion,
    )
    if viewport.get("tv"):
        # What ?tv=1 does, but before the very first load of every page.
        ctx.add_init_script("try { localStorage.setItem('theoria-tv', '1'); } catch (e) {}")
    return ctx


def load(ctx, url):
    cookie = session_cookie()
    if cookie:
        parts = urlsplit(url)
        ctx.add_cookies([{"name": cookie[0], "value": cookie[1], "url": f"{parts.scheme}://{parts.netloc}"}])
    page = ctx.new_page()
    page.goto(url, wait_until="load")
    settle(page)
    return page


@pytest.fixture
def open_page(browser, base, viewport):
    """Factory: open_page(path, reduced_motion='no-preference') -> a fresh,
    loaded Page, for tests that interact or need their own motion setting."""
    contexts = []

    def _open(path, reduced_motion="no-preference"):
        ctx = new_context(browser, viewport, reduced_motion)
        contexts.append(ctx)
        return load(ctx, base + path)

    yield _open
    for ctx in contexts:
        ctx.close()


@pytest.fixture
def browser_name_slug(browser):
    return re.sub(r"\W+", "-", browser.browser_type.name)
