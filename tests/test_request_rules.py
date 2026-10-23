from datetime import UTC, datetime, timedelta

import pytest

from server.config import Settings
from server.db import closing_connection, initialize
from tests.conftest import ClientFactory, ask, booking_payload, create_booking


def test_request_cancel_unlocks_and_new_request_relocks(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    booking = create_booking(owner)
    path = f"/api/bookings/{booking['id']}"
    response = ask(guest, booking["id"], 2)
    assert response.status_code == 201
    request_id = response.json()["id"]
    assert client_factory(None).get("/api/bookings").json() == []
    # Full bookings are filtered out of Browse even for their owner.
    assert owner.get("/api/bookings").json() == []
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["locked"] is True
    assert owned["seats_available"] == 0
    assert owned["requests"][0]["requester_login"] == "guest"
    assert "requester_name" not in owned["requests"][0]
    assert guest.get("/api/requests/mine").json()[0]["status"] == "active"
    assert owner.patch(path, json={"description": "No"}).status_code == 409
    assert owner.delete(path).status_code == 409
    assert guest.delete(f"/api/requests/{request_id}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["locked"] is False
    assert owned["seats_available"] == 2
    assert owned["requests"][0]["status"] == "cancelled"
    assert owned["requests"][0]["cancelled_at"] is not None
    assert owner.patch(path, json={"description": "Editable again"}).status_code == 200
    assert guest.delete(f"/api/requests/{request_id}").status_code == 409
    assert owner.get("/api/bookings/mine").json()[0]["seats_available"] == 2
    response = ask(guest, booking["id"])
    assert response.status_code == 201
    assert owner.get("/api/bookings/mine").json()[0]["locked"] is True
    assert owner.patch(path, json={"name": "Cannot edit yet"}).status_code == 409
    assert owner.delete(path).status_code == 409
    assert guest.delete(f"/api/requests/{response.json()['id']}").status_code == 204
    assert owner.delete(path).status_code == 204
    assert owner.get("/api/bookings/mine").json() == []
    assert guest.get("/api/requests/mine").json() == []


def test_booking_stays_locked_until_last_active_request_is_cancelled(
    client_factory: ClientFactory,
) -> None:
    owner = client_factory("owner")
    first = client_factory("first")
    second = client_factory("second")
    booking = create_booking(owner)
    path = f"/api/bookings/{booking['id']}"
    first_id = ask(first, booking["id"]).json()["id"]
    second_id = ask(second, booking["id"]).json()["id"]
    assert owner.post(path + "/close").status_code == 200
    assert first.delete(f"/api/requests/{first_id}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["locked"] is True
    assert owned["seats_available"] == 1
    assert owner.patch(path, json={"name": "No"}).status_code == 409
    assert owner.delete(path).status_code == 409
    assert second.delete(f"/api/requests/{second_id}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["locked"] is False
    assert owned["seats_available"] == 2
    assert owned["closed"] is True
    assert owner.patch(path, json={"name": "New table name"}).status_code == 200


def test_migration_unlocks_existing_bookings_without_active_requests(
    client_factory: ClientFactory, settings: Settings
) -> None:
    cancelled = create_booking(client_factory("owner"))
    active = create_booking(client_factory("host"))
    create_booking(client_factory("unrequested"))
    guest = client_factory("guest")
    request_id = ask(guest, cancelled["id"]).json()["id"]
    assert guest.delete(f"/api/requests/{request_id}").status_code == 204
    assert ask(guest, active["id"]).status_code == 201
    with closing_connection(settings) as connection, connection:
        connection.execute("UPDATE bookings SET locked=1")
        connection.execute("DELETE FROM migrations WHERE name='0006_active_request_locks.sql'")
    initialize(settings)
    initialize(settings)
    assert client_factory("owner").get("/api/bookings/mine").json()[0]["locked"] is False
    assert client_factory("host").get("/api/bookings/mine").json()[0]["locked"] is True
    assert client_factory("unrequested").get("/api/bookings/mine").json()[0]["locked"] is False
    statuses = {
        request["booking_id"]: request["status"]
        for request in guest.get("/api/requests/mine").json()
    }
    assert statuses == {cancelled["id"]: "cancelled", active["id"]: "active"}


def test_active_request_overlap_and_cross_role_constraints(client_factory: ClientFactory) -> None:
    first_owner = client_factory("owner1")
    second_owner = client_factory("owner2")
    guest = client_factory("guest")
    first = create_booking(first_owner)
    second = create_booking(second_owner)
    response = ask(guest, first["id"])
    assert response.status_code == 201
    assert ask(guest, first["id"]).status_code == 409
    assert ask(guest, second["id"]).status_code == 409
    assert guest.post("/api/bookings", json=booking_payload()).status_code == 409
    assert ask(first_owner, second["id"]).status_code == 409
    assert ask(first_owner, first["id"]).status_code == 409
    assert guest.delete(f"/api/requests/{response.json()['id']}").status_code == 204
    assert guest.post("/api/bookings", json=booking_payload()).status_code == 201


def test_editing_into_requested_time_is_forbidden(client_factory: ClientFactory) -> None:
    host = client_factory("host")
    guest = client_factory("guest")
    booking = create_booking(host)
    tomorrow = datetime.fromisoformat(booking_payload()["starts_at"]) + timedelta(days=1)
    own = create_booking(guest, starts_at=tomorrow.isoformat())
    assert ask(guest, booking["id"]).status_code == 201
    response = guest.patch(f"/api/bookings/{own['id']}", json={"starts_at": booking["starts_at"]})
    assert response.status_code == 409


def test_capacity_failure_rolls_back(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    booking = create_booking(owner)
    assert ask(guest, booking["id"], 3).status_code == 409
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["locked"] is False
    assert owned["seats_available"] == 2
    assert owned["requests"] == []
    assert guest.get("/api/requests/mine").json() == []


@pytest.mark.parametrize("field", ["requester_name", "requester_login"])
def test_request_identity_cannot_be_supplied(client_factory: ClientFactory, field: str) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    booking = create_booking(owner)
    url = f"/api/bookings/{booking['id']}/requests"
    assert guest.post(url, json={"seats": 1, field: "someone-else"}).status_code == 400
    assert owner.get("/api/bookings/mine").json()[0]["seats_available"] == 2
    response = ask(guest, booking["id"])
    assert response.status_code == 201
    assert response.json()["requester_login"] == "guest"
    assert "requester_name" not in response.json()
    mine = guest.get("/api/requests/mine").json()[0]
    assert mine["requester_login"] == "guest"
    assert mine["owner_login"] == "owner"
    assert (mine["party_size"], mine["seats_available"]) == (4, 1)
    assert "area" in mine and "description" in mine
    assert "requester_name" not in mine
    assert "owner_name" not in mine


@pytest.mark.parametrize("seats", [0, -1, 2**63, True, 1.5, "1"])
def test_invalid_seat_counts(client_factory: ClientFactory, seats: object) -> None:
    booking = create_booking(client_factory("owner"))
    response = client_factory("guest").post(
        f"/api/bookings/{booking['id']}/requests",
        json={"seats": seats},
    )
    assert response.status_code == 400


def test_privacy_and_cancellation_permissions(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    other = client_factory("other")
    booking = create_booking(owner)
    response = guest.post(
        f"/api/bookings/{booking['id']}/requests",
        json={"seats": 1},
    )
    request_id = response.json()["id"]
    public = client_factory(None).get("/api/bookings").json()[0]
    assert "requests" not in public
    assert "requester_login" not in public
    assert "guest" not in str(public)
    assert other.get("/api/bookings/mine").json() == []
    assert other.get("/api/requests/mine").json() == []
    assert owner.delete(f"/api/requests/{request_id}").status_code == 403
    assert other.delete(f"/api/requests/{request_id}").status_code == 403
    assert guest.delete("/api/requests/999").status_code == 404
    assert ask(guest, 999).status_code == 404


def test_past_booking_operations(client_factory: ClientFactory, settings: Settings) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    booking = create_booking(owner)
    response = ask(guest, booking["id"])
    with closing_connection(settings) as connection, connection:
        connection.execute(
            "UPDATE bookings SET starts_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=1)).isoformat(), booking["id"]),
        )
    assert owner.get("/api/bookings").json() == []
    assert ask(client_factory("other"), booking["id"]).status_code == 409
    assert guest.delete(f"/api/requests/{response.json()['id']}").status_code == 409
    assert owner.post(f"/api/bookings/{booking['id']}/close").status_code == 409
    assert owner.post(f"/api/bookings/{booking['id']}/reopen").status_code == 409


def test_bookings_expire_automatically(client_factory: ClientFactory, settings: Settings) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    unrequested = create_booking(owner)
    tomorrow = datetime.fromisoformat(booking_payload()["starts_at"]) + timedelta(days=1)
    requested = create_booking(owner, starts_at=tomorrow.isoformat())
    assert ask(guest, requested["id"]).status_code == 201
    assert unrequested["expired"] is False
    assert guest.get("/api/requests/mine").json()[0]["expired"] is False
    with closing_connection(settings) as connection, connection:
        connection.execute(
            "UPDATE bookings SET starts_at=?",
            ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(),),
        )
    assert client_factory(None).get("/api/bookings").json() == []
    assert all(booking["expired"] for booking in owner.get("/api/bookings/mine").json())
    mine = guest.get("/api/requests/mine").json()[0]
    assert mine["expired"] is True
    assert mine["status"] == "active"
    path = f"/api/bookings/{unrequested['id']}"
    future = (datetime.now(UTC) + timedelta(days=5)).replace(second=0, microsecond=0)
    response = owner.patch(path, json={"starts_at": future.isoformat()})
    assert response.status_code == 409
    assert "expired" in response.json()["detail"]
    assert owner.patch(path, json={"description": "Too late"}).status_code == 409
    assert owner.delete(path).status_code == 409
    assert len(owner.get("/api/bookings/mine").json()) == 2


def test_browse_includes_own_bookings_and_active_requests_matching_filters(
    client_factory: ClientFactory,
) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    anonymous = client_factory(None)
    owned = create_booking(owner, bookable_id=2)
    tomorrow = datetime.fromisoformat(booking_payload()["starts_at"]) + timedelta(days=1)
    requested = create_booking(client_factory("host"), starts_at=tomorrow.isoformat())
    assert ask(guest, requested["id"]).status_code == 201
    assert owner.post(f"/api/bookings/{owned['id']}/close").status_code == 200
    # Closed bookings stay visible to their owner, but every booking needs enough spare spots.
    assert [b["id"] for b in anonymous.get("/api/bookings").json()] == [requested["id"]]
    assert [b["id"] for b in owner.get("/api/bookings").json()] == [owned["id"], requested["id"]]
    assert [b["id"] for b in guest.get("/api/bookings").json()] == [requested["id"]]
    assert owner.get("/api/bookings?min_seats=2").json()[0]["id"] == owned["id"]
    assert len(owner.get("/api/bookings?min_seats=2").json()) == 1
    assert owner.get("/api/bookings?min_seats=3").json() == []
    assert guest.get("/api/bookings?min_seats=2").json() == []
    # The bookable filter applies to the user's own and requested tables too.
    assert owner.get("/api/bookings?bookable_id=3").json() == []
    assert guest.get("/api/bookings?bookable_id=3").json() == []
    assert [b["id"] for b in owner.get("/api/bookings?bookable_id=2").json()] == [owned["id"]]
    assert [b["id"] for b in guest.get("/api/bookings?bookable_id=1").json()] == [requested["id"]]
    assert [b["id"] for b in guest.get("/api/bookables?active=true").json()] == [1]
    # Full bookings are hidden from Browse and the bookable list, even when owned or requested.
    assert ask(client_factory("other"), requested["id"]).status_code == 201
    host = client_factory("host")
    for client in [host, guest]:
        assert client.get("/api/bookings?bookable_id=1").json() == []
        assert client.get("/api/bookables?active=true").json() == []
    request_id = guest.get("/api/requests/mine").json()[0]["id"]
    assert guest.delete(f"/api/requests/{request_id}").status_code == 204
    assert [b["id"] for b in guest.get("/api/bookings").json()] == [requested["id"]]
