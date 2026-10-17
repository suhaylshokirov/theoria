# Only the decimal mark differs from Django's own format: the site prints every
# figure with a dot in every language (ratings, shares, multiples, money), so a
# reader never sees 7.5 on one card and 7,5 on the next. Grouping stays Django's.
DECIMAL_SEPARATOR = "."
