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
