from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

from fastapi.testclient import TestClient

from tests.conftest import ClientFactory, ask, booking_payload, create_booking


def test_last_seat_cannot_be_oversold(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, seats_available=1)
    guests = [client_factory(f"guest-{index}") for index in range(8)]
    barrier = Barrier(len(guests))

    def compete(client: TestClient) -> int:
        barrier.wait()
        return ask(client, booking["id"]).status_code

    with ThreadPoolExecutor(max_workers=len(guests)) as pool:
        statuses = list(pool.map(compete, guests))
    assert statuses.count(201) == 1
    assert statuses.count(409) == len(guests) - 1
    owned = owner.get("/api/bookings/mine").json()[0]
    assert owned["seats_available"] == 0
    assert owned["locked"] is True
    assert len(owned["requests"]) == 1


def test_concurrent_duplicate_cancellations_restore_once(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner, seats_available=1)
    request_id = ask(client_factory("guest"), booking["id"]).json()["id"]
    clients = [client_factory("guest") for _ in range(4)]
    barrier = Barrier(len(clients))

    def compete(client: TestClient) -> int:
        barrier.wait()
        return int(client.delete(f"/api/requests/{request_id}").status_code)

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        statuses = list(pool.map(compete, clients))
    assert statuses.count(204) == 1
    assert statuses.count(409) == 3
    assert owner.get("/api/bookings/mine").json()[0]["seats_available"] == 1


def test_concurrent_owner_request_cross_role(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    posting = client_factory("dual")
    requesting = client_factory("dual")
    barrier = Barrier(2)

    def post() -> int:
        barrier.wait()
        return int(posting.post("/api/bookings", json=booking_payload()).status_code)

    def request() -> int:
        barrier.wait()
        return ask(requesting, booking["id"]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(post), pool.submit(request)
        assert sorted([left.result(), right.result()]) == [201, 409]


def test_concurrent_same_user_requests_on_two_bookings(client_factory: ClientFactory) -> None:
    first = create_booking(client_factory("owner1"))
    second = create_booking(client_factory("owner2"))
    clients = [client_factory("guest"), client_factory("guest")]
    barrier = Barrier(2)

    def compete(index: int) -> int:
        barrier.wait()
        return ask(clients[index], [first, second][index]["id"]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, range(2))) == [201, 409]


def test_locked_resize_and_request_cannot_overcommit_seats(client_factory: ClientFactory) -> None:
    owner = client_factory("owner")
    booking = create_booking(owner)
    assert ask(client_factory("first"), booking["id"]).status_code == 201
    guest = client_factory("second")
    barrier = Barrier(2)

    def resize() -> int:
        barrier.wait()
        return int(
            owner.patch(
                f"/api/bookings/{booking['id']}", json={"party_size": 2, "seats_available": 0}
            ).status_code
        )

    def request() -> int:
        barrier.wait()
        return ask(guest, booking["id"]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        resizing, requesting = pool.submit(resize), pool.submit(request)
        assert (resizing.result(), requesting.result()) in {(200, 409), (409, 201)}
    owned = owner.get("/api/bookings/mine").json()[0]
    requested = sum(row["seats"] for row in owned["requests"] if row["status"] == "active")
    assert owned["party_size"] >= 1 + requested + owned["seats_available"]
    assert owned["locked"] is True


def test_concurrent_nonoverlapping_bookings_on_same_day(client_factory: ClientFactory) -> None:
    clients = [client_factory("owner"), client_factory("owner")]
    start = datetime.fromisoformat(booking_payload()["starts_at"])
    barrier = Barrier(2)

    def post(index: int) -> int:
        barrier.wait()
        return int(
            clients[index]
            .post(
                "/api/bookings",
                json=booking_payload(starts_at=(start + timedelta(hours=2 * index)).isoformat()),
            )
            .status_code
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(post, range(2))) == [201, 201]


def test_duration_extension_and_request_serialize_conflict_checks(
    client_factory: ClientFactory,
) -> None:
    user = client_factory("user")
    first = create_booking(user)
    later = datetime.fromisoformat(first["starts_at"]) + timedelta(hours=2)
    second = create_booking(client_factory("host"), starts_at=later.isoformat())
    requester = client_factory("user")
    barrier = Barrier(2)

    def extend() -> int:
        barrier.wait()
        return int(
            user.patch(f"/api/bookings/{first['id']}", json={"duration_minutes": 180}).status_code
        )

    def request() -> int:
        barrier.wait()
        return ask(requester, second["id"]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        extension, requesting = pool.submit(extend), pool.submit(request)
        assert (extension.result(), requesting.result()) in {(200, 409), (409, 201)}
