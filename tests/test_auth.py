import base64
import json
import time
from collections import deque
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from server.auth import MutationLimiter
from server.config import Settings
from tests.conftest import ClientFactory, booking_payload


def test_anonymous_session_and_security_headers(client_factory: ClientFactory) -> None:
    client = client_factory(None)
    response = client.get("/api/me")
    assert response.json()["login"] is None
    assert "timezone" not in response.json()
    assert len(response.json()["csrf_token"]) >= 32
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in response.headers["strict-transport-security"]


def test_csrf_and_origin_required_for_mutations(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    owner.headers.pop("X-CSRF-Token")
    assert owner.post("/api/bookings", json=booking_payload()).status_code == 403
    owner.headers["X-CSRF-Token"] = "wrong"
    assert owner.post("/auth/logout").status_code == 403
    owner.headers["X-CSRF-Token"] = "test-csrf-token"
    owner.headers["Origin"] = "https://evil.example"
    assert owner.post("/auth/logout").status_code == 403
    owner.headers["Origin"] = "https://testserver"
    assert owner.post("/auth/logout").status_code == 204
    assert owner.get("/api/me").json()["login"] is None
    assert owner.post("/api/bookings", json=booking_payload()).status_code == 401


def test_tampered_session_is_not_authenticated(client_factory: ClientFactory) -> None:
    client = client_factory(None)
    client.cookies.set("seat_share", "forged.session.cookie")
    assert client.get("/api/bookings/mine").status_code == 401


def start_login(client: TestClient) -> str:
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 307
    location = str(response.headers["location"])
    assert location.startswith("https://github.com/login/oauth/authorize?")
    query = parse_qs(urlsplit(location).query)
    assert query["redirect_uri"] == ["https://testserver/auth/callback"]
    return query["state"][0]


def test_oauth_success_discards_token(
    client_factory: ClientFactory, app: FastAPI, settings: Settings
) -> None:
    client = client_factory(None)
    state = start_login(client)
    calls: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("access_token"):
            return httpx.Response(200, json={"access_token": "sensitive-access-token"})
        assert request.headers["Authorization"] == "Bearer sensitive-access-token"
        return httpx.Response(200, json={"login": "Example-User", "email": "private@example.test"})

    app.state.oauth_client = httpx.AsyncClient(transport=httpx.MockTransport(github))
    response = client.get(
        "/auth/callback",
        params={"code": "temporary-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert len(calls) == 2
    cookie = response.cookies["seat_share"]
    session = json.loads(base64.b64decode(TimestampSigner(settings.session_secret).unsign(cookie)))
    assert session["login"] == "example-user"
    assert set(session) == {"login", "csrf"}
    assert "sensitive-access-token" not in json.dumps(session)
    assert client.get("/api/me").json()["login"] == "example-user"
    assert (
        client.get("/auth/callback", params={"code": "temporary-code", "state": state}).status_code
        == 400
    )


@pytest.mark.parametrize("callback", [{}, {"state": "wrong", "code": "some-code"}])
def test_oauth_state_rejected(client_factory: ClientFactory, callback: dict[str, str]) -> None:
    client = client_factory(None)
    start_login(client)
    assert client.get("/auth/callback", params=callback).status_code == 400


def test_expired_oauth_state(client_factory: ClientFactory, settings: Settings) -> None:
    client = client_factory(None)
    data = base64.b64encode(
        json.dumps(
            {
                "oauth_state": "expected",
                "oauth_started": time.time() - 601,
            }
        ).encode()
    )
    cookie = TimestampSigner(settings.session_secret).sign(data).decode()
    client.cookies.set("seat_share", cookie)
    assert (
        client.get("/auth/callback", params={"state": "expected", "code": "code"}).status_code
        == 400
    )


@pytest.mark.parametrize(
    "status,body",
    [(500, {}), (200, {"error": "bad_code"}), (200, {"access_token": ""})],
)
def test_oauth_upstream_errors(
    client_factory: ClientFactory, app: FastAPI, status: int, body: dict[str, Any]
) -> None:
    client = client_factory(None)
    state = start_login(client)
    app.state.oauth_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    response = client.get("/auth/callback", params={"state": state, "code": "code"})
    assert response.status_code == 502
    assert client.get("/api/me").json()["login"] is None


def test_mutation_rate_limit(client_factory: ClientFactory, app: FastAPI) -> None:
    app.state.limiter = MutationLimiter(1)
    client = client_factory("owner")
    assert client.post("/api/bookings", json=booking_payload()).status_code == 201
    response = client.post("/api/bookings", json=booking_payload())
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"
    assert client.get("/api/bookings/mine").status_code == 200
    assert client_factory("other").post("/api/bookings", json=booking_payload()).status_code == 201


def test_default_rate_limit_threshold_and_expiry() -> None:
    limiter = MutationLimiter(30)
    for _ in range(30):
        limiter.check("owner")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        limiter.check("owner")
    assert error.value.status_code == 429
    limiter.entries["owner"] = deque([time.monotonic() - 61])
    limiter.check("owner")
    assert len(limiter.entries["owner"]) == 1


def test_unicode_oauth_state_is_validation_error(client_factory: ClientFactory) -> None:
    client = client_factory(None)
    start_login(client)
    assert (
        client.get("/auth/callback", params={"state": "\u2603", "code": "code"}).status_code == 400
    )


@pytest.mark.parametrize("body", [[], None, {"login": 123}, {"login": "<invalid>"}])
def test_invalid_github_user_body(client_factory: ClientFactory, app: FastAPI, body: Any) -> None:
    client = client_factory(None)
    state = start_login(client)

    def github(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("access_token"):
            return httpx.Response(200, json={"access_token": "temporary"})
        return httpx.Response(
            200, content=json.dumps(body), headers={"Content-Type": "application/json"}
        )

    app.state.oauth_client = httpx.AsyncClient(transport=httpx.MockTransport(github))
    assert client.get("/auth/callback", params={"state": state, "code": "code"}).status_code == 502
