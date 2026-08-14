from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.unit.conftest import FakeContainer

pytestmark = pytest.mark.unit


def test_register_returns_201_with_user_body(http_client: TestClient) -> None:
    resp = http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["display_name"] == "A"
    assert "id" in body
    assert "password" not in body and "password_hash" not in body


def test_register_duplicate_email_returns_409(http_client: TestClient) -> None:
    payload = {"email": "a@example.com", "password": "s3cret!!", "display_name": "A"}
    http_client.post("/v1/auth/register", json=payload)
    resp = http_client.post("/v1/auth/register", json=payload)
    assert resp.status_code == 409


def test_login_returns_access_token_and_sets_refresh_cookie(http_client: TestClient) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    resp = http_client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "s3cret!!"}
    )
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"
    assert resp.json()["access_token"]

    set_cookie = resp.headers.get("set-cookie", "")
    assert "__Host-refresh_token=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=lax" in set_cookie.lower()
    assert "Path=/" in set_cookie


def test_login_wrong_password_returns_401(http_client: TestClient) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    resp = http_client.post("/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_me_requires_authentication(http_client: TestClient) -> None:
    resp = http_client.get("/v1/me")
    assert resp.status_code == 401


def test_me_returns_identity_with_valid_bearer_token(http_client: TestClient) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    login_resp = http_client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "s3cret!!"}
    )
    access_token = login_resp.json()["access_token"]

    resp = http_client.get("/v1/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "a@example.com"


def test_logout_requires_authentication(http_client: TestClient) -> None:
    resp = http_client.post("/v1/auth/logout")
    assert resp.status_code == 401


def test_logout_denies_the_access_token_and_it_stops_working(
    http_client: TestClient, fake_container: FakeContainer
) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    login_resp = http_client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "s3cret!!"}
    )
    access_token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    logout_resp = http_client.post("/v1/auth/logout", headers=headers)
    assert logout_resp.status_code == 204

    me_resp = http_client.get("/v1/me", headers=headers)
    assert me_resp.status_code == 401


def test_refresh_without_cookie_returns_401(http_client: TestClient) -> None:
    resp = http_client.post("/v1/auth/refresh")
    assert resp.status_code == 401


def test_refresh_with_cookie_from_login_rotates_and_returns_new_access_token(
    http_client: TestClient,
) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    login_resp = http_client.post(
        "/v1/auth/login", json={"email": "a@example.com", "password": "s3cret!!"}
    )
    access_token_1 = login_resp.json()["access_token"]

    # TestClient's cookie jar carries the __Host-refresh_token cookie
    # forward automatically, the same way a browser would.
    refresh_resp = http_client.post("/v1/auth/refresh")
    assert refresh_resp.status_code == 200
    access_token_2 = refresh_resp.json()["access_token"]
    assert access_token_2 != access_token_1

    # The rotated refresh cookie works too — proves the successor, not just
    # the access token, is a real usable credential.
    refresh_resp_2 = http_client.post("/v1/auth/refresh")
    assert refresh_resp_2.status_code == 200


def test_reused_refresh_token_outside_grace_window_is_rejected(
    http_client: TestClient, fake_container: FakeContainer
) -> None:
    http_client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "s3cret!!", "display_name": "A"},
    )
    http_client.post("/v1/auth/login", json={"email": "a@example.com", "password": "s3cret!!"})

    old_cookie_value = http_client.cookies.get("__Host-refresh_token")
    assert http_client.post("/v1/auth/refresh").status_code == 200

    # Move well past the grace window, then replay the *original* (now
    # once-used) refresh cookie — this must be rejected, not silently
    # re-honored.
    from datetime import timedelta

    fake_container.clock.advance(timedelta(seconds=60))
    http_client.cookies.set("__Host-refresh_token", old_cookie_value)
    replay_resp = http_client.post("/v1/auth/refresh")
    assert replay_resp.status_code == 401
