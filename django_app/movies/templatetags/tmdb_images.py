"""Template filter that turns a TMDB relative image path into a full URL.

The warehouse stores only the relative path TMDB returns (e.g.
"/abc123.jpg"); the CDN base URL and size segment are presentation
concerns, so they live here (base URL from config.py) rather than in the
data. Usage: {{ movie.poster_path|tmdb_image:"w342" }}.

Returns "" for a missing/empty path so templates can guard with {% if %}.
This also keeps templates working before the image columns exist in the
warehouse (Workstream B): a nonexistent model attribute resolves to "" in
Django templates, and this filter passes that through.
"""

from django import template

import config

register = template.Library()


@register.filter
def tmdb_image(path, size="w342"):
    if not path:
        return ""
    return f"{config.TMDB_IMAGE_BASE_URL}/{size}{path}"


# TMDB serves each image kind at a fixed ladder of widths. A single fixed size
# is soft on high-density screens (a 150px card on a 3x phone wants ~450px) and
# on wide ones (a full-bleed backdrop on a 1920px monitor), so templates offer
# the whole ladder through srcset and let the browser pick for the slot and the
# screen. Each rung is (TMDB size segment, intrinsic width in px). Profile "h632"
# is a height-keyed size: 632px tall is ~421px wide at TMDB's 2:3 crop. The
# backdrop "original" is nominally 1920px wide (TMDB's usual master size).
SRCSET_LADDERS = {
    "poster": (("w185", 185), ("w342", 342), ("w500", 500), ("w780", 780)),
    "backdrop": (("w780", 780), ("w1280", 1280), ("original", 1920)),
    "profile": (("w185", 185), ("h632", 421)),
    "logo": (("w185", 185), ("w300", 300), ("w500", 500)),
}


@register.filter
def tmdb_srcset(path, kind="poster"):
    """The `srcset` value for a TMDB image path, or "" for a missing path.

    Pair it with a `sizes` attribute describing the slot the image fills, and
    keep a mid-ladder `src` (via `tmdb_image`) as the fallback.
    """
    if not path:
        return ""
    ladder = SRCSET_LADDERS.get(kind, SRCSET_LADDERS["poster"])
    return ", ".join(
        f"{config.TMDB_IMAGE_BASE_URL}/{size}{path} {width}w" for size, width in ladder
    )
