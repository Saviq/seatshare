from tests.conftest import ClientFactory, ask, create_booking


def test_close_preserves_requests_and_cancel_does_not_reopen(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    other = client_factory("other")
    booking = create_booking(owner)
    path = f"/api/bookings/{booking['id']}"
    response = ask(guest, booking["id"])
    assert owner.post(path + "/close").status_code == 200
    assert owner.post(path + "/close").status_code == 200
    assert client_factory(None).get("/api/bookings").json() == []
    assert [b["id"] for b in owner.get("/api/bookings").json()] == [booking["id"]]
    assert ask(other, booking["id"]).status_code == 409
    assert guest.get("/api/requests/mine").json()[0]["status"] == "active"
    assert guest.delete(f"/api/requests/{response.json()['id']}").status_code == 204
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["closed"] is True
    assert owned["seats_available"] == 2
    assert owner.post(path + "/reopen").json()["closed"] is False
    assert ask(other, booking["id"]).status_code == 201


def test_owner_only_and_cannot_reopen_full_booking(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    guest = client_factory("guest")
    booking = create_booking(owner, seats_available=1)
    path = f"/api/bookings/{booking['id']}"
    assert guest.post(path + "/close").status_code == 403
    assert guest.post(path + "/reopen").status_code == 403
    assert ask(guest, booking["id"]).status_code == 201
    assert owner.post(path + "/close").status_code == 200
    assert owner.post(path + "/reopen").status_code == 409
    assert owner.post("/api/bookings/999/close").status_code == 404
