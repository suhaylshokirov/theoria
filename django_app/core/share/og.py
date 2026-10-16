"""The link-preview image (`/s/<token>/og.png`, Task 140), drawn with Pillow.

A chat app unfurling a shared link fetches one 1200x630 PNG and cannot run the
browser capture the share sheet uses, so the server draws the Wide card itself,
Ink theme only. This module is pure drawing: plain values in, PNG bytes out --
no request, no model, no Django. The view decides what to say and in which
language; the poster fetch is the only I/O here.

Pillow has no per-glyph font fallback, so the face is picked per string: the
Latin faces (Archivo, Spline Sans Mono) have no Cyrillic and no U+02BB (the
Uzbek okina), so any string with such a character is drawn whole in the Roboto
Flex twin, which has all of them.
"""

from __future__ import annotations

import io
import logging
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_FONTS = _HERE / "fonts"
_ICONS = _HERE / "icons"

WIDTH, HEIGHT = 1200, 630
_PAD_X, _PAD_Y = 56, 48
_MAX_POSTER_BYTES = 2_000_000

# Mirrors the Ink tokens in static/css/share-card.css (`.sc`); change both.
PAPER = "#09090b"
SHEET_2 = "#1b1b1f"
RULE = "#232327"
INK = "#f2f1ed"
INK_MUTED = "#a9a7a1"
INK_FAINT = "#8a8882"
LIME = "#a3e635"
KIND_COLOURS = {"top": "#facc15", "liked": "#ef4444", "watch_later": "#a3e635"}

_FACES = {
    "display": ("Archivo-ExpandedExtraBold.ttf", "RobotoFlex-ExpandedExtraBold.ttf"),
    "body": ("Archivo-Medium.ttf", "RobotoFlex-Medium.ttf"),
    "mono": ("SplineSansMono-Medium.ttf", "RobotoFlex-Medium.ttf"),
}


@dataclass
class Poster:
    title: str = ""
    year: str = ""            # "1972", or "2008-2013" for a show
    rank: int | None = None   # Top only
    url: str | None = None    # TMDB w342 URL, or None for a title plate
    image: bytes | None = field(default=None, repr=False)  # filled by fetch_posters


@dataclass
class OgData:
    kind: str
    heading: str
    line: str
    owner_name: str
    owner_since: date
    month_label: str
    since_template: str       # "Member since %(month)s %(year)s", translated
    posters: list
    more: int
    printed_url: str
    more_label: str


# --- fonts -------------------------------------------------------------------

def _needs_fallback(text: str) -> bool:
    return any(not (ord(c) <= 0x24F or 0x2000 <= ord(c) <= 0x206F) for c in text)


def face_file(role: str, text: str) -> str:
    """The font file to draw `text` in for this role (display, body, mono)."""
    primary, fallback = _FACES[role]
    return fallback if _needs_fallback(text) else primary


_font_cache: dict = {}


def _font(role: str, text: str, size: int):
    key = (face_file(role, text), size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(_FONTS / key[0]), size)
    return _font_cache[key]


# --- posters -----------------------------------------------------------------

def _fetch_one(url: str, timeout: float):
    prefix = config.TMDB_IMAGE_BASE_URL + "/"
    # URLs come from warehouse poster paths, never from a request; refuse
    # anything else anyway so this can never become a fetch-anything proxy.
    if not url.startswith(prefix):
        logger.warning("Refusing to fetch a non-TMDB poster URL")
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            body = resp.read(_MAX_POSTER_BYTES + 1)
        if len(body) > _MAX_POSTER_BYTES:
            return None
        return body
    except Exception:
        logger.warning("Poster fetch failed: %s", url, exc_info=True)
        return None


def fetch_posters(urls, timeout: float = 3.0):
    """Bodies for `urls`, in order, in parallel; None for any that failed. Never raises."""
    urls = list(urls)
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futures = [pool.submit(_fetch_one, u, timeout) if u else None for u in urls]
        out = []
        for f in futures:
            try:
                out.append(f.result() if f else None)
            except Exception:
                out.append(None)
        return out


# --- drawing helpers ---------------------------------------------------------

def _shape(img, box, painter, colour, scale=4):
    """Draw an antialiased shape: paint a mask at 4x, shrink, paste in colour."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    mask = Image.new("L", (w * scale, h * scale), 0)
    painter(ImageDraw.Draw(mask), scale)
    mask = mask.resize((w, h), Image.LANCZOS)
    img.paste(colour, (x0, y0), mask)


def _text_w(text, font):
    return ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=font)


def _fit_line(text, role, size, max_w, min_size=14):
    """The largest font (down to min_size) that fits one line; ellipsis if still too wide."""
    while size > min_size and _text_w(text, _font(role, text, size)) > max_w:
        size -= 1
    font = _font(role, text, size)
    if _text_w(text, font) > max_w:
        while text and _text_w(text + "…", font) > max_w:
            text = text[:-1]
        text = text.rstrip() + "…"
        font = _font(role, text, size)
    return text, font


def _wrap(text, role, size, max_w, max_lines):
    """Word-wrap into at most max_lines; the last line ends in an ellipsis if cut."""
    font = _font(role, text, size)
    words = text.split()
    lines, cur = [], ""
    i = 0
    while i < len(words):
        trial = f"{cur} {words[i]}".strip()
        if _text_w(trial, font) <= max_w or not cur:
            cur = trial
            i += 1
        else:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_w(last + "…", font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    # A single unbreakable word wider than the box: clip with an ellipsis.
    fixed = []
    for ln in lines:
        while ln and _text_w(ln, font) > max_w:
            ln = ln[:-2] + "…" if len(ln) > 1 else ""
        fixed.append(ln)
    return fixed, font


def _draw_text(draw, xy, text, role, size, fill, anchor="la"):
    draw.text(xy, text, font=_font(role, text, size), fill=fill, anchor=anchor)


def _wordmark(img, x, cy):
    """The Θ: a ring with a lime bar through it, as in the card's SVG (26x32 box)."""
    h = 36
    w = round(26 * h / 32)
    k = h / 32
    top = cy - h // 2

    def ring(d, s):
        d.ellipse([3.1 * k * s, 2.7 * k * s, 22.9 * k * s, 29.3 * k * s],
                  outline=255, width=round(6.4 * k * s))

    _shape(img, (x, top, x + w, top + h), ring, INK)

    def bar(d, s):
        d.rectangle([3.6 * k * s, 12.7 * k * s, 22.4 * k * s, 19.3 * k * s], fill=255)

    _shape(img, (x, top, x + w, top + h), bar, LIME)
    return x + w


def _cover(data: bytes, w: int, h: int):
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        return ImageOps.fit(im.convert("RGB"), (w, h), Image.LANCZOS)
    except Exception:
        logger.warning("Poster bytes did not decode", exc_info=True)
        return None


def _plate(img, box, poster: Poster):
    x0, y0, x1, y1 = box
    d = ImageDraw.Draw(img)
    d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=SHEET_2)
    pad = 16
    title = poster.title or ""
    if title:
        lines, font = _wrap(title, "body", 20, x1 - x0 - 2 * pad, 4)
        y = y0 + pad
        for ln in lines:
            d.text((x0 + pad, y), ln, font=font, fill=INK)
            y += 26
    if poster.year:
        _draw_text(d, (x0 + pad, y1 - pad), poster.year, "mono", 16, INK_FAINT, "ls")


def _rank(img, box, rank):
    x0, y0, x1, y1 = box
    text = f"{rank:02d}"
    size = round((x1 - x0) * 0.3)
    font = _font("display", text, size)
    right, base = x1 - 12, y1 - 12
    shadow = Image.new("L", img.size, 0)
    ImageDraw.Draw(shadow).text((right + 2, base + 3), text, font=font, fill=210, anchor="rs")
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    img.paste((0, 0, 0), (0, 0), shadow)
    ImageDraw.Draw(img).text((right, base), text, font=font, fill=LIME, anchor="rs")


# --- the card ----------------------------------------------------------------

def render_og(data: OgData) -> bytes:
    img = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    d = ImageDraw.Draw(img)
    left, right = _PAD_X, WIDTH - _PAD_X
    content_w = right - left
    kind_colour = KIND_COLOURS.get(data.kind, LIME)

    # Mast.
    mast_top, mast_h = _PAD_Y, 44
    mast_cy = mast_top + mast_h // 2
    x = _wordmark(img, left, mast_cy) + 12
    _draw_text(d, (x, mast_cy), "THEORIA", "display", 24, INK, "lm")

    heading = data.heading.upper()
    tile = 44
    tile_x = right - tile
    heading, hfont = _fit_line(heading, "display", 22, 520)
    hw = _text_w(heading, hfont)
    d.text((tile_x - 14 - hw, mast_cy), heading, font=hfont, fill=INK, anchor="lm")
    _shape(img, (tile_x, mast_top, right, mast_top + tile),
           lambda dr, s: dr.rounded_rectangle([0, 0, tile * s - 1, tile * s - 1],
                                              radius=10 * s, fill=255), SHEET_2)
    icon_file = _ICONS / f"{data.kind}.png"
    if icon_file.exists():
        icon = Image.open(icon_file).convert("RGBA").resize((24, 24), Image.LANCZOS)
        img.paste(icon, (tile_x + 10, mast_cy - 12), icon)
    rule_y = mast_top + mast_h + 16
    d.line([(left, rule_y), (right, rule_y)], fill=RULE, width=1)

    # Owner row.
    row_top = rule_y + 28
    disc = 72
    _shape(img, (left, row_top, left + disc, row_top + disc),
           lambda dr, s: dr.ellipse([0, 0, disc * s - 1, disc * s - 1], fill=255), SHEET_2)
    initial = (data.owner_name[:1] or "?").upper()
    _draw_text(d, (left + disc // 2, row_top + disc // 2 + 1), initial, "display", 34, INK, "mm")

    # The line, right-aligned, up to 520 px, two lines at most.
    line_w = 520
    lines, lfont = _wrap(data.line, "body", 28, line_w, 2)
    lh = 34
    ly = row_top + disc // 2 - lh * len(lines) / 2 + lh / 2
    for ln in lines:
        d.text((right, ly), ln, font=lfont, fill=INK, anchor="rm")
        ly += lh
    line_left = right - max((_text_w(ln, lfont) for ln in lines), default=0)

    text_x = left + disc + 20
    handle_room = max(line_left - 32 - text_x, 120)
    handle, hfont2 = _fit_line("@" + data.owner_name, "mono", 28, handle_room, min_size=16)
    since_tpl = data.since_template
    month, year = data.month_label, str(data.owner_since.year)
    sfont_m = _font("body", since_tpl + month, 18)
    sy = row_top + 52
    d.text((text_x, row_top + 20), handle, font=hfont2, fill=INK, anchor="lm")
    # "Member since <Month> <year>", the year in mono as on the card.
    before, _, after = since_tpl.partition("%(year)s")
    before = before.replace("%(month)s", month)
    after = after.replace("%(month)s", month)
    sx = text_x
    d.text((sx, sy), before, font=sfont_m, fill=INK_FAINT, anchor="lm")
    sx += _text_w(before, sfont_m)
    yfont = _font("mono", year, 18)
    d.text((sx, sy), year, font=yfont, fill=INK_FAINT, anchor="lm")
    sx += _text_w(year, yfont)
    if after:
        d.text((sx, sy), after, font=sfont_m, fill=INK_FAINT, anchor="lm")

    # Shelf.
    foot_h = 22
    foot_top = HEIGHT - _PAD_Y - foot_h
    shelf_top = row_top + disc + 24
    shelf_bottom = foot_top - 24
    ph = shelf_bottom - shelf_top
    pw = round(ph * 2 / 3)
    posters = data.posters[:4]
    n = len(posters)
    gap = (content_w - 4 * pw) // 3 if n == 4 else 16
    gap = max(gap, 16)
    for i, p in enumerate(posters):
        x0 = left + i * (pw + gap)
        box = (x0, shelf_top, x0 + pw, shelf_bottom)
        art = _cover(p.image, pw, ph) if p.image else None
        if art is not None:
            img.paste(art, (x0, shelf_top))
        else:
            _plate(img, box, p)
        if p.rank:
            _rank(img, box, p.rank)

    # Foot.
    foot_cy = foot_top + foot_h // 2
    d = ImageDraw.Draw(img)
    d.rectangle([left, foot_cy - 1, left + 15, foot_cy + 1], fill=kind_colour)
    url_max = content_w - 16 - 14 - (200 if data.more else 0)
    url, ufont = _fit_line(data.printed_url, "mono", 18, url_max, min_size=12)
    d.text((left + 16 + 12, foot_cy), url, font=ufont, fill=INK_MUTED, anchor="lm")
    if data.more > 0:
        label = data.more_label
        lfont2 = _font("body", label, 18)
        nfont = _font("mono", "+0", 18)
        num = f"+{data.more}"
        lw, nw = _text_w(label, lfont2), _text_w(num, nfont)
        d.text((right, foot_cy), label, font=lfont2, fill=INK_FAINT, anchor="rm")
        d.text((right - lw - 6, foot_cy), num, font=nfont, fill=INK, anchor="rm")

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
