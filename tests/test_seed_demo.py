from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from server.config import Settings
from server.db import closing_connection
from server.seed_demo import main, seed_demo
from tests.conftest import ClientFactory, ask, create_booking


def test_demo_seeding_populates_empty_catalog(settings: Settings) -> None:
    assert seed_demo(settings) == (32, 0)
    assert seed_demo(settings) == (0, 32)
    with closing_connection(settings) as connection:
        assert (
            connection.execute("SELECT count(*) FROM bookables WHERE type='restaurant'").fetchone()[
                0
            ]
            == 3
        )
        assert connection.execute("SELECT count(*) FROM bookings").fetchone()[0] == 32


@pytest.mark.parametrize("timezone", ["UTC", "Europe/London", "Asia/Kathmandu"])
def test_demo_bookings_are_varied_and_browsable(
    settings: Settings, client_factory: ClientFactory, timezone: str
) -> None:
    assert seed_demo(settings, timezone) == (32, 0)
    everything = client_factory(None).get("/api/bookings").json()
    bookings = [b for b in everything if b["bookable_type"] == "restaurant"]
    excursions = [b for b in everything if b["bookable_type"] == "excursion"]
    assert len(bookings) == 24
    assert len({booking["owner_login"] for booking in bookings}) == 6
    assert len({booking["bookable_id"] for booking in bookings}) == 3
    assert {booking["duration_minutes"] for booking in bookings} == {120}
    today = datetime.now(ZoneInfo(timezone)).date()
    assert {booking["local_date"] for booking in bookings} == {
        (today + timedelta(days=days)).isoformat() for days in (1, 2, 3, 7)
    }
    assert len(excursions) == 8
    assert {b["bookable_name"] for b in excursions} == {
        "Harbour boat tour",
        "Old town walking tour",
    }
    assert {b["local_date"] for b in excursions} == {b["local_date"] for b in bookings}
    assert {
        (
            datetime.fromisoformat(b["starts_at"]).astimezone(ZoneInfo(timezone)).hour,
            b["duration_minutes"],
            b["party_size"],
            b["seats_available"],
        )
        for b in excursions
    } == {(8, 180, 20, 8), (14, 90, 10, 4)}
    assert all(not b["closed"] and b["timezone"] == timezone for b in excursions)
    wall_times = set()
    for booking in bookings:
        starts_at = datetime.fromisoformat(booking["starts_at"])
        local = starts_at.astimezone(ZoneInfo(timezone))
        assert starts_at > datetime.now(UTC)
        assert local.date().isoformat() == booking["local_date"]
        assert local.minute % 15 == 0
        assert local.second == local.microsecond == 0
        assert booking["timezone"] == timezone
        assert 0 < booking["seats_available"] <= booking["party_size"]
        assert not booking["closed"]
        assert not booking["locked"]
        wall_times.add((local.hour, local.minute))
    assert wall_times == {(12, 0), (12, 30), (18, 0), (18, 15), (19, 30), (20, 45)}
    assert {booking["seats_available"] for booking in bookings} == {1, 2, 3, 4, 6, 10}
    assert max(booking["party_size"] for booking in bookings) > 12


def test_demo_seeding_preserves_existing_data_and_requests(
    settings: Settings, client_factory: ClientFactory
) -> None:
    owner = client_factory("real-owner")
    real_booking = create_booking(owner)
    assert seed_demo(settings) == (32, 0)
    demo = next(
        booking
        for booking in owner.get("/api/bookings").json()
        if booking["owner_login"] == "demo-alex"
    )
    response = ask(client_factory("real-guest"), demo["id"])
    assert response.status_code == 201
    with closing_connection(settings) as connection:
        before = [dict(row) for row in connection.execute("SELECT * FROM bookings ORDER BY id")]
    assert seed_demo(settings) == (0, 32)
    with closing_connection(settings) as connection:
        after = [dict(row) for row in connection.execute("SELECT * FROM bookings ORDER BY id")]
        assert after == before
        assert connection.execute("SELECT count(*) FROM seat_requests").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert owner.get("/api/bookings/mine").json()[0]["id"] == real_booking["id"]
    assert client_factory("demo-alex").get("/api/bookings/mine").json()[-1]["locked"]


def test_demo_seeding_refuses_production_before_creating_database(settings: Settings) -> None:
    with pytest.raises(ValueError, match="development or test"):
        seed_demo(replace(settings, environment="production"))
    assert not settings.db_path.exists()


@pytest.mark.parametrize("user_count", [1, 2, 4, 6, 8])
def test_demo_bookings_use_supplied_usernames_repeatably(
    settings: Settings, client_factory: ClientFactory, user_count: int
) -> None:
    usernames = [f"workshop-user-{index}" for index in range(user_count)]
    assert seed_demo(settings, usernames=usernames) == (32, 0)
    before = client_factory(None).get("/api/bookings").json()
    meals = [booking for booking in before if booking["bookable_type"] == "restaurant"]
    assert len(before) == 32
    assert len(meals) == 24
    assert {booking["owner_login"] for booking in before} <= set(usernames)
    assert len({(booking["owner_login"], booking["local_date"]) for booking in meals}) == 24
    assert len({booking["starts_at"][11:16] for booking in meals}) >= 6
    assert seed_demo(settings, usernames=list(reversed(usernames))) == (0, 32)
    assert client_factory(None).get("/api/bookings").json() == before
    if user_count <= 6:
        assert {booking["owner_login"] for booking in before} == set(usernames)


def test_demo_usernames_are_normalized_and_deduplicated(
    settings: Settings, client_factory: ClientFactory
) -> None:
    assert seed_demo(settings, usernames=["Alice", "BOB", "alice"]) == (32, 0)
    for login in ("alice", "bob"):
        mine = client_factory(login).get("/api/bookings/mine").json()
        assert len([booking for booking in mine if booking["bookable_type"] == "restaurant"]) == 12
    assert (
        len(client_factory("alice").get("/api/bookings/mine").json())
        + len(client_factory("bob").get("/api/bookings/mine").json())
        == 32
    )
    assert seed_demo(settings, usernames=["bob", "ALICE"]) == (0, 32)


@pytest.mark.parametrize("username", ["", "@alice", "has spaces", "a" * 40, "é"])
def test_demo_seeding_rejects_invalid_usernames(settings: Settings, username: str) -> None:
    with pytest.raises(ValueError, match="GitHub usernames"):
        seed_demo(settings, usernames=[username])
    assert not settings.db_path.exists()


def test_demo_seeding_skips_overlapping_active_requests(
    settings: Settings, client_factory: ClientFactory
) -> None:
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=13, minute=0, second=0, microsecond=0
    )
    booking = create_booking(
        client_factory("real-host"),
        starts_at=tomorrow.isoformat(),
        timezone="UTC",
        duration_minutes=24 * 60,
    )
    assert ask(client_factory("demo-alex"), booking["id"]).status_code == 201
    assert seed_demo(settings) == (31, 1)
    with closing_connection(settings) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM bookings WHERE owner_login=? AND local_date=?",
                ("demo-alex", booking["local_date"]),
            ).fetchone()
            is None
        )


def test_demo_seeding_rejects_unknown_timezone(settings: Settings) -> None:
    with pytest.raises(ValueError):
        seed_demo(settings, "Not/AZone")
    assert not settings.db_path.exists()


def test_demo_cli_rejects_unknown_timezone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["seed-demo", "--timezone", "Not/AZone"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "--timezone" in capsys.readouterr().err


def test_demo_cli(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GITHUB_CLIENT_ID", settings.github_client_id)
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", settings.github_client_secret)
    monkeypatch.setenv("SESSION_SECRET", settings.session_secret)
    monkeypatch.setenv("BASE_URL", settings.base_url)
    monkeypatch.setenv("DB_PATH", str(settings.db_path))
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setattr("sys.argv", ["seed-demo", "--timezone", "Europe/London", "Alice", "bob"])
    main()
    assert f"32 created, 0 skipped in {settings.db_path}" in capsys.readouterr().out
    main()
    assert "0 created, 32 skipped" in capsys.readouterr().out
    with closing_connection(settings) as connection:
        assert {
            row["owner_login"] for row in connection.execute("SELECT owner_login FROM bookings")
        } == {"alice", "bob"}
