from datetime import UTC, datetime, timedelta

import pytest

from server.config import Settings
from server.db import closing_connection
from tests.conftest import ClientFactory, ask, create_booking


@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("bookable_type", ["restaurant", "excursion"])
def test_locked_booking_can_shrink_to_host_plus_all_requests(
    client_factory: ClientFactory, closed: bool, bookable_type: str
) -> None:
    owner = client_factory("owner")
    first = client_factory("first")
    second = client_factory("second")
    bookable = owner.post(
        "/api/bookables", json={"type": bookable_type, "name": "Resize example"}
    ).json()
    booking = create_booking(owner, bookable_id=bookable["id"], party_size=8, seats_available=6)
    path = f"/api/bookings/{booking['id']}"
    first_request = ask(first, booking["id"], 2).json()
    second_request = ask(second, booking["id"], 1).json()
    if closed:
        assert owner.post(path + "/close").status_code == 200
    response = owner.patch(path, json={"party_size": 4, "seats_available": 0})
    assert response.status_code == 200
    resized = response.json()
    assert resized["party_size"] == 4
    assert resized["seats_available"] == 0
    assert resized["locked"] is True
    assert resized["closed"] is closed
    assert first.get("/api/requests/mine").json()[0]["party_size"] == 4
    assert first.get("/api/requests/mine").json()[0]["seats"] == 2
    assert first.delete(f"/api/requests/{first_request['id']}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["seats_available"] == 2
    assert owned["locked"] is True
    assert second.delete(f"/api/requests/{second_request['id']}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["party_size"] == 4
    assert owned["seats_available"] == 3
    assert owned["locked"] is False


@pytest.mark.parametrize(
    ("patch", "size", "spare"),
    [
        ({"party_size": 10}, 10, 3),
        ({"seats_available": 0}, 8, 0),
        ({"seats_available": 4}, 8, 4),
        ({"party_size": 4, "seats_available": 0}, 4, 0),
    ],
)
def test_locked_booking_allows_size_and_spare_changes(
    client_factory: ClientFactory, patch: dict[str, int], size: int, spare: int
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, party_size=8, seats_available=6)
    assert ask(client_factory("guest"), booking["id"], 3).status_code == 201
    response = owner.patch(f"/api/bookings/{booking['id']}", json=patch)
    assert response.status_code == 200
    assert response.json()["party_size"] == size
    assert response.json()["seats_available"] == spare
    assert response.json()["locked"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"party_size": 3, "seats_available": 0},
        {"party_size": 4, "seats_available": 1},
        {"party_size": 4},
        {"seats_available": 5},
    ],
)
def test_locked_resize_preserves_host_and_requested_seats(
    client_factory: ClientFactory, patch: dict[str, int]
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, party_size=8, seats_available=6)
    assert ask(client_factory("guest"), booking["id"], 3).status_code == 201
    before = owner.get("/api/bookings/mine").json()
    response = owner.patch(f"/api/bookings/{booking['id']}", json=patch)
    assert response.status_code == 409
    assert response.json()["detail"]
    assert owner.get("/api/bookings/mine").json() == before


@pytest.mark.parametrize(
    "protected",
    [
        {"name": "Changed"},
        {"bookable_id": 2},
        {"description": "Changed"},
        {"starts_at": "2030-01-01T19:00:00Z"},
        {"timezone": "UTC"},
    ],
)
def test_locked_resize_does_not_allow_other_changes(
    client_factory: ClientFactory, protected: dict[str, object]
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    assert ask(client_factory("guest"), booking["id"]).status_code == 201
    before = owner.get("/api/bookings/mine").json()
    response = owner.patch(f"/api/bookings/{booking['id']}", json={"party_size": 5, **protected})
    assert response.status_code == 409
    assert owner.get("/api/bookings/mine").json() == before
    assert owner.delete(f"/api/bookings/{booking['id']}").status_code == 409


def test_cancelled_requests_do_not_count_toward_resize_minimum(
    client_factory: ClientFactory,
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, party_size=8, seats_available=6)
    cancelled = client_factory("cancelled")
    request_id = ask(cancelled, booking["id"], 3).json()["id"]
    assert ask(client_factory("active"), booking["id"]).status_code == 201
    assert cancelled.delete(f"/api/requests/{request_id}").status_code == 204
    response = owner.patch(
        f"/api/bookings/{booking['id']}", json={"party_size": 2, "seats_available": 0}
    )
    assert response.status_code == 200
    assert response.json()["locked"] is True


def test_locked_resize_requires_owner_and_future_booking(
    client_factory: ClientFactory, settings: Settings
) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    assert ask(client_factory("guest"), booking["id"]).status_code == 201
    path = f"/api/bookings/{booking['id']}"
    patch = {"party_size": 5, "seats_available": 2}
    assert client_factory("guest").patch(path, json=patch).status_code == 403
    assert client_factory(None).patch(path, json=patch).status_code == 401
    with closing_connection(settings) as connection, connection:
        connection.execute(
            "UPDATE bookings SET starts_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(), booking["id"]),
        )
    response = owner.patch(path, json=patch)
    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
