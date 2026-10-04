"""Browser sign-in with an httpOnly session cookie and CSRF protection (DP-14)."""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from .factories import make_org

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_throttle():
    cache.clear()


@pytest.fixture
def user():
    return make_org()[1]


def browser():
    return APIClient(enforce_csrf_checks=True)


def sign_in(client, username="user-acme", password="pw"):
    token = client.get("/api/auth/csrf/").data["csrfToken"]
    return client.post("/api/auth/login/", {"username": username, "password": password}, format="json",
                       HTTP_X_CSRFTOKEN=token)


def test_sign_in_sets_httponly_session_cookie(user):
    client = browser()
    resp = sign_in(client)
    assert resp.status_code == 200
    assert resp.data["user"]["username"] == user.username
    cookie = resp.cookies["sessionid"]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert "access" not in resp.data  # no token handed to page scripts
    assert client.get("/api/users/me/").status_code == 200


def test_wrong_password(user):
    resp = sign_in(browser(), password="nope")
    assert resp.status_code == 400
    assert "sessionid" not in resp.cookies


def test_sign_in_needs_csrf_token(user):
    """Login CSRF: another site must not be able to sign a visitor in."""
    resp = browser().post("/api/auth/login/", {"username": "user-acme", "password": "pw"}, format="json")
    assert resp.status_code == 403


def test_writes_need_csrf_token_with_session(user):
    client = browser()
    token = sign_in(client).data["csrfToken"]
    url = "/api/documents/webhooks/"
    body = {"target_url": "https://example.com/hook", "secret": "s"}
    assert client.post(url, body, format="json").status_code == 403
    assert client.post(url, body, format="json", HTTP_X_CSRFTOKEN=token).status_code == 201


def test_reads_work_without_csrf_token(user):
    client = browser()
    sign_in(client)
    assert client.get("/api/documents/").status_code == 200


def test_sign_out_ends_session(user):
    client = browser()
    token = sign_in(client).data["csrfToken"]
    assert client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code == 204
    assert client.get("/api/users/me/").status_code in (401, 403)


def test_failed_sign_ins_are_rate_limited(user, settings):
    client = browser()
    codes = [sign_in(client, password="wrong").status_code for _ in range(12)]
    assert codes[:10] == [400] * 10
    assert codes[-1] == 429


def test_jwt_still_works_for_scripts(user):
    client = APIClient(enforce_csrf_checks=True)
    access = client.post("/api/auth/token/", {"username": "user-acme", "password": "pw"}, format="json").data["access"]
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    body = {"target_url": "https://example.com/hook", "secret": "s"}
    assert client.post("/api/documents/webhooks/", body, format="json").status_code == 201  # no CSRF for bearer tokens


def test_session_id_changes_on_sign_in(user):
    client = browser()
    client.get("/api/auth/csrf/")
    client.cookies["sessionid"] = "attacker-chosen"
    resp = sign_in(client)
    assert resp.cookies["sessionid"].value != "attacker-chosen"
