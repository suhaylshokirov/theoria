"""Template filters that render warehouse vocabulary in the active language.

Usage: {% load i18n_vocab %}{{ series.status|display_status }}. The database
value stays English (it is compared and sorted against elsewhere); these only
translate what a reader sees. See movies/vocab.py.
"""

from django import template

from movies import vocab

register = template.Library()

register.filter("display_department", vocab.display_department)
register.filter("display_job", vocab.display_job)
register.filter("display_status", vocab.display_status)
