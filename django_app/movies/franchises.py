"""Franchises: what the list page, the detail page and the movie-page strip read (Task 153).

A franchise (a TMDB collection) lists *parts*. Some are films the catalog holds;
most of the rest are films it does not (692 of 1,313 on 2026-11-16). Those are
stored as listing rows and shown unlinked: the user chose that over pulling them
in as full movies, which would have grown the catalog by 47%.

One idea runs through this module: an **installment** is a part as the reader
meets it. A catalog part *is* its `Movie` (with the site's translated title, IMDb
rating and link); a listing row is the TMDB data stored for it, translated where
TMDB had a translation. Both sort together by release date, which is the only
order that means anything — TMDB's own array order is release order in only 277
of 368 collections.

`movie_id` on a part has no foreign key, so "is this part in the catalog" is
answered here, at read time, against the movies that point at the franchise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django.db.models import Max, Q

from core.datacache import cached
from movies.i18n import (
    SEARCH_LANGS, current_lang, franchise_part_texts, genre_labels, localize_movies,
)
from movies.models import (
    Franchise, FranchiseImage, FranchisePart, FranchiseTranslation, Genre, Movie,
)

# How many posters / backdrops the gallery shows before "show all" (22 images,
# inside the ~24 a first screen should hold).
GALLERY_POSTERS = 14      # two rows of seven at the 1240px measure
GALLERY_BACKDROPS = 8     # two rows of four

# A part with no position sorts after every real one.
_NO_POSITION = 10_000


@dataclass
class Installment:
    """One part of a franchise, ready to render."""

    movie_id: int
    title: str
    release_date: date | None
    position: int
    poster_path: str | None = None
    overview: str | None = None
    overview_is_fallback: bool = False
    movie: Movie | None = None          # set when the part is in the catalog
    genres: list[str] = field(default_factory=list)
    tmdb_score: Decimal | None = None   # TMDB's, never shown as the site's rating
    direct_to_video: bool = False

    @property
    def in_catalog(self) -> bool:
        return self.movie is not None

    @property
    def is_upcoming(self) -> bool:
        return self.release_date is not None and self.release_date > date.today()


def year_span(dates) -> str:
    """"2001–2003", "2001", or "" for a franchise with no dated part."""
    years = sorted(d.year for d in dates if d)
    if not years:
        return ""
    return str(years[0]) if years[0] == years[-1] else f"{years[0]}–{years[-1]}"


def _sort_key(item: Installment):
    return (item.release_date is None, item.release_date or date.max, item.position, item.movie_id)


def _genre_names() -> dict[int, str]:
    """{genre_id: name in the reader's language} for the genres a part can carry."""
    labels = genre_labels()
    return {
        genre_id: labels.get(name, name)
        for genre_id, name in Genre.objects.using("warehouse").values_list("genre_id", "genre_name")
    }


def franchise_installments(franchise: Franchise) -> list[Installment]:
    """Every installment of a franchise in release order; undated ones last.

    Parts TMDB flags `adult` are left out (3 today; they stay in the warehouse).
    A catalog film missing from TMDB's list (a collection that was never fetched,
    or a film TMDB hasn't added yet) is still included, so the page can't contradict
    the movie page that links to it.
    """
    parts = list(FranchisePart.objects.using("warehouse").filter(franchise=franchise))
    hidden = {p.movie_id for p in parts if p.adult}
    parts = [p for p in parts if not p.adult]

    movies = {
        m.movie_id: m
        for m in localize_movies(Movie.objects.using("warehouse").filter(franchise=franchise))
        .annotate(imdb_rating=Max("movierating__rating", filter=Q(movierating__source="imdb")))
        if m.movie_id not in hidden
    }
    texts = franchise_part_texts(franchise.pk)
    genre_names = _genre_names()

    items: list[Installment] = []
    for part in parts:
        movie = movies.pop(part.movie_id, None)
        if movie is not None:
            items.append(Installment(
                movie_id=movie.movie_id, title=movie.display_title,
                release_date=movie.release_date or part.release_date,
                position=part.position, poster_path=movie.poster_path or part.poster_path,
                movie=movie,
            ))
            continue
        text = texts.get(part.movie_id, {})
        overview = text.get("overview") or part.overview
        items.append(Installment(
            movie_id=part.movie_id,
            title=text.get("title") or part.title or part.original_title or "",
            release_date=part.release_date, position=part.position,
            poster_path=part.poster_path, overview=overview,
            overview_is_fallback=bool(part.overview) and not text.get("overview")
            and current_lang() != "en",
            genres=[genre_names[g] for g in part.genre_ids or [] if g in genre_names],
            tmdb_score=part.vote_average if (part.vote_count or 0) > 0 else None,
            direct_to_video=bool(part.video),
        ))
    for movie in movies.values():  # catalog films TMDB's list does not name
        items.append(Installment(
            movie_id=movie.movie_id, title=movie.display_title,
            release_date=movie.release_date, position=_NO_POSITION,
            poster_path=movie.poster_path, movie=movie,
        ))
    return sorted(items, key=_sort_key)


def franchise_stats(items: list[Installment]) -> dict:
    """The figures at the top of a franchise page.

    Parts and the year span cover every installment. Rating, revenue and budget
    cover **catalog films only**: a listing row has no revenue or budget, and its
    score is TMDB's, not the IMDb rating of record the site uses.
    """
    in_catalog = [i.movie for i in items if i.movie]
    ratings = [float(m.imdb_rating) for m in in_catalog if m.imdb_rating is not None]
    revenue = sum(m.revenue or 0 for m in in_catalog)
    budget = sum(m.budget or 0 for m in in_catalog)
    return {
        "parts": len(items),
        "in_catalog": len(in_catalog),
        "years": year_span(i.release_date for i in items),
        "avg_rating": sum(ratings) / len(ratings) if ratings else None,
        "revenue": revenue or None,
        "budget": budget or None,
    }


# How many of a franchise's movies the movie page lists before "see the full collection".
STRIP_MOVIES = 3


def franchise_strip(franchise: Franchise, movie_id: int) -> dict | None:
    """What a movie page says about its franchise: the first few movies, and a way in.

    `movies` is the first STRIP_MOVIES movies **the catalog holds**, in release
    order, so every title in the list has a page to open (an installment the
    catalog only lists has none). `current_id` lets the template mark the page the
    reader is already on.

    `is_trilogy` is true when the series has exactly three movies (counting every
    installment TMDB lists, not only the ones the catalog holds): the page calls it
    "the trilogy" then, and "the franchise" for any other count.

    None when the franchise has fewer than two installments (a strip about a
    franchise of one is noise) or the film is not among them.
    """
    items = franchise_installments(franchise)
    if len(items) < 2 or not any(i.movie_id == movie_id for i in items):
        return None
    held = [i for i in items if i.in_catalog]
    return {
        "franchise": franchise,
        "movies": held[:STRIP_MOVIES],
        "current_id": movie_id,
        "total": len(items),
        "held": len(held),
        "is_trilogy": len(items) == 3,
    }


# --- Gallery -----------------------------------------------------------------

def franchise_gallery(franchise: Franchise) -> dict:
    """Posters and backdrops, best first for this reader.

    Posters: the reader's language (a Russian poster for a Russian reader), then
    textless, then the rest; within a group by TMDB vote. Backdrops carry text far
    less often, so a textless one leads, then the reader's language.
    """
    lang = current_lang()
    images = list(FranchiseImage.objects.using("warehouse").filter(franchise=franchise))

    def rank(preferred_first: bool):
        def key(img):
            own = img.iso_639_1 == lang
            textless = img.iso_639_1 is None
            group = (0 if own else 1 if textless else 2) if preferred_first else (
                0 if textless else 1 if own else 2
            )
            return (group, -(img.vote_average or 0), -(img.vote_count or 0), img.file_path)
        return key

    return {
        "posters": sorted((i for i in images if i.kind == "poster"), key=rank(True)),
        "backdrops": sorted((i for i in images if i.kind == "backdrop"), key=rank(False)),
    }


def hero_backdrop(franchise: Franchise, gallery: dict) -> str | None:
    """The page's banner: a textless backdrop if the gallery has one, else the
    franchise's own, else the best of whatever is there."""
    for image in gallery["backdrops"]:
        if image.iso_639_1 is None:
            return image.file_path
    return franchise.backdrop_path or (
        gallery["backdrops"][0].file_path if gallery["backdrops"] else None
    )


def franchise_homepage(franchise: Franchise) -> str | None:
    """The reader's language's own homepage if TMDB has one, else the English one."""
    lang = current_lang()
    if lang != "en":
        own = (
            FranchiseTranslation.objects.using("warehouse")
            .filter(franchise_id=franchise.pk, lang=lang)
            .exclude(homepage__isnull=True).exclude(homepage="")
            .values_list("homepage", flat=True).first()
        )
        if own:
            return own
    return franchise.homepage or None


# --- List page ---------------------------------------------------------------

def build_franchise_rows() -> list[dict]:
    """One plain dict per franchise for the list page (see franchise_rows()).

    Counts and the year span treat a franchise the way its page does: TMDB's
    parts plus any catalog film it omits, minus `adult` parts. The whole table
    is ~370 rows, so the list page filters and sorts it in Python.
    """
    lang = current_lang()

    names: dict[int, dict[str, str]] = {}
    for franchise_id, row_lang, name in (
        FranchiseTranslation.objects.using("warehouse")
        .filter(lang__in={lang, *SEARCH_LANGS})
        .exclude(name__isnull=True).exclude(name="")
        .values_list("franchise_id", "lang", "name")
    ):
        names.setdefault(franchise_id, {})[row_lang] = name

    catalog: dict[int, dict[int, date | None]] = {}
    for franchise_id, movie_id, released in (
        Movie.objects.using("warehouse").filter(franchise__isnull=False)
        .values_list("franchise_id", "movie_id", "release_date")
    ):
        catalog.setdefault(franchise_id, {})[movie_id] = released

    listed: dict[int, dict[int, tuple]] = {}
    hidden: dict[int, set[int]] = {}
    for franchise_id, movie_id, released, popularity, genre_ids, adult in (
        FranchisePart.objects.using("warehouse")
        .values_list("franchise_id", "movie_id", "release_date", "popularity", "genre_ids", "adult")
    ):
        if adult:
            hidden.setdefault(franchise_id, set()).add(movie_id)
        else:
            listed.setdefault(franchise_id, {})[movie_id] = (released, popularity, genre_ids or [])

    today = date.today()
    rows = []
    for f in Franchise.objects.using("warehouse").values(
        "collection_id", "slug", "name", "poster_path"
    ):
        fid = f["collection_id"]
        skip = hidden.get(fid, set())
        in_catalog = {m: d for m, d in catalog.get(fid, {}).items() if m not in skip}
        parts = dict(listed.get(fid, {}))
        dates = {m: v[0] for m, v in parts.items()}
        dates.update({m: d or dates.get(m) for m, d in in_catalog.items()})  # the film's own date wins
        for movie_id in in_catalog:
            parts.setdefault(movie_id, (None, None, []))
        if not parts:
            continue
        popularity = [float(v[1]) for v in parts.values() if v[1] is not None]
        released = [d for d in dates.values() if d and d <= today]
        by_lang = names.get(fid, {})
        rows.append({
            "id": fid,
            "slug": f["slug"],
            "name": f["name"],
            "display_name": by_lang.get(lang) or f["name"],
            "search_text": " ".join([f["name"], *(by_lang.get(l, "") for l in SEARCH_LANGS)]).casefold(),
            "poster_path": f["poster_path"],
            "parts": len(parts),
            "in_catalog": len(in_catalog),
            "years": year_span(dates.values()),
            "newest": max(released) if released else None,
            "popularity": sum(popularity) / len(popularity) if popularity else None,
            "genre_ids": frozenset(g for v in parts.values() for g in v[2]),
        })
    return rows


def franchise_rows() -> list[dict]:
    """The list page's rows, cached per language (the display name is translated)."""
    return cached("franchise_rows", build_franchise_rows, lang=current_lang())
