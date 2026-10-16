"""Task 136: sharing a collection by unlisted link -- the service and the
owner's endpoint. The public page itself arrives in Task 137."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DJANGO_APP_DIR = PROJECT_ROOT / "django_app"
if str(DJANGO_APP_DIR) not in sys.path:
    sys.path.insert(0, str(DJANGO_APP_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "theoria_site.settings")
django.setup()

import pytest  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.exceptions import ValidationError  # noqa: E402
from django.test import Client, override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from accounts.models import validate_username_not_reserved  # noqa: E402
from core import sharing  # noqa: E402
from core.models import Collection, CollectionItem  # noqa: E402
from core.services import (  # noqa: E402
    move_collection_item,
    remove_collection_item,
    toggle_collection_item,
)

User = get_user_model()
_EMAIL = "sharing-test@example.com"
_NAMES = {Collection.LIKED: "Liked", Collection.WATCH_LATER: "Watch later", Collection.TOP: "Top"}


@pytest.fixture
def owner():
    User.objects.filter(email=_EMAIL).delete()
    cache.clear()
    user = User.objects.create_user(email=_EMAIL, username="share_owner")
    yield user
    User.objects.filter(email=_EMAIL).delete()


def _collection(user, kind=Collection.TOP, ids=(1, 2)):
    collection, _ = Collection.objects.get_or_create(
        user=user, kind=kind, defaults={"name": _NAMES[kind]}
    )
    for position, content_id in enumerate(ids):
        CollectionItem.objects.create(
            collection=collection, content_type=CollectionItem.MOVIE,
            content_id=content_id, position=position,
        )
    return collection


def _post(client, kind, **data):
    return client.post(
        reverse("account:share_collection", kwargs={"kind": kind}),
        data, HTTP_ACCEPT="application/json",
    )


# --- updated_at ---------------------------------------------------------

def test_item_changes_move_collection_updated_at(owner):
    collection = _collection(owner, ids=(1, 2))
    stamps = [Collection.objects.get(pk=collection.pk).updated_at]

    def moved():
        stamp = Collection.objects.get(pk=collection.pk).updated_at
        assert stamp > stamps[-1]
        stamps.append(stamp)

    with patch("core.services._content_exists", return_value=True):
        toggle_collection_item(owner, "top", "movie", 3)      # add
        moved()
        toggle_collection_item(owner, "top", "movie", 3)      # un-toggle
        moved()
    first = collection.items.get(content_id=1)
    move_collection_item(owner, "top", collection.items.get(content_id=2).pk, "up")
    moved()
    remove_collection_item(owner, "top", first.pk)
    moved()


def test_removing_a_missing_item_does_not_touch_updated_at(owner):
    collection = _collection(owner)
    before = Collection.objects.get(pk=collection.pk).updated_at
    assert remove_collection_item(owner, "top", 999999) is False
    assert Collection.objects.get(pk=collection.pk).updated_at == before


# --- service ------------------------------------------------------------

def test_enable_mints_a_token_and_is_idempotent(owner):
    _collection(owner)
    first = sharing.enable_share(owner, "top", "  my   best\nfive ")
    assert len(first.share_token) == sharing.TOKEN_LENGTH
    assert first.share_token.isalnum()
    assert first.shared_at is not None
    assert first.share_note == "my best five"
    again = sharing.enable_share(owner, "top", "my best five")
    assert again.share_token == first.share_token


def test_enable_without_note_keeps_existing_note(owner):
    _collection(owner)
    sharing.enable_share(owner, "top", "keep me")
    assert sharing.enable_share(owner, "top").share_note == "keep me"
    assert sharing.enable_share(owner, "top", "").share_note == ""


def test_empty_or_missing_collection_cannot_be_shared(owner):
    with pytest.raises(sharing.ShareError):
        sharing.enable_share(owner, "top")           # no row at all
    _collection(owner, ids=())
    with pytest.raises(sharing.ShareError):
        sharing.enable_share(owner, "top")           # row, no items
    with pytest.raises(sharing.ShareError):
        sharing.enable_share(owner, "nonsense")


def test_note_longer_than_140_is_refused_not_cut(owner):
    _collection(owner)
    assert sharing.enable_share(owner, "top", "x" * 140).share_note == "x" * 140
    with pytest.raises(sharing.ShareError):
        sharing.enable_share(owner, "top", "x" * 141)


def test_rotate_replaces_token_and_requires_a_live_share(owner):
    _collection(owner)
    with pytest.raises(sharing.ShareError):
        sharing.rotate_share(owner, "top")
    old = sharing.enable_share(owner, "top", "note").share_token
    rotated = sharing.rotate_share(owner, "top")
    assert rotated.share_token != old
    assert rotated.share_note == "note"
    assert sharing.shared_collection(old) is None
    assert not sharing.is_revoked(old)               # gone without trace: 404


def test_disable_revokes_and_re_enable_mints_a_new_token(owner):
    _collection(owner)
    token = sharing.enable_share(owner, "top").share_token
    sharing.disable_share(owner, "top")
    assert sharing.shared_collection(token) is None
    assert sharing.is_revoked(token)                 # 410
    fresh = sharing.enable_share(owner, "top").share_token
    assert fresh != token
    assert sharing.shared_collection(token) is None


def test_shared_collection_unknown_and_inactive_owner(owner):
    _collection(owner)
    token = sharing.enable_share(owner, "top").share_token
    assert sharing.shared_collection("nope") is None
    assert sharing.shared_collection(None) is None
    User.objects.filter(pk=owner.pk).update(is_active=False)
    assert sharing.shared_collection(token) is None
    assert not sharing.is_revoked(token)


def test_shared_collection_hydrates_in_display_order(owner):
    _collection(owner, Collection.LIKED, ids=(1, 2, 3))
    token = sharing.enable_share(owner, "liked").share_token

    def fake_hydrate(items):
        for item in items:
            item.content = _movie(item.content_id, 1990 + item.content_id, 100, 8.0)
            item.title = str(item.content_id)
        return items

    with patch("core.sharing._hydrate", side_effect=fake_hydrate):
        shared = sharing.shared_collection(token)
    assert shared.owner.pk == owner.pk
    assert [row.content_id for row in shared.items] == [3, 2, 1]   # newest first


def test_s_is_a_reserved_username():
    with pytest.raises(ValidationError):
        validate_username_not_reserved("s")


# --- endpoint -----------------------------------------------------------

@override_settings(ALLOWED_HOSTS=["testserver"])
def test_endpoint_enable_rotate_disable_json(owner):
    _collection(owner)
    client = Client()
    client.force_login(owner)

    enabled = _post(client, "top", action="enable", note="hello")
    assert enabled.status_code == 200
    body = enabled.json()
    token = Collection.objects.get(user=owner, kind="top").share_token
    assert body == {"url": f"http://testserver/s/{token}/", "note": "hello"}

    rotated = _post(client, "top", action="rotate").json()
    assert rotated["url"] != body["url"]
    assert rotated["note"] == "hello"

    disabled = _post(client, "top", action="disable").json()
    assert disabled == {"url": None, "note": "hello"}


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_endpoint_rejects_bad_requests(owner):
    client = Client()
    client.force_login(owner)
    assert _post(client, "top", action="enable").status_code == 400      # empty
    _collection(owner)
    assert _post(client, "top", action="explode").status_code == 400
    assert _post(client, "top", action="enable", note="x" * 141).status_code == 400
    assert client.get(
        reverse("account:share_collection", kwargs={"kind": "top"})
    ).status_code == 405


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_endpoint_requires_login_and_only_touches_own_collection(owner):
    _collection(owner)
    other_email = "sharing-other@example.com"
    User.objects.filter(email=other_email).delete()
    other = User.objects.create_user(email=other_email, username="share_other")
    try:
        anon = Client()
        response = _post(anon, "top", action="enable")
        assert response.status_code == 302
        assert not Collection.objects.get(user=owner, kind="top").share_token

        client = Client()
        client.force_login(other)
        assert _post(client, "top", action="enable").status_code == 400
        assert not Collection.objects.get(user=owner, kind="top").share_token
    finally:
        User.objects.filter(email=other_email).delete()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_endpoint_form_post_redirects_back(owner):
    _collection(owner)
    client = Client()
    client.force_login(owner)
    response = client.post(
        reverse("account:share_collection", kwargs={"kind": "top"}),
        {"action": "enable", "next": "/me/#top"},
    )
    assert response.status_code == 302
    assert response["Location"] == "/me/#top"


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_endpoint_is_rate_limited(owner):
    _collection(owner)
    client = Client()
    client.force_login(owner)
    with patch("core.views.SHARE_RATE_LIMIT", 2):
        assert _post(client, "top", action="enable").status_code == 200
        assert _post(client, "top", action="enable").status_code == 200
        assert _post(client, "top", action="enable").status_code == 429


# --- share_stats ----------------------------------------------------------

def _movie(movie_id, year, runtime, rating):
    from datetime import date

    from movies.models import Movie

    movie = Movie(movie_id=movie_id, title=f"Movie {movie_id}", slug=f"movie-{movie_id}",
                  release_date=date(year, 6, 1), runtime=runtime)
    movie.imdb_rating = rating
    return movie


def _series(series_id, year, episodes, rating):
    from datetime import date

    from movies.models import Series

    show = Series(series_id=series_id, name=f"Show {series_id}", slug=f"show-{series_id}",
                  first_air_date=date(year, 1, 1), number_of_episodes=episodes)
    show.imdb_rating = rating
    show.year_span = str(year)
    return show


def _row(content, content_type, pk, added=0):
    from datetime import datetime, timedelta, timezone as tz

    item = CollectionItem(
        content_type=content_type,
        content_id=pk,
        position=pk,
        added_at=datetime(2026, 1, 1, tzinfo=tz.utc) + timedelta(days=added),
    )
    item.id = pk
    item.content = content
    item.title = getattr(content, "title", None) or content.name
    return item


def test_share_stats_measures_the_list():
    rows = [
        _row(_movie(1, 1972, 175, 9.2), "movie", 1),
        _row(_movie(2, 1994, 154, 8.9), "movie", 2),
        _row(_movie(3, 1999, 90, None), "movie", 3),
        _row(_series(4, 2008, 62, 9.5), "series", 4),
    ]
    stats = sharing.share_stats(Collection.TOP, rows)
    assert (stats["movies"], stats["shows"], stats["total"]) == (3, 1, 4)
    assert stats["avg_imdb"] == 9.2           # (9.2 + 8.9 + 9.5) / 3, one unrated
    assert stats["rated"] == 3
    assert (stats["year_min"], stats["year_max"]) == (1972, 2008)
    assert stats["runtime_minutes"] == 175 + 154 + 90   # a show adds episodes, not minutes
    assert (stats["runtime_hours"], stats["runtime_rest"]) == (6, 59)
    assert stats["episodes"] == 62
    assert stats["genre"] is None             # only computed for Liked
    assert stats["decades"] == [
        {"decade": 1970, "count": 1, "pct": 50},
        {"decade": 1990, "count": 2, "pct": 100},
        {"decade": 2000, "count": 1, "pct": 50},
    ]


def test_share_stats_of_nothing_is_empty_not_an_error():
    stats = sharing.share_stats(Collection.LIKED, [])
    assert stats["total"] == 0
    assert stats["avg_imdb"] is None
    assert stats["year_min"] is None
    assert stats["genre"] is None
    assert stats["decades"] == []


# --- the public page ---------------------------------------------------------

def _hydrate_with(rows):
    def fake(items):
        return list(rows)
    return patch("core.sharing._hydrate", side_effect=fake)


def _shared_top(owner, note="Five I'd stake my taste on."):
    collection = _collection(owner, Collection.TOP, ids=(1, 2, 3))
    sharing.enable_share(owner, "top", note)
    return Collection.objects.get(pk=collection.pk)


def _top_rows():
    return [
        _row(_movie(1, 1972, 175, 9.2), "movie", 1),
        _row(_movie(2, 1994, 154, 8.9), "movie", 2),
        _row(_series(3, 2008, 62, 9.5), "series", 3),
    ]


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_public_page_needs_no_login_and_hides_the_owners_email(owner):
    collection = _shared_top(owner)
    with _hydrate_with(_top_rows()):
        response = Client().get(f"/s/{collection.share_token}/")
    assert response.status_code == 200
    html = response.content.decode()
    assert _EMAIL not in html
    assert "example.com" not in html
    assert "share_owner" in html                      # the handle is shown
    assert "Five I&#x27;d stake my taste on." in html or "Five I'd stake my taste on." in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert 'property="og:title" content="Top · @share_owner"' in html
    assert "og-default.png" in html
    assert response["Referrer-Policy"] == "same-origin"
    assert "noindex" in response["X-Robots-Tag"]
    # Signed out: the invitation shows, the owner strip does not.
    assert "Make your own list on Theoria" in html
    assert "This is your public link" not in html
    # A friend who isn't signed in can read the whole list without being sent
    # to the sign-in wall: the cards carry no links into the gated pages.
    assert "/movies/movie-1/" not in html
    assert "card-static" in html


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_public_page_ranks_a_top_list_and_measures_it(owner):
    collection = _shared_top(owner)
    with _hydrate_with(_top_rows()):
        html = Client().get(f"/s/{collection.share_token}/").content.decode()
    for rank in ("01", "02", "03"):
        assert f'account-card__rank" aria-hidden="true">{rank}<' in html
    assert "9.2" in html                      # average IMDb of 9.2, 8.9, 9.5
    assert "1972–2008" in html
    assert "5 h 29 m" in html                 # 175 + 154 minutes


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_owner_sees_the_manage_strip_not_the_invitation(owner):
    collection = _shared_top(owner)
    client = Client()
    client.force_login(owner)
    with _hydrate_with(_top_rows()):
        response = client.get(f"/s/{collection.share_token}/")
    html = response.content.decode()
    assert "This is your public link" in html
    assert 'href="/me/#top"' in html
    assert "Make your own list on Theoria" not in html
    assert _EMAIL not in html
    assert 'href="/movies/movie-1/"' in html           # signed in: cards link through


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_unknown_token_is_404_revoked_is_410(owner):
    collection = _shared_top(owner)
    token = collection.share_token
    assert Client().get("/s/doesnotexist/").status_code == 404
    sharing.disable_share(owner, "top")
    response = Client().get(f"/s/{token}/")
    assert response.status_code == 410
    assert "noindex" in response["X-Robots-Tag"]
    assert _EMAIL not in response.content.decode()


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_public_page_renders_in_russian_from_the_viewers_cookie(owner):
    collection = _shared_top(owner)
    client = Client()
    client.cookies["django_language"] = "ru"
    with _hydrate_with(_top_rows()):
        response = client.get(f"/s/{collection.share_token}/")
    assert response.status_code == 200
    html = response.content.decode()
    assert '<html lang="ru">' in html
    assert "Топ" in html                      # the kind's heading, translated


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_public_page_follows_the_collection_after_an_edit(owner):
    """The cache is keyed by updated_at, so a change shows at once."""
    collection = _shared_top(owner)
    token = collection.share_token
    first = [_row(_movie(1, 1972, 175, 9.2), "movie", 1)]
    second = first + [_row(_movie(2, 1994, 154, 8.9), "movie", 2)]
    with _hydrate_with(first):
        html = Client().get(f"/s/{token}/").content.decode()
    assert "Movie 2" not in html
    with patch("core.services._content_exists", return_value=True):
        toggle_collection_item(owner, "top", "movie", 99)
    with _hydrate_with(second):
        html = Client().get(f"/s/{token}/").content.decode()
    assert "Movie 2" in html


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_empty_after_titles_leave_the_catalogue(owner):
    collection = _shared_top(owner)
    with _hydrate_with([]):
        response = Client().get(f"/s/{collection.share_token}/")
    assert response.status_code == 200
    assert "Nothing in this list right now." in response.content.decode()


# --- the artwork (Task 138) ----------------------------------------------------

def _card(token, client=None, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return (client or Client()).get(f"/s/{token}/card/" + (f"?{query}" if query else ""))


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_card_renders_every_format_and_theme_without_login(owner):
    collection = _shared_top(owner)
    sizes = {"story": ("1080px", "1920px"), "wide": ("1600px", "900px")}
    for fmt, (width, height) in sizes.items():
        for theme in ("ink", "paper"):
            with _hydrate_with(_top_rows()):
                response = _card(collection.share_token, format=fmt, theme=theme)
            assert response.status_code == 200
            html = response.content.decode()
            # Pixel sizes are never localized (a "1,080px" would void them).
            assert f"--sc-w: {width}; --sc-h: {height}" in html
            assert f'data-format="{fmt}" data-theme="{theme}"' in html
            assert _EMAIL not in html and "example.com" not in html
            assert "@share_owner" in html
            assert '<meta name="robots" content="noindex, nofollow">' in html
            assert "noindex" in response["X-Robots-Tag"]
            assert response["Referrer-Policy"] == "same-origin"


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_card_falls_back_to_story_ink_and_prints_the_link(owner):
    collection = _shared_top(owner)
    with _hydrate_with(_top_rows()):
        html = _card(collection.share_token, format="poster", theme="neon").content.decode()
    assert 'data-format="story" data-theme="ink"' in html
    assert f"testserver/s/{collection.share_token}<" in html      # no scheme, no slash
    # The card carries its collection's line, never the owner's own note.
    assert "My best, in order." in html
    assert "stake my taste" not in html


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_card_ranks_top_and_draws_plates_for_missing_posters(owner):
    collection = _shared_top(owner)
    rows = _top_rows()
    rows[0].content.poster_path = "/godfather.jpg"
    with _hydrate_with(rows):
        html = _card(collection.share_token).content.decode()
    assert "/w500/godfather.jpg" in html
    assert 'crossorigin="anonymous"' in html
    assert html.count("sc-plate__title") == 2          # Movie 2 and Show 3
    # On a Story, Top's posters carry their rank in lime, as on /me.
    for rank in ("01", "02", "03"):
        assert f'sc-print__rank" aria-hidden="true">{rank}<' in html
    # Only the people, the posters, the note and the link: no measurements.
    assert "IMDb" not in html and "9.2" not in html


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_liked_card_fills_the_contact_sheet(owner):
    _collection(owner, Collection.LIKED, ids=range(1, 14))
    collection = sharing.enable_share(owner, "liked")
    many = [_row(_movie(i, 1990 + i, 100, 7.0), "movie", i) for i in range(1, 14)]
    with _hydrate_with(many):
        html = _card(collection.share_token, format="story").content.decode()
    # A Story is four big posters, 2 × 2, unnumbered on Liked; the other
    # nine are pointed at the link.
    assert html.count('class="sc-cell"') == 4
    assert "--rows: 2" in html
    assert "sc-print__rank" not in html
    assert '<span class="sc-mono">+9</span> more<' in html
    cache.clear()                   # the list is cached by updated_at
    with _hydrate_with(many[:5]):
        html = _card(collection.share_token, format="wide").content.decode()
    # A Wide sheet is 4 across: five prints make two rows.
    assert html.count('class="sc-frame"') == 5
    assert "--rows: 2" in html
    assert "sc-foot__more" not in html             # all five fit


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_card_fragment_for_the_share_sheet(owner):
    collection = _shared_top(owner)
    with _hydrate_with(_top_rows()):
        html = Client().get(
            f"/s/{collection.share_token}/card/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        ).content.decode()
    assert html.lstrip().startswith("<article") or "<html" not in html
    assert 'class="sc"' in html


@override_settings(ALLOWED_HOSTS=["testserver"])
def test_card_unknown_is_404_revoked_is_410_and_renders_in_russian(owner):
    collection = _shared_top(owner)
    token = collection.share_token
    client = Client()
    client.cookies["django_language"] = "ru"
    with _hydrate_with(_top_rows()):
        html = _card(token, client).content.decode()
    assert '<html lang="ru">' in html
    assert "Топ" in html
    assert Client().get("/s/doesnotexist/card/").status_code == 404
    sharing.disable_share(owner, "top")
    assert _card(token).status_code == 410
