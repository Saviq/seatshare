ALTER TABLE bookings ADD COLUMN name TEXT NOT NULL DEFAULT 'Table'
    CHECK (length(trim(name)) BETWEEN 1 AND 100);
UPDATE bookings SET name = 'Table ' || id;
