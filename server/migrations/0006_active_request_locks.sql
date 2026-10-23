UPDATE bookings
SET locked=EXISTS (
    SELECT 1 FROM seat_requests
    WHERE booking_id=bookings.id AND status='active'
);
