from datetime import date

from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Max, Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import urlencode
from django.utils.text import slugify


from movies.models import (
    Company, Credit, Genre, Movie, MovieCompany,
    MovieCountry, MovieLanguage, MovieRating, Person,
)

MOVIES_PER_PAGE = 24
PEOPLE_PER_PAGE = 30
STUDIOS_PER_PAGE = 30


def _is_ajax(request):
    """True for the fetch() requests static/js/theoria.js's initLiveFilter()
    makes as a filter form changes — set explicitly in the JS, never sent by
    a plain browser navigation or a no-JS form submit."""
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"

# The movie page's cast and crew are paged in the browser, not here — see
# static/js/theoria.js. The page size lives in the template's data-page-size
# attribute, since it's a property of the rendered widget rather than of the
# query: this view sends every credit either way.
#
# The list pages (/movies/, /people/) stay server-side paged: those queries
# can return 1,215 and 122,685 rows, which is not a payload to hand a browser.
# One film's ~1,200 credits is.

# How many posters the home contact sheet draws. The mosaic is meant to read
# as "the whole catalog at once", so this is a ceiling, not a page size. Since
# the Task 42 corpus expansion (1,200+ films) it is a genuine sample of the
# most recent films rather than the entire catalog.
MOSAIC_LIMIT = 120

# ?sort= values accepted by movie_list, mapped to an order_by expression.
# Nulls always sort last so movies missing a field don't lead the list.
#
# "rating" points at imdb_rating — the single filtered annotation
# (Max("movierating__rating", filter=Q(movierating__source="imdb"))) that
# every rating-bearing view below also uses for card display, so sorting and
# display can never disagree (Task 68). fact_movie_rating is one row per
# (movie, source) — no genre fan-out — so Max() here is a defensive plain
# lookup, not a fan-out collapse the way the equivalent moviemetrics
# annotation used to be.
MOVIE_SORTS = {
    "release": F("release_date").desc(nulls_last=True),
    "rating": F("imdb_rating").desc(nulls_last=True),
    "revenue": F("revenue").desc(nulls_last=True),
    "title": F("title").asc(),
}


def home(request):
    """Landing page: the catalog as a contact sheet, plus warehouse-wide stats."""
    top_rated = (
        Movie.objects.using("warehouse")
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("imdb_rating").desc(nulls_last=True))[:12]
    )
    newest = (
        Movie.objects.using("warehouse")
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        .order_by(F("release_date").desc(nulls_last=True))[:12]
    )

    # The mosaic only holds films that actually have a poster — a missing
    # image would punch a hole in the sheet. It never renders through
    # _movie_card.html (see home.html), so it doesn't need imdb_rating.
    mosaic = (
        Movie.objects.using("warehouse")
        .filter(poster_path__isnull=False)
        .order_by(F("release_date").desc(nulls_last=True))[:MOSAIC_LIMIT]
    )

    context = {
        "movie_count": Movie.objects.using("warehouse").count(),
        "person_count": Person.objects.using("warehouse").count(),
        "credit_count": Credit.objects.using("warehouse").count(),
        # Reads fact_movie_rating instead of fact_movie_metrics (Task 68):
        # the old figure averaged every fact_movie_metrics row, silently
        # over-weighting multi-genre films since a film's rating repeats
        # once per genre there. This is a true per-film average.
        "avg_rating": MovieRating.objects.using("warehouse").filter(
            source="imdb"
        ).aggregate(avg_rating=Avg("rating"))["avg_rating"],
        "top_rated": top_rated,
        "newest": newest,
        "mosaic": mosaic,
    }
    return render(request, "movies/home.html", context)


def movie_list(request):
    """Browsable movie catalog: poster grid + title search + sort + pagination.

    Genre is a facet of a film, filtered here rather than given its own
    /genres/ index page (Task 70; a genre-browsing UI existed briefly and was
    removed on 2026-08-14 — see the status block in CLAUDE.md). Genre
    membership lives only in fact_movie_metrics — there is no bridge table
    for it — so the URL carries a slugified genre *name*
    (?genre=science-fiction), never the raw genre_id: dim_genre has no slug
    column of its own, and a surrogate key has no business in a user-facing
    URL.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in MOVIE_SORTS:
        sort = "release"

    # Only genres that actually have a film in the catalog are offered as a
    # choice — Documentary currently has 0, and a choice that can never
    # return anything is worse than not offering it. distinct=True matters
    # for the same reason the .distinct() below does: fact_movie_metrics'
    # PK is (movie_id, date_id, genre_id), and a film whose release date
    # moved between ingestions holds two date_id rows per genre, which
    # would otherwise double-count its film_count.
    genre_rows = (
        Genre.objects.using("warehouse")
        .annotate(film_count=Count("moviemetrics__movie", distinct=True))
        .filter(film_count__gt=0)
        .order_by("genre_name")
        .values_list("genre_id", "genre_name")
    )
    genre_slugs = {slugify(name): genre_id for genre_id, name in genre_rows}
    genre_choices = [(slugify(name), name) for _, name in genre_rows]

    genre = request.GET.get("genre", "").strip()
    if genre not in genre_slugs:
        # Silent fallback to unfiltered on an unknown slug, the same posture
        # sort/gender/known_for already take elsewhere in this file — not a
        # 404, and not an empty grid.
        genre = ""

    movies = Movie.objects.using("warehouse").all()
    if q:
        movies = movies.filter(title__icontains=q)
    if genre:
        # fact_movie_metrics' PK is (movie_id, date_id, genre_id) and
        # date_id is derived from the *release* date — so a film whose
        # release date moved between ingestions keeps both rows. Two films
        # in the catalog do (Avatar Aang: The Last Airbender, The Odyssey —
        # 7 duplicate (movie, genre) pairs), and without this they would
        # render twice in the grid and be counted twice by the paginator.
        movies = movies.filter(moviemetrics__genre_id=genre_slugs[genre]).distinct()
    # Annotated unconditionally, not only when sort == "rating" — the cards
    # display this figure too, so it must exist whether or not the list is
    # being sorted by it (Task 68). MOVIE_SORTS["rating"] points at the same
    # annotation, so sorting and display can never disagree.
    movies = movies.annotate(
        imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb"))
    )
    movies = movies.order_by(MOVIE_SORTS[sort])

    page_obj = Paginator(movies, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    # Built here, not in the template, so the shared _pager.html partial
    # doesn't need to know which params any given page carries — see
    # _pager.html's docstring.
    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "genre": genre,
        "genre_choices": genre_choices,
        "base_query": urlencode({"q": q, "sort": sort, "genre": genre}),
    }
    # See _person_list()'s identical branch: static/js/theoria.js's
    # initLiveFilter() re-requests this URL with this header on every filter
    # change and only wants the results fragment back, not the page around it.
    if _is_ajax(request):
        return render(request, "movies/_movie_results.html", context)
    return render(request, "movies/movie_list.html", context)


# ?sort= values accepted by person_list, mapped to an order_by expression.
PERSON_SORTS = {
    "popularity": F("popularity").desc(nulls_last=True),
    "name": F("name").asc(),
}

# TMDB's gender codes. 0 ("not specified") is deliberately not a filter option
# below — it isn't a fact about the person, it's TMDB having no answer, and
# offering it as a choice would imply otherwise.
GENDER_LABELS = {"1": "Female", "2": "Male", "3": "Non-binary"}

# Filter options for "known for" — the person's own TMDB craft, independent of
# which credits they hold in *this* catalog (that's what the Acting/Directing
# scope switch already does). Reuses DEPARTMENT_ORDER's names rather than a
# fresh DISTINCT query every request; "Creator" (25 people, a TMDB rarity
# outside this list) is the one department it doesn't offer as a choice.


def _person_list(request, people, list_title, scope):
    """Shared list view for every people index: search, filter, sort, pagination.

    Takes a queryset rather than a model, because the three indexes now differ
    by which credits a person holds, not by which table they live in. Every
    person page lives at /people/<slug>/, so there is no per-list URL name.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "popularity")
    if sort not in PERSON_SORTS:
        sort = "popularity"
    gender = request.GET.get("gender", "")
    if gender not in GENDER_LABELS:
        gender = ""
    known_for = request.GET.get("known_for", "")
    if known_for not in DEPARTMENT_ORDER:
        known_for = ""

    if q:
        people = people.filter(name__icontains=q)
    if gender:
        people = people.filter(gender=int(gender))
    if known_for:
        people = people.filter(known_for_department=known_for)
    people = people.order_by(PERSON_SORTS[sort])

    page_obj = Paginator(people, PEOPLE_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "gender": gender,
        "known_for": known_for,
        "gender_choices": GENDER_LABELS,
        "known_for_choices": DEPARTMENT_ORDER,
        "list_title": list_title,
        "scope": scope,
        "detail_url_name": "movies:person_detail",
        "base_query": urlencode(
            {"q": q, "sort": sort, "gender": gender, "known_for": known_for}
        ),
    }
    # static/js/theoria.js's initLiveFilter() re-requests this same URL with
    # this header on every filter change, and only wants the results back —
    # not the page around them. Without JS, this header is never sent and the
    # form's plain GET submit renders the full page as always.
    if _is_ajax(request):
        return render(request, "movies/_person_results.html", context)
    return render(request, "movies/person_list.html", context)


def _person_queryset(department=None):
    """dim_person, optionally narrowed to people with a credit in one department.

    "Actors" and "Directors" are no longer separate tables — they're the people
    holding an Acting or Directing credit, which is a question about
    fact_credit, not about which dimension someone landed in.
    """
    people = Person.objects.using("warehouse")
    if department:
        people = people.filter(credits__department=department).distinct()
    return people


def person_list(request):
    return _person_list(request, _person_queryset(), "People", "all")


def actor_list(request):
    return _person_list(request, _person_queryset("Acting"), "Acting", "acting")


def director_list(request):
    return _person_list(request, _person_queryset("Directing"), "Directing", "directing")


def movie_detail(request, movie_slug):
    """Single movie: core facts, genres, directors, and cast."""
    movie = get_object_or_404(
        Movie.objects.using("warehouse"), slug=movie_slug
    )
    movie_id = movie.movie_id

    genres = (
        Genre.objects.using("warehouse")
        .filter(moviemetrics__movie_id=movie_id)
        .distinct()
    )

    # One query for every credit on the film, cast and crew alike, then split
    # in Python. Materialized with list() because both the cast Paginator and
    # _merge_crew() below need a concrete sequence — a lazy queryset would
    # otherwise re-hit the database for each. Before fact_credit this page
    # could only show the cast and the director — the ~150 other people who
    # made the film had no warehouse row at all.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("person")
        .order_by(F("ordering").asc(nulls_last=True), "job")
    )

    # Cast is already one row per person — fact_credit's job is the literal
    # "Actor" for every Acting row, so there's nothing to merge here. Its
    # problem is volume alone: the median film has 47, the worst 210. Every
    # one is sent to the page and paged client-side (static/js/theoria.js) —
    # a server round-trip per ten actors was the smoothness cost the reader
    # actually felt, and the whole list is already in memory here anyway.
    cast = [c for c in credits if c.department == "Acting"]

    # Crew is the opposite problem: duplication, not volume alone. A director
    # who also wrote and produced is three fact_credit rows, rendered before
    # this fix as three names in three department sections. _merge_crew()
    # collapses each person to one row under their most senior department.
    #
    # Grouped by department and sent in full, paged client-side the same way.
    # There is no billed-crew subset and no expand toggle any more: paging ten
    # at a time makes the first page short whatever it contains, so a second
    # definition of "important crew" stopped earning its keep.
    crew_credits = [c for c in credits if c.department != "Acting"]
    merged_crew = _merge_crew(crew_credits)

    by_department = {}
    for m in merged_crew:
        by_department.setdefault(m["department"], []).append(m)
    crew = [
        {
            "name": name,
            "people": sorted(rows, key=lambda m: m["person"].name),
            "count": len(rows),
        }
        for name, rows in sorted(
            by_department.items(), key=lambda kv: _department_rank(kv[0])
        )
    ]

    # Unchanged: the "Directed by" record line reads raw credits, not the
    # merged crew list — it only ever needs the Director job, never the full
    # job_display string the merged rows carry.
    directors = [c.person for c in credits if c.job == "Director"]

    studios = [
        mc.company for mc in
        MovieCompany.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("company")
        .order_by("company__name")
    ]

    country_rows = list(
        MovieCountry.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("country")
    )
    countries = _country_provenance(country_rows)

    language_rows = list(
        MovieLanguage.objects.using("warehouse")
        .filter(movie_id=movie_id)
        .select_related("language")
    )
    languages = _movie_languages(movie, language_rows)

    # fact_movie_rating (Task 66-68) is one row per (movie, source) — no
    # genre fan-out — so this is a plain lookup, replacing the old
    # fact_movie_metrics read that needed .values(...).distinct() to
    # collapse a genre-repeated rating before taking one row.
    movie_rating = (
        MovieRating.objects.using("warehouse")
        .filter(movie_id=movie_id, source="imdb")
        .first()
    )

    context = {
        "movie": movie,
        "genres": genres,
        "cast": cast,
        "cast_count": len(cast),
        "crew": crew,
        "crew_person_count": len(merged_crew),
        "credit_count": len(credits),
        "directors": directors,
        "studios": studios,
        "countries": countries,
        "languages": languages,
        "movie_rating": movie_rating,
    }
    return render(request, "movies/movie_detail.html", context)


def _country_provenance(country_rows):
    """Group a film's bridge_movie_country rows into a display-ready shape.

    Origin and production are two simultaneously-true claims about a film's
    country (Task 57) that agree on ~77% of films. Showing both as separately
    labeled rows only when they actually disagree keeps the common case to
    one row instead of two identical lists — the same judgment Task 56 made
    for original_title. Returns a dict with all three keys always present so
    the template doesn't have to branch on which shape it got.
    """
    origin = sorted({r.country.name for r in country_rows if r.relation == "origin"})
    production = sorted(
        {r.country.name for r in country_rows if r.relation == "production"}
    )
    if origin and production and origin != production:
        return {"origin": origin, "production": production, "countries": []}
    return {"origin": [], "production": [], "countries": origin or production}


def _movie_languages(movie, language_rows):
    """One merged, deduplicated language list, anchored on original_language.

    dim_movie.original_language and bridge_movie_language (Task 57/61) are
    two facts about the same thing rather than two different things, so this
    reconciles them into a single ordered list instead of shipping both
    side by side unexplained (Task 62): the original language leads if it
    resolves to a known dim_language row, followed by any other language the
    bridge records for the film, each name listed once.
    """
    names = []
    seen = set()
    original = next(
        (r.language for r in language_rows if r.language_id == movie.original_language),
        None,
    )
    if original:
        names.append(original.name)
        seen.add(original.language_code)
    for r in sorted(language_rows, key=lambda r: r.language.name):
        if r.language_id not in seen:
            names.append(r.language.name)
            seen.add(r.language_id)
    return names


# ?sort= values accepted by studio_list, mapped to an order_by expression.
STUDIO_SORTS = {
    "film_count": F("film_count").desc(nulls_last=True),
    "revenue": F("total_revenue").desc(nulls_last=True),
    "name": F("name").asc(),
}


def studio_list(request):
    """Browsable studio index: logo grid + name search + sort + pagination —
    the same shape as /people/ (Task 62 redesign), not the ranked table it
    used to be. A studio is a browsable entity with its own identity and
    artwork, same as a person; a table of numbers was the wrong instinct
    even though .annotate(Count).filter(...__gt=0) (a HAVING clause) still
    does the ranking underneath.
    """
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "film_count")
    if sort not in STUDIO_SORTS:
        sort = "film_count"

    # total_revenue sums straight off dim_movie through the bridge — one bridge
    # row per (movie, studio), so no genre-fanout guard is needed here (same as
    # studio_detail()'s revenue stat).
    companies = (
        Company.objects.using("warehouse")
        .annotate(
            film_count=Count("movie_companies"),
            total_revenue=Sum("movie_companies__movie__revenue"),
        )
        .filter(film_count__gt=0)
    )
    if q:
        companies = companies.filter(name__icontains=q)
    companies = companies.order_by(STUDIO_SORTS[sort], "name")

    page_obj = Paginator(companies, STUDIOS_PER_PAGE).get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj, "q": q, "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
    }
    # See _person_list()'s identical branch: static/js/theoria.js's
    # initLiveFilter() re-requests this URL with this header on every filter
    # change and only wants the results fragment back, not the page around it.
    if _is_ajax(request):
        return render(request, "movies/_studio_results.html", context)
    return render(request, "movies/studio_list.html", context)


def studio_detail(request, company_slug):
    """One studio: header stats over its whole output, plus a searchable,
    sortable, paginated filmography — the same movie-browsing toolbar as
    /movies/, scoped to this studio's films (Task 62 redesign).
    """
    company = get_object_or_404(Company.objects.using("warehouse"), slug=company_slug)

    # Task 65: resolve the parent company for a link, if it has one *and* that
    # parent is itself in the catalog. A holding-company parent (Warner Bros.
    # Entertainment, Viacom International) is often never directly credited on
    # a film, so it has no dim_company row — the template then falls back to
    # company.parent_company_name as plain text. One extra query, and only on
    # a single studio page, never the list.
    parent_company = None
    if company.parent_company_id:
        parent_company = (
            Company.objects.using("warehouse")
            .filter(company_id=company.parent_company_id)
            .first()
        )

    movie_ids = list(
        MovieCompany.objects.using("warehouse")
        .filter(company_id=company.company_id)
        .values_list("movie_id", flat=True)
    )
    all_movies = Movie.objects.using("warehouse").filter(movie_id__in=movie_ids)

    # Revenue sums straight off dim_movie (one row per film). Rating used to
    # need a fact_movie_metrics genre-fanout guard (.values().distinct()
    # before averaging) — fact_movie_rating is one row per (movie, source),
    # so that guard is unnecessary here and deliberately not ported
    # (Task 68): a plain .filter(source="imdb").aggregate(Avg(...)) is
    # already correct at this grain. Computed once, over the *whole*
    # filmography — the header stats describe this studio's entire output
    # and must not shift as the grid below is filtered, the same way a
    # person page's stat row doesn't move when someone pages through their
    # filmography.
    stats = all_movies.aggregate(film_count=Count("movie_id"), total_revenue=Sum("revenue"))
    avg_rating = (
        MovieRating.objects.using("warehouse")
        .filter(movie_id__in=movie_ids, source="imdb")
        .aggregate(avg_rating=Avg("rating"))["avg_rating"]
    )
    span = all_movies.aggregate(start=Min("release_date"), end=Max("release_date"))

    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in MOVIE_SORTS:
        sort = "release"

    # Annotated unconditionally (not only when sort == "rating"), same as
    # movie_list() — the grid below always displays this figure, so it must
    # always be there to display, whichever way the list is sorted.
    movies = all_movies.annotate(
        imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb"))
    )
    if q:
        movies = movies.filter(title__icontains=q)
    movies = movies.order_by(MOVIE_SORTS[sort])

    page_obj = Paginator(movies, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "company": company,
        "parent_company": parent_company,
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
        "film_count": stats["film_count"],
        "total_revenue": stats["total_revenue"],
        "avg_rating": avg_rating,
        "period": _career_period(span["start"], span["end"]),
    }
    # Same live-filter contract as movie_list()/_person_list(): the header
    # stats above never need to be part of the swap, so only the grid+pager
    # fragment is returned for an AJAX refetch.
    if _is_ajax(request):
        return render(request, "movies/_studio_movies_results.html", context)
    return render(request, "movies/studio_detail.html", context)


def _career_period(start, end):
    """Render a career span as a person-page stat, e.g. "1997–2019" or "2019–Active".

    A closed range naming the same year twice (e.g. "2026–2026" for a single
    film released this year) reads as a typo, not a fact. And a range that
    ends in the current year isn't really "closed" — the person's latest
    known film is one that just came out, not one that ended their career.
    """
    if not start:
        return "—"
    current_year = date.today().year
    if end.year >= current_year:
        return "Active" if start.year == end.year else f"{start.year}–Active"
    return str(start.year) if start.year == end.year else f"{start.year}–{end.year}"


# Acting first, then the crafts in roughly the order a viewer thinks about
# them; anything TMDB reports outside this list is appended alphabetically.
DEPARTMENT_ORDER = [
    "Acting", "Directing", "Writing", "Production", "Camera", "Editing",
    "Sound", "Art", "Costume & Make-Up", "Visual Effects", "Lighting", "Crew",
]


def _department_rank(name):
    """Sort key for a department name against DEPARTMENT_ORDER.

    Extracted from the identical inline lambda that used to live separately in
    both movie_detail and person_detail. Anything TMDB reports outside the
    list (e.g. the "Actors" anomaly on movie 1372) sorts after every known
    one, alphabetically among themselves.
    """
    return (
        DEPARTMENT_ORDER.index(name) if name in DEPARTMENT_ORDER
        else len(DEPARTMENT_ORDER),
        name,
    )


def _merge_crew(credits):
    """Collapse a person's several crew credits on one film into one record.

    fact_credit's PK is (movie_id, person_id, department, job), so a director
    who also wrote and produced is three separate rows — rendered before this
    as three names in three department sections. This groups by person_id and
    files each person under their single most senior department (min by
    _department_rank), while listing every job they hold in the same order,
    so "Director / Screenplay / Story / Producer" leads with the job that
    matters instead of alphabetizing it away.

    Cast is untouched by this — fact_credit's job is the literal "Actor" for
    every one of the 62,713 Acting rows, so cast is already one row per
    person; merging is purely a crew-department concern.
    """
    by_person = {}
    for credit in credits:
        by_person.setdefault(credit.person_id, []).append(credit)

    merged = []
    for person_id, rows in by_person.items():
        rows_sorted = sorted(rows, key=lambda c: _department_rank(c.department))
        jobs = [c.job for c in rows_sorted]
        merged.append({
            "person": rows_sorted[0].person,
            "department": rows_sorted[0].department,
            "jobs": jobs,
            "job_display": " / ".join(jobs),
        })
    return merged


# ?sort= values accepted by person_detail's filmography toolbar, each mapped to
# (Movie attribute, descending?). The same four segments as the /movies/ and
# studio-page toolbars (MOVIE_SORTS), but applied in Python rather than the ORM:
# a person's filmography is a merged list of {"movie", "job_display"} dicts —
# one row per film, carrying every job held on it — not a queryset, so it can't
# be reordered with .order_by().
FILMOGRAPHY_SORTS = {
    "release": ("release_date", True),
    "rating": ("imdb_rating", True),
    "revenue": ("revenue", True),
    "title": ("title", False),
}


def _sorted_filmography(rows, sort):
    """Order merged filmography rows by one Movie attribute, nulls always last.

    Mirrors MOVIE_SORTS' nulls_last=True: a film missing the sort field (no
    IMDb rating yet, no revenue figure) sorts after every film that has one,
    whichever direction the sort runs — rather than a null leading a
    descending list. `imdb_rating` is the attribute person_detail() attaches
    to each row's Movie just below _merge_person_credits().
    """
    attr, descending = FILMOGRAPHY_SORTS[sort]
    if attr == "title":
        return sorted(rows, key=lambda r: (r["movie"].title or "").lower())
    present = [r for r in rows if getattr(r["movie"], attr) is not None]
    missing = [r for r in rows if getattr(r["movie"], attr) is None]
    present.sort(key=lambda r: getattr(r["movie"], attr), reverse=descending)
    return present + missing


def _merge_person_credits(credits):
    """Collapse a person's several credits on one film into one filmography row.

    Same shape of problem as _merge_crew, keyed by movie instead of person: an
    actor who also directed or wrote the same film would otherwise appear as
    duplicate entries in separate department sections (Acting, Directing, ...).
    One row per movie, with every job on it joined in department order, so a
    director who also wrote the script reads "Director / Screenplay" once,
    under one poster, rather than twice under two.
    """
    by_movie = {}
    for credit in credits:
        by_movie.setdefault(credit.movie_id, []).append(credit)

    merged = []
    for movie_id, rows in by_movie.items():
        rows_sorted = sorted(rows, key=lambda c: _department_rank(c.department))
        labels = [
            c.character_name if c.department == "Acting" and c.character_name else c.job
            for c in rows_sorted
        ]
        merged.append({
            "movie": rows_sorted[0].movie,
            "job_display": " / ".join(labels),
        })
    return merged


def _redirect_to_person(slug):
    """301 a legacy /actors/<slug>/ or /directors/<slug>/ URL to /people/<slug>/.

    Task 51 resolved this through dim_actor/dim_director to handle the 381 slugs
    that changed when the two namespaces merged. Those tables are gone as of
    Task 53, so the mapping is gone with them: a legacy URL now resolves only if
    its slug still names the same person in dim_person, which is true of 44,178
    of the 44,554 actor slugs. The remaining 376 are unrecoverable and 404 —
    the honest outcome, rather than carrying two dead dimension tables purely as
    a redirect map.
    """
    person = get_object_or_404(Person.objects.using("warehouse"), slug=slug)
    return redirect("movies:person_detail", person_slug=person.slug, permanent=True)


def actor_detail(request, actor_slug):
    return _redirect_to_person(actor_slug)


def director_detail(request, director_slug):
    return _redirect_to_person(director_slug)


def person_detail(request, person_slug):
    """One person, every film they worked on, and what they did there."""
    person = get_object_or_404(Person.objects.using("warehouse"), slug=person_slug)
    person_id = person.person_id

    # One query for every credit, joined to its film. Merging happens in
    # Python below, since a GROUP BY can't return the rows themselves.
    credits = list(
        Credit.objects.using("warehouse")
        .filter(person_id=person_id)
        .select_related("movie")
        .order_by(F("movie__release_date").desc(nulls_last=True))
    )

    filmography = _merge_person_credits(credits)

    movie_ids = {c.movie_id for c in credits}

    # fact_movie_rating is one row per (movie, source) — no genre fan-out —
    # so, unlike the old fact_movie_metrics read this replaces, there's no
    # .values(...).distinct() dedupe guard to port here (Task 68).
    avg_rating = (
        MovieRating.objects.using("warehouse")
        .filter(movie_id__in=movie_ids, source="imdb")
        .aggregate(avg_rating=Avg("rating"))["avg_rating"]
    )

    # One more query gives every poster in the filmography grid its own IMDb
    # figure, without turning the grid into one query per card (Task 68) —
    # same film set as the aggregate above, so this stays a constant number
    # of queries regardless of how many films this person has.
    imdb_ratings = dict(
        MovieRating.objects.using("warehouse")
        .filter(movie_id__in=movie_ids, source="imdb")
        .values_list("movie_id", "rating")
    )
    for row in filmography:
        row["movie"].imdb_rating = imdb_ratings.get(row["movie"].movie_id)

    span = Movie.objects.using("warehouse").filter(movie_id__in=movie_ids).aggregate(
        earliest=Min("release_date"), latest=Max("release_date")
    )

    # Search + reorder the filmography, the same toolbar /studios/<slug>/ puts
    # over its filmography (Task 62). Done in Python: the list above is already
    # merged one-row-per-film and fully in memory, and a filmography is small
    # (a few hundred rows for the most prolific person here). The header stats
    # are computed over the whole filmography above and never move as this
    # narrows — the same contract the studio page keeps.
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "release")
    if sort not in FILMOGRAPHY_SORTS:
        sort = "release"

    rows = filmography
    if q:
        rows = [r for r in rows if q.lower() in r["movie"].title.lower()]
    rows = _sorted_filmography(rows, sort)

    page_obj = Paginator(rows, MOVIES_PER_PAGE).get_page(request.GET.get("page"))

    context = {
        "person": person,
        "filmography": filmography,
        "page_obj": page_obj,
        "q": q,
        "sort": sort,
        "base_query": urlencode({"q": q, "sort": sort}),
        "film_count": len(movie_ids),
        "credit_count": len(credits),
        "avg_rating": avg_rating,
        "career_period": _career_period(span["earliest"], span["latest"]),
    }
    # Same live-filter contract as movie_list()/studio_detail(): the record
    # header is never part of the swap, so only the grid+pager fragment comes
    # back for an AJAX refetch.
    if _is_ajax(request):
        return render(request, "movies/_person_filmography_results.html", context)
    return render(request, "movies/person_detail.html", context)


