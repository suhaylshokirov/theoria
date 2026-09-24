from __future__ import annotations

from django.utils.translation import gettext as _


def js_strings(request):
    """Every string theoria.js writes into the page, in the active language.

    Rendered once by base.html as a json_script block and read by the script's
    `t()` helper. The msgids deliberately match the ones the templates use
    ("Open menu", "See more", ...), so gettext gives a phrase one translation
    whether markup or script displays it -- never a copy in each file.
    """
    return {
        "js_strings": {
            "Open menu": _("Open menu"),
            "Close menu": _("Close menu"),
            "Switch to dark theme": _("Switch to dark theme"),
            "Switch to light theme": _("Switch to light theme"),
            "See more": _("See more"),
            "See less": _("See less"),
            "Filter": _("Filter"),
            "Avg rating": _("Avg rating"),
            "Total revenue": _("Total revenue"),
            "Video": _("Video"),
            "Open film guide": _("Open film guide"),
            "Close film guide": _("Close film guide"),
            "Sending…": _("Sending…"),
            "Enter a valid email address.": _("Enter a valid email address."),
            "Pick for tonight": _(
                "Tell me a little about your mood. I can start with something funny, "
                "thoughtful, intense, or comforting."
            ),
            "Find by mood": _(
                "Choose a feeling and I will narrow it down: light, romantic, "
                "thrilling, or strange."
            ),
            "Surprise me": _(
                "A surprise pick will be ready once your personal recommendations "
                "are connected."
            ),
            "assistant fallback": _(
                "I am still in demo mode. Soon I will use your taste profile to make "
                "a personal recommendation."
            ),
            # {label} is the button's own text; {seconds} counts down.
            "Resend wait": _("{label} ({seconds}s)"),
            # Magnitude suffixes for the analytics axes: billions, millions,
            # thousands. Russian/Uzbek abbreviate these differently ("млрд").
            "compact B": _("B"),
            "compact M": _("M"),
            "compact K": _("K"),
        }
    }
