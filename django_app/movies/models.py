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
    imdb_id = models.CharField(max_length=20, null=True)
    original_title = models.TextField(null=True)
    homepage = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "dim_movie"

    def __str__(self):
        return self.title


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


class MovieRating(models.Model):
    """fact_movie_rating: one row per (movie, source) — Tasks 66-68.

    Unlike fact_movie_metrics (movie, date, genre), this table carries no
    genre fan-out: a film has exactly one row per rating source, so reading
    it needs none of the .values(...).distinct() dedupe guards the older
    table required everywhere it was read. `source` is 'imdb' or 'tmdb'
    (enforced by a CHECK in warehouse/ddl/15_movie_ratings.sql); the UI
    reads 'imdb' exclusively (Task 68) — TMDB's own vote_average/vote_count
    are still loaded and queryable here, never rendered.

    Same fake-single-PK workaround as the other composite-PK fact models:
    `movie` carries primary_key=True purely to satisfy Django's one-pk rule;
    the real PK is the composite (movie_id, source) in Postgres.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True
    )
    source = models.CharField(max_length=16)
    rating = models.DecimalField(max_digits=4, decimal_places=2, null=True)
    vote_count = models.IntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_movie_rating"

    def __str__(self):
        return f"{self.movie_id}/{self.source}"


class Company(models.Model):
    """A production company (Task 58). Films have 2.81 companies on average."""

    company_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    logo_path = models.TextField(null=True)
    origin_country = models.CharField(max_length=10, null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)
    # Task 65: from GET /company/{id}. Sparse — description ~1% of studios,
    # headquarters ~53%, homepage ~29%, a parent ~10% of the ones people open.
    # parent_company_id is a soft reference (its target often has no
    # dim_company row); studio_detail() resolves it at read time.
    description = models.TextField(null=True)
    headquarters = models.TextField(null=True)
    homepage = models.TextField(null=True)
    parent_company_id = models.IntegerField(null=True)
    parent_company_name = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "dim_company"

    def __str__(self):
        return self.name


class MovieCompany(models.Model):
    """bridge_movie_company: which studios worked on which films.

    Same fake-single-PK workaround as the other composite-PK tables above —
    `movie` carries primary_key=True purely to satisfy Django's one-pk-per-
    model rule; the real PK is the composite (movie_id, company_id) in
    Postgres. Declared explicitly rather than as a ManyToManyField(through=...)
    on Movie/Company: a ManyToManyField expects Django to own and generate the
    join table, but bridge_movie_company already exists and is managed
    entirely by warehouse/ddl/13_companies.sql — this model just describes it.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True,
        related_name="movie_companies",
    )
    company = models.ForeignKey(
        Company, on_delete=models.DO_NOTHING, db_column="company_id",
        related_name="movie_companies",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_movie_company"

    def __str__(self):
        return f"{self.movie_id}/{self.company_id}"


class Country(models.Model):
    """A country (Task 61). Uses its ISO 3166-1 alpha-2 code directly as the
    primary key — no surrogate id or slug, since the code is already short,
    stable and URL-safe."""

    country_code = models.CharField(max_length=10, primary_key=True)
    name = models.TextField()

    class Meta:
        managed = False
        db_table = "dim_country"

    def __str__(self):
        return self.name


class Language(models.Model):
    """A language (Task 61). Same natural-key reasoning as Country, keyed on
    ISO 639-1. `english_name` is nullable — prefer it for display and fall
    back to `name` (its own-language form) when absent."""

    language_code = models.CharField(max_length=10, primary_key=True)
    name = models.TextField()
    english_name = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "dim_language"

    def __str__(self):
        return self.english_name or self.name


class MovieCountry(models.Model):
    """bridge_movie_country: which countries a film originates from and/or
    was produced in. Same fake-single-PK workaround as the other bridge/fact
    models above; the real PK is the composite (movie_id, country_code,
    relation) in Postgres. `relation` ("origin"/"production") is part of that
    key rather than a plain payload column, because Task 57 found the two are
    simultaneously-true claims about a film's country that disagree on ~23%
    of films — folding relation out of the key would let one overwrite the
    other on upsert.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True,
        related_name="movie_countries",
    )
    country = models.ForeignKey(
        Country, on_delete=models.DO_NOTHING, db_column="country_code",
        related_name="movie_countries",
    )
    relation = models.CharField(max_length=20)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_movie_country"

    def __str__(self):
        return f"{self.movie_id}/{self.country_id}/{self.relation}"


class MovieLanguage(models.Model):
    """bridge_movie_language: which languages are spoken in a film. Same
    fake-single-PK workaround as the other bridge/fact models above; the real
    PK is the composite (movie_id, language_code) in Postgres.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id", primary_key=True,
        related_name="movie_languages",
    )
    language = models.ForeignKey(
        Language, on_delete=models.DO_NOTHING, db_column="language_code",
        related_name="movie_languages",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_movie_language"

    def __str__(self):
        return f"{self.movie_id}/{self.language_id}"
