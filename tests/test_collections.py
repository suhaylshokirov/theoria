"""End-to-end tests for personal collections (Liked / Watch later / Top).

Identity now lives entirely in the `accounts` app (see tests/test_accounts.py
and, once Task 96 lands, its planned sign-up/sign-in/gating suite) — this
file only covers what `core` still owns: a signed-in user's Collection /
CollectionItem rows.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.test.utils import setup_test_environment, teardown_test_environment  # noqa: E402
from django.urls import reverse  # noqa: E402

from core.models import Collection, CollectionItem  # noqa: E402
from movies.models import Movie, Series  # noqa: E402

User = get_user_model()

_TEST_EMAIL = "collections-test@example.com"


def setup_module(module):
    # Enables response.context on the test Client for the /me/ tests below
    # (normally wired up by Django's own test runner, not in play for these
    # plain-pytest tests) -- same as tests/test_django_views.py.
    setup_test_environment()


def teardown_module(module):
    teardown_test_environment()

_COLLECTION_NAMES = {
    Collection.LIKED: "Liked",
    Collection.WATCH_LATER: "Watch later",
    Collection.TOP: "Top",
}


def _add_items(user, kind, content_type, content_ids):
    """Create real Collection/CollectionItem rows (position = list order),
    mirroring core.services._collection's get_or_create shape."""
    collection, _ = Collection.objects.get_or_create(
        user=user, kind=kind, defaults={"name": _COLLECTION_NAMES[kind]}
    )
    for position, content_id in enumerate(content_ids):
        CollectionItem.objects.create(
            collection=collection,
            content_type=content_type,
            content_id=content_id,
            position=position,
        )
    return collection


def _mock_warehouse_content(movie_mgr, series_mgr, movies=(), series=()):
    """Wires Movie.objects/Series.objects for core.services.collection_rows's
    shape: .using("warehouse").filter(...).annotate(imdb_rating=...).
    Same "mock the boundary" pattern as tests/test_django_views.py."""
    movie_mgr.using.return_value.filter.return_value.annotate.return_value = list(movies)
    series_mgr.using.return_value.filter.return_value.annotate.return_value = list(series)


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_authenticated_user_can_toggle_warehouse_content_in_collections():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            response = client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert response.status_code == 302
        assert response["Location"] == "/movies/example/"
        item = CollectionItem.objects.get(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        )

        with patch("core.services._content_exists", return_value=True):
            client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert not CollectionItem.objects.filter(pk=item.pk).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_json_toggle_returns_selected_state_without_redirecting():
    """The icon-fill bloom (theoria.js's initCollectionActions) only has
    something to animate if the pill doesn't reload out from under it -- a
    request that says it can read JSON gets the new state back instead of
    the redirect a plain form POST gets."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        url = reverse(
            "account:toggle_collection",
            kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
        )
        with patch("core.services._content_exists", return_value=True):
            response = client.post(url, {"next": "/movies/example/"}, HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        assert response.json() == {"selected": True}
        assert CollectionItem.objects.filter(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        ).exists()

        with patch("core.services._content_exists", return_value=True):
            response = client.post(url, {"next": "/movies/example/"}, HTTP_ACCEPT="application/json")
        assert response.status_code == 200
        assert response.json() == {"selected": False}
        assert not CollectionItem.objects.filter(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        ).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_non_json_toggle_still_redirects():
    """The no-JS / no-Accept-header path is unchanged -- a plain form POST
    (no JS, or theoria.js's fetch fallback) still gets the old redirect
    behaviour, not JSON."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            response = client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        assert response.status_code == 302
        assert response["Location"] == "/movies/example/"
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_remove_item_with_no_next_falls_back_to_profile():
    """account.html's remove forms send no `next` -- the reader should land
    back on /me/, not the homepage safe_next used to fall back to."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                {"next": "/movies/example/"},
            )
        item = CollectionItem.objects.get(
            collection__user=user,
            collection__kind=Collection.LIKED,
            content_type=CollectionItem.MOVIE,
            content_id=550,
        )

        response = client.post(
            reverse("account:remove_item", kwargs={"kind": "liked", "item_id": item.pk})
        )

        assert response.status_code == 302
        assert response["Location"] == "/me/"
        assert not CollectionItem.objects.filter(pk=item.pk).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_toggle_creates_only_the_collection_it_touches():
    """A Like click used to get_or_create all three collections -- three extra
    auth-database round-trips per press. Only the touched one is needed."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch("core.services._content_exists", return_value=True):
            client.post(
                reverse(
                    "account:toggle_collection",
                    kwargs={"kind": "liked", "content_type": "movie", "content_id": 550},
                ),
                HTTP_ACCEPT="application/json",
            )
        kinds = set(Collection.objects.filter(user=user).values_list("kind", flat=True))
        assert kinds == {Collection.LIKED}
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_html_pages_are_private_and_revalidated():
    """The nav depends on who's signed in, so no cache may reuse a page
    rendered for a different sign-in state."""
    response = Client().get("/this-page-does-not-exist/")
    assert response["Content-Type"].startswith("text/html")
    cache_control = response["Cache-Control"]
    assert "private" in cache_control
    assert "no-cache" in cache_control


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_views_that_set_their_own_cache_policy_keep_it():
    response = Client().get(reverse("accounts:login"))
    assert "no-store" in response["Cache-Control"]


# ---------------------------------------------------------------------------
# /me/ (account page redesign)
# ---------------------------------------------------------------------------


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_page_renders_shared_poster_card_markup():
    """/me/ must reuse the same poster-card partials as every other grid on
    the site (movies/_movie_card.html, movies/_series_card.html), not a
    bespoke card of its own."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        _add_items(user, Collection.LIKED, CollectionItem.MOVIE, [101])
        _add_items(user, Collection.WATCH_LATER, CollectionItem.SERIES, [201])
        movie = Movie(movie_id=101, title="Liked Movie")
        show = Series(series_id=201, name="Watch Later Show")

        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr, movies=[movie], series=[show])
            response = client.get(reverse("profile"))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'class="poster-card is-film"' in content
        assert "Liked Movie" in content
        assert "Watch Later Show" in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_page_pages_each_collection_independently():
    """?liked=2 pages Liked while Watch later stays on its own page, and
    Liked's own pager preserves watch_later= and lands back on #liked."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        liked_ids = list(range(1, 16))  # 15 items -> 3 pages at 5/page
        _add_items(user, Collection.LIKED, CollectionItem.MOVIE, liked_ids)
        _add_items(user, Collection.WATCH_LATER, CollectionItem.MOVIE, [901])
        movies = [Movie(movie_id=i, title=f"Movie {i}") for i in liked_ids]
        movies.append(Movie(movie_id=901, title="Watch later movie"))

        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr, movies=movies)
            response = client.get(reverse("profile"), {"liked_page": "2", "later_page": "2"})

        assert response.status_code == 200
        sections = {s["slug"]: s for s in response.context["sections"]}
        assert sections["liked"]["page_obj"].number == 2
        # Watch later only has 1 item -> 1 page -> get_page clamps the
        # explicit ?later_page=2 back down to page 1, independently of Liked.
        assert sections["later"]["page_obj"].number == 1

        # Liked's page-1 link drops its own default page param and keeps
        # nothing stale for Watch later (clamped to its default, page 1).
        assert sections["liked"]["prev_url"] == "/me/#liked"
        assert sections["liked"]["page_links"][2]["url"] == "/me/?liked_page=3#liked"
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_page_out_of_range_page_clamps_to_last_page():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        ids = list(range(1, 16))
        _add_items(user, Collection.LIKED, CollectionItem.MOVIE, ids)
        movies = [Movie(movie_id=i, title=f"Movie {i}") for i in ids]

        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr, movies=movies)
            response = client.get(reverse("profile"), {"liked_page": "99"})

        assert response.status_code == 200
        sections = {s["slug"]: s for s in response.context["sections"]}
        assert sections["liked"]["page_obj"].number == 3  # only 3 pages exist
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_remove_form_next_ends_with_kind_fragment_and_redirects_there():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        _add_items(user, Collection.LIKED, CollectionItem.MOVIE, [42])
        movie = Movie(movie_id=42, title="Solo Movie")

        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr, movies=[movie])
            response = client.get(reverse("profile"))

        content = response.content.decode()
        assert 'value="/me/#liked"' in content

        item = CollectionItem.objects.get(collection__user=user, collection__kind=Collection.LIKED)
        remove_response = client.post(
            reverse("account:remove_item", kwargs={"kind": "liked", "item_id": item.pk}),
            {"next": "/me/#liked"},
        )
        assert remove_response.status_code == 302
        assert remove_response["Location"] == "/me/#liked"
        assert not CollectionItem.objects.filter(pk=item.pk).exists()
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_top_section_renders_move_buttons():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        _add_items(user, Collection.TOP, CollectionItem.MOVIE, [1, 2, 3])
        movies = [Movie(movie_id=i, title=f"Top Movie {i}") for i in (1, 2, 3)]

        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr, movies=movies)
            response = client.get(reverse("profile"))

        content = response.content.decode()
        assert "account-card__moves" in content
        assert "Rank 1:" in content
        assert "Rank 3:" in content
        # First item overall can't move up, last item overall can't move down.
        assert 'aria-label="Move Top Movie 1 up" disabled' in content
        assert 'aria-label="Move Top Movie 3 down" disabled' in content
        # The middle item can move both ways.
        assert 'aria-label="Move Top Movie 2 up">' in content
        assert 'aria-label="Move Top Movie 2 down">' in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_empty_collections_show_empty_state_copy():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
            Series, "objects", new=MagicMock()
        ) as series_mgr:
            _mock_warehouse_content(movie_mgr, series_mgr)
            response = client.get(reverse("profile"))

        content = response.content.decode()
        assert "Nothing liked yet" in content
        assert "Nothing saved for later" in content
        # An empty Top shows its five open ranks rather than a panel.
        assert content.count('class="account-slot"') == 5
        # Nothing to page through.
        assert "account-pager" not in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


# --- /me/ redesign: paging, panels --------------------------------

from datetime import date  # noqa: E402
from decimal import Decimal  # noqa: E402

from core.views import _page_window  # noqa: E402


def test_page_window_elides_long_runs():
    assert _page_window(1, 1) == [1]
    assert _page_window(3, 7) == [1, 2, 3, 4, 5, 6, 7]
    assert _page_window(5, 12) == [1, None, 4, 5, 6, None, 12]
    assert _page_window(1, 12) == [1, 2, None, 12]
    assert _page_window(12, 12) == [1, None, 11, 12]


def _seeded_liked(user, count):
    """`count` Liked movies whose titles, ratings and dates all run in an
    order different from their insertion order, with every 7th rating and
    every 5th release date missing."""
    ids = list(range(1, count + 1))
    _add_items(user, Collection.LIKED, CollectionItem.MOVIE, ids)
    movies = []
    for i in ids:
        movie = Movie(
            movie_id=i,
            title=f"Title {(i * 7) % count:02d}",
            release_date=None if i % 5 == 0 else date(1990 + (i * 3) % count, 1, 1),
        )
        movie.imdb_rating = None if i % 7 == 0 else Decimal(str(((i * 11) % count) / 3)).quantize(Decimal("0.1"))
        movies.append(movie)
    return movies


def _get_profile(client, movies, params=None, series=(), **headers):
    with patch.object(Movie, "objects", new=MagicMock()) as movie_mgr, patch.object(
        Series, "objects", new=MagicMock()
    ) as series_mgr:
        _mock_warehouse_content(movie_mgr, series_mgr, movies=movies, series=series)
        return client.get(reverse("profile"), params or {}, **headers)


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_lists_newest_first_with_no_sort_control_and_counts_movies():
    """Liked and Watch later run newest-added first across every page, there
    is no sort control, and each section's counter reads "N movies"."""
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 12)
        _add_items(user, Collection.WATCH_LATER, CollectionItem.MOVIE, [1])
        walked = []
        for page in (1, 2, 3):
            response = _get_profile(client, movies, {"liked_page": page, "later_page": "1"})
            liked = {s["slug"]: s for s in response.context["sections"]}["liked"]
            walked += [item.pk for item in liked["page_obj"]]
        newest_first = list(
            CollectionItem.objects.filter(collection__user=user, collection__kind=Collection.LIKED)
            .order_by("-added_at", "-pk")
            .values_list("pk", flat=True)
        )
        assert walked == newest_first

        content = response.content.decode()
        assert "_sort" not in content
        assert "<select" not in content
        assert '<span class="mono">12</span> movies' in content
        assert '<span class="mono">1</span> movie<' in content
        assert "</span> title" not in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_pager_readout_current_page_and_disabled_ends():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 12)
        content = _get_profile(client, movies, {"liked_page": "3"}).content.decode()
        assert "Showing 11–12 of 12" in content
        assert '<nav class="account-pager" aria-label="Liked pages">' in content
        assert 'aria-current="page" data-account-nav>3</a>' in content
        # Last page: Next is a disabled button, Previous a live link.
        assert 'aria-label="Next page of Liked" disabled' in content
        assert 'href="/me/?liked_page=2#liked" aria-label="Previous page of Liked"' in content

        content = _get_profile(client, movies).content.decode()
        assert "Showing 1–5 of 12" in content
        assert 'aria-label="Previous page of Liked" disabled' in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_fill_panel_only_on_a_short_last_page():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 13)
        # Page 1 is full and not last: no panel.
        content = _get_profile(client, movies).content.decode()
        assert "account-fill" not in content
        # Page 3 holds 3 of 5: the panel spans what's left of the row.
        content = _get_profile(client, movies, {"liked_page": "3"}).content.decode()
        assert '<li class="account-fill" data-on-page="3">' in content
        assert "Room for more" in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()

    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 10)
        # 10 items: the last page is a full row, so no panel.
        content = _get_profile(client, movies, {"liked_page": "2"}).content.decode()
        assert "account-fill" not in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_top_pages_by_rank_and_shows_open_rank_slots():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        ids = [30, 10, 20, 50, 40, 60, 70]
        _add_items(user, Collection.TOP, CollectionItem.MOVIE, ids)  # position = list order
        movies = [Movie(movie_id=i, title=f"Top {i}") for i in ids]

        response = _get_profile(client, movies)
        top = {s["slug"]: s for s in response.context["sections"]}["top"]
        assert [item.content_id for item in top["page_obj"]] == [30, 10, 20, 50, 40]
        assert top["rank_slots"] == []  # not the last page

        response = _get_profile(client, movies, {"top_page": "2"})
        top = {s["slug"]: s for s in response.context["sections"]}["top"]
        assert [item.content_id for item in top["page_obj"]] == [60, 70]
        assert top["rank_slots"] == [8, 9, 10]
        content = response.content.decode()
        assert "Rank 6:" in content
        assert '<span class="account-slot__rank" aria-hidden="true">08</span>' in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_section_fetch_returns_only_that_section():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 6)
        response = _get_profile(
            client, movies, {"_section": "liked", "liked_page": "2"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        content = response.content.decode()
        assert response.status_code == 200
        assert 'id="liked"' in content
        assert 'id="later"' not in content
        assert "account-hero" not in content
        assert "Showing 6–6 of 6" in content

        response = _get_profile(
            client, movies, {"_section": "nope"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        assert response.status_code == 400
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_remove_answers_json_and_emptied_page_clamps_back():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        movies = _seeded_liked(user, 6)
        last = CollectionItem.objects.filter(collection__user=user).order_by("added_at", "pk").first()
        # Liked is newest first, so the oldest item is alone on page 2.
        response = client.post(
            reverse("account:remove_item", kwargs={"kind": "liked", "item_id": last.pk}),
            HTTP_ACCEPT="application/json",
        )
        assert response.json() == {"removed": True}

        response = _get_profile(client, movies, {"liked_page": "2"})
        liked = {s["slug"]: s for s in response.context["sections"]}["liked"]
        assert liked["page_obj"].number == 1
        assert liked["count"] == 5
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_account_hero_counts_and_remove_labels():
    User.objects.filter(email=_TEST_EMAIL).delete()
    user = User.objects.create_user(email=_TEST_EMAIL, username="collections-reader")
    client = Client()
    client.force_login(user)
    try:
        _add_items(user, Collection.LIKED, CollectionItem.MOVIE, [1, 2])
        _add_items(user, Collection.WATCH_LATER, CollectionItem.SERIES, [9])
        movies = [Movie(movie_id=i, title=f"Movie {i}") for i in (1, 2)]
        show = Series(series_id=9, name="A Show")
        content = _get_profile(client, movies, series=[show]).content.decode()
        assert '<a class="chip account-jump" href="#later">' in content
        assert '<span class="account-jump__count mono">2</span>' in content
        assert 'aria-label="Remove A Show from Watch later"' in content
        assert "Account · collections-reader" in content
    finally:
        User.objects.filter(email=_TEST_EMAIL).delete()
