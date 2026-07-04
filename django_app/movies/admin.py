from django.contrib import admin  # noqa: F401

# Warehouse tables are read-only (managed = False); intentionally not
# registered in the admin, which expects writable models.
