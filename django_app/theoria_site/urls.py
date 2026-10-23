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
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.contrib import admin
from django.http import HttpResponseRedirect
from django.templatetags.static import static
from django.urls import include, path, re_path

from core import account_urls, share_urls
from core import views as core_views

# Every page has one URL in every language; the language is a cookie (see
# core.middleware.LanguageCookieMiddleware). The old /ru/ and /uz/ prefixes
# (Task 92, superseded by Task 107) stay as redirects so shared links work.
_legacy_langs = "|".join(
    code for code, _name in settings.LANGUAGES if code != settings.LANGUAGE_CODE
)

def favicon(_request):
    # Browsers and crawlers probe /favicon.ico before (or without) reading <head>.
    # Resolved per request: the hashed static name only exists after collectstatic.
    return HttpResponseRedirect(static('img/favicon.ico'))


urlpatterns = [
    path('favicon.ico', favicon),
    re_path(rf"^(?P<lang>{_legacy_langs})/(?P<rest>.*)$", core_views.legacy_language_redirect),
    path('i18n/', include('django.conf.urls.i18n')),
    path('assistant/', include('assistant.urls')),
    path('account/', include(account_urls)),
    # Public, unlisted collection links (core.sharing). /s/ is a reserved username.
    path('s/', include(share_urls)),
    path('analytics/', include('analytics.urls')),
    path('accounts/', include('accounts.urls')),
    # /me/, not /accounts/me/: the reader page reads as a site-level route
    # (the username's own reserved slot -- see the accounts.User username
    # blocklist), not as belonging to the accounts app's own URL space.
    path('me/', core_views.account, name='profile'),
    # A reader's public page. Any non-slash segment routes, not just what the
    # signup rule allows (accounts.models.validate_username_characters): an
    # account made in the admin or shell can bypass that rule, and /me/ links
    # here, so a strict pattern would 500 it. An unknown name is a 404 from
    # the view. There is no /users/ index: handles are not meant to be browsed.
    path('users/<str:username>/', core_views.public_profile, name='public_profile'),
    path('', include('movies.urls')),
]

# See settings.ADMIN_ENABLED: the admin has nothing to administer here and no
# database to sign in against once deployed, so the route exists only where it
# can actually work rather than 404-ing or 500-ing on a public URL.
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path('admin/', admin.site.urls))

# Under DEBUG a missing page gets Django's technical 404, never the site's own,
# so the browser layout suite (tests/ui) could not see the page production
# serves. This renders that same view at a fixed URL. Production does not route
# it, so there the URL is just a real 404 -- the same page either way.
if settings.DEBUG:
    from django.http import Http404
    from django.views.defaults import page_not_found

    urlpatterns.insert(0, path('__404__/', lambda request: page_not_found(request, Http404())))
