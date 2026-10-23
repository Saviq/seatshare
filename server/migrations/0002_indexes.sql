CREATE UNIQUE INDEX one_active_request_per_day
    ON seat_requests(requester_login, local_date) WHERE status = 'active';
CREATE INDEX browse_bookings ON bookings(local_date, starts_at) WHERE closed = 0;
CREATE INDEX requests_by_booking ON seat_requests(booking_id);
