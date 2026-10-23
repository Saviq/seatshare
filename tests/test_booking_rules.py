from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import ClientFactory, ask, booking_payload, create_booking


def test_table_name_persisted_and_locked(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, name="  Window table  ")
    path = f"/api/bookings/{booking['id']}"
    assert booking["name"] == "Window table"
    response = owner.patch(path, json={"name": "Garden table"})
    assert response.status_code == 200
    assert response.json()["name"] == "Garden table"
    assert owner.get("/api/bookings").json()[0]["name"] == "Garden table"
    assert owner.get("/api/bookings/mine").json()[0]["name"] == "Garden table"
    guest = client_factory("guest")
    assert ask(guest, booking["id"]).status_code == 201
    assert guest.get("/api/requests/mine").json()[0]["booking_name"] == "Garden table"
    assert owner.patch(path, json={"name": "Renamed"}).status_code == 409


def test_table_name_required(client_factory: ClientFactory) -> None:
    payload = booking_payload()
    del payload["name"]
    assert client_factory("owner").post("/api/bookings", json=payload).status_code == 400


@pytest.mark.parametrize("field", ["owner_name", "owner_login"])
def test_booking_identity_cannot_be_supplied(client_factory: ClientFactory, field: str) -> None:
    owner = client_factory("owner")
    assert (
        owner.post("/api/bookings", json=booking_payload(**{field: "someone-else"})).status_code
        == 400
    )
    booking = create_booking(owner)
    assert booking["owner_login"] == "owner"
    path = f"/api/bookings/{booking['id']}"
    assert owner.patch(path, json={field: "someone-else"}).status_code == 400
    public = client_factory(None).get("/api/bookings").json()[0]
    assert public["owner_login"] == "owner"
    assert "owner_name" not in public
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["owner_login"] == "owner"
    assert "owner_name" not in owned


def test_crud_and_partial_edit(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    assert booking["locked"] is False
    assert booking["closed"] is False
    assert booking["starts_at"].endswith("Z")
    response = owner.patch(f"/api/bookings/{booking['id']}", json={"description": "Updated"})
    assert response.status_code == 200
    assert response.json()["description"] == "Updated"
    assert response.json()["owner_login"] == "owner"
    assert "owner_name" not in response.json()
    assert len(owner.get("/api/bookings/mine").json()) == 1
    assert len(owner.get("/api/bookings").json()) == 1
    assert owner.delete(f"/api/bookings/{booking['id']}").status_code == 204
    assert owner.get("/api/bookings").json() == []


def test_overlapping_booking_and_edit_conflict(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    first = create_booking(owner)
    assert owner.post("/api/bookings", json=booking_payload()).status_code == 409
    tomorrow = datetime.fromisoformat(booking_payload()["starts_at"]) + timedelta(days=1)
    second = create_booking(owner, starts_at=tomorrow.isoformat())
    response = owner.patch(f"/api/bookings/{second['id']}", json={"starts_at": first["starts_at"]})
    assert response.status_code == 409
    assert len(owner.get("/api/bookings/mine").json()) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"party_size": 0},
        {"party_size": 2**63},
        {"party_size": True},
        {"seats_available": -1},
        {"seats_available": 5},
        {"owner_name": "  "},
        {"owner_name": "x" * 101},
        {"name": "  "},
        {"name": "x" * 101},
        {"name": None},
        {"description": "x" * 2001},
        {"bookable_id": "1"},
        {"starts_at": "not-a-date"},
        {"locked": True},
    ],
)
def test_invalid_create_is_400(client_factory: ClientFactory, changes: dict[str, object]) -> None:
    response = client_factory("owner").post("/api/bookings", json=booking_payload(**changes))
    assert response.status_code == 400
    assert response.json()["detail"]


def test_future_time_and_existing_bookable(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert owner.post("/api/bookings", json=booking_payload(starts_at=past)).status_code == 409
    assert owner.post("/api/bookings", json=booking_payload(bookable_id=999)).status_code == 409


def test_party_size_limit_and_integer_storage_bounds(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, party_size=20, seats_available=19)
    assert booking["party_size"] == 20
    assert booking["seats_available"] == 19
    path = f"/api/bookings/{booking['id']}"
    assert owner.patch(path, json={"party_size": 21}).status_code == 400
    assert owner.patch(path, json={"seats_available": 21}).status_code == 400
    for size, seats in ((21, 1), (200, 150)):
        response = owner.post(
            "/api/bookings", json=booking_payload(party_size=size, seats_available=seats)
        )
        assert response.status_code == 400
    assert owner.get(f"/api/bookings?bookable_id={2**63}").status_code == 400
    assert owner.delete(f"/api/bookings/{2**63}").status_code == 400
    assert owner.post("/api/bookings", json=booking_payload(bookable_id=2**63)).status_code == 400


@pytest.mark.parametrize(
    "patch",
    [
        {"owner_name": None},
        {"name": None},
        {"name": " "},
        {"name": "x" * 101},
        {},
        {"party_size": 1},
        {"locked": False},
    ],
)
def test_invalid_patch(client_factory: ClientFactory, patch: dict[str, object]) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    assert owner.patch(f"/api/bookings/{booking['id']}", json=patch).status_code == 400


def test_authorization_and_missing_objects(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    stranger = client_factory("stranger")
    anonymous = client_factory(None)
    booking = create_booking(owner)
    path = f"/api/bookings/{booking['id']}"
    assert stranger.patch(path, json={"description": "Changed"}).status_code == 403
    assert stranger.delete(path).status_code == 403
    assert anonymous.post("/api/bookings", json=booking_payload()).status_code == 401
    assert anonymous.get("/api/bookings/mine").status_code == 401
    assert anonymous.get("/api/requests/mine").status_code == 401
    assert owner.delete("/api/bookings/999").status_code == 404
    assert owner.patch("/api/bookings/999", json={"description": "x"}).status_code == 404


def test_filters_and_bookable_search(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    public = client_factory(None)
    assert len(public.get("/api/bookings?min_seats=2").json()) == 1
    assert public.get("/api/bookings?min_seats=3").json() == []
    assert public.get("/api/bookings?bookable_id=2").json() == []
    assert len(public.get(f"/api/bookings?date={booking['local_date']}").json()) == 1
    assert public.get("/api/bookings?date=2000-01-01").json() == []
    assert public.get("/api/bookings?date=bad").status_code == 400
    assert public.get("/api/bookings?min_seats=0").status_code == 400
    results = public.get("/api/bookables?q=KITCHEN").json()
    assert len(results) == 1
    assert results[0]["name"] == "Test Kitchen"
    assert public.get("/api/bookables?q=does-not-exist").json() == []
