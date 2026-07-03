"""Database router sending warehouse-backed apps to the read-only Postgres DB.

`movies` and `analytics` models are unmanaged mirrors of the star schema
built by the ETL pipeline (see docs/architecture.md). Everything else
(auth, sessions, admin) stays on Django's own sqlite database.
"""

WAREHOUSE_APPS = {"movies", "analytics"}


class WarehouseRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label in WAREHOUSE_APPS:
            return "warehouse"
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label in WAREHOUSE_APPS:
            return "warehouse"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in WAREHOUSE_APPS:
            return False
        if db == "warehouse":
            return False
        return None
