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
