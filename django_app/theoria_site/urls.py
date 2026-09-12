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
from django.urls import include, path

from accounts.views import profile

urlpatterns = [
    path('analytics/', include('analytics.urls')),
    path('accounts/', include('accounts.urls')),
    # /me/, not /accounts/me/: the reader page reads as a site-level route
    # (the username's own reserved slot -- see the accounts.User username
    # blocklist), not as belonging to the accounts app's own URL space.
    path('me/', profile, name='profile'),
    path('', include('movies.urls')),
]

# See settings.ADMIN_ENABLED: the admin has nothing to administer here and no
# database to sign in against once deployed, so the route exists only where it
# can actually work rather than 404-ing or 500-ing on a public URL.
if settings.ADMIN_ENABLED:
    urlpatterns.insert(0, path('admin/', admin.site.urls))
