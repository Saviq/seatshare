from datetime import UTC, date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SQLITE_MAX_INT = 2**63 - 1
SeatCount = Annotated[int, Field(strict=True, ge=1, le=SQLITE_MAX_INT)]
Duration = Annotated[int, Field(strict=True, ge=1, le=SQLITE_MAX_INT)]
MAX_PARTY_SIZE = 20
PartySize = Annotated[int, Field(strict=True, ge=1, le=MAX_PARTY_SIZE)]
SpareSeats = Annotated[int, Field(strict=True, ge=0, le=MAX_PARTY_SIZE)]
BookableType = Literal["restaurant", "excursion", "sports"]


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("Unknown IANA time zone") from exc
    return value


TimeZone = Annotated[
    str, StringConstraints(min_length=1, max_length=64), AfterValidator(valid_timezone)
]


class BookingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    bookable_id: SeatCount
    starts_at: datetime
    duration_minutes: Duration = 120
    timezone: TimeZone
    party_size: PartySize
    seats_available: SpareSeats
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""

    @model_validator(mode="after")
    def valid_seats(self) -> "BookingInput":
        if self.seats_available > self.party_size:
            raise ValueError("Available spots cannot exceed party size")
        return self


class BookingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name | None = None
    bookable_id: SeatCount | None = None
    starts_at: datetime | None = None
    duration_minutes: Duration | None = None
    timezone: TimeZone | None = None
    party_size: PartySize | None = None
    seats_available: SpareSeats | None = None
    description: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] | None
    ) = None

    @model_validator(mode="after")
    def no_nulls(self) -> "BookingPatch":
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Booking fields cannot be null")
        if not self.model_fields_set:
            raise ValueError("Provide at least one field to update")
        return self


class RequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seats: SeatCount


class BookingOut(BaseModel):
    id: int
    name: str
    owner_login: str
    bookable_id: int
    bookable_name: str
    bookable_type: BookableType
    area: str | None
    starts_at: datetime
    duration_minutes: int
    timezone: str
    local_date: date
    party_size: int
    seats_available: int
    description: str
    locked: bool
    closed: bool
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expired(self) -> bool:
        return self.starts_at <= datetime.now(UTC)


class SeatRequestOut(BaseModel):
    id: int
    booking_id: int
    requester_login: str
    seats: int
    status: Literal["active", "cancelled"]
    local_date: date
    created_at: datetime
    cancelled_at: datetime | None


class OwnerBookingOut(BookingOut):
    requests: list[SeatRequestOut]


class MyRequestOut(SeatRequestOut):
    booking_name: str
    bookable_id: int
    bookable_name: str
    bookable_type: BookableType
    area: str | None
    party_size: int
    seats_available: int
    description: str
    starts_at: datetime
    duration_minutes: int
    owner_login: str
    closed: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def expired(self) -> bool:
        return self.starts_at <= datetime.now(UTC)


class BookableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: BookableType = "restaurant"
    name: Name
    area: Name | None = None


class BookableOut(BaseModel):
    id: int
    type: BookableType
    name: str
    area: str | None
