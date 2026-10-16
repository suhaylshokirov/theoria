"""Browser tests for sharing a collection (Phase 16): the image export, the
share sheet on narrow phones, and the public /s/<token>/ page.

Opt-in like the rest of tests/ui. The owner-side tests need a session of the
site under test, and WRITE to it: opening the sheet and pressing Download image
creates the collection's link if it was still private.

    UI_SESSION=sessionid=<key> pytest tests/ui/test_share.py \
        --base-url http://127.0.0.1:8000 --browser chromium --browser webkit

UI_SHARE_TOKEN=<token> points the public-page test at an existing link;
without it the token is read from the owner's sheet (needs UI_SESSION).
"""

import io
import os

import pytest

pytest.importorskip("playwright", reason="pip install -r requirements-ui.txt")
pytest.importorskip("pytest_playwright")
PIL_Image = pytest.importorskip("PIL.Image", reason="Pillow is needed to inspect the exported PNG")
from PIL import ImageStat  # noqa: E402

from . import geometry as g  # noqa: E402
from .conftest import VIEWPORTS, load, new_context, session_cookie  # noqa: E402

needs_owner = pytest.mark.skipif(not session_cookie(), reason="needs UI_SESSION=sessionid=<key>")

FORMATS = {"story": (1080, 1920), "wide": (1600, 900)}
PHONES = {
    f"phone-{w}": dict(width=w, height=800, dsf=3, mobile=True, touch=True) for w in (320, 360, 390, 430)
}


def open_sheet(page):
    """Open the sheet from the first enabled Share button on /me/."""
    btn = page.locator("[data-share-open]:not([disabled])").first
    btn.scroll_into_view_if_needed()
    btn.click()
    page.wait_for_selector("dialog#share-sheet[open]")
    return page.locator("dialog#share-sheet")


def wait_preview(page):
    page.wait_for_selector('[data-share-preview][aria-busy="false"]', timeout=30000)


def pick_format(page, fmt):
    page.locator(f'input[name="share-format"][value="{fmt}"] + span').click()
    wait_preview(page)


@needs_owner
@pytest.mark.parametrize("fmt", list(FORMATS))
def test_export_is_exact_size_and_drawn(browser, base, fmt):
    """Download image gives a PNG of the exact pixel size with the posters
    really drawn (a capture that dropped them is flat, so it fails)."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    try:
        page = load(ctx, base + "/me/")
        open_sheet(page)
        wait_preview(page)
        pick_format(page, fmt)
        # Headless browsers cannot share files, so the primary button downloads.
        sel = "[data-share-image]"
        if "Share" in (page.locator(sel).inner_text() or "") and page.locator("[data-share-download]").is_visible():
            sel = "[data-share-download]"
        with page.expect_download(timeout=60000) as dl:
            page.locator(sel).click()
        path = dl.value.path()
        im = PIL_Image.open(path).convert("RGB")
        assert im.size == FORMATS[fmt]
        w, h = im.size
        band = im.crop((int(w * 0.1), int(h * 0.35), int(w * 0.9), int(h * 0.75)))
        dev = sum(ImageStat.Stat(band).stddev) / 3
        colours = len(band.resize((80, 80)).getcolors(10000) or [])
        assert dev > 20 and colours > 500, f"looks blank: stddev {dev:.1f}, {colours} colours"
    finally:
        ctx.close()


@needs_owner
@pytest.mark.parametrize("vp", list(PHONES), ids=list(PHONES))
def test_sheet_fits_narrow_phones(browser, base, vp):
    ctx = new_context(browser, PHONES[vp])
    try:
        page = load(ctx, base + "/me/")
        open_sheet(page)
        wait_preview(page)
        d = page.evaluate(g.OVERFLOW)
        assert d["scrollWidth"] <= d["innerWidth"] + 1, d
        panel = page.evaluate(
            "() => {const p = document.querySelector('.share-sheet__panel');"
            " return {sw: p.scrollWidth, cw: p.clientWidth};}"
        )
        assert panel["sw"] <= panel["cw"] + 1, panel
        width = PHONES[vp]["width"]
        targets = {
            "image button": "[data-share-image]",
            "close button": "[data-share-close]",
        }
        for fmt_input in page.locator('input[name="share-format"], input[name="share-theme"]').all():
            targets[f"radio {fmt_input.get_attribute('name')}={fmt_input.get_attribute('value')}"] = (
                fmt_input.locator("xpath=..")
            )
        for name, target in targets.items():
            loc = page.locator(target).first if isinstance(target, str) else target
            box = loc.bounding_box()
            assert box, f"{name} has no box"
            assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, f"{name} outside viewport: {box}"
            assert box["height"] >= 44 - 1, f"{name} is {box['height']:.0f}px tall"
        page.keyboard.press("Escape")
        page.wait_for_selector("dialog#share-sheet[open]", state="detached")
    finally:
        ctx.close()


@pytest.fixture(scope="module")
def share_token(browser, base):
    token = os.environ.get("UI_SHARE_TOKEN", "").strip()
    if token:
        return token
    if not session_cookie():
        pytest.skip("set UI_SHARE_TOKEN or UI_SESSION=sessionid=<key>")
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        page = load(ctx, base + "/me/")
        open_sheet(page)
        if page.locator("[data-share-off]").is_visible():
            page.locator("[data-share-enable]").click()
        page.wait_for_selector("[data-share-on]:not([hidden])")
        link = page.locator("[data-share-link]").input_value()
    finally:
        ctx.close()
    parts = [p for p in link.split("/") if p]
    return parts[-1]


@pytest.mark.parametrize("vp", list(VIEWPORTS), ids=list(VIEWPORTS))
def test_public_page_no_sideways_scroll(browser, base, share_token, vp):
    """/s/<token>/ opened signed out (a fresh context carries no cookie)."""
    ctx = new_context(browser, {"id": vp, **VIEWPORTS[vp]})
    try:
        page = ctx.new_page()
        page.goto(f"{base}/s/{share_token}/", wait_until="load")
        page.wait_for_load_state("load")
        d = page.evaluate(g.OVERFLOW)
        assert d["scrollWidth"] <= d["innerWidth"] + 1, d
    finally:
        ctx.close()
