"""ORM mirrors of the `theoria` PostgreSQL warehouse (star schema).

Every model is `managed = False`: Django never creates, alters, or drops
these tables — the DDL in warehouse/ddl/ is the single source of truth.
This is enforced twice over: `WarehouseRouter.allow_migrate()` already
refuses migrations against the `warehouse` database (see core/routers.py);
`managed = False` here is defense-in-depth so a future `makemigrations`
never generates a migration for these models even by accident.

All fact tables have a composite primary key in Postgres
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


class Collection(models.Model):
    """A film franchise (TMDB `belongs_to_collection`)."""

    collection_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    poster_path = models.TextField(null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

    class Meta:
        managed = False
        db_table = "dim_collection"

    def __str__(self):
        return self.name


class Movie(models.Model):
    movie_id = models.IntegerField(primary_key=True)
    title = models.TextField()
    release_date = models.DateField(null=True)
    runtime = models.IntegerField(null=True)
    budget = models.BigIntegerField(null=True)
    revenue = models.BigIntegerField(null=True)
    original_language = models.CharField(max_length=10, null=True)
    status = models.CharField(max_length=50, null=True)
    overview = models.TextField(null=True)
    tagline = models.TextField(null=True)
    poster_path = models.TextField(null=True)
    backdrop_path = models.TextField(null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)
    # Nullable by design: roughly half the catalog belongs to no franchise.
    collection = models.ForeignKey(
        Collection, on_delete=models.DO_NOTHING, db_column="collection_id",
        null=True, related_name="movies",
    )

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
    profile_path = models.TextField(null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

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
    profile_path = models.TextField(null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

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


class Person(models.Model):
    """Anyone holding any credit, in any department.

    Supersedes Actor/Director, which split one person across two tables
    according to whichever credit happened to introduce them.
    """

    person_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    gender = models.SmallIntegerField(null=True)
    popularity = models.DecimalField(max_digits=10, decimal_places=4, null=True)
    profile_path = models.TextField(null=True)
    known_for_department = models.TextField(null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

    class Meta:
        managed = False
        db_table = "dim_person"

    def __str__(self):
        return self.name


class Credit(models.Model):
    """One row per (movie, person, department, job).

    Same fake-primary-key workaround as the other fact models: the real key is
    composite in Postgres and Django can't express that, so `movie` carries
    primary_key=True purely to satisfy the ORM. A person legitimately has
    several rows per film (director + writer + producer), so this PK is not
    unique in the data — the fields.W342 warning is expected and silenced.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    person = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_column="person_id",
        related_name="credits",
    )
    department = models.TextField()
    job = models.TextField()
    character_name = models.TextField(null=True)
    ordering = models.SmallIntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_credit"

    def __str__(self):
        return f"{self.movie_id}/{self.person_id}/{self.job}"


class Collaboration(models.Model):
    """How often two people have worked together. Derived in Gold.

    Pairs are canonical (person_a_id < person_b_id), so a lookup for one person
    has to check both columns — see views.person_detail.
    """

    person_a = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_column="person_a_id",
        primary_key=True, related_name="collaborations_as_a",
    )
    person_b = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_column="person_b_id",
        related_name="collaborations_as_b",
    )
    films_together = models.IntegerField()
    first_year = models.SmallIntegerField(null=True)
    last_year = models.SmallIntegerField(null=True)

    class Meta:
        managed = False
        db_table = "fact_collaboration"

    def __str__(self):
        return f"{self.person_a_id}+{self.person_b_id} ({self.films_together})"


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


class Cast(models.Model):
    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    actor = models.ForeignKey(
        Actor, on_delete=models.DO_NOTHING, db_column="actor_id"
    )
    role = models.TextField(null=True)
    ordering = models.SmallIntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_cast"

    def __str__(self):
        return f"{self.movie_id}/{self.actor_id}"


class Crew(models.Model):
    # fact_crew currently models director credits only, mirroring dim_director
    # (which itself only contains people credited as director) — see
    # warehouse/ddl/02_facts.sql.
    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    director = models.ForeignKey(
        Director, on_delete=models.DO_NOTHING, db_column="director_id"
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_crew"

    def __str__(self):
        return f"{self.movie_id}/{self.director_id}"
