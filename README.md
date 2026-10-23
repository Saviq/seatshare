# Seat Share

A single-service FastAPI application for sharing spare places on restaurant, excursion, and sports
bookings. GitHub is used only to establish a username; access tokens are discarded.
HTML, JavaScript, and vendored Vanilla Framework CSS are served by the same app.

## Development

Dependencies and Python 3.13 are managed by **uv**, with exact versions in
`uv.lock`. From inside the existing workshop (do not launch a nested workshop):

```sh
uv sync --locked
uv run pre-commit install
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `SESSION_SECRET` in `.env` to that generated value. Register a
[GitHub OAuth app](https://github.com/settings/developers), put its client ID
and secret in `.env`, and configure the callback as
`http://<hostname -f>:8080/auth/callback`, using the URL printed by the `dev`
action. `BASE_URL` is optional and defaults to that origin; set it only if the
browser reaches the app through a different URL.
Keep `COOKIE_SECURE=true` for HTTPS. The example's explicit
`APP_ENV=development` / `COOKIE_SECURE=false` combination is only for local HTTP.
Never commit `.env`.

Run directly **inside** the workshop:

```sh
uv run --locked pytest
uv run --locked pytest tests/test_request_rules.py -q
uv run --locked pre-commit run --all-files
DB_PATH=.local/dev.db uv run --locked --env-file .env python -m server.seed_demo
uv run --locked --env-file .env uvicorn server.main:create_app --factory --host "$(hostname -f)" --port 8080 --reload --no-access-log
```

From the **host outside** the workshop, use the actions in
[.workshop/dev.yaml](.workshop/dev.yaml):

```sh
workshop run dev -- tests
workshop run dev -- tests tests/test_request_rules.py -q
workshop run dev -- seed-demo
workshop run dev -- dev
```

The `dev` action prints its URL, `http://$(hostname -f):8080`, and listens on
that name. It selects `.local/dev.db` regardless of the production database
setting. Stop it with Ctrl+C. Action changes do not require a refresh.
The direct command uses `DB_PATH` from `.env`; leave it as `.local/dev.db`.
Tests use temporary databases and mocked GitHub responses, and require no
credentials. Pre-commit runs Ruff lint/format and mypy through the locked uv
environment; tests and servers deliberately do not run during commits.

The `seed-demo` action populates `.local/dev.db` with 32 open, requestable bookings
from six fictional `demo-*` users, tomorrow, the following two days, and a week
from today: 24 restaurant tables, with times from lunch to late supper in 15-minute
increments and different party sizes and spare-spot counts, plus 8 excursions (a
3-hour morning "Harbour boat tour" and a 90-minute afternoon "Old town walking
tour" on each of those four dates). Excursion times never overlap the demo meals. It uses `.env` like
the dev server and applies database migrations automatically. These are sample
records only: no demo accounts or authentication bypasses are created.

Dates and times default to UTC. To use your browser's time zone, for example:

```sh
# From the host:
workshop run dev -- seed-demo --timezone Europe/London alice bob carol
# From inside the workshop:
DB_PATH=.local/dev.db uv run --locked --env-file .env python -m server.seed_demo --timezone Europe/London alice bob carol
```

Pass space-separated GitHub usernames (without `@`) to assign the examples to
those users instead of fictional ones. Names are normalized to lowercase and
deduplicated. Assignment is randomized repeatably per date and username set, so
rerunning with the same users in any order does not create duplicate bookings.
With fewer than six users, the 24 restaurant examples span more upcoming dates to keep at
most one booking per user/day. With more than six, up to six users are randomly
selected per date. Sign in normally as a supplied username to explore **My
bookings**; the seeder does not authenticate you.

It is safe to rerun: existing restaurant bookings for each demo user/date, and
existing bookings of each demo excursion per user/date, are skipped,
including any edits or spot requests; examples overlapping an active request by
that demo user are skipped too. Other users' data is untouched. Running on
a later date adds missing upcoming examples without removing older ones. The
action always selects the development database, and the seeder refuses
`APP_ENV=production`. Refresh Browse after seeding to see the added bookings.

The CI workflow runs the exact test action's Bash body directly, rather than
provisioning a nested workshop. It also checks pre-commit, builds the image,
and publishes to GHCR on main or version tags. No deployment host is assumed.

## Using the application

The table-and-spots icon links home. Sign-in/out controls sit at the right of the
navigation bar, alongside the booking links.

- **Browse**: filter by bookable and minimum spare spots; the bookable filter is a tree with an entry per type (**Excursions**, **Restaurants**, **Sports**) to show all bookables of that type, followed by its bookables sorted by name; it only lists bookables with upcoming bookings you could see in Browse (with at least one spare spot, and open or your own or requested); results update automatically as fields change. Bookings are grouped into tabs by date (in your time zone), each showing its booking count; the selected tab is kept while filters change. When signed in, your own bookings are outlined in purple and labelled "Your booking", while bookings you have requested spots on are outlined in blue, labelled "Requested by you", and show your spots as blue `✓` spots. Date tabs show a purple dot when they contain your booking and a blue dot when they contain your request. Within a date tab, each bookable has its own area, with a spot diagram per booking. Each bookable area header shows the bookable type and area (for example "Excursion · Cozumel"); cards show the start and end times together as `HH:MM - HH:MM` (or the end date if it ends on a later day); duration is only shown in the edit form. Green spots marked `+` are free; dark spots with a person figure are taken. Diagrams draw every spot of the booking (up to 20). Request spots from a matching booking. When signed in, Browse also includes your own upcoming bookings and the bookings you have active requests on, even if they are closed; the bookable and minimum spare spots filters (default 1) apply to them as well, so full bookings appear only in **My spots**.
  **Request spots** is disabled with an explanation when you are signed out,
  own the booking, have an overlapping booking or active request,
  or the booking is expired, closed, or full. Sign-in and **My spots**
  links are provided where relevant. The ownership check includes
  bookings hidden by Browse filters (including closed or full bookings).
  Cancelled requests do not block requesting again.
- **New booking**: enter a booking name, choose a **Restaurant**, **Excursion**, or **Sports**
  bookable, and enter a wall-clock time in your browser's time zone, duration in
  hours, party size (1–20), spare spots, and an optional description. Empty duration
  defaults to **2 hours**; fractional hours are supported to whole-minute precision.
  Descriptions support Markdown formatting, including links, emphasis, headings,
  lists, blockquotes, and code.
  The **Bookable** dropdown uses the same type-and-name tree as Browse, but lists
  the whole catalog. Choosing an entry fills in the separate editable **Bookable type**,
  **Bookable name**, and **Area** fields. Choose **New bookable** or a type heading
  to enter a new name and optional area. Slashes are treated as literal characters.
  Saving stores the name and area in the shared catalog, or reuses an existing
  name case-insensitively within its type. Supplying a different area for an existing name shows
  an error rather than overwriting the catalog. The same fields are available
  when editing an unlocked booking.
- **My spots** (`/mine`): one page for your bookings and the tables you have
  requested spots on, grouped in local-date tabs. The selector includes every date
  from today through your last upcoming booking or request, and always extends at
  least seven days ahead, including dates without tables. Other users'
  unrelated tables are not shown. Each table appears once, even if you have
  cancelled and requested again. Edit/delete unlocked bookings, close/reopen
  upcoming bookings, see requester GitHub usernames and statuses on your own
  bookings, and cancel your active requests. Cancelled request history is retained.
  Locked bookings still allow size and spare-spot adjustments, keeping at least
  one host spot plus all actively requested spots. Spare spots are additional to
  those requested spots; other details and deletion remain locked.
  Only upcoming bookings and tables with active requests are highlighted; expired
  bookings and cancelled-only requests are shown without a highlight. The old
  `/requests` page redirects here.

Excursions are drawn as a bus seen from above, facing right: window spots line
both sides of the aisle inside the cabin, with the engine compartment, windscreen,
and mirrors at the front.

Sports bookings use a rectangular tennis/volleyball-style court with a centre
net and court markings. Places alternate between its two halves, giving equal
sides for even capacities and one extra place on the first half for odd
capacities. Within each half, spots are arranged in a circle. The same taken,
requested, and free states apply, in both light and dark themes.

Browse and **My spots** share the same upcoming bookable groups, card layout,
spot legend, time/type details, and ownership/request labels. Bookable names and
areas appear in the group header; each card shows its start time, end time,
host, description, and closed/full badges where applicable. My spots adds owner
management or request-cancellation controls. Expired history keeps the full date
and bookable name on each standalone card, without active highlights.
Date tabs, bookable headers, and booking headings are underlined, shareable links: use
`/#day-2026-10-24` for a day, `/#bookable-12-2026-10-24` for a bookable on that day, or
`/#booking-123` for a booking (also supported on `/mine`). Bookable and booking
links automatically open the right date tab; booking links expand expired history
on My spots. Day and bookable links use the viewer's local date; booking links
keep pointing to the same booking across time zones. Normal visibility and filters still apply.
Your active request's spots appear as blue `✓` spots on both pages.
Every diagram has three spot states: dark "taken" spots with a person figure, occupied by the original booking
or other users' requests; blue checkmarks for your request; and green `+` free spots
(hover any spot for its label). Taken spots come first and stay in place when you request spots;
blue checkmarks replace green spots, never taken ones.

Every `@username` (hosts, requesters, and the signed-in user) links to that
person's GitHub profile in a new tab.

The theme icon button next to **Sign in/Sign out** cycles through **Auto** (sun and
moon), **Dark** (moon), and **Light** (sun), and remembers your choice in this
browser. Its tooltip and accessible label describe the current mode.
Auto (the default) follows your system
light/dark preference and switches live when it changes.
`web/js/theme.js` applies Vanilla's `is-dark` theme, and the app's own colours are CSS
custom properties in `web/css/app.css` with light and dark values.

Bookings expire automatically when their start time passes. Expired bookings
drop out of Browse (an open Browse page refreshes as each shown booking starts)
and become read-only: they can no longer be requested, edited, deleted, closed,
or reopened, and requests on them can no longer be cancelled. They are kept for
history in a collapsed **Expired** section on **My spots**, and API responses
include a computed `expired` flag.

A booking is locked while it has active requests. Cancelling the last active
request unlocks it for editing or deletion, provided it has not expired.
While locked, the owner can still change booking size and spare spots: size must
be at least one plus the active requested spots, and spare spots cannot exceed
size minus those requested spots minus one host spot. Cancelled requests do not
count toward these limits. This applies to restaurants, excursions, and sports.
When reducing capacity in the form, spare spots are automatically reduced if
needed to preserve all requested spots and one host spot.
Existing bookings without active requests are unlocked automatically on startup.
Cancellation returns spots but does not reopen a closed booking. Deleting an
unlocked booking also removes its cancelled request history.
Booking names are required (1–100 characters) and can be edited while unlocked.
Each diagram shows its name and “X of Y available”.
Existing bookings receive a `Table <id>` name during migration.
Bookings reference a shared **Bookable** catalog with `restaurant`, `excursion`, and `sports`
types. Existing restaurants and their booking/request history migrate without
changing IDs; existing bookings receive a 120-minute duration.
The API uses `GET /api/bookables?q=&type=&active=` and authenticated
`POST /api/bookables` (`type`, `name`, optional `area`), replacing the restaurant-only
catalog endpoints. Booking payloads use `bookable_id` and optional
`duration_minutes` (positive integer, default 120); booking and personal-request
responses include the bookable ID, name, type, and duration.

Only overlapping time ranges prevent booking or requesting spots, across both
bookable types and across owner/requester roles. Multiple non-overlapping
bookings and requests on the same day are allowed, including back-to-back
bookings. Conflicts compare UTC instants using `[start, start + duration)`,
including across midnight and time zones. Closed/full bookings still occupy
their owner's time; cancelled requests do not. Conflict checks and mutations
share a serialized write transaction.
Usernames are normalized to lowercase because GitHub logins
are case-insensitive. No notifications are sent.

Only open, future bookings with enough spots appear in public browse; signed-in users additionally see their own upcoming bookings and those they have active requests on.
Requester identities are returned only to the booking owner and the requester.
Identity comes exclusively from the authenticated GitHub username; there are no
editable personal-name fields. The API rejects supplied owner/requester names or
logins. Existing custom names are removed during migration, preserving the
authenticated usernames, bookings, and spot requests.
All changes require a signed session plus `X-CSRF-Token` obtained from
`GET /api/me`; supplied cross-origin `Origin` headers are rejected. Each user is
limited to 30 mutations/minute per process. Sessions expire after seven days.

Time zones come from the browser. Each booking stores the IANA zone of the
browser that created or last rescheduled it (`timezone`); naive datetimes are
interpreted in that zone, and its materialized `local_date` drives
the API's `date` filter, not conflict checks. Every viewer sees times in their
own browser's zone on a 24-hour clock. Start times must fall on a 15-minute
boundary (:00, :15, :30, :45); the form offers only those minutes and the API
rejects others with 400 (existing off-boundary bookings stay editable as long as
their time is unchanged). Nonexistent or ambiguous DST times are rejected; API
clients can disambiguate by supplying an explicit UTC offset. Databases created
with the former `APP_TZ` setting are migrated by assigning that zone to existing
bookings.

## Production (Docker)

```sh
docker compose build
docker compose up -d
curl --fail http://localhost:8080/healthz
```

Before starting, configure `.env`:

| Variable | Production setting |
| --- | --- |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | Your production GitHub OAuth app |
| `SESSION_SECRET` | Random secret of at least 32 characters |
| `BASE_URL` | Public HTTPS origin, no path; callback is `/auth/callback`. Optional: defaults to `https://$(hostname -f)` (container hostname), so set it in production |
| `APP_ENV` | `production` |
| `COOKIE_SECURE` | `true` (enforced in production) |
| `DB_PATH` | Compose forces `/data/app.db` |

The container runs as non-root with **one worker**. Compose binds only to
loopback; place it behind a TLS reverse proxy. For remote/containerized proxies,
adjust networking deliberately rather than exposing unencrypted traffic.
Do not enable access logging with OAuth query strings: the default container
command disables it. Configure proxy logs to omit callback query strings too.
Do not scale to multiple replicas: the rate limiter is process-local and SQLite
is intended for this single-instance deployment.

Data persists in the `app_data` volume. SQL migrations are applied in filename
order on startup and recorded atomically. `restaurants.json` contains clearly
labelled sample venues; replace it with your catalog before the first launch.
Startup adds new restaurant names without overwriting existing rows.

### Operations

- Check `GET /healthz` for database connectivity and `docker compose logs app`
  for errors. The API schema is available at `/openapi.json`; hosted Swagger
  assets are intentionally not used under the app's self-only content policy.
- Before migrations/deployment, create a SQLite-consistent backup using the
  SQLite backup API (not a raw copy of a live WAL database), and store it
  securely. Test restores against a separate volume.
- Deploy a new image only after checks pass, retaining a prior image and a
  compatible backup for rollback. Rotate `SESSION_SECRET` to invalidate all
  sessions. There is no automatic deploy job without a configured target.
- Avoid `docker compose down -v` unless intentionally deleting all data.

## Implementation

- `server/`: configuration, sessions/OAuth, typed API, transactional SQLite.
- `web/`: shared HTML shell and DOM-based UI. User text is rendered with
  `textContent`, not HTML interpolation.
- `tests/`: authentication, validation, time zones, privacy, business rules,
  SQLite concurrency, and workflow contracts.
- Python invalid input returns 400, unauthenticated 401, forbidden 403,
  missing objects 404, rule conflicts 409, rate limits 429, and SQLite
  contention 503 with `Retry-After`. Errors do not silently turn into success.

Vanilla Framework 4.59.0 is vendored under `web/css/` with its upstream license.
Its three remote `@font-face` definitions are removed from the vendored build;
the custom stylesheet uses system fonts so these pages need no external assets.
