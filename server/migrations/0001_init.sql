CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE restaurants (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    area TEXT
);
CREATE TABLE bookings (
    id INTEGER PRIMARY KEY,
    owner_login TEXT NOT NULL COLLATE NOCASE,
    owner_name TEXT NOT NULL,
    restaurant_id INTEGER NOT NULL REFERENCES restaurants(id),
    starts_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    party_size INTEGER NOT NULL CHECK (party_size >= 1),
    seats_available INTEGER NOT NULL CHECK (seats_available BETWEEN 0 AND party_size),
    description TEXT NOT NULL DEFAULT '',
    locked INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (owner_login, local_date)
);
CREATE TABLE seat_requests (
    id INTEGER PRIMARY KEY,
    booking_id INTEGER NOT NULL REFERENCES bookings(id),
    requester_login TEXT NOT NULL COLLATE NOCASE,
    requester_name TEXT NOT NULL,
    seats INTEGER NOT NULL CHECK (seats >= 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'cancelled')),
    local_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    CHECK ((status = 'active' AND cancelled_at IS NULL)
        OR (status = 'cancelled' AND cancelled_at IS NOT NULL))
);
