"""Franchises (TMDB collections) on the site, Task 153.

Same "mock the boundary" posture as test_django_views.py: every warehouse
manager is a MagicMock, model instances are real unsaved objects, and the views
are driven through Django's test Client. The data layer (`movies.franchises`) is
tested directly, then the pages through it.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.template.loader import render_to_string

from tests.test_django_views import (  # noqa: F401  (also runs django.setup())
    Genre, Movie, _movie, _movie_detail_video_mocks, client, setup_module, teardown_module,
)

from movies import franchises as fr  # noqa: E402
from movies.models import Franchise, FranchiseImage, FranchisePart  # noqa: E402

FRANCHISE_ID = 119
TOMORROW = date.today() + timedelta(days=30)


def _franchise(**over):
    row = dict(collection_id=FRANCHISE_ID, name="The Lord of the Rings Collection",
               slug="the-lord-of-the-rings-collection", poster_path="/p.jpg",
               backdrop_path="/b.jpg", original_name="The Lord of the Rings Collection",
               original_language="en", overview="An epic.", homepage="https://example.com")
    row.update(over)
    return Franchise(**row)


def _part(movie_id, title, released, position=0, **over):
    row = dict(movie_id=movie_id, franchise_id=FRANCHISE_ID, position=position, title=title,
               original_title=title, release_date=released, overview=f"About {title}.",
               poster_path=f"/{movie_id}.jpg", genre_ids=[12, 14], popularity=Decimal("10"),
               vote_average=Decimal("7.4"), vote_count=100, adult=False, video=False)
    row.update(over)
    return FranchisePart(**row)


def _catalog_movie(movie_id, title, released, rating="8.0", **over):
    m = _movie(movie_id, title)
    m.release_date = released
    m.slug = title.lower().replace(" ", "-")
    m.poster_path = f"/{movie_id}.jpg"
    m.imdb_rating = Decimal(rating) if rating else None
    for k, v in over.items():
        setattr(m, k, v)
    return m


@pytest.fixture
def warehouse(monkeypatch):
    """Fake the warehouse under movies.franchises. Returns a setter:
    `warehouse(parts=[...], movies=[...], texts={...})`."""
    parts_mgr, movies_mgr = MagicMock(), MagicMock()
    monkeypatch.setattr(fr, "FranchisePart", MagicMock(objects=parts_mgr))
    monkeypatch.setattr(fr, "Movie", MagicMock(objects=movies_mgr))
    monkeypatch.setattr(fr, "localize_movies", lambda qs: qs)
    monkeypatch.setattr(fr, "_genre_names", lambda: {12: "Adventure", 14: "Fantasy"})
    state = {"texts": {}}
    monkeypatch.setattr(fr, "franchise_part_texts", lambda _id: state["texts"])

    def setter(parts=(), movies=(), texts=None):
        parts_mgr.using.return_value.filter.return_value = list(parts)
        movies_mgr.using.return_value.filter.return_value.annotate.return_value = list(movies)
        state["texts"] = texts or {}
    return setter


# ---------------------------------------------------------------------------
# The installment list
# ---------------------------------------------------------------------------

def test_installments_sort_by_release_date_with_undated_ones_last(warehouse):
    warehouse(parts=[
        _part(3, "Third", date(2003, 1, 1), position=0),     # TMDB's array order is not release order
        _part(1, "First", date(2001, 1, 1), position=2),
        _part(9, "Untitled", None, position=1),
        _part(2, "Second", date(2002, 1, 1), position=3),
    ])
    items = fr.franchise_installments(_franchise())
    assert [i.title for i in items] == ["First", "Second", "Third", "Untitled"]


def test_a_catalog_part_is_its_movie_and_a_listing_row_is_not(warehouse):
    fellowship = _catalog_movie(120, "Fellowship", date(2001, 12, 18))
    warehouse(
        parts=[_part(120, "The Fellowship of the Ring", date(2001, 12, 18)),
               _part(121, "The Two Towers", date(2002, 12, 18))],
        movies=[fellowship],
    )
    held, listed = fr.franchise_installments(_franchise())
    assert held.in_catalog and held.movie is fellowship and held.title == "Fellowship"
    assert not listed.in_catalog and listed.title == "The Two Towers"
    assert listed.tmdb_score == Decimal("7.4")
    assert listed.genres == ["Adventure", "Fantasy"]
    assert listed.overview == "About The Two Towers."


def test_adult_parts_are_hidden_even_when_the_catalog_holds_the_film(warehouse):
    held = _catalog_movie(7, "Held Adult", date(2005, 1, 1))
    warehouse(
        parts=[_part(7, "Held Adult", date(2005, 1, 1), adult=True),
               _part(8, "Listed Adult", date(2006, 1, 1), adult=True),
               _part(9, "Fine", date(2007, 1, 1))],
        movies=[held],
    )
    assert [i.title for i in fr.franchise_installments(_franchise())] == ["Fine"]


def test_a_catalog_film_tmdb_does_not_list_is_still_included(warehouse):
    stray = _catalog_movie(55, "Stray", date(2010, 1, 1))
    warehouse(parts=[_part(1, "First", date(2001, 1, 1))], movies=[stray])
    assert [i.title for i in fr.franchise_installments(_franchise())] == ["First", "Stray"]


def test_a_franchise_with_no_parts_falls_back_to_its_catalog_films(warehouse):
    warehouse(parts=[], movies=[_catalog_movie(1, "Only", date(2001, 1, 1))])
    (item,) = fr.franchise_installments(_franchise())
    assert item.in_catalog


def test_a_score_is_withheld_until_tmdb_has_votes_and_flags_carry_through(warehouse):
    warehouse(parts=[_part(1, "Quiet", date(2001, 1, 1), vote_count=0, video=True)])
    (item,) = fr.franchise_installments(_franchise())
    assert item.tmdb_score is None and item.direct_to_video


def test_listing_text_prefers_the_translation_and_flags_an_english_overview(warehouse, monkeypatch):
    monkeypatch.setattr(fr, "current_lang", lambda: "ru")
    warehouse(
        parts=[_part(1, "Two Towers", date(2002, 1, 1)), _part(2, "Return", date(2003, 1, 1))],
        texts={1: {"title": "Две крепости", "overview": "Текст"}},      # translated
    )
    translated, untranslated = fr.franchise_installments(_franchise())
    assert translated.title == "Две крепости" and translated.overview == "Текст"
    assert not translated.overview_is_fallback
    assert untranslated.title == "Return" and untranslated.overview_is_fallback


def test_is_upcoming_only_for_a_future_date(warehouse):
    warehouse(parts=[_part(1, "Out", date(2001, 1, 1)), _part(2, "Soon", TOMORROW), _part(3, "TBA", None)])
    out, soon, tba = fr.franchise_installments(_franchise())
    assert (out.is_upcoming, soon.is_upcoming, tba.is_upcoming) == (False, True, False)


# ---------------------------------------------------------------------------
# Figures, years, the strip, the gallery
# ---------------------------------------------------------------------------

def test_year_span():
    assert fr.year_span([date(2001, 5, 1), date(2003, 1, 1), None]) == "2001–2003"
    assert fr.year_span([date(2001, 5, 1), date(2001, 9, 1)]) == "2001"
    assert fr.year_span([None]) == ""


def test_stats_cover_the_whole_series_except_rating_revenue_and_budget_which_are_catalog_only(warehouse):
    held_a = _catalog_movie(1, "A", date(2001, 1, 1), rating="8.0", revenue=900, budget=300)
    held_b = _catalog_movie(2, "B", date(2003, 1, 1), rating="6.0", revenue=100, budget=100)
    warehouse(
        parts=[_part(1, "A", date(2001, 1, 1)), _part(2, "B", date(2003, 1, 1)),
               _part(3, "Listed", date(2005, 1, 1))],
        movies=[held_a, held_b],
    )
    stats = fr.franchise_stats(fr.franchise_installments(_franchise()))
    assert stats["parts"] == 3 and stats["in_catalog"] == 2
    assert stats["years"] == "2001–2005"
    assert stats["avg_rating"] == pytest.approx(7.0)
    assert (stats["revenue"], stats["budget"]) == (1000, 400)


def test_stats_have_no_revenue_or_rating_when_the_catalog_holds_nothing(warehouse):
    warehouse(parts=[_part(1, "Listed", date(2001, 1, 1))])
    stats = fr.franchise_stats(fr.franchise_installments(_franchise()))
    assert stats["avg_rating"] is None and stats["revenue"] is None and stats["budget"] is None


def test_strip_lists_the_first_three_movies_the_catalog_holds_in_release_order(warehouse):
    held = [_catalog_movie(i, f"Held {i}", date(2000 + i, 1, 1)) for i in (2, 4, 5, 6)]
    warehouse(
        parts=[_part(1, "Listed first", date(2001, 1, 1)),        # a part the catalog only lists
               *[_part(m.movie_id, m.title, m.release_date) for m in held]],
        movies=held,
    )
    strip = fr.franchise_strip(_franchise(), 5)
    assert [i.title for i in strip["movies"]] == ["Held 2", "Held 4", "Held 5"]   # no "Listed first": no page
    assert all(i.in_catalog for i in strip["movies"])
    assert strip["current_id"] == 5
    assert (strip["total"], strip["held"]) == (5, 4)


@pytest.mark.parametrize("count, trilogy", [(2, False), (3, True), (4, False)])
def test_strip_flags_a_series_of_exactly_three_movies_as_a_trilogy(warehouse, count, trilogy):
    """Counts every installment TMDB lists, not only the ones the catalog holds."""
    parts = [_part(i, f"Part {i}", date(2000 + i, 1, 1)) for i in range(1, count + 1)]
    warehouse(parts=parts, movies=[_catalog_movie(1, "Part 1", date(2001, 1, 1))])   # only one held
    strip = fr.franchise_strip(_franchise(), 1)
    assert strip["total"] == count and strip["held"] == 1
    assert strip["is_trilogy"] is trilogy


def test_strip_lists_fewer_than_three_when_the_catalog_holds_fewer(warehouse):
    held = _catalog_movie(2, "Only held", date(2002, 1, 1))
    warehouse(parts=[_part(1, "Listed", date(2001, 1, 1)), _part(2, "Only held", date(2002, 1, 1))], movies=[held])
    strip = fr.franchise_strip(_franchise(), 2)
    assert [i.title for i in strip["movies"]] == ["Only held"]
    assert strip["total"] == 2 and strip["held"] == 1


def test_strip_is_none_for_a_series_of_one_or_a_film_that_is_not_in_it(warehouse):
    warehouse(parts=[_part(1, "Alone", date(2001, 1, 1))])
    assert fr.franchise_strip(_franchise(), 1) is None
    warehouse(parts=[_part(1, "A", date(2001, 1, 1)), _part(2, "B", date(2002, 1, 1))])
    assert fr.franchise_strip(_franchise(), 999) is None


def _image(path, kind, lang=None, votes=0.0):
    return FranchiseImage(file_path=path, franchise_id=FRANCHISE_ID, kind=kind, iso_639_1=lang,
                          vote_average=Decimal(str(votes)), vote_count=1)


def test_gallery_leads_with_the_readers_language_then_textless_for_posters(monkeypatch):
    images = [_image("/en.jpg", "poster", "en", 9), _image("/none.jpg", "poster", None, 5),
              _image("/ru-low.jpg", "poster", "ru", 1), _image("/ru-high.jpg", "poster", "ru", 7)]
    monkeypatch.setattr(fr, "FranchiseImage", MagicMock(objects=MagicMock(**{
        "using.return_value.filter.return_value": images})))
    monkeypatch.setattr(fr, "current_lang", lambda: "ru")
    posters = fr.franchise_gallery(_franchise())["posters"]
    assert [p.file_path for p in posters] == ["/ru-high.jpg", "/ru-low.jpg", "/none.jpg", "/en.jpg"]


def test_gallery_backdrops_lead_with_a_textless_one_and_that_becomes_the_banner(monkeypatch):
    images = [_image("/text.jpg", "backdrop", "en", 9), _image("/clean.jpg", "backdrop", None, 2)]
    monkeypatch.setattr(fr, "FranchiseImage", MagicMock(objects=MagicMock(**{
        "using.return_value.filter.return_value": images})))
    gallery = fr.franchise_gallery(_franchise())
    assert [b.file_path for b in gallery["backdrops"]] == ["/clean.jpg", "/text.jpg"]
    assert fr.hero_backdrop(_franchise(), gallery) == "/clean.jpg"


def test_banner_falls_back_to_the_franchises_own_backdrop():
    gallery = {"posters": [], "backdrops": [_image("/text.jpg", "backdrop", "en")]}
    assert fr.hero_backdrop(_franchise(backdrop_path="/own.jpg"), gallery) == "/own.jpg"
    assert fr.hero_backdrop(_franchise(backdrop_path=None), gallery) == "/text.jpg"
    assert fr.hero_backdrop(_franchise(backdrop_path=None), {"posters": [], "backdrops": []}) is None


# ---------------------------------------------------------------------------
# The list page
# ---------------------------------------------------------------------------

def _row(name, slug, parts, held, genres=(12,), popularity=5.0, newest=date(2020, 1, 1), years="2001–2020",
         search=None):
    return {"id": hash(slug) % 1000, "slug": slug, "name": name, "display_name": name,
            "search_text": (search or name).casefold(), "poster_path": f"/{slug}.jpg", "parts": parts,
            "in_catalog": held, "years": years, "newest": newest, "popularity": popularity,
            "genre_ids": frozenset(genres)}


ROWS = [
    _row("Bond Collection", "bond", 27, 17, genres=(12, 28), popularity=40.0, newest=date(2021, 1, 1)),
    _row("Alien Collection", "alien", 6, 3, genres=(878,), popularity=20.0, newest=date(2024, 1, 1)),
    _row("Rocky Collection", "rocky", 8, 8, genres=(18,), popularity=9.0, newest=None),
]


@pytest.fixture
def list_page():
    with patch("movies.views.franchise_rows", return_value=ROWS), patch(
        "movies.views.cached_reads.genre_rows",
        return_value=[(12, "Adventure"), (28, "Action"), (878, "Science Fiction"), (18, "Drama")],
    ):
        yield


def _titles(response):
    return [c["display_name"] for c in response.context["page_obj"]]


def test_list_defaults_to_the_biggest_franchise_first(list_page):
    response = client.get("/franchises/")
    assert response.status_code == 200
    assert _titles(response) == ["Bond Collection", "Rocky Collection", "Alien Collection"]


@pytest.mark.parametrize("sort, expected", [
    ("name", ["Alien Collection", "Bond Collection", "Rocky Collection"]),
    ("newest", ["Alien Collection", "Bond Collection", "Rocky Collection"]),  # undated sorts last
    ("popularity", ["Bond Collection", "Alien Collection", "Rocky Collection"]),
])
def test_list_sorts(list_page, sort, expected):
    assert _titles(client.get("/franchises/", {"sort": sort})) == expected


def test_list_ignores_an_unknown_sort(list_page):
    response = client.get("/franchises/", {"sort": "nonsense"})
    assert response.context["sort"] == "parts"


def test_list_searches_the_english_and_russian_names(list_page):
    assert _titles(client.get("/franchises/", {"q": "ROCK"})) == ["Rocky Collection"]
    ROWS_RU = [dict(r, search_text=r["search_text"] + (" чужой" if r["slug"] == "alien" else "")) for r in ROWS]
    with patch("movies.views.franchise_rows", return_value=ROWS_RU):
        assert _titles(client.get("/franchises/", {"q": "чужой"})) == ["Alien Collection"]


def test_list_genre_facet_requires_every_ticked_genre_and_offers_only_present_ones(list_page):
    response = client.get("/franchises/", {"genre": ["adventure", "action"]})
    assert _titles(response) == ["Bond Collection"]
    offered = {slug for slug, _ in response.context["genre_choices"]}
    assert {"adventure", "action", "science-fiction", "drama"} <= offered
    # an unknown slug is dropped, not an error
    assert len(client.get("/franchises/", {"genre": "nope"}).context["page_obj"]) == 3


def test_list_cards_show_the_catalog_coverage_and_link_to_the_franchise(list_page):
    body = client.get("/franchises/").content.decode()
    assert "17 of 27 in Theoria" in body
    assert 'href="/franchises/bond/"' in body
    assert "27 movies" in body


def test_list_empty_states(list_page):
    assert "No franchises match “zzz”" in client.get("/franchises/", {"q": "zzz"}).content.decode()
    assert "No franchises match this filter" in client.get(
        "/franchises/", {"genre": ["action", "drama"]}).content.decode()


def test_list_ajax_returns_only_the_grid_fragment(list_page):
    response = client.get("/franchises/", {"q": "bond"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    body = response.content.decode()
    assert 'id="franchises-grid"' in body and "<html" not in body
    assert "Bond Collection" in body and "Rocky Collection" not in body


# ---------------------------------------------------------------------------
# The franchise page
# ---------------------------------------------------------------------------

def _installments():
    held = _catalog_movie(120, "Fellowship", date(2001, 12, 18), rating="8.9")
    return [
        fr.Installment(movie_id=120, title="Fellowship", release_date=date(2001, 12, 18), position=0,
                       poster_path="/120.jpg", movie=held),
        fr.Installment(movie_id=121, title="The Two Towers", release_date=date(2002, 12, 18), position=1,
                       poster_path="/121.jpg", overview="Two towers fall.", genres=["Fantasy"],
                       tmdb_score=Decimal("8.4"), direct_to_video=True),
        fr.Installment(movie_id=122, title="The Return", release_date=TOMORROW, position=2,
                       poster_path="/122.jpg", movie=_catalog_movie(122, "The Return", TOMORROW, rating=None)),
        fr.Installment(movie_id=123, title="Untitled", release_date=None, position=3),
    ]


@pytest.fixture
def detail_page():
    gallery = {"posters": [_image(f"/p{i}.jpg", "poster", None, i) for i in range(20)],
               "backdrops": [_image("/clean.jpg", "backdrop", None, 1)]}
    with patch("movies.views.get_object_or_404", return_value=_franchise()), patch(
        "movies.views.franchise_installments", return_value=_installments()
    ), patch("movies.views.franchise_gallery", return_value=gallery), patch(
        "movies.views.franchise_homepage", return_value="https://example.com"
    ), patch("movies.views.Language.objects", new=MagicMock(**{
        "using.return_value.filter.return_value.first.return_value": MagicMock(name="English")})):
        yield


def test_franchise_page_renders_the_record(detail_page):
    response = client.get("/franchises/the-lord-of-the-rings-collection/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "The Lord of the Rings Collection" in body
    assert "An epic." in body and 'href="https://example.com"' in body
    assert response.context["stats"]["parts"] == 4 and response.context["stats"]["in_catalog"] == 2
    assert "Rating, revenue and budget count only the movies in Theoria." in body
    assert response.context["hero_backdrop"] == "/clean.jpg"


def test_a_catalog_part_links_to_its_movie_and_a_listing_row_does_not(detail_page):
    body = client.get("/franchises/the-lord-of-the-rings-collection/").content.decode()
    assert 'href="/movies/fellowship/"' in body
    row = body[body.index("The Two Towers"):]
    card = row[: row.index("</div>\n</div>")]
    assert "<a " not in card.split("listing-more")[0]       # nothing to click on the listing card
    assert "Not in Theoria" in body and "is-listing" in body
    assert "TMDB" in card and "8.4 / 10" in card             # the score is labelled TMDB's
    assert "Direct-to-video" in card and "Two towers fall." in card


def test_upcoming_and_undated_parts_are_marked(detail_page):
    body = client.get("/franchises/the-lord-of-the-rings-collection/").content.decode()
    assert "· Upcoming" in body            # an upcoming catalog film
    assert "TBA" in body                   # the undated listing row


def test_gallery_shows_the_first_screen_and_keeps_the_rest_behind_show_all(detail_page):
    response = client.get("/franchises/the-lord-of-the-rings-collection/")
    assert len(response.context["posters"]) == fr.GALLERY_POSTERS
    assert len(response.context["more_posters"]) == 20 - fr.GALLERY_POSTERS
    body = response.content.decode()
    assert "Show 6 more posters" in body and '<details class="gallery-more">' in body
    assert "20 posters · 1 backdrops" in body


def test_franchise_page_renders_in_russian(detail_page):
    franchise = _franchise()
    franchise.name_i18n = "Властелин колец (Коллекция)"
    franchise.overview_i18n = "Путь Фродо."
    franchise.overview_tr = "Путь Фродо."
    client.cookies["django_language"] = "ru"
    try:
        with patch("movies.views.get_object_or_404", return_value=franchise):
            body = client.get("/franchises/the-lord-of-the-rings-collection/").content.decode()
    finally:
        del client.cookies["django_language"]
    assert "Властелин колец (Коллекция)" in body
    assert "Путь Фродо." in body
    assert "Нет в Theoria" in body
    assert "Shown in English" not in body and "originally" not in body.split("<h1")[1][:400]


def test_an_english_overview_on_a_translated_page_is_flagged(detail_page):
    franchise = _franchise()
    franchise.name_i18n = franchise.name
    franchise.overview_tr = None     # no translation: the English text stands in
    client.cookies["django_language"] = "uz"
    try:
        with patch("movies.views.get_object_or_404", return_value=franchise):
            body = client.get("/franchises/the-lord-of-the-rings-collection/").content.decode()
    finally:
        del client.cookies["django_language"]
    assert 'lang="en"' in body and "An epic." in body


def test_franchise_page_requires_sign_in():
    from django.test import Client
    response = Client().get("/franchises/the-lord-of-the-rings-collection/")
    assert response.status_code == 302 and "/accounts/login/" in response["Location"]


def test_franchise_list_is_public():
    from django.test import Client
    with patch("movies.views.franchise_rows", return_value=ROWS), patch(
        "movies.views.cached_reads.genre_rows", return_value=[]
    ):
        assert Client().get("/franchises/").status_code == 200


# ---------------------------------------------------------------------------
# The movie page's strip
# ---------------------------------------------------------------------------

def _inst(movie_id, title, year, held=True, rating="8.0"):
    movie = _catalog_movie(movie_id, title, date(year, 1, 1), rating=rating) if held else None
    return fr.Installment(movie_id=movie_id, title=title, release_date=date(year, 1, 1), position=0,
                          poster_path=f"/{movie_id}.jpg", movie=movie)


def _strip(movies, current_id=0, total=None):
    total = total or len(movies)
    return {"franchise": _franchise(), "movies": movies, "current_id": current_id,
            "total": total, "held": len(movies), "is_trilogy": total == 3}


def test_strip_lists_each_title_with_its_year_then_its_bare_imdb_rating():
    movies = [_inst(1, "Dr No", 1962, rating="7.2"), _inst(2, "Goldfinger", 1964, rating="7.7"),
              _inst(3, "Thunderball", 1965, rating="6.8")]
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip(movies, total=27)})
    for slug, title, rating in (("dr-no", "Dr No", "7.2"), ("goldfinger", "Goldfinger", "7.7"),
                                ("thunderball", "Thunderball", "6.8")):
        assert f'<a href="/movies/{slug}/">{title}</a>' in html
        assert f'<span aria-hidden="true">{rating}</span>' in html            # the bare figure, on the yellow chip
        assert f"IMDb rating {rating} out of 10" in html                      # the words are for screen readers only
    assert html.count('class="mono franchise-strip__rating"') == 3
    assert html.index("Dr No") < html.index("Goldfinger") < html.index("Thunderball")
    # the release year comes first, then the rating chip: title, year, rating
    for title, year, rating in (("Dr No", "1962", "7.2"), ("Goldfinger", "1964", "7.7"), ("Thunderball", "1965", "6.8")):
        row = html[html.index(f">{title}</a>"):]
        row = row[:row.index("</li>")]
        assert row.index(f'franchise-strip__year">{year}<') < row.index(f'<span aria-hidden="true">{rating}</span>')


def test_strip_shows_no_imdb_mark_or_out_of_ten_style():
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip([_inst(1, "Dr No", 1962)])})
    assert "rating-badge" not in html and ">IMDb<" not in html and "/ 10" not in html


def test_strip_shows_no_chip_for_a_movie_with_no_rating_but_still_its_year():
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip([_inst(1, "Unrated", 2020, rating=None)])})
    assert "Unrated" in html and "franchise-strip__rating" not in html
    assert 'franchise-strip__year">2020<' in html


def test_strip_shows_no_year_for_an_undated_movie():
    item = _inst(1, "Undated", 2020)
    item.release_date = None
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip([item])})
    assert "Undated" in html and "franchise-strip__year" not in html


def test_strip_is_a_numbered_ordered_list_in_the_order_given():
    """One movie per line, top to bottom, numbered by the browser's own <ol> markers."""
    movies = [_inst(1, "Dr No", 1962), _inst(2, "Goldfinger", 1964), _inst(3, "Thunderball", 1965)]
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip(movies)})
    assert html.count("<ol ") == 1 and html.count("<li>") == 3
    assert html.index("<ol") < html.index("Dr No") < html.index("Goldfinger") < html.index("Thunderball") < html.index("</ol>")


def test_strip_calls_a_series_of_three_a_trilogy_and_anything_else_a_franchise():
    movies = [_inst(1, "Dr No", 1962)]
    trilogy = render_to_string("movies/_franchise_strip.html", {"strip": _strip(movies, total=3)})
    assert "Explore the trilogy" in trilogy and "See the full trilogy" in trilogy
    assert "franchise</a>" not in trilogy and "Explore the franchise" not in trilogy
    for total in (2, 4, 27):
        html = render_to_string("movies/_franchise_strip.html", {"strip": _strip(movies, total=total)})
        assert "Explore the franchise" in html and "See the full franchise" in html, total
        assert "trilogy" not in html, total


def test_strip_never_says_collection_or_names_the_franchise_in_the_heading():
    import re
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip([_inst(1, "Dr No", 1962)], total=27)})
    text = re.sub(r"<[^>]+>", " ", html)          # what a reader sees; the link's URL slug is not copy
    assert "ollection" not in text and "Part of" not in text
    assert "The Lord of the Rings" not in text


def test_strip_ends_in_a_link_to_the_franchise_page_not_a_button():
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip([_inst(1, "Dr No", 1962)], total=27)})
    assert ('<a class="franchise-strip__all" '
            'href="/franchises/the-lord-of-the-rings-collection/">See the full franchise</a>') in html
    assert 'class="btn' not in html and "<button" not in html
    assert "<img" not in html
    assert "Previous" not in html and "Next" not in html and "release order" not in html


def test_strip_marks_the_page_the_reader_is_on_instead_of_linking_to_it():
    movies = [_inst(1, "Dr No", 1962), _inst(2, "Goldfinger", 1964)]
    html = render_to_string("movies/_franchise_strip.html", {"strip": _strip(movies, current_id=2)})
    assert '<span class="franchise-strip__current" aria-current="page">Goldfinger</span>' in html
    assert 'href="/movies/goldfinger/"' not in html
    assert 'href="/movies/dr-no/"' in html


def test_movie_page_shows_the_strip_for_a_franchise_film():
    movie = _movie()
    movie.franchise_id = FRANCHISE_ID
    strip = _strip([_inst(2, "Sequel", 2022)])
    with _movie_detail_video_mocks(movie, []), patch(
        "movies.views.Franchise.objects", new=MagicMock(**{
            "using.return_value.filter.return_value.first.return_value": _franchise()})
    ), patch("movies.views.localize_franchises", side_effect=lambda qs: qs), patch(
        "movies.views.franchise_strip", return_value=strip
    ) as build:
        response = client.get(f"/movies/{movie.movie_id}/")
    build.assert_called_once()
    assert response.context["franchise_strip"] is strip
    assert "franchise-strip" in response.content.decode()


def test_movie_page_without_a_franchise_has_no_strip_and_asks_for_none():
    movie = _movie()
    movie.franchise_id = None
    with _movie_detail_video_mocks(movie, []), patch("movies.views.franchise_strip") as build:
        response = client.get(f"/movies/{movie.movie_id}/")
    build.assert_not_called()
    assert response.context["franchise_strip"] is None
    assert "franchise-strip" not in response.content.decode()


# ---------------------------------------------------------------------------
# The list rows builder (what the cache holds)
# ---------------------------------------------------------------------------

def test_rows_builder_treats_a_franchise_as_its_page_does(monkeypatch):
    """Parts + catalog films TMDB omits, minus adult parts; years across all of them;
    a film's own date beats the part's; newest counts only released dates."""
    monkeypatch.setattr(fr, "current_lang", lambda: "en")
    monkeypatch.setattr(fr, "FranchiseTranslation", MagicMock(objects=MagicMock(**{
        "using.return_value.filter.return_value.exclude.return_value.exclude.return_value"
        ".values_list.return_value": [(1, "ru", "Властелин")]})))
    movies = MagicMock(objects=MagicMock(**{
        "using.return_value.filter.return_value.values_list.return_value": [
            (1, 10, date(2001, 5, 5)),     # catalog, listed
            (1, 11, date(2004, 1, 1)),     # catalog, not in TMDB's list
            (1, 12, date(2006, 1, 1)),     # catalog but flagged adult upstream: hidden
        ]}))
    parts = MagicMock(objects=MagicMock(**{
        "using.return_value.values_list.return_value": [
            (1, 10, date(2001, 1, 1), Decimal("10"), [12], False),
            (1, 20, TOMORROW, Decimal("20"), [14], False),       # upcoming, listed
            (1, 12, date(2006, 1, 1), Decimal("5"), [99], True),
        ]}))
    franchises = MagicMock(objects=MagicMock(**{
        "using.return_value.values.return_value": [
            {"collection_id": 1, "slug": "lotr", "name": "Lord", "poster_path": "/p.jpg"},
            {"collection_id": 2, "slug": "empty", "name": "Empty", "poster_path": None}]}))
    monkeypatch.setattr(fr, "Movie", movies)
    monkeypatch.setattr(fr, "FranchisePart", parts)
    monkeypatch.setattr(fr, "Franchise", franchises)

    (row,) = fr.build_franchise_rows()          # the franchise with nothing in it is not listed

    assert row["parts"] == 3 and row["in_catalog"] == 2    # 10, 11 held + 20 listed; 12 hidden
    assert row["years"] == f"2001–{TOMORROW.year}"
    assert row["newest"] == date(2004, 1, 1)                # the upcoming film is not "newest"
    assert row["genre_ids"] == frozenset({12, 14})          # adult part's genre is excluded
    assert row["popularity"] == pytest.approx(15.0)
    assert "властелин" in row["search_text"] and "lord" in row["search_text"]


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def test_localize_franchises_is_a_no_op_for_english(monkeypatch):
    from movies import i18n
    monkeypatch.setattr(i18n, "current_lang", lambda: "en")
    qs = MagicMock()
    assert i18n.localize_franchises(qs) is qs
    qs.annotate.assert_not_called()


def test_localize_franchises_annotates_name_and_overview_for_a_translated_request(monkeypatch):
    from movies import i18n
    monkeypatch.setattr(i18n, "current_lang", lambda: "ru")
    qs = MagicMock()
    i18n.localize_franchises(qs)
    first = qs.annotate.call_args.kwargs
    assert set(first) == {"name_tr", "overview_tr"}
    second = qs.annotate.return_value.annotate.call_args.kwargs
    assert set(second) == {"name_i18n", "overview_i18n"}


def test_franchise_display_properties_fall_back_to_english():
    plain = _franchise()
    assert plain.display_name == plain.name and plain.display_overview == plain.overview
    assert not plain.overview_is_fallback                  # nothing annotated: an English request
    translated = _franchise()
    translated.name_i18n, translated.overview_i18n, translated.overview_tr = "Имя", "Текст", None
    assert translated.display_name == "Имя"
    assert translated.overview_is_fallback                 # asked for Russian, no Russian text exists


def test_franchise_names_by_slug(monkeypatch):
    from movies import i18n
    manager = MagicMock()
    manager.using.return_value.filter.return_value.exclude.return_value.exclude.return_value \
        .values_list.return_value = [("lotr", "Властелин колец")]
    monkeypatch.setattr(i18n, "FranchiseTranslation", MagicMock(objects=manager))
    monkeypatch.setattr(i18n, "current_lang", lambda: "en")
    assert i18n.franchise_names_by_slug(["lotr"]) == {}                    # English: nothing to look up
    monkeypatch.setattr(i18n, "current_lang", lambda: "ru")
    assert i18n.franchise_names_by_slug(["lotr", None]) == {"lotr": "Властелин колец"}
    assert i18n.franchise_names_by_slug([]) == {}
