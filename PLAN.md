## Complete Implementation Plan — Restaurant, Excursion, and Sports Spot-Share App (Python + Docker + Vanilla)

### 1) Goal and scope

Build a web app where users can:

- sign in with GitHub (username identity),
- post available spots on a Restaurant, Excursion, or Sports booking,
- find and request spots from other users’ bookings,
- cancel their own spot requests,
- and where booking records are immutable while requests are active.

Additional rules:

- a shared Bookable catalog with Restaurant, Excursion, and Sports types,
- per-booking duration, defaulting to two hours,
- **no overlapping bookings or active requests for a user**, across both roles and types,
- multiple non-overlapping bookings/requests per date, including back-to-back bookings,
- booking owner can **close/reopen** a booking to stop/allow new requests,
- times entered and displayed in each user's browser time zone,
- no notifications.

---

### 2) High-level architecture

Single Dockerized service:

- **Backend**: Python FastAPI API + session auth + server-side business rules.
- **Frontend**: static HTML/CSS/JS served by FastAPI.
- **Database**: SQLite file in mounted Docker volume (`/data/app.db`).

Why this is simplest:
- no separate DB service to manage,
- no CORS/origin complexity,
- easy local dev and deployment,
- strong enough transactional guarantees for this workload.

---

### 3) Tech stack

- Python 3.13
- `uv` for dependency management, Python provisioning, and a committed `uv.lock`
- FastAPI + Uvicorn
- SQLite (stdlib `sqlite3`)
- `httpx` for GitHub OAuth/user calls
- `itsdangerous` or Starlette session middleware for signed cookies
- Vanilla Framework CSS (vendored locally)
- pytest for tests
- pre-commit with Ruff (Python linting/formatting) and mypy (static type analysis)
- Workshop actions for running tests and the development server

---

### 4) Authentication design (GitHub)

Flow: GitHub OAuth web flow.

- User clicks “Sign in with GitHub”.
- Server redirects to GitHub auth endpoint.
- Callback exchanges `code` for token using `GITHUB_CLIENT_SECRET`.
- Server calls GitHub `/user`, reads `login`.
- Server creates session containing only:
  - `login` (required)
  - optional cached display fields if desired
- Token is discarded after identity fetch.

Security:
- HttpOnly, Secure, SameSite=Lax session cookie.
- CSRF `state` parameter for OAuth.
- No long-term GitHub token storage.

---

### 5) Data model (authoritative constraints)

Each booking records the IANA time zone of the browser that created it, used for local date derivation.

#### `bookables`
- `id` PK
- `type` (`restaurant`, `excursion`, or `sports`)
- `name` (unique within type; API reuses case-insensitive matches)
- `area` optional text

#### `bookings`
- `id` PK
- `name` (required booking name, 1–100 characters; immutable once locked)
- `owner_login` (GitHub username)
- `bookable_id` FK
- `starts_at` (UTC timestamp, ISO-8601)
- `duration_minutes` (positive integer, default 120)
- `timezone` (IANA zone from the owner's browser)
- `local_date` (YYYY-MM-DD in the booking's `timezone`, materialized)
- `party_size` int
- `seats_available` int
- `description` text
- `locked` bool default false (true while any spot request is active)
- `closed` bool default false (owner-controlled availability gate)
- `created_at`

Constraints:
- `1 <= party_size <= 20`
- `0 <= seats_available <= party_size`
- No overlapping owner or active-request time ranges, checked under `BEGIN IMMEDIATE`.

#### `seat_requests`
- `id` PK
- `booking_id` FK
- `requester_login` (authenticated GitHub username, visible to booking owner)
- `seats` int
- `status` enum text: `active` or `cancelled`
- `local_date` (copied from booking at create time)
- `created_at`
- `cancelled_at` nullable

Constraint:
- **UNIQUE(requester_login, booking_id) WHERE status='active'**
- No overlapping owner or active-request time ranges for the requester.

Migration preserves catalog, booking, and request IDs, assigns existing restaurants
the Restaurant type and existing bookings a 120-minute duration, and replaces
the former owner/requester per-date uniqueness constraints.

---

### 6) Core business rules

#### 6.1 Create booking
Allowed if authenticated and:
- booking time is future,
- bookable exists,
- spots and party size valid,
- no overlapping active spot request or owned booking for this user.

If violated, return `409`.

#### 6.2 Request spots
Allowed if authenticated and:
- booking exists, future, not closed,
- requester is not booking owner,
- sufficient spots remain,
- requester has no overlapping active request or owned booking.

On success:
- decrement `seats_available`,
- set `locked = true`,
- create `seat_request(status='active')`.

All done in one transaction.

#### 6.3 Cancel spot request
Requester only, future bookings only.
On cancel:
- mark request `cancelled`,
- increment booking `seats_available`.
- clear `locked` when no active requests remain.

#### 6.4 Edit/delete booking
Owner only and unexpired. Deletion requires `locked = false`.
While requests are active, only booking size, spare spots, and the close/reopen flag
can change. Size must be at least one host spot plus all active requested spots;
spare spots must not exceed size minus requested spots minus one. Other booking
details remain immutable.
Cancelling the last active request unlocks the booking; expiry still prevents changes.
Deleting an unlocked booking also deletes its cancelled request history.
On startup, a migration reconciles existing locks with active requests.

#### 6.5 Close/reopen booking
Owner only, future only.
- `close`: stop accepting new requests (`closed=true`).
- `reopen`: allow requests again if `seats_available > 0` (`closed=false`).
- Does not affect existing requests.

#### 6.6 Automatic expiry
A booking expires as soon as its start time passes; no scheduled job is needed
because expiry is evaluated against the current UTC time on every read and write.
- Expired bookings disappear from Browse and cannot be requested, edited,
  deleted, closed, or reopened (409); requests on them cannot be cancelled.
- Expired bookings and requests are kept for history; API responses include a
  computed `expired` flag. Request `status` stays `active`/`cancelled`.
- Open pages refresh when the next shown booking starts (My pages wait until no
  edit/request form is open).

#### 6.7 Visibility
- Public browse: booking metadata + spot counts, no requester identities.
- Signed-in browse also includes the user's own upcoming bookings and bookings with their active requests even when closed or outside the date filter; bookable, type, and minimum spare spot filters still apply, so full bookings are shown only on My spots.
- Owner view for own bookings: includes requester `@login`, spots, status.
- Requester view: own requests and their statuses.

---

### 7) Transaction strategy (SQLite correctness)

Use `BEGIN IMMEDIATE` for booking creation, request, cancel, and state toggles where relevant.

Important:
- Check constraints and write operations in same transaction.
- Use rowcount/affected rows checks to detect race failures.
- Roll back on any uniqueness conflict.

SQLite settings:
- `PRAGMA foreign_keys=ON`
- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=5000`

Run as single app instance for correctness simplicity.

---

### 8) API contract

#### Auth/session
- `GET /auth/login`
- `GET /auth/callback`
- `POST /auth/logout`

#### Health
- `GET /healthz`

#### User
- `GET /api/me`

#### Bookables
- `GET /api/bookables?q=&type=&active=` (`restaurant`, `excursion`, or `sports`; optional type filter)
- `POST /api/bookables` (authenticated, CSRF-protected; type, name and optional area; reuse case-insensitive names within type, reject conflicting areas)

#### Bookings
- `GET /api/bookings?bookable_id=&bookable_type=&min_seats=&date=`
- `POST /api/bookings` (`bookable_id`, `duration_minutes` default 120, plus other booking fields)
- `PATCH /api/bookings/{id}` (unexpired + owner; only size/spare spots while locked)
- `DELETE /api/bookings/{id}` (only unlocked, unexpired + owner)
- `POST /api/bookings/{id}/close`
- `POST /api/bookings/{id}/reopen`
- `GET /api/bookings/mine`

#### Spot requests
- `POST /api/bookings/{id}/requests`
- `DELETE /api/requests/{id}` (cancel own request)
- `GET /api/requests/mine`

Error model:
- 400 validation,
- 401 unauthenticated,
- 403 forbidden,
- 404 not found,
- 409 business-rule conflict.

---

### 9) Frontend plan (Vanilla Framework)

Pages:
1. **Browse** (`/`)
   - bookable dropdown with Restaurant/Excursion/Sports type labels,
   - min spots filter (default 1), applied to every booking shown,
   - list of open/future bookings in date tabs (viewer's time zone), grouped by bookable within each tab, showing type and end time (duration only in the edit form), with every spot drawn,
   - highlight the signed-in user's own bookings and requested spots; these are shown even when closed, but respect the bookable and spot filters,
   - list only bookables with active bookings (`GET /api/bookables?active=true`) in the bookable filter, as a tree: one entry per type (filtering by `bookable_type`) followed by its bookables sorted by name,
   - “Request spots” form (`seats` only; identity from the authenticated GitHub username).
2. **New booking** (`/new`)
   - fields: booking name, bookable dropdown, bookable type, bookable name, optional area, date/time, duration in hours (empty defaults to 2), booking size, available spots, description.
   - bookable dropdown uses Browse's type-and-name tree with the full catalog; choosing a bookable populates the separate editable type, name, and area fields, while choosing a type or New bookable clears name and area for a new entry. New entries are saved to the shared catalog, duplicate names within type are reused, and conflicting areas are rejected.
3. **My spots** (`/mine`; legacy `/requests` redirects here)
   - shared bookable grouping, card renderer, legend, bookable type in the area header, time details, closed/full badges, and active ownership/request labels with Browse (a table for restaurants; a top-down bus facing right for excursions; a tennis/volleyball-style court with spots arranged in circles on both halves for sports),
   - only your own bookings and tables with your active/cancelled requests,
   - upcoming tables in local-date tabs (soonest first), with highlights only for active bookings and active requests,
   - include every local date from today through the last upcoming table, extending at least seven days ahead even when there are no tables,
   - one card per table, including cancelled request history and your active spots in blue,
   - own-booking state badges (Editable / Locked / Closed), edit/delete and close/reopen controls,
   - requester list with @username + spots + status on your own bookings,
   - cancel controls for your active upcoming requests,
   - expired tables in a collapsed, read-only "Expired" section.

UX notes:
- Shareable date-tab, bookable-header, and booking-heading anchors (`#day-YYYY-MM-DD`, `#bookable-ID-YYYY-MM-DD`, `#booking-ID`) select the matching local-date tab or expand expired history, including on initial load and browser Back/Forward navigation.
- Personal identity comes only from GitHub authentication, never editable form fields.
- Remove legacy custom personal names during migration; keep authenticated logins and booking/request data.
- provide a header button cycling through Auto, Dark, and Light, with the choice saved in localStorage. Auto follows the system light/dark preference (`prefers-color-scheme`): a small synchronous `theme.js` toggles Vanilla's `is-dark` body class (CSP allows only same-origin scripts), and app colours are CSS custom properties with dark overrides.
- show timezone label globally in the site header (`All times shown in your time zone (<browser zone>)`).
- client-side checks for friendliness, but server remains authoritative.
- disable obvious impossible actions, still handle server 409 gracefully.
- Disable “Request spots” for bookings whose time ranges overlap an owned booking
  or active request; show an explanation and a link to My spots. Back-to-back
  bookings and cancelled requests do not disable the control. Refresh request state with
  browse results and after a successful request.

---

### 10) Time handling plan (browser time zone)

- The browser supplies its IANA zone (`Intl.DateTimeFormat().resolvedOptions().timeZone`) with booking input; the server validates it.
- Input datetime interpreted in that zone; convert and store `starts_at` in UTC.
- Store the zone on the booking and compute `local_date` in it.
- Compute conflicts in UTC using half-open `[start, start + duration)` intervals;
  include cross-midnight, different-zone, and in-progress conflicts. Duration is
  elapsed time in whole minutes, not wall-clock hours across DST changes.
- For filtering “today/future”, compare with now in UTC.
- Display all times in each viewer's browser zone, on a 24-hour clock.
- Times are entered with a date, an hour (00–23) and a minute (00/15/30/45); the server rejects start times off a 15-minute boundary with 400 unless an edit leaves the time unchanged.

---

### 11) Security and privacy

- Secure session cookies.
- OAuth `state` validation.
- Validate all numeric bounds server-side.
- Rate-limit mutating endpoints per session/login.
- Escape/sanitize user-entered text at render.
- Do not expose requester identities outside owner-specific endpoints.
- Log minimal PII; avoid storing GitHub tokens.

---

### 12) Deployment (Docker-first)

#### Dockerfile
- installs dependencies
- copies server + web
- exposes `8080`
- mounts `/data` volume
- runs `uvicorn` (1 worker)
- includes `/healthz` healthcheck

#### docker-compose (example)
- app service
- bind `8080:8080`
- volume `app_data:/data`
- env vars from `.env`

#### Required env vars
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `SESSION_SECRET`
- `BASE_URL` (public app URL; optional, defaults to `https://$(hostname -f)`, or `http://$(hostname -f):8080` when `COOKIE_SECURE=false`)
- `DB_PATH=/data/app.db`

Run behind TLS reverse proxy in production.

---

### 13) Development workflow and CI/CD plan

#### Pre-commit checks

- Define local hooks in `.pre-commit-config.yaml` for Ruff linting, Ruff formatting, and mypy static type analysis; run all tools through `uv run --locked` so `uv.lock` pins their versions.
- Configure dependencies, linting, and type analysis in `pyproject.toml`; hooks use the same uv environment as development and CI.
- Install application and development dependencies with `uv sync --locked`, then run `uv run pre-commit install` once per clone.
- Hooks run automatically on staged files before commits; run `uv run --locked pre-commit run --all-files` for a full check.
- Keep tests and the long-running development server out of pre-commit hooks; run them through Workshop actions instead.

#### Workshop actions

Extend the existing uv-based development workshop named `dev` in `.workshop/dev.yaml`. Python 3.13 and application/development dependencies are provisioned by uv. Follow the [Workshop actions guide](https://ubuntu.com/workshop/docs/how-to/customize-workshops/add-actions/).

Add these Bash action bodies under the workshop's `actions` mapping:

```yaml
actions:
  tests: |
    exec uv run --locked pytest "$@"
  seed-demo: |
    set -eu
    mkdir -p .local
    export DB_PATH=.local/dev.db
    export APP_ENV=development
    exec uv run --locked --env-file .env python -m server.seed_demo "$@"
  dev: |
    set -eu
    mkdir -p .local
    export DB_PATH=.local/dev.db
    host="$(hostname -f)"
    echo "Seat Share development server: http://${host}:8080"
    exec uv run --locked --env-file .env uvicorn server.main:create_app --factory --host "$host" --port 8080 --reload --no-access-log "$@"
```

- Run the suite with `workshop run dev -- tests`.
- Forward pytest selectors/options, for example `workshop run dev -- tests tests/test_request_rules.py -q`.
- Populate the dev database with `workshop run dev -- seed-demo` (optional
  `--timezone Europe/London`, default UTC, and space-separated GitHub usernames):
  24 open restaurant bookings with varied restaurants, times, party sizes, and
  upcoming dates, plus 8 non-overlapping demo excursions (boat and walking tours).
  Default to six fictional users over four dates; randomly assign supplied users
  with repeatable per-date shuffling, spreading over more dates for fewer users.
  Normalize/deduplicate logins and preserve one booking per user/date. Skip
  existing user/date pairs and active-request conflicts without changing bookings
  or requests. Never seed in production or create demo authentication sessions.
- Start the reload-enabled development application with `workshop run dev -- dev`; it prints and listens on `http://$(hostname -f):8080` inside the workshop. Stop it with Ctrl+C.
- Supply the required application environment variables in the workshop environment, keeping secrets out of version control. `BASE_URL` defaults to the printed development URL; register that URL's `/auth/callback` as the GitHub OAuth callback.
- Keep development data in the ignored `.local/dev.db`, separate from the production `/data/app.db`. Tests use temporary databases and mocked GitHub calls, not the development database or live OAuth credentials.
- Preserve secure cookies when using HTTPS; any HTTP-only local-development cookie override must be explicit and disabled in production.
- Changes to action bodies do not require `workshop refresh`.
- These `workshop run` commands are for the host. Inside an existing workshop, run the action bodies directly; do not attempt to launch a nested workshop.

#### CI/CD

GitHub Actions remains responsible for automated checks and image delivery:

1. Run `uv sync --locked` and `uv run --locked pre-commit run --all-files` so CI enforces the same linting and static analysis as local commits.
2. Execute the Workshop test action's Bash body directly in CI's existing environment, without provisioning a nested workshop.
3. Build Docker image after checks pass.
4. Push image to GHCR on main/tag.
5. Optional deploy job (if target host supports pull + restart).

Artifacts:
- migration SQL files,
- test reports.

---

### 14) Repository layout

```text
.
├─ Dockerfile
├─ docker-compose.yml
├─ .dockerignore
├─ .gitignore
├─ .pre-commit-config.yaml
├─ pyproject.toml
├─ uv.lock
├─ .python-version
├─ .workshop/
│  └─ dev.yaml
├─ restaurants.json
├─ server/
│  ├─ main.py
│  ├─ auth.py
│  ├─ db.py
│  ├─ config.py
│  ├─ models.py
│  ├─ time_utils.py
│  ├─ routes/
│  │  ├─ bookings.py
│  │  └─ requests.py
│  └─ migrations/
│     ├─ 0001_init.sql
│     └─ 0002_indexes.sql
├─ web/
│  ├─ index.html
│  ├─ css/
│  │  ├─ app.css
│  │  └─ vanilla.min.css
│  └─ js/
│     ├─ api.js
│     ├─ app.js
│     └─ theme.js
└─ tests/
   ├─ test_auth.py
   ├─ test_booking_rules.py
   ├─ test_request_rules.py
   ├─ test_close_reopen.py
   ├─ test_race_conditions.py
   ├─ test_time_and_config.py
   └─ test_workflows.py
```

---

### 15) Milestones

#### Milestone 1 — Skeleton + auth + infra
- FastAPI app, static hosting, health endpoint.
- GitHub OAuth login/logout.
- Session cookie setup.
- Dockerfile + compose.
- Workshop development environment and `dev` action with reload and an isolated local database.
- Development dependencies, pre-commit hook installation, and Ruff/mypy configuration.

#### Milestone 2 — DB + booking CRUD foundations
- migrations + seed restaurants,
- create/list bookings,
- basic browse/new UI.

#### Milestone 3 — Spot request flow + hard constraints
- request/cancel endpoints with transactions,
- lock behavior,
- interval-overlap constraints (owner, requester, cross-role), same-day and back-to-back availability,
- conflict messages.

#### Milestone 4 — Owner controls + user pages
- close/reopen endpoints/UI,
- my bookings (owner sees requester GitHub usernames),
- my requests page.

#### Milestone 5 — tests + polish
- race tests and edge cases,
- Workshop `tests` action with pytest argument forwarding and temporary test databases,
- validation and error UX,
- docs and runbook.

#### Milestone 6 — CI/CD
- pre-commit checks and Workshop tests/build/push workflow,
- deployment instructions.

---

### 16) Acceptance criteria (done definition)

- GitHub login works and only username identity is retained.
- Creating booking requires all required fields and valid ranges.
- Active spot requests lock booking data; cancelling the last one unlocks it.
- Booking owner can close/reopen booking to stop/allow new requests.
- Requesters can cancel active requests before booking time.
- Owner sees authenticated requester GitHub usernames for their bookings.
- One booking per owner/day enforced.
- One active request per requester/day enforced.
- No overlapping involvement across owner/requester roles; non-overlapping same-day involvement allowed.
- Times are entered in the owner's browser time zone and displayed in each viewer's browser time zone.
- App runs via `docker compose up` with persistent data volume.
- Installed pre-commit hooks enforce linting, formatting, and static type analysis; the same checks pass across all files in CI.
- `workshop run dev -- tests` runs the suite, forwards pytest arguments, and fails when tests fail.
- `workshop run dev -- dev` serves a reload-enabled development app with a separate database.

---

Implementation notes:
- The four frontend URLs share one HTML shell and JavaScript module, avoiding duplicated navigation and form code.
- Restaurant and user/session endpoints live alongside the related booking and authentication modules.
- See `README.md` for setup, development commands, deployment, and operational guidance.
