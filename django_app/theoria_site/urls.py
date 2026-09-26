"""
URL configuration for theoria_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.conf.urls.i18n import i18n_patterns
from django.urls import include, path

from core import account_urls
from core import views as core_views

urlpatterns = []

# English keeps its bare URLs (no redirects, no broken bookmarks); ru and uz
# live under /ru/ and /uz/. Every reader-facing route is inside the prefix so
# a language survives navigation -- a `{% url %}` reverses under the active
# language, so links never fall back to English on their own.
urlpatterns += i18n_patterns(
    # set_language, the switcher's POST endpoint. It sits INSIDE the prefix on
    # purpose: set_language re-prefixes `next` by resolving it under the
    # request's active language, and that language comes from the URL prefix.
    # An unprefixed endpoint would resolve a reader's /ru/... `next` as
    # English, fail to translate it, and leave them on the Russian page.
    path('i18n/', include('django.conf.urls.i18n')),
    path('assistant/', include('assistant.urls')),
    path('account/', include(account_urls)),
    path('analytics/', include('analytics.urls')),
    path('accounts/', include('accounts.urls')),
    # /me/, not /accounts/me/: the reader page reads as a site-level route
    # (the username's own reserved slot -- see the accounts.User username
    # blocklist), not as belonging to the accounts app's own URL space.
    path('me/', core_views.account, name='profile'),
    path('', include('movies.urls')),
    prefix_default_language=False,
)

# See settings.ADMIN_ENABLED: the admin has nothing to administer here and no
# database to sign in against once deployed, so the route exists only where it
# can actually work rather than 404-ing or 500-ing on a public URL.
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path('admin/', admin.site.urls))
