import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import ClientFactory, ask, create_booking


def test_create_bookable_and_book_seats(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    response = owner.post("/api/bookables", json={"name": "  New Garden Cafe  "})
    assert response.status_code == 201
    bookable = response.json()
    assert bookable["name"] == "New Garden Cafe"
    assert bookable["area"] is None
    assert owner.get("/api/bookables?q=new garden").json() == [bookable]
    booking = create_booking(owner, bookable_id=bookable["id"])
    assert booking["bookable_name"] == bookable["name"]
    assert client_factory(None).get("/api/bookings").json()[0]["bookable_id"] == bookable["id"]


@pytest.mark.parametrize("name", ["Garden Cafe", "Caf\u00e9"])
def test_bookable_creation_reuses_case_insensitive_name(
    client_factory: ClientFactory, name: str
) -> None:
    owner = client_factory("owner")
    bookable = owner.post("/api/bookables", json={"name": name}).json()
    response = client_factory("another-owner").post(
        "/api/bookables", json={"name": f"  {name.upper()}  "}
    )
    assert response.status_code == 200
    assert response.json() == bookable
    assert (
        len([row for row in owner.get("/api/bookables").json() if row["id"] == bookable["id"]]) == 1
    )


def test_bookable_creation_reuses_seeded_bookable(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    existing = owner.get("/api/bookables").json()[0]
    response = owner.post("/api/bookables", json={"name": existing["name"].lower()})
    assert response.status_code == 200
    assert response.json() == existing


def test_bookable_area_is_saved_and_used_in_bookings(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    response = owner.post(
        "/api/bookables", json={"name": "  Garden Cafe  ", "area": "  Riverside  "}
    )
    assert response.status_code == 201
    bookable = response.json()
    assert bookable["name"] == "Garden Cafe"
    assert bookable["area"] == "Riverside"
    assert bookable in owner.get("/api/bookables?q=riverside").json()
    booking = create_booking(owner, bookable_id=bookable["id"])
    assert booking["area"] == "Riverside"
    reused = owner.post("/api/bookables", json={"name": "GARDEN CAFE", "area": "RIVERSIDE"})
    assert reused.status_code == 200
    assert reused.json() == bookable
    assert owner.post("/api/bookables", json={"name": "Garden Cafe"}).json() == bookable


@pytest.mark.parametrize("existing_area", [None, "Riverside"])
def test_bookable_area_conflict_does_not_overwrite_catalog(
    client_factory: ClientFactory, existing_area: str | None
) -> None:
    owner = client_factory("owner")
    owner.post("/api/bookables", json={"name": "Garden Cafe", "area": existing_area})
    before = owner.get("/api/bookables").json()
    response = owner.post("/api/bookables", json={"name": "garden cafe", "area": "City centre"})
    assert response.status_code == 409
    assert "different area" in response.json()["detail"]
    assert owner.get("/api/bookables").json() == before


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": ""},
        {"name": "  "},
        {"name": "x" * 101},
        {"name": None},
        {"name": 123},
        {"name": "Cafe", "id": 99},
        {"name": "Cafe", "area": ""},
        {"name": "Cafe", "area": "  "},
        {"name": "Cafe", "area": "x" * 101},
        {"name": "Cafe", "area": 123},
    ],
)
def test_invalid_bookable_is_rejected(
    client_factory: ClientFactory, payload: dict[str, object]
) -> None:
    owner = client_factory("owner")
    before = owner.get("/api/bookables").json()
    response = owner.post("/api/bookables", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]
    assert owner.get("/api/bookables").json() == before


def test_bookable_creation_requires_authentication_and_csrf(
    client_factory: ClientFactory,
) -> None:
    assert client_factory(None).post("/api/bookables", json={"name": "Cafe"}).status_code == 401
    owner = client_factory("owner")
    owner.headers.pop("X-CSRF-Token")
    assert owner.post("/api/bookables", json={"name": "Cafe"}).status_code == 403
    owner.headers["X-CSRF-Token"] = "test-csrf-token"
    owner.headers["Origin"] = "https://evil.example"
    assert owner.post("/api/bookables", json={"name": "Cafe"}).status_code == 403


def test_bookable_creation_is_rate_limited(client_factory: ClientFactory, app: FastAPI) -> None:
    app.state.limiter.limit = 1
    owner = client_factory("owner")
    assert owner.post("/api/bookables", json={"name": "First cafe"}).status_code == 201
    assert owner.post("/api/bookables", json={"name": "Second cafe"}).status_code == 429


def test_active_bookables_only_lists_browsable_bookings(client_factory: ClientFactory) -> None:
    anonymous = client_factory(None)
    open_owner = client_factory("open-owner")
    closed_owner = client_factory("closed-owner")
    requested_owner = client_factory("requested-owner")
    guest = client_factory("guest")

    def active_ids(client: TestClient) -> set[int]:
        return {row["id"] for row in client.get("/api/bookables?active=true").json()}

    assert active_ids(anonymous) == set()
    assert len(anonymous.get("/api/bookables").json()) == 3
    create_booking(open_owner, bookable_id=1)
    closed = create_booking(closed_owner, bookable_id=2)
    assert closed_owner.post(f"/api/bookings/{closed['id']}/close").status_code == 200
    requested = create_booking(requested_owner, bookable_id=3)
    assert ask(guest, requested["id"]).status_code == 201
    assert requested_owner.post(f"/api/bookings/{requested['id']}/close").status_code == 200

    assert active_ids(anonymous) == {1}
    assert active_ids(closed_owner) == {1, 2}
    assert active_ids(guest) == {1, 3}
    assert active_ids(requested_owner) == {1, 3}
    assert anonymous.get("/api/bookables?active=true&q=zzz-no-match").json() == []
