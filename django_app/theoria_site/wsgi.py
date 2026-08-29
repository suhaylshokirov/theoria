"""
WSGI config for theoria_site project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os
import sys
from pathlib import Path

# Put django_app/ on sys.path before Django resolves DJANGO_SETTINGS_MODULE.
#
# Locally this is implicit: manage.py lives in django_app/, so Python puts that
# directory on sys.path and "theoria_site.settings" imports. A hosted function
# has no manage.py in the loop — the platform imports this file by path from
# the *repository* root, where "theoria_site" resolves to nothing and Django
# dies at startup with ModuleNotFoundError. Making the entrypoint state its own
# import root removes the assumption rather than depending on who launched it.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'theoria_site.settings')

application = get_wsgi_application()
