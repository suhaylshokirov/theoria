"""`{% datacache %}`: cache a rendered template fragment (Task 118).

    {% load datacache %}
    {% datacache "series-episodes" series.series_id %}
        ...markup that is identical for every reader...
    {% enddatacache %}

Django's own `{% cache %}` would work, but it would bypass what Task 113 built:
the data version in the key (so a warehouse load retires every fragment without
anyone deleting anything), the schema number, the fail-open behaviour when
Redis is down, the size ceiling, and the Server-Timing report. This tag goes
through `core.datacache.cached()` so a fragment gets all of that.

The active language is added to the key here, so a template cannot forget it:
a fragment holds translated labels and localised dates, and leaving the language
out would hand the first Russian reader's page to everyone.

What does NOT belong inside: anything that depends on who is looking -- the
signed-in user, a CSRF token, per-user flags such as "in my collection". The
fragment is shared between readers.
"""

from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from core.datacache import cached

register = template.Library()


class DataCacheNode(template.Node):
    def __init__(self, nodelist, name, parts):
        self.nodelist = nodelist
        self.name = name
        self.parts = parts

    def render(self, context):
        name = str(self.name.resolve(context))
        parts = [part.resolve(context) for part in self.parts]
        lang = (get_language() or "en").split("-")[0]
        # Builds the fragment (and so evaluates any lazy context value it
        # reads) only on a miss.
        return cached(
            name,
            lambda: mark_safe(self.nodelist.render(context)),
            *parts,
            lang=lang,
        )


@register.tag("datacache")
def do_datacache(parser, token):
    bits = token.split_contents()
    if len(bits) < 2:
        raise template.TemplateSyntaxError(
            "'datacache' needs a fragment name, then any values that tell "
            "fragments apart (e.g. {% datacache \"series-episodes\" series.series_id %})."
        )
    nodelist = parser.parse(("enddatacache",))
    parser.delete_first_token()
    name = parser.compile_filter(bits[1])
    parts = [parser.compile_filter(bit) for bit in bits[2:]]
    return DataCacheNode(nodelist, name, parts)
