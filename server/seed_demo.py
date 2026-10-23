import argparse
import re
from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from server.config import Settings
from server.db import initialize, transaction
from server.models import valid_timezone
from server.time_utils import booking_time, check_overlap, now

DEMO_USERS = ("demo-alex", "demo-blair", "demo-casey", "demo-drew", "demo-ellis", "demo-frankie")
DEMO_TABLES = (
    ("Lunch club", 12, 0, 2, 1),
    ("Catch-up lunch", 12, 30, 4, 2),
    ("Early dinner", 18, 0, 6, 3),
    ("Dinner together", 18, 15, 8, 4),
    ("Long table", 19, 30, 12, 6),
    ("Late supper", 20, 45, 16, 10),
)
# Excursions avoid the meal times above, so one user can own a meal and both trips.
DEMO_EXCURSIONS = (
    ("Harbour boat tour", "Waterfront"),
    ("Old town walking tour", "Old town"),
)
DEMO_TRIPS = (
    ("Morning boat trip", 8, 0, 180, 20, 8),
    ("Afternoon walking tour", 14, 30, 90, 10, 4),
)
DEMO_TRIP_DAYS = (1, 2, 3, 7)


def github_username(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", value):
        raise ValueError("Provide GitHub usernames using 1-39 letters, digits, or hyphens (no @)")
    return value.lower()


def seed_demo(
    settings: Settings, timezone: str = "UTC", usernames: list[str] | None = None
) -> tuple[int, int]:
    if settings.environment not in {"development", "test"}:
        raise ValueError("Demo data can only be seeded in development or test environments")
    today = datetime.now(ZoneInfo(valid_timezone(timezone))).date()
    logins = sorted({github_username(login) for login in usernames or DEMO_USERS})
    per_day = min(len(logins), len(DEMO_TABLES))
    initialize(settings)
    created = 0
    skipped = 0
    with transaction(settings) as connection:
        restaurants = connection.execute(
            "SELECT id FROM bookables WHERE type='restaurant' ORDER BY id"
        ).fetchall()

        def add(
            login: str,
            bookable_id: int,
            day: date,
            trip: tuple[str, int, int, int, int, int],
        ) -> bool:
            name, hour, minute, duration, party_size, seats = trip
            starts_at, local_date = booking_time(
                datetime.combine(day, time(hour, minute)), timezone
            )
            # Preserve edited examples on reruns, even if their time has changed: one meal
            # per demo user/date (at any restaurant), and one booking per excursion/user/date.
            if connection.execute(
                """SELECT 1 FROM bookings b JOIN bookables r ON r.id=b.bookable_id
                   WHERE b.owner_login=? AND b.local_date=? AND CASE
                       WHEN r.type='restaurant' THEN (SELECT type FROM bookables WHERE id=?)
                           = 'restaurant'
                       ELSE b.bookable_id=? END""",
                (login, local_date, bookable_id, bookable_id),
            ).fetchone():
                return False
            try:
                check_overlap(connection, login, starts_at, duration)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                return False
            connection.execute(
                """INSERT INTO bookings
                (name, owner_login, bookable_id, starts_at, duration_minutes, timezone,
                 local_date, party_size, seats_available, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    login,
                    bookable_id,
                    starts_at,
                    duration,
                    timezone,
                    local_date,
                    party_size,
                    seats,
                    "Demo booking for exploring Seat Share.",
                    now(),
                ),
            )
            return True

        for day_index in range((24 + per_day - 1) // per_day):
            days_ahead = day_index + 1 if day_index < 3 else day_index + 4
            day = today + timedelta(days=days_ahead)
            # Repeatable randomness keeps reruns stable, independent of username argument order.
            random = Random(f"{day.isoformat()}:{','.join(logins)}")
            owners = random.sample(logins, min(per_day, 24 - day_index * per_day))
            for index, login in enumerate(owners):
                name, hour, minute, party_size, seats = DEMO_TABLES[
                    (day_index * per_day + index) % len(DEMO_TABLES)
                ]
                bookable_id = restaurants[(index + day_index) % len(restaurants)]["id"]
                meal = (name, hour, minute, 120, party_size, seats)
                if add(login, bookable_id, day, meal):
                    created += 1
                else:
                    skipped += 1

        excursions = []
        for name, area in DEMO_EXCURSIONS:
            connection.execute(
                "INSERT OR IGNORE INTO bookables (type, name, area) VALUES ('excursion', ?, ?)",
                (name, area),
            )
            excursions.append(
                connection.execute(
                    "SELECT id FROM bookables WHERE type='excursion' AND name=?", (name,)
                ).fetchone()["id"]
            )
        for days_ahead in DEMO_TRIP_DAYS:
            day = today + timedelta(days=days_ahead)
            random = Random(f"excursions:{day.isoformat()}:{','.join(logins)}")
            for bookable_id, trip in zip(excursions, DEMO_TRIPS, strict=True):
                if add(random.choice(logins), bookable_id, day, trip):
                    created += 1
                else:
                    skipped += 1
    return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Add demo bookings to the development database.")
    parser.add_argument(
        "--timezone",
        default="UTC",
        type=valid_timezone,
        help="IANA time zone for booking dates and times (default: UTC)",
    )
    parser.add_argument(
        "usernames",
        nargs="*",
        type=github_username,
        help="GitHub usernames to assign bookings to (default: six fictional demo users)",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    created, skipped = seed_demo(settings, args.timezone, args.usernames)
    print(f"Demo bookings: {created} created, {skipped} skipped in {settings.db_path}")


if __name__ == "__main__":
    main()
