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
    # Task 72: from GET /person/{id}. Sparse even among people with a photo
    # (imdb_id 94%, birthday 66%, biography 64%, place_of_birth 64%,
    # homepage 16%, deathday 14%); person_detail() renders only the pieces
    # that resolve. also_known_as is a list, so it lives in Alias, not here.
    biography = models.TextField(null=True)
    birthday = models.DateField(null=True)
    deathday = models.DateField(null=True)
    place_of_birth = models.TextField(null=True)
    homepage = models.TextField(null=True)
    imdb_id = models.CharField(max_length=20, null=True)

    class Meta:
        managed = False
        db_table = "dim_person"

    def __str__(self):
        return self.name


class Alias(models.Model):
    """person_alias: a person's also_known_as entries (Task 72).

    Neither a fact nor a bridge — repeating text attached to one dimension.
    Same fake-single-PK workaround as the composite-PK fact models: `person`
    carries primary_key=True purely to satisfy Django's one-pk rule; the real
    PK is the composite (person_id, alias) in Postgres.
    """

    person = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_column="person_id",
        primary_key=True, related_name="aliases",
    )
    alias = models.TextField()
    ordering = models.IntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "person_alias"

    def __str__(self):
        return self.alias


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


class MovieVideo(models.Model):
    """dim_movie_video: a film's trailers and clips (Task 74).

    Not a fact (no measure — `size` is a resolution) and not a bridge (there
    is no dim_video to join to); it's a multi-valued attribute of dim_movie.
    Same fake-single-PK workaround as the composite-PK fact models: `movie`
    carries primary_key=True purely to satisfy Django's one-pk rule; the real
    PK is the composite (movie_id, video_id) in Postgres.

    The loader REPLACES a film's rows rather than upserting them (a video can
    vanish upstream), so the ORM never sees a stale row — see
    warehouse/ddl/18_movie_videos.sql.
    """

    movie = models.ForeignKey(
        Movie, on_delete=models.DO_NOTHING, db_column="movie_id",
        primary_key=True, related_name="videos",
    )
    video_id = models.CharField(max_length=24)
    name = models.TextField(null=True)
    key = models.TextField(null=True)
    site = models.TextField(null=True)
    type = models.TextField(null=True)
    official = models.BooleanField(null=True)
    size = models.IntegerField(null=True)
    iso_639_1 = models.CharField(max_length=8, null=True)
    iso_3166_1 = models.CharField(max_length=8, null=True)
    published_at = models.DateTimeField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "dim_movie_video"

    def __str__(self):
        return f"{self.movie_id}/{self.video_id}"


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


# ---------------------------------------------------------------------------
# TV Shows (Task 86+) — the structural mirror of dim_movie and its bridges.
# See warehouse/ddl/19_series.sql-22_episodes.sql and the "Feature — TV Shows"
# block in tasks.md for the full design rationale.
# ---------------------------------------------------------------------------


class Series(models.Model):
    """dim_series: a TV show (Task 79)."""

    series_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    original_name = models.TextField(null=True)
    first_air_date = models.DateField(null=True)
    last_air_date = models.DateField(null=True)
    number_of_seasons = models.IntegerField(null=True)
    number_of_episodes = models.IntegerField(null=True)
    status = models.TextField(null=True)
    type = models.TextField(null=True)
    in_production = models.BooleanField(null=True)
    original_language = models.CharField(max_length=10, null=True)
    overview = models.TextField(null=True)
    tagline = models.TextField(null=True)
    poster_path = models.TextField(null=True)
    backdrop_path = models.TextField(null=True)
    homepage = models.TextField(null=True)
    imdb_id = models.CharField(max_length=16, null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

    class Meta:
        managed = False
        db_table = "dim_series"

    def __str__(self):
        return self.name


class Network(models.Model):
    """dim_network: a broadcaster/streamer (Task 79).

    A separate dimension from Company, not a `kind` column on dim_company —
    TMDB keys networks in their own id namespace (network 49 is HBO, company
    49 is Universal Studios), so a shared table would collide on the PK.
    """

    network_id = models.IntegerField(primary_key=True)
    name = models.TextField()
    logo_path = models.TextField(null=True)
    origin_country = models.CharField(max_length=10, null=True)
    slug = models.SlugField(max_length=300, unique=True, null=True)

    class Meta:
        managed = False
        db_table = "dim_network"

    def __str__(self):
        return self.name


class Season(models.Model):
    """dim_season: a show's seasons (Task 84).

    Unlike the composite-PK fact/bridge tables, season_id is TMDB's real
    global season id, so this needs none of the fake-single-PK workaround.
    """

    season_id = models.IntegerField(primary_key=True)
    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id",
        related_name="seasons",
    )
    season_number = models.SmallIntegerField(null=True)
    name = models.TextField(null=True)
    air_date = models.DateField(null=True)
    episode_count = models.IntegerField(null=True)
    overview = models.TextField(null=True)
    poster_path = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "dim_season"

    def __str__(self):
        return f"{self.series_id}/S{self.season_number}"


class Episode(models.Model):
    """dim_episode: every episode, at TMDB's real global episode id — again a
    real single-column PK, no fake-PK workaround (Task 84).

    Loaded by REPLACE, not upsert (_replace_by_parent(), scoped to series_id):
    TMDB can renumber or withdraw episodes, so a series' episode set must be
    able to shrink — see 22_episodes.sql.
    """

    episode_id = models.IntegerField(primary_key=True)
    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id",
        related_name="episodes",
    )
    season_number = models.SmallIntegerField(null=True)
    episode_number = models.SmallIntegerField(null=True)
    name = models.TextField(null=True)
    air_date = models.DateField(null=True)
    runtime = models.IntegerField(null=True)
    overview = models.TextField(null=True)
    still_path = models.TextField(null=True)
    episode_type = models.TextField(null=True)
    production_code = models.TextField(null=True)
    imdb_id = models.CharField(max_length=16, null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "dim_episode"

    def __str__(self):
        return f"{self.series_id}/S{self.season_number}E{self.episode_number}"


class SeriesCredit(models.Model):
    """fact_series_credit: every person who worked on a show (Task 80).

    Same fake-single-PK workaround as Credit: `series` carries
    primary_key=True purely to satisfy Django's one-pk rule; the real PK is
    the composite (series_id, person_id, department, job, character_name) —
    wider than fact_credit's because 6.7% of cast hold more than one
    character in a single series (see 20_series_credits.sql).
    """

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="credits",
    )
    person = models.ForeignKey(
        Person, on_delete=models.DO_NOTHING, db_column="person_id",
        related_name="series_credits",
    )
    department = models.TextField()
    job = models.TextField()
    character_name = models.TextField()
    episode_count = models.IntegerField(null=True)
    ordering = models.SmallIntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_series_credit"

    def __str__(self):
        return f"{self.series_id}/{self.person_id}/{self.job}"


class SeriesRating(models.Model):
    """fact_series_rating: a show's rating of record, from IMDb (Task 81).

    The Phase 15 contract extended to TV — byte-for-byte the fact_movie_rating
    shape with series_id in place of movie_id. Same fake-single-PK workaround
    as MovieRating.
    """

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
    )
    source = models.CharField(max_length=16)
    rating = models.DecimalField(max_digits=4, decimal_places=2, null=True)
    vote_count = models.IntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_series_rating"

    def __str__(self):
        return f"{self.series_id}/{self.source}"


class EpisodeRating(models.Model):
    """fact_episode_rating: an episode's rating of record, from IMDb (Task 84).

    The third copy of the fact_movie_rating shape, after SeriesRating. Same
    fake-single-PK workaround: `episode` carries primary_key=True.
    """

    episode = models.ForeignKey(
        Episode, on_delete=models.DO_NOTHING, db_column="episode_id", primary_key=True,
    )
    source = models.CharField(max_length=16)
    rating = models.DecimalField(max_digits=4, decimal_places=2, null=True)
    vote_count = models.IntegerField(null=True)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "fact_episode_rating"

    def __str__(self):
        return f"{self.episode_id}/{self.source}"


class SeriesGenre(models.Model):
    """bridge_series_genre: which genres a show belongs to (Task 79).

    Unlike dim_movie, which has no genre bridge (genre lives only in
    fact_movie_metrics), TV genres are a real factless bridge — so, unlike
    movie_list()'s genre filter, series_list()'s needs no .distinct() dedupe
    guard. Same fake-single-PK workaround as MovieCompany: `series` carries
    primary_key=True.
    """

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="series_genres",
    )
    genre = models.ForeignKey(
        Genre, on_delete=models.DO_NOTHING, db_column="genre_id",
        related_name="series_genres",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_series_genre"

    def __str__(self):
        return f"{self.series_id}/{self.genre_id}"


class SeriesCompany(models.Model):
    """bridge_series_company: which studios worked on which shows (Task 79)."""

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="series_companies",
    )
    company = models.ForeignKey(
        Company, on_delete=models.DO_NOTHING, db_column="company_id",
        related_name="series_companies",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_series_company"

    def __str__(self):
        return f"{self.series_id}/{self.company_id}"


class SeriesCountry(models.Model):
    """bridge_series_country: which countries a show originates from and/or
    was produced in (Task 79). `relation` is part of the PK for the same
    reason MovieCountry's is — origin and production are simultaneously-true
    claims about a show's country that can disagree.
    """

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="series_countries",
    )
    country = models.ForeignKey(
        Country, on_delete=models.DO_NOTHING, db_column="country_code",
        related_name="series_countries",
    )
    relation = models.CharField(max_length=20)
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_series_country"

    def __str__(self):
        return f"{self.series_id}/{self.country_id}/{self.relation}"


class SeriesLanguage(models.Model):
    """bridge_series_language: which languages are spoken in a show (Task 79)."""

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="series_languages",
    )
    language = models.ForeignKey(
        Language, on_delete=models.DO_NOTHING, db_column="language_code",
        related_name="series_languages",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_series_language"

    def __str__(self):
        return f"{self.series_id}/{self.language_id}"


class SeriesNetwork(models.Model):
    """bridge_series_network: which networks aired which shows (Task 79)."""

    series = models.ForeignKey(
        Series, on_delete=models.DO_NOTHING, db_column="series_id", primary_key=True,
        related_name="series_networks",
    )
    network = models.ForeignKey(
        Network, on_delete=models.DO_NOTHING, db_column="network_id",
        related_name="series_networks",
    )
    ingestion_date = models.DateField()

    class Meta:
        managed = False
        db_table = "bridge_series_network"

    def __str__(self):
        return f"{self.series_id}/{self.network_id}"
