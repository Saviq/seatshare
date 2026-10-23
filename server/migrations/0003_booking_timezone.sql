ALTER TABLE bookings ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
-- Existing bookings were interpreted in the former app-wide APP_TZ.
UPDATE bookings SET timezone = coalesce(
    (SELECT value FROM app_settings WHERE key = 'timezone'), 'UTC'
);
DROP TABLE app_settings;
