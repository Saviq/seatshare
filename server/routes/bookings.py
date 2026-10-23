import sqlite3
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import ValidationError

from server.auth import require_user, settings_for
from server.db import closing_connection, transaction
from server.models import (
    SQLITE_MAX_INT,
    BookableInput,
    BookableOut,
    BookableType,
    BookingInput,
    BookingOut,
    BookingPatch,
    OwnerBookingOut,
    SeatRequestOut,
)
from server.time_utils import booking_time, check_overlap, check_quarter_hour, now

router = APIRouter(prefix="/api")
User = Annotated[str, Depends(require_user)]
ResourceId = Annotated[int, Path(ge=1, le=SQLITE_MAX_INT)]
BOOKINGS = """SELECT b.*, r.name AS bookable_name, r.type AS bookable_type, r.area
              FROM bookings b JOIN bookables r ON r.id=b.bookable_id"""


def get_booking(connection: sqlite3.Connection, booking_id: int) -> sqlite3.Row:
    row = connection.execute(BOOKINGS + " WHERE b.id=?", (booking_id,)).fetchone()
    if not isinstance(row, sqlite3.Row):
        raise HTTPException(404, "Booking not found")
    return row


def check_owner(row: sqlite3.Row, login: str, *, editable: bool = False) -> None:
    if row["owner_login"] != login:
        raise HTTPException(403, "Only the booking owner can do that")
    if editable and row["locked"]:
        raise HTTPException(409, "This booking is locked while it has active spot requests")
    if editable and row["starts_at"] <= now():
        raise HTTPException(409, "This booking has expired and can no longer be changed")


def check_bookable(connection: sqlite3.Connection, bookable_id: int) -> None:
    if not connection.execute("SELECT 1 FROM bookables WHERE id=?", (bookable_id,)).fetchone():
        raise HTTPException(409, "Choose an existing bookable")


@router.get("/bookables", response_model=list[BookableOut])
def bookables(
    request: Request,
    q: str = Query("", max_length=100),
    active: bool = False,
    type: BookableType | None = None,
) -> list[BookableOut]:
    # With active=true, only list bookables that have upcoming bookings visible in
    # Browse: with spare spots, and open or owned or actively requested by the caller.
    login = request.session.get("login")
    with closing_connection(settings_for(request)) as connection:
        rows = connection.execute(
            """SELECT * FROM bookables r
               WHERE instr(lower(r.name || ' ' || coalesce(r.area, '')), lower(?)) > 0
               AND (? IS NULL OR r.type=?)
               AND (? = 0 OR EXISTS (
                   SELECT 1 FROM bookings b WHERE b.bookable_id=r.id AND b.starts_at>?
                   AND b.seats_available>=1 AND (
                       b.closed=0
                       OR b.owner_login=?
                       OR EXISTS (SELECT 1 FROM seat_requests s WHERE s.booking_id=b.id
                                  AND s.requester_login=? AND s.status='active'))))
               ORDER BY r.name""",
            (q, type, type, int(active), now(), login, login),
        )
        return [BookableOut(**dict(row)) for row in rows]


@router.post("/bookables", response_model=BookableOut, status_code=201)
def create_bookable(
    request: Request, payload: BookableInput, login: User, response: Response
) -> BookableOut:
    with transaction(settings_for(request)) as connection:
        for row in connection.execute(
            "SELECT * FROM bookables WHERE type=? ORDER BY id", (payload.type,)
        ):
            if row["name"].casefold() == payload.name.casefold():
                if (
                    payload.area is not None
                    and payload.area.casefold() != (row["area"] or "").casefold()
                ):
                    raise HTTPException(
                        409,
                        "This bookable already exists with a different area; "
                        "choose its existing entry",
                    )
                response.status_code = 200
                return BookableOut(**dict(row))
        cursor = connection.execute(
            "INSERT INTO bookables (type, name, area) VALUES (?, ?, ?)",
            (payload.type, payload.name, payload.area),
        )
        assert cursor.lastrowid is not None
        return BookableOut(
            id=cursor.lastrowid, type=payload.type, name=payload.name, area=payload.area
        )


@router.get("/bookings", response_model=list[BookingOut])
def browse(
    request: Request,
    bookable_id: int | None = Query(None, ge=1, le=SQLITE_MAX_INT),
    bookable_type: BookableType | None = None,
    min_seats: int = Query(1, ge=1, le=SQLITE_MAX_INT),
    date: date | None = None,
) -> list[BookingOut]:
    # Signed-in users also see their own upcoming bookings and those they have
    # actively requested spots on, even when closed or outside the date filter.
    # The bookable, bookable type, and minimum spare spot filters apply to all.
    login = request.session.get("login")
    with closing_connection(settings_for(request)) as connection:
        rows = connection.execute(
            BOOKINGS
            + """ WHERE b.starts_at>? AND (? IS NULL OR b.bookable_id=?)
            AND (? IS NULL OR r.type=?) AND b.seats_available>=? AND (
                (b.closed=0 AND (? IS NULL OR b.local_date=?))
                OR b.owner_login=?
                OR EXISTS (SELECT 1 FROM seat_requests s WHERE s.booking_id=b.id
                           AND s.requester_login=? AND s.status='active'))
            ORDER BY b.starts_at, b.id""",
            (
                now(),
                bookable_id,
                bookable_id,
                bookable_type,
                bookable_type,
                min_seats,
                date.isoformat() if date else None,
                date.isoformat() if date else None,
                login,
                login,
            ),
        )
        return [BookingOut(**dict(row)) for row in rows]


@router.get("/bookings/mine", response_model=list[OwnerBookingOut])
def mine(request: Request, login: User) -> list[OwnerBookingOut]:
    with closing_connection(settings_for(request)) as connection:
        rows = connection.execute(
            BOOKINGS + " WHERE b.owner_login=? ORDER BY b.starts_at DESC", (login,)
        ).fetchall()
        return [
            OwnerBookingOut(
                **dict(row),
                requests=[
                    SeatRequestOut(**dict(seat_request))
                    for seat_request in connection.execute(
                        "SELECT * FROM seat_requests WHERE booking_id=? ORDER BY created_at, id",
                        (row["id"],),
                    )
                ],
            )
            for row in rows
        ]


@router.post("/bookings", response_model=BookingOut, status_code=201)
def create(request: Request, payload: BookingInput, login: User) -> BookingOut:
    settings = settings_for(request)
    with transaction(settings) as connection:
        starts_at, local_date = booking_time(payload.starts_at, payload.timezone)
        check_quarter_hour(starts_at)
        check_bookable(connection, payload.bookable_id)
        check_overlap(connection, login, starts_at, payload.duration_minutes)
        cursor = connection.execute(
            """INSERT INTO bookings
            (name, owner_login, bookable_id, starts_at, timezone, local_date,
             party_size, seats_available, description, duration_minutes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload.name,
                login,
                payload.bookable_id,
                starts_at,
                payload.timezone,
                local_date,
                payload.party_size,
                payload.seats_available,
                payload.description,
                payload.duration_minutes,
                now(),
            ),
        )
        assert cursor.lastrowid is not None
        return BookingOut(**dict(get_booking(connection, cursor.lastrowid)))


@router.patch("/bookings/{booking_id}", response_model=BookingOut)
def edit(request: Request, booking_id: ResourceId, patch: BookingPatch, login: User) -> BookingOut:
    settings = settings_for(request)
    with transaction(settings) as connection:
        row = get_booking(connection, booking_id)
        check_owner(row, login)
        if row["starts_at"] <= now():
            raise HTTPException(409, "This booking has expired and can no longer be changed")
        if row["locked"] and patch.model_fields_set - {"party_size", "seats_available"}:
            raise HTTPException(
                409, "Only booking size and spare spots can be changed while requests are active"
            )
        values = {key: row[key] for key in BookingInput.model_fields}
        values.update(patch.model_dump(exclude_unset=True))
        try:
            payload = BookingInput.model_validate(values)
        except ValidationError as exc:
            raise HTTPException(400, "Available spots must not exceed party size") from exc
        requested = connection.execute(
            """SELECT coalesce(sum(seats), 0) FROM seat_requests
               WHERE booking_id=? AND status='active'""",
            (booking_id,),
        ).fetchone()[0]
        if requested:
            if payload.party_size < requested + 1:
                raise HTTPException(
                    409,
                    f"Booking size must be at least {requested + 1}: "
                    f"one host spot plus {requested} requested spot(s)",
                )
            if payload.seats_available > payload.party_size - requested - 1:
                raise HTTPException(
                    409, "Spare spots must leave room for one host spot and all active requests"
                )
        starts_at, local_date = booking_time(payload.starts_at, payload.timezone)
        # Bookings created before the 15-minute rule stay editable if their time is unchanged.
        if datetime.fromisoformat(starts_at) != datetime.fromisoformat(row["starts_at"]):
            check_quarter_hour(starts_at)
        check_bookable(connection, payload.bookable_id)
        check_overlap(connection, login, starts_at, payload.duration_minutes, booking_id)
        cursor = connection.execute(
            """UPDATE bookings SET name=?, bookable_id=?, starts_at=?, timezone=?,
            local_date=?, party_size=?, seats_available=?, description=?, duration_minutes=?
            WHERE id=? AND starts_at>?""",
            (
                payload.name,
                payload.bookable_id,
                starts_at,
                payload.timezone,
                local_date,
                payload.party_size,
                payload.seats_available,
                payload.description,
                payload.duration_minutes,
                booking_id,
                now(),
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(409, "Booking changed; refresh and retry")
        return BookingOut(**dict(get_booking(connection, booking_id)))


@router.delete("/bookings/{booking_id}", status_code=204)
def delete(request: Request, booking_id: ResourceId, login: User) -> None:
    with transaction(settings_for(request)) as connection:
        row = get_booking(connection, booking_id)
        check_owner(row, login, editable=True)
        connection.execute(
            "DELETE FROM seat_requests WHERE booking_id=? AND status='cancelled'",
            (booking_id,),
        )
        if (
            connection.execute(
                "DELETE FROM bookings WHERE id=? AND locked=0 AND starts_at>?",
                (booking_id, now()),
            ).rowcount
            != 1
        ):
            raise HTTPException(409, "Booking changed; refresh and retry")


def set_closed(request: Request, booking_id: int, login: str, closed: bool) -> BookingOut:
    with transaction(settings_for(request)) as connection:
        row = get_booking(connection, booking_id)
        check_owner(row, login)
        if row["starts_at"] <= now():
            raise HTTPException(409, "Expired bookings cannot be closed or reopened")
        if not closed and row["seats_available"] == 0:
            raise HTTPException(409, "A booking without spare spots cannot be reopened")
        if (
            connection.execute(
                "UPDATE bookings SET closed=? WHERE id=?", (closed, booking_id)
            ).rowcount
            != 1
        ):
            raise HTTPException(409, "Booking changed; refresh and retry")
        return BookingOut(**dict(get_booking(connection, booking_id)))


@router.post("/bookings/{booking_id}/close", response_model=BookingOut)
def close(request: Request, booking_id: ResourceId, login: User) -> BookingOut:
    return set_closed(request, booking_id, login, True)


@router.post("/bookings/{booking_id}/reopen", response_model=BookingOut)
def reopen(request: Request, booking_id: ResourceId, login: User) -> BookingOut:
    return set_closed(request, booking_id, login, False)
