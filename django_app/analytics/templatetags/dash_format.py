"""Number formatting for the analytics dashboard (Task 133).

`compact_money` is the server-side twin of `compact()` in static/js/analytics.js,
so a revenue figure reads the same in a table cell as on a chart axis: "$45.4B",
"$797.3M", and in Russian "$45.4 млрд". The magnitude suffixes are the same
translated msgids the script uses ("B", "M", "K"). The "$" is not localized --
TMDB reports money in USD, so it is a unit of the data, not a formatting choice.
"""

from django import template
from django.utils.formats import number_format
from django.utils.translation import gettext as _

register = template.Library()


@register.filter
def compact_money(value):
    """12345678 -> "$12.3M" in the reader's language; None/"" -> an em dash."""
    if value in (None, ""):
        return "—"
    n = float(value)
    magnitude = abs(n)
    if magnitude >= 1e9:
        return "$" + number_format(n / 1e9, decimal_pos=1) + _("B")
    if magnitude >= 1e6:
        return "$" + number_format(n / 1e6, decimal_pos=1) + _("M")
    if magnitude >= 1e3:
        return "$" + number_format(n / 1e3, decimal_pos=0) + _("K")
    return "$" + number_format(n, decimal_pos=0)
