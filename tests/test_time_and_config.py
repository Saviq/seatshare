import socket
import sqlite3
from datetime import datetime

import pytest
from fastapi import HTTPException

from server import db
from server.config import ROOT, Settings
from server.db import closing_connection, initialize, transaction
from server.time_utils import booking_time
from tests.conftest import ClientFactory, booking_payload, create_booking


def test_wall_time_stored_as_utc_and_local_date(client_factory: ClientFactory) -> None:
    booking = create_booking(client_factory("owner"), starts_at="2030-07-01T00:30:00")
    assert booking["starts_at"] == "2030-06-30T23:30:00Z"
    assert booking["timezone"] == "Europe/London"
    assert booking["local_date"] == "2030-07-01"
    assert len(client_factory(None).get("/api/bookings?date=2030-07-01").json()) == 1


def test_browser_timezone_sets_instant_and_local_date(client_factory: ClientFactory) -> None:
    booking = create_booking(
        client_factory("owner"), starts_at="2030-07-01T20:30:00", timezone="America/Chicago"
    )
    assert booking["starts_at"] == "2030-07-02T01:30:00Z"
    assert booking["timezone"] == "America/Chicago"
    assert booking["local_date"] == "2030-07-01"


@pytest.mark.parametrize("timezone", ["Mars/Olympus", "../etc/passwd", "", None])
def test_invalid_timezone_rejected(client_factory: ClientFactory, timezone: str | None) -> None:
    response = client_factory("owner").post(
        "/api/bookings", json=booking_payload(timezone=timezone)
    )
    assert response.status_code == 400


def test_edit_moves_booking_to_new_timezone(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, starts_at="2030-07-01T00:30:00")
    response = owner.patch(f"/api/bookings/{booking['id']}", json={"timezone": "America/Chicago"})
    assert response.status_code == 200, response.text
    # The stored instant is unchanged; only the booking's local date zone moves.
    assert response.json()["starts_at"] == "2030-06-30T23:30:00Z"
    assert response.json()["local_date"] == "2030-06-30"


def test_legacy_app_timezone_migrated_to_bookings(settings: Settings) -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations = ROOT / "server" / "migrations"
    with closing_connection(settings) as connection:
        connection.execute("CREATE TABLE migrations (name TEXT PRIMARY KEY)")
        for name in ("0001_init.sql", "0002_indexes.sql"):
            connection.executescript((migrations / name).read_text())
            connection.execute("INSERT INTO migrations VALUES (?)", (name,))
        connection.execute("INSERT INTO app_settings VALUES ('timezone', 'Asia/Tokyo')")
        connection.execute("INSERT INTO restaurants (id, name) VALUES (1, 'Legacy')")
        connection.execute(
            """INSERT INTO bookings (owner_login, owner_name, restaurant_id, starts_at,
            local_date, party_size, seats_available, created_at)
            VALUES ('owner', 'Owner', 1, '2030-01-01T10:00:00+00:00', '2030-01-01', 2, 1,
            '2026-01-01T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO seat_requests
            (booking_id, requester_login, requester_name, seats, status, local_date, created_at)
            VALUES (1, 'guest', 'Unverified guest', 1, 'active', '2030-01-01',
            '2026-01-01T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO seat_requests
            (booking_id, requester_login, requester_name, seats, status, local_date,
             created_at, cancelled_at)
            VALUES (1, 'cancelled', 'Former guest', 1, 'cancelled', '2030-01-01',
            '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"""
        )
        connection.commit()
    initialize(settings)
    with closing_connection(settings) as connection:
        assert connection.execute("SELECT timezone FROM bookings").fetchone()[0] == "Asia/Tokyo"
        assert connection.execute("SELECT name FROM bookings").fetchone()[0] == "Table 1"
        assert connection.execute("SELECT duration_minutes FROM bookings").fetchone()[0] == 120
        assert (
            connection.execute("SELECT type FROM bookables WHERE id=1").fetchone()[0]
            == "restaurant"
        )
        assert connection.execute("SELECT owner_login FROM bookings").fetchone()[0] == "owner"
        assert (
            connection.execute("SELECT requester_login FROM seat_requests").fetchone()[0] == "guest"
        )
        assert "owner_name" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(bookings)")
        }
        assert "requester_name" not in {
            row["name"] for row in connection.execute("PRAGMA table_info(seat_requests)")
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT id, booking_id, status FROM seat_requests ORDER BY id"
            )
        ] == [(1, 1, "active"), (2, 1, "cancelled")]
        assert (
            connection.execute("SELECT 1 FROM sqlite_master WHERE name='app_settings'").fetchone()
            is None
        )


@pytest.mark.parametrize("value", ["2030-03-31T01:30:00", "2030-10-27T01:30:00"])
def test_dst_invalid_or_ambiguous_wall_times(value: str) -> None:
    with pytest.raises(HTTPException) as error:
        booking_time(datetime.fromisoformat(value), "Europe/London")
    assert error.value.status_code == 400


def test_explicit_offset_disambiguates_dst() -> None:
    first, day = booking_time(datetime.fromisoformat("2030-10-27T01:30:00+01:00"), "Europe/London")
    second, _ = booking_time(datetime.fromisoformat("2030-10-27T01:30:00+00:00"), "Europe/London")
    assert first != second
    assert day == "2030-10-27"


def test_database_initialization_idempotent(settings: Settings) -> None:
    initialize(settings)
    initialize(settings)
    with closing_connection(settings) as connection:
        assert connection.execute("SELECT count(*) FROM bookables").fetchone()[0] == 3
        assert connection.execute("SELECT count(*) FROM migrations").fetchone()[0] == 8
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_sports_migration_preserves_existing_data_and_constraints(settings: Settings) -> None:
    index_query = (
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL ORDER BY name"
    )
    with closing_connection(settings) as connection:
        connection.execute("CREATE TABLE migrations (name TEXT PRIMARY KEY)")
        for path in sorted((ROOT / "server/migrations").glob("*.sql")):
            if path.name >= "0008_sports_bookables.sql":
                break
            connection.executescript(path.read_text())
            connection.execute("INSERT INTO migrations VALUES (?)", (path.name,))
        connection.executemany(
            "INSERT INTO bookables (id, type, name, area) VALUES (?, ?, ?, ?)",
            [
                (10, "restaurant", "Existing cafe", "Centre"),
                (11, "excursion", "Existing tour", None),
            ],
        )
        connection.execute(
            """INSERT INTO bookings
            (id, name, owner_login, bookable_id, starts_at, duration_minutes, timezone,
             local_date, party_size, seats_available, description, locked, closed, created_at)
            VALUES (42, 'Existing booking', 'host', 11, '2030-01-01T12:00:00+00:00', 90,
                    'Europe/London', '2030-01-01', 8, 3, '**Saved description**', 1, 1,
                    '2026-01-01T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO seat_requests
            (id, booking_id, requester_login, seats, status, local_date, created_at, cancelled_at)
            VALUES (51, 42, 'guest', 2, 'active', '2030-01-01',
                    '2026-01-01T00:00:00+00:00', NULL),
                   (52, 42, 'former', 1, 'cancelled', '2030-01-01',
                    '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"""
        )
        tables = ("bookables", "bookings", "seat_requests")
        before = {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in tables
        }
        indexes = connection.execute(index_query).fetchall()
        connection.commit()
    initialize(settings)
    initialize(settings)
    with closing_connection(settings) as connection:
        for table, rows in before.items():
            for row in rows:
                saved = connection.execute(
                    f"SELECT * FROM {table} WHERE id=?", (row["id"],)
                ).fetchone()
                assert dict(saved) == row
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(index_query).fetchall() == indexes
        connection.execute("INSERT INTO bookables (type, name) VALUES ('sports', 'Tennis court')")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute("INSERT INTO bookables (type, name) VALUES ('other', 'Invalid')")
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            connection.execute("DELETE FROM bookables WHERE id=11")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                """INSERT INTO seat_requests
                (booking_id, requester_login, seats, status, local_date, created_at)
                VALUES (42, 'guest', 1, 'active', '2030-01-01', '2026-01-02T00:00:00+00:00')"""
            )


def test_transaction_rolls_back_on_error(settings: Settings) -> None:
    initialize(settings)
    with pytest.raises(HTTPException):
        with transaction(settings) as connection:
            connection.execute("INSERT INTO bookables (name) VALUES ('Rolled back')")
            raise HTTPException(409, "Conflict")
    with closing_connection(settings) as connection:
        assert (
            connection.execute("SELECT 1 FROM bookables WHERE name='Rolled back'").fetchone()
            is None
        )


def test_integrity_error_is_conflict_and_rolls_back(settings: Settings) -> None:
    initialize(settings)
    with pytest.raises(HTTPException) as error:
        with transaction(settings) as connection:
            connection.execute("INSERT INTO bookables (name) VALUES ('Rolled back')")
            connection.execute("INSERT INTO bookables (id, name) VALUES (1, 'Duplicate')")
    assert error.value.status_code == 409
    with closing_connection(settings) as connection:
        assert (
            connection.execute("SELECT 1 FROM bookables WHERE name='Rolled back'").fetchone()
            is None
        )


def test_database_contention_is_retryable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize(settings)
    original_connect = db.connect

    def fast_timeout(config: Settings) -> sqlite3.Connection:
        connection = original_connect(config)
        connection.execute("PRAGMA busy_timeout=1")
        return connection

    monkeypatch.setattr(db, "connect", fast_timeout)
    with closing_connection(settings) as locked:
        locked.execute("BEGIN IMMEDIATE")
        with pytest.raises(HTTPException) as error, transaction(settings):
            pytest.fail("A second writer must not acquire the transaction")
        assert error.value.status_code == 503
        assert error.value.headers == {"Retry-After": "1"}


def test_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "GITHUB_CLIENT_ID": "client",
        "GITHUB_CLIENT_SECRET": "secret",
        "SESSION_SECRET": "test-secret-long-enough-for-a-session",
        "BASE_URL": "https://example.test/",
        "APP_ENV": "production",
        "COOKIE_SECURE": "true",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.base_url == "https://example.test"
    assert settings.cookie_secure is True
    monkeypatch.setenv("COOKIE_SECURE", "maybe")
    with pytest.raises(ValueError, match="COOKIE_SECURE"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("secure", "environment", "expected"),
    [
        ("true", "production", "https://host.example.test"),
        ("false", "development", "http://host.example.test:8080"),
    ],
)
def test_base_url_defaults_to_fqdn(
    monkeypatch: pytest.MonkeyPatch, secure: str, environment: str, expected: str
) -> None:
    for name, value in {
        "GITHUB_CLIENT_ID": "client",
        "GITHUB_CLIENT_SECRET": "secret",
        "SESSION_SECRET": "test-secret-long-enough-for-a-session",
        "APP_ENV": environment,
        "COOKIE_SECURE": secure,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.setattr(socket, "getfqdn", lambda: "host.example.test")
    assert Settings.from_env().base_url == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"session_secret": "short"},
        {"environment": "production", "cookie_secure": False},
        {"environment": "production", "base_url": "http://localhost:8080"},
        {"base_url": "https://example.test/path"},
        {"environment": "unknown"},
        {"github_client_secret": ""},
    ],
)
def test_invalid_configuration(settings: Settings, changes: dict[str, object]) -> None:
    values = {name: getattr(settings, name) for name in settings.__dataclass_fields__}
    values.update(changes)
    with pytest.raises(ValueError):
        Settings(**values)


@pytest.mark.parametrize("starts_at", ["2030-07-01T19:20:00", "2030-07-01T19:15:30"])
def test_start_time_must_be_on_quarter_hour(client_factory: ClientFactory, starts_at: str) -> None:
    response = client_factory("owner").post(
        "/api/bookings", json=booking_payload(starts_at=starts_at)
    )
    assert response.status_code == 400
    assert "15-minute" in response.json()["detail"]


def test_legacy_off_quarter_booking_stays_editable(
    client_factory: ClientFactory, settings: Settings
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, starts_at="2030-07-01T19:00:00")
    with closing_connection(settings) as connection, connection:
        connection.execute(
            "UPDATE bookings SET starts_at='2030-07-01T18:10:00Z' WHERE id=?", (booking["id"],)
        )
    url = f"/api/bookings/{booking['id']}"
    response = owner.patch(url, json={"description": "Still here"})
    assert response.status_code == 200, response.text
    assert owner.patch(url, json={"starts_at": "2030-07-01T19:20:00"}).status_code == 400
    assert owner.patch(url, json={"starts_at": "2030-07-01T19:30:00"}).status_code == 200
