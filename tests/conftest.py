import base64
import json
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from server.config import Settings
from server.db import closing_connection
from server.main import create_app

ClientFactory = Callable[[str | None], TestClient]
CSRF = "test-csrf-token"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        github_client_id="test-client",
        github_client_secret="test-secret",
        session_secret="test-session-secret-that-is-long-enough",
        base_url="https://testserver",
        db_path=tmp_path / "app.db",
        environment="test",
        mutation_limit=1000,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client_factory(app: FastAPI, settings: Settings) -> Iterator[ClientFactory]:
    with TestClient(app, base_url=settings.base_url), ExitStack() as stack:
        with closing_connection(settings) as connection, connection:
            connection.executemany(
                "INSERT INTO bookables (type, name, area) VALUES ('restaurant', ?, ?)",
                [
                    ("Test Kitchen", "City centre"),
                    ("Test Garden", "Riverside"),
                    ("Test Noodle House", "Old town"),
                ],
            )

        def make(login: str | None = None) -> TestClient:
            client = TestClient(app, base_url=settings.base_url)
            stack.callback(client.close)
            if login is not None:
                data = base64.b64encode(json.dumps({"login": login, "csrf": CSRF}).encode())
                cookie = TimestampSigner(settings.session_secret).sign(data).decode()
                client.cookies.set("seat_share", cookie, domain="testserver.local", path="/")
                client.headers.update({"X-CSRF-Token": CSRF, "Origin": settings.base_url})
            return client

        yield make


def booking_payload(**overrides: Any) -> dict[str, Any]:
    starts_at = (datetime.now() + timedelta(days=30)).replace(
        hour=19, minute=0, second=0, microsecond=0
    )
    return {
        "name": "Dinner table",
        "bookable_id": 1,
        "starts_at": starts_at.isoformat(),
        "timezone": "Europe/London",
        "party_size": 4,
        "seats_available": 2,
        "description": "Dinner together",
        **overrides,
    }


def create_booking(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/api/bookings", json=booking_payload(**overrides))
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


def ask(client: TestClient, booking_id: int, seats: int = 1) -> httpx.Response:
    response = client.post(
        f"/api/bookings/{booking_id}/requests",
        json={"seats": seats},
    )
    assert isinstance(response, httpx.Response)
    return response
