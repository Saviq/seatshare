from fastapi import APIRouter, HTTPException, Request

from server.auth import settings_for
from server.db import closing_connection, transaction
from server.models import MyRequestOut, RequestInput, SeatRequestOut
from server.routes.bookings import ResourceId, User, get_booking
from server.time_utils import check_overlap, now

router = APIRouter(prefix="/api")


@router.post("/bookings/{booking_id}/requests", response_model=SeatRequestOut, status_code=201)
def request_seats(
    request: Request, booking_id: ResourceId, payload: RequestInput, login: User
) -> SeatRequestOut:
    with transaction(settings_for(request)) as connection:
        booking = get_booking(connection, booking_id)
        if booking["owner_login"] == login:
            raise HTTPException(409, "You cannot request spots on your own booking")
        if booking["closed"] or booking["starts_at"] <= now():
            raise HTTPException(409, "Booking is closed or has expired")
        check_overlap(connection, login, booking["starts_at"], booking["duration_minutes"])
        cursor = connection.execute(
            """UPDATE bookings SET seats_available=seats_available-?, locked=1
            WHERE id=? AND seats_available>=? AND closed=0 AND starts_at>?""",
            (payload.seats, booking_id, payload.seats, now()),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "Not enough spots remain; refresh and retry")
        cursor = connection.execute(
            """INSERT INTO seat_requests
            (booking_id, requester_login, seats, status, local_date, created_at)
            VALUES (?, ?, ?, 'active', ?, ?)""",
            (
                booking_id,
                login,
                payload.seats,
                booking["local_date"],
                now(),
            ),
        )
        row = connection.execute(
            "SELECT * FROM seat_requests WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return SeatRequestOut(**dict(row))


@router.delete("/requests/{request_id}", status_code=204)
def cancel(request: Request, request_id: ResourceId, login: User) -> None:
    with transaction(settings_for(request)) as connection:
        row = connection.execute(
            """SELECT s.*, b.starts_at FROM seat_requests s
            JOIN bookings b ON b.id=s.booking_id WHERE s.id=?""",
            (request_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Spot request not found")
        if row["requester_login"] != login:
            raise HTTPException(403, "Only the requester can cancel")
        if row["starts_at"] <= now():
            raise HTTPException(409, "Requests on expired bookings cannot be cancelled")
        if (
            connection.execute(
                """UPDATE seat_requests SET status='cancelled', cancelled_at=?
            WHERE id=? AND status='active'""",
                (now(), request_id),
            ).rowcount
            != 1
        ):
            raise HTTPException(409, "Request has already been cancelled")
        if (
            connection.execute(
                """UPDATE bookings SET seats_available=seats_available+?,
                locked=EXISTS (SELECT 1 FROM seat_requests
                               WHERE booking_id=bookings.id AND status='active')
                WHERE id=?""",
                (row["seats"], row["booking_id"]),
            ).rowcount
            != 1
        ):
            raise HTTPException(409, "Booking changed; refresh and retry")


@router.get("/requests/mine", response_model=list[MyRequestOut])
def mine(request: Request, login: User) -> list[MyRequestOut]:
    with closing_connection(settings_for(request)) as connection:
        rows = connection.execute(
            """SELECT s.*, b.name AS booking_name, r.name AS bookable_name,
            r.type AS bookable_type, b.bookable_id, b.duration_minutes,
            b.starts_at, b.owner_login, r.area, b.party_size, b.seats_available,
            b.description, b.closed
            FROM seat_requests s JOIN bookings b ON b.id=s.booking_id
            JOIN bookables r ON r.id=b.bookable_id
            WHERE s.requester_login=? ORDER BY b.starts_at DESC, s.id DESC""",
            (login,),
        )
        return [MyRequestOut(**dict(row)) for row in rows]
