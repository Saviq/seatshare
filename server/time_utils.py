import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException


def now() -> str:
    return datetime.now(UTC).isoformat()


def booking_end(starts_at: str, duration_minutes: int) -> datetime:
    try:
        return datetime.fromisoformat(starts_at) + timedelta(minutes=duration_minutes)
    except OverflowError as exc:
        raise HTTPException(400, "Duration extends beyond the supported date range") from exc


def check_overlap(
    connection: sqlite3.Connection,
    login: str,
    starts_at: str,
    duration_minutes: int,
    exclude_id: int = -1,
) -> None:
    start = datetime.fromisoformat(starts_at)
    end = booking_end(starts_at, duration_minutes)
    # Callers hold BEGIN IMMEDIATE, serializing conflict checks with mutations.
    rows = connection.execute(
        """SELECT b.starts_at, b.duration_minutes FROM bookings b
           WHERE b.id != ? AND (
               b.owner_login=? OR EXISTS (
                   SELECT 1 FROM seat_requests s WHERE s.booking_id=b.id
                   AND s.requester_login=? AND s.status='active'))""",
        (exclude_id, login, login),
    )
    if any(
        start < booking_end(row["starts_at"], row["duration_minutes"])
        and datetime.fromisoformat(row["starts_at"]) < end
        for row in rows
    ):
        raise HTTPException(409, "This overlaps a booking or active spot request you already have")


def check_quarter_hour(starts_at: str) -> None:
    # Every current IANA zone offset is a multiple of 15 minutes, so UTC minutes suffice.
    value = datetime.fromisoformat(starts_at)
    if value.minute % 15 or value.second or value.microsecond:
        raise HTTPException(400, "Choose a time on a 15-minute boundary (:00, :15, :30, :45)")


def booking_time(value: datetime, timezone: str) -> tuple[str, str]:
    zone = ZoneInfo(timezone)
    if value.tzinfo is None:
        # Reject nonexistent and ambiguous wall times instead of silently choosing a DST offset.
        candidates = {
            candidate.astimezone(UTC)
            for fold in (0, 1)
            if (candidate := value.replace(tzinfo=zone, fold=fold))
            .astimezone(UTC)
            .astimezone(zone)
            .replace(tzinfo=None)
            == value
        }
        if len(candidates) != 1:
            raise HTTPException(
                400, "Time is ambiguous or nonexistent; supply an explicit UTC offset"
            )
        value = candidates.pop()
    value = value.astimezone(UTC)
    if value <= datetime.now(UTC):
        raise HTTPException(409, "Booking time must be in the future")
    return value.isoformat(), value.astimezone(zone).date().isoformat()
