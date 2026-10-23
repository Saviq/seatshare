import pytest

from tests.conftest import ClientFactory, ask, create_booking


@pytest.mark.parametrize("bookable_type", ["restaurant", "excursion", "sports"])
def test_booking_display_data_matches_across_pages(
    client_factory: ClientFactory, bookable_type: str
) -> None:
    host = client_factory("host")
    guest = client_factory("guest")
    bookable_response = host.post(
        "/api/bookables",
        json={"type": bookable_type, "name": "Shared display", "area": "Waterfront"},
    )
    assert bookable_response.status_code == 201
    booking = create_booking(
        host,
        bookable_id=bookable_response.json()["id"],
        party_size=20,
        seats_available=10,
        duration_minutes=1080,
    )
    request = ask(guest, booking["id"], 3)
    assert request.status_code == 201
    path = f"/api/bookings/{booking['id']}"
    fields = (
        "bookable_id",
        "bookable_name",
        "bookable_type",
        "area",
        "starts_at",
        "duration_minutes",
        "party_size",
        "seats_available",
        "description",
        "owner_login",
        "closed",
        "expired",
    )

    def assert_matching_display() -> None:
        owned = host.get("/api/bookings/mine").json()[0]
        requested = guest.get("/api/requests/mine").json()[0]
        assert requested["booking_name"] == owned["name"]
        assert {key: requested[key] for key in fields} == {key: owned[key] for key in fields}
        if requested["status"] == "active" and owned["seats_available"] > 0:
            browsed = guest.get("/api/bookings").json()[0]
            assert {key: browsed[key] for key in fields} == {key: owned[key] for key in fields}

    assert_matching_display()
    assert host.post(path + "/close").status_code == 200
    assert guest.get("/api/requests/mine").json()[0]["closed"] is True
    assert_matching_display()
    assert host.patch(path, json={"party_size": 4, "seats_available": 0}).status_code == 200
    assert_matching_display()
    assert guest.delete(f"/api/requests/{request.json()['id']}").status_code == 204
    assert_matching_display()
    assert host.post(path + "/reopen").status_code == 200
    assert guest.get("/api/requests/mine").json()[0]["closed"] is False
    assert_matching_display()


@pytest.mark.parametrize("bookable_type", ["excursion", "sports"])
def test_activity_catalog_booking_and_request(
    client_factory: ClientFactory, bookable_type: str
) -> None:
    host = client_factory("host")
    response = host.post(
        "/api/bookables",
        json={"type": bookable_type, "name": "Shared activity", "area": "Old town"},
    )
    assert response.status_code == 201
    excursion = response.json()
    assert excursion["type"] == bookable_type
    booking = create_booking(host, bookable_id=excursion["id"], duration_minutes=90)
    assert booking["bookable_type"] == bookable_type
    assert booking["bookable_name"] == "Shared activity"
    assert booking["duration_minutes"] == 90
    guest = client_factory("guest")
    assert ask(guest, booking["id"]).status_code == 201
    for row in [
        guest.get("/api/requests/mine").json()[0],
        host.get("/api/bookings/mine").json()[0],
        client_factory(None).get("/api/bookings").json()[0],
    ]:
        assert row["bookable_id"] == excursion["id"]
        assert row["bookable_type"] == bookable_type
        assert row["bookable_name"] == "Shared activity"
        assert row["duration_minutes"] == 90
    assert host.get(f"/api/bookables?type={bookable_type}").json() == [excursion]
    assert excursion not in host.get("/api/bookables?type=restaurant").json()
    assert host.get("/api/bookables?active=true").json() == [excursion]


def test_bookable_names_are_scoped_to_type(client_factory: ClientFactory) -> None:
    host = client_factory("host")
    restaurant = host.post("/api/bookables", json={"name": "Garden", "type": "restaurant"})
    excursion = host.post("/api/bookables", json={"name": "Garden", "type": "excursion"})
    sports = host.post("/api/bookables", json={"name": "Garden", "type": "sports"})
    assert restaurant.status_code == excursion.status_code == sports.status_code == 201
    assert len({response.json()["id"] for response in [restaurant, excursion, sports]}) == 3
    reused = host.post("/api/bookables", json={"name": "GARDEN", "type": "excursion"})
    assert reused.status_code == 200
    assert reused.json() == excursion.json()
    conflict = host.post(
        "/api/bookables", json={"name": "Garden", "type": "excursion", "area": "Elsewhere"}
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize("type", ["hotel", "", None, 1])
def test_invalid_bookable_type(client_factory: ClientFactory, type: object) -> None:
    host = client_factory("host")
    assert host.post("/api/bookables", json={"name": "Invalid", "type": type}).status_code == 400
    if isinstance(type, str):
        assert host.get("/api/bookables", params={"type": type}).status_code == 400


@pytest.mark.parametrize("bookable_type", ["excursion", "sports"])
def test_edit_bookable_and_duration(client_factory: ClientFactory, bookable_type: str) -> None:
    host = client_factory("host")
    booking = create_booking(host)
    assert booking["duration_minutes"] == 120
    excursion = host.post("/api/bookables", json={"name": "Activity", "type": bookable_type}).json()
    path = f"/api/bookings/{booking['id']}"
    changed = host.patch(path, json={"bookable_id": excursion["id"], "duration_minutes": 45})
    assert changed.status_code == 200
    assert changed.json()["bookable_type"] == bookable_type
    assert changed.json()["duration_minutes"] == 45
    assert ask(client_factory("guest"), booking["id"]).status_code == 201
    for patch in [{"duration_minutes": 60}, {"bookable_id": 1}]:
        assert host.patch(path, json=patch).status_code == 409


@pytest.mark.parametrize("bookable_type", ["excursion", "sports"])
def test_bookable_filter_includes_own_and_requested_activities(
    client_factory: ClientFactory, bookable_type: str
) -> None:
    host = client_factory("host")
    excursion = host.post("/api/bookables", json={"name": "Activity", "type": bookable_type}).json()
    booking = create_booking(host, bookable_id=excursion["id"])
    guest = client_factory("guest")
    assert ask(guest, booking["id"]).status_code == 201
    assert host.post(f"/api/bookings/{booking['id']}/close").status_code == 200
    for client in [host, guest]:
        assert client.get(f"/api/bookables?active=true&type={bookable_type}").json() == [excursion]
        assert (
            client.get(f"/api/bookings?bookable_type={bookable_type}").json()[0]["id"]
            == booking["id"]
        )
        assert client.get("/api/bookings?bookable_id=1").json() == []
        assert (
            client.get(f"/api/bookings?bookable_id={excursion['id']}").json()[0]["id"]
            == booking["id"]
        )
    assert client_factory(None).get("/api/bookables?active=true").json() == []


def test_bookable_type_filter(client_factory: ClientFactory) -> None:
    host = client_factory("host")
    excursion = host.post("/api/bookables", json={"name": "Walk", "type": "excursion"}).json()
    trip = create_booking(host, bookable_id=excursion["id"])
    meal = create_booking(client_factory("diner"), bookable_id=1)
    sports = host.post("/api/bookables", json={"name": "Tennis", "type": "sports"}).json()
    game = create_booking(client_factory("player"), bookable_id=sports["id"])
    public = client_factory(None)

    def ids(query: str) -> list[int]:
        return [booking["id"] for booking in public.get(f"/api/bookings?{query}").json()]

    assert ids("bookable_type=excursion") == [trip["id"]]
    assert ids("bookable_type=restaurant") == [meal["id"]]
    assert ids("bookable_type=sports") == [game["id"]]
    assert ids(f"bookable_type=restaurant&bookable_id={excursion['id']}") == []
    assert public.get("/api/bookings?bookable_type=boat").status_code == 400
