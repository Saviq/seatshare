CREATE TABLE bookables (
    id INTEGER PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'restaurant' CHECK (type IN ('restaurant', 'excursion')),
    name TEXT NOT NULL,
    area TEXT,
    UNIQUE (type, name)
);
INSERT INTO bookables (id, name, area) SELECT id, name, area FROM restaurants;

CREATE TABLE bookings_new (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner_login TEXT NOT NULL COLLATE NOCASE,
    bookable_id INTEGER NOT NULL REFERENCES bookables(id),
    starts_at TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 120 CHECK (duration_minutes >= 1),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    local_date TEXT NOT NULL,
    party_size INTEGER NOT NULL CHECK (party_size >= 1),
    seats_available INTEGER NOT NULL CHECK (seats_available BETWEEN 0 AND party_size),
    description TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    created_at TEXT NOT NULL
);
INSERT INTO bookings_new
    (id, name, owner_login, bookable_id, starts_at, timezone, local_date,
     party_size, seats_available, description, locked, closed, created_at)
SELECT id, name, owner_login, restaurant_id, starts_at, timezone, local_date,
       party_size, seats_available, description, locked, closed, created_at FROM bookings;

CREATE TABLE seat_requests_new (
    id INTEGER PRIMARY KEY,
    booking_id INTEGER NOT NULL REFERENCES bookings_new(id),
    requester_login TEXT NOT NULL COLLATE NOCASE,
    seats INTEGER NOT NULL CHECK (seats >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    local_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    CHECK ((status = 'active' AND cancelled_at IS NULL)
        OR (status = 'cancelled' AND cancelled_at IS NOT NULL))
);
INSERT INTO seat_requests_new SELECT * FROM seat_requests;
DROP TABLE seat_requests;
DROP TABLE bookings;
DROP TABLE restaurants;
ALTER TABLE bookings_new RENAME TO bookings;
ALTER TABLE seat_requests_new RENAME TO seat_requests;
CREATE INDEX browse_bookings ON bookings(local_date, starts_at) WHERE closed = 0;
CREATE INDEX bookings_by_owner ON bookings(owner_login, starts_at);
CREATE INDEX requests_by_booking ON seat_requests(booking_id);
CREATE INDEX requests_by_user ON seat_requests(requester_login) WHERE status = 'active';
CREATE UNIQUE INDEX one_active_request_per_booking
    ON seat_requests(requester_login, booking_id) WHERE status = 'active';
