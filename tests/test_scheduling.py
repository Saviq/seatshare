from datetime import UTC, datetime, timedelta

import pytest

from server.config import Settings
from server.db import closing_connection
from tests.conftest import ClientFactory, ask, booking_payload, create_booking


@pytest.mark.parametrize("duration", [0, -1, True, 1.5, "120", "", None, 2**63])
def test_invalid_duration(client_factory: ClientFactory, duration: object) -> None:
    host = client_factory("host")
    assert (
        host.post("/api/bookings", json=booking_payload(duration_minutes=duration)).status_code
        == 400
    )
    booking = create_booking(host)
    assert (
        host.patch(
            f"/api/bookings/{booking['id']}", json={"duration_minutes": duration}
        ).status_code
        == 400
    )


def test_duration_outside_date_range_is_rejected(client_factory: ClientFactory) -> None:
    response = client_factory("host").post(
        "/api/bookings", json=booking_payload(duration_minutes=2**63 - 1)
    )
    assert response.status_code == 400
    assert "Duration" in response.json()["detail"]


@pytest.mark.parametrize("first_role", ["owner", "requester"])
@pytest.mark.parametrize("second_role", ["owner", "requester"])
@pytest.mark.parametrize(
    ("offset_minutes", "duration", "expected"),
    [
        (0, 120, 409),
        (60, 15, 409),
        (-60, 90, 409),
        (-60, 240, 409),
        (120, 120, 201),
        (-120, 120, 201),
        (135, 120, 201),
    ],
)
def test_only_overlapping_involvements_conflict(
    client_factory: ClientFactory,
    first_role: str,
    second_role: str,
    offset_minutes: int,
    duration: int,
    expected: int,
) -> None:
    user = client_factory("user")
    first = create_booking(user if first_role == "owner" else client_factory("first-host"))
    if first_role == "requester":
        assert ask(user, first["id"]).status_code == 201
    starts_at = (
        datetime.fromisoformat(first["starts_at"]) + timedelta(minutes=offset_minutes)
    ).isoformat()
    if second_role == "owner":
        response = user.post(
            "/api/bookings",
            json=booking_payload(starts_at=starts_at, duration_minutes=duration),
        )
    else:
        second = create_booking(
            client_factory("second-host"), starts_at=starts_at, duration_minutes=duration
        )
        response = ask(user, second["id"])
    assert response.status_code == expected, response.text


@pytest.mark.parametrize("bookable_type", ["excursion", "sports"])
def test_conflicts_across_bookable_types_and_midnight(
    client_factory: ClientFactory, bookable_type: str
) -> None:
    user = client_factory("user")
    excursion = user.post(
        "/api/bookables", json={"type": bookable_type, "name": "Night activity"}
    ).json()
    start = (datetime.now(UTC) + timedelta(days=30)).replace(
        hour=23, minute=0, second=0, microsecond=0
    )
    restaurant = create_booking(user, starts_at=start.isoformat(), timezone="UTC")
    next_day = (start + timedelta(hours=1)).isoformat()
    conflict = user.post(
        "/api/bookings",
        json=booking_payload(bookable_id=excursion["id"], starts_at=next_day, timezone="UTC"),
    )
    assert conflict.status_code == 409
    excursion_booking = create_booking(
        client_factory("host"), bookable_id=excursion["id"], starts_at=next_day, timezone="UTC"
    )
    assert excursion_booking["local_date"] != restaurant["local_date"]
    assert ask(user, excursion_booking["id"]).status_code == 409
    assert (
        user.post(
            "/api/bookings",
            json=booking_payload(
                bookable_id=excursion["id"],
                starts_at=(start + timedelta(hours=2)).isoformat(),
                timezone="UTC",
            ),
        ).status_code
        == 201
    )


def test_conflicts_use_instants_not_local_dates(client_factory: ClientFactory) -> None:
    user = client_factory("user")
    create_booking(user, starts_at="2030-01-01T23:00:00+00:00", timezone="UTC")
    assert (
        user.post(
            "/api/bookings",
            json=booking_payload(starts_at="2030-01-02T08:00:00+09:00", timezone="Asia/Tokyo"),
        ).status_code
        == 409
    )
    create_booking(user, starts_at="2030-01-02T10:00:00+09:00", timezone="Asia/Tokyo")


def test_duration_is_elapsed_time_across_dst(client_factory: ClientFactory) -> None:
    user = client_factory("user")
    create_booking(user, starts_at="2030-10-27T01:00:00+01:00", timezone="Europe/London")
    assert (
        user.post(
            "/api/bookings",
            json=booking_payload(starts_at="2030-10-27T01:30:00+00:00", timezone="Europe/London"),
        ).status_code
        == 409
    )
    create_booking(user, starts_at="2030-10-27T02:00:00+00:00", timezone="Europe/London")


def test_duration_edit_rechecks_owned_and_requested_conflicts(
    client_factory: ClientFactory,
) -> None:
    user = client_factory("user")
    first = create_booking(user, duration_minutes=60)
    later = (datetime.fromisoformat(first["starts_at"]) + timedelta(hours=2)).isoformat()
    requested = create_booking(client_factory("host"), starts_at=later)
    assert ask(user, requested["id"]).status_code == 201
    path = f"/api/bookings/{first['id']}"
    assert user.patch(path, json={"duration_minutes": 120}).status_code == 200
    assert user.patch(path, json={"duration_minutes": 121}).status_code == 409
    assert user.get("/api/bookings/mine").json()[0]["duration_minutes"] == 120
    request_id = user.get("/api/requests/mine").json()[0]["id"]
    assert user.delete(f"/api/requests/{request_id}").status_code == 204
    assert user.patch(path, json={"duration_minutes": 240}).status_code == 200


def test_started_booking_still_blocks_its_remaining_duration(
    client_factory: ClientFactory, settings: Settings
) -> None:
    user = client_factory("user")
    existing = create_booking(user)
    past = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=30)
    future = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    with closing_connection(settings) as connection, connection:
        connection.execute(
            "UPDATE bookings SET starts_at=?, local_date=? WHERE id=?",
            (past.isoformat(), past.date().isoformat(), existing["id"]),
        )
    response = user.post("/api/bookings", json=booking_payload(starts_at=future.isoformat()))
    assert response.status_code == 409
