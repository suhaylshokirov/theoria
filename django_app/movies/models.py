"""ORM mirrors of the `theoria` PostgreSQL warehouse (star schema).

Every model is `managed = False`: Django never creates, alters, or drops
these tables — the DDL in warehouse/ddl/ is the single source of truth.
This is enforced twice over: `WarehouseRouter.allow_migrate()` already
refuses migrations against the `warehouse` database (see core/routers.py);
`managed = False` here is defense-in-depth so a future `makemigrations`
never generates a migration for these models even by accident.

Both fact tables have a composite primary key in Postgres
(see warehouse/ddl/02_facts.sql), which Django's ORM does not support
natively. Each fact model instead marks `movie_id` as `primary_key=True`
purely to satisfy Django's "every model needs exactly one pk field"
requirement; the real uniqueness constraint lives in the database via the
named `pk_fact_*` constraints, not in the ORM. These models are read-only,
so nothing here ever relies on `movie_id` alone being unique.
"""

from django.db import models


class Genre(models.Model):
    genre_id = models.IntegerField(primary_key=True)
    genre_name = models.TextField()

    class Meta:
        managed = False
        db_table = "dim_genre"

    def __str__(self):
        return self.genre_name


class Movie(models.Model):
    movie_id = models.IntegerField(primary_key=True)
    title = models.TextField()
    release_date = models.DateField(null=True)
    runtime = models.IntegerField(null=True)
    budget = models.BigIntegerField(null=True)
    revenue = models.BigIntegerField(null=True)
    original_language = models.CharField(max_length=10, null=True)
    status = models.CharField(max_length=50, null=True)

    class Meta:
        managed = False
        db_table = "dim_movie"

    def __str__(self):
        return self.title


class Actor(models.Model):
    actor_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    gender = models.SmallIntegerField(null=True)
    popularity = models.DecimalField(max_digits=10, decimal_places=4, null=True)

    class Meta:
        managed = False
        db_table = "dim_actor"

    def __str__(self):
        return self.name


class Director(models.Model):
    director_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    gender = models.SmallIntegerField(null=True)
    popularity = models.DecimalField(max_digits=10, decimal_places=4, null=True)

    class Meta:
        managed = False
        db_table = "dim_director"

    def __str__(self):
        return self.name


class Date(models.Model):
    date_id = models.IntegerField(primary_key=True)  # surrogate key: YYYYMMDD
    full_date = models.DateField()
    year = models.SmallIntegerField()
    month = models.SmallIntegerField()
    day = models.SmallIntegerField()
    decade = models.SmallIntegerField()

    class Meta:
        managed = False
        db_table = "dim_date"

    def __str__(self):
        return str(self.full_date)


class MovieMetrics(models.Model):
    # unique=True is implied by primary_key=True but is not actually true in
    # the data (one row per movie/date/genre) — see module docstring. The
    # resulting fields.W342 warning is expected and silenced in settings.py.
    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    date = models.ForeignKey(
        Date, on_delete=models.DO_NOTHING, db_column="date_id"
    )
    genre = models.ForeignKey(
        Genre, on_delete=models.DO_NOTHING, db_column="genre_id"
    )
    rating = models.DecimalField(max_digits=4, decimal_places=2, null=True)
    vote_count = models.IntegerField(null=True)
    revenue = models.BigIntegerField(null=True)
    budget = models.BigIntegerField(null=True)
    popularity = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_movie_metrics"

    def __str__(self):
        return f"{self.movie_id}/{self.date_id}/{self.genre_id}"


class Casting(models.Model):
    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    actor = models.ForeignKey(
        Actor, on_delete=models.DO_NOTHING, db_column="actor_id"
    )
    director = models.ForeignKey(
        Director, on_delete=models.DO_NOTHING, db_column="director_id"
    )
    role = models.TextField(null=True)
    ordering = models.SmallIntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_casting"

    def __str__(self):
        return f"{self.movie_id}/{self.actor_id}/{self.director_id}"
