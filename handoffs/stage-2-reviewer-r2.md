@chiraanth/reviewer HANDOFF — Re-review graded Stage 2 revision 74fb95b (fix round 1)

TASK
Independently review commit 74fb95b on main in /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded (same machine). Your r1 review of fe7547c rejected exactly one defect:
- DEFECT 1: signed-out screens rendered the literal text "null" in the header because `renderHeader()` in stage-2/static/app.js passed `null` to `replaceChildren`.
The fix (code diff `git diff fe7547c 74fb95b -- stage-2`): a `kids()` helper in app.js that flattens and drops null/undefined/false, used in `renderHeader()` and in the lookup detail view (which had the same pattern for its error box); a new selftest/ui.py check for stray "null"/"undefined"/"NaN"/"[object Object]" text on every route, signed in and out, at 375 and 1280 px. stage-1/ must remain unchanged since cf904c2.

Required:
1. Verify DEFECT 1 is fixed: build and run the image (`cd /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded && docker build -t tablekeeper-graded-s2-review stage-2 && docker run --rm -d --cpus 2 --memory 2g -e PORT=18082 -p 18082:18082 tablekeeper-graded-s2-review`). With your own headless-browser checks (Playwright for Python: /Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs/.venv/bin/python), confirm no stray "null"/"undefined"/"NaN"/"[object Object]" text on /, /signup, /login, /lookup, signed out and signed in, at 375 and 1280 px, including lookup detail and refused-cancel states, booking form/confirmation/uncertain/error states and the empty/no-slots state.
2. Confirm the diff introduces no regression: re-run your r1 UI probes (testids, out-of-order search, 409, lost response + retry, unchanged resubmit, combination cells, lookup, import mid-flow, 375 px no page scroll) and a short API smoke; confirm `git diff cf904c2 74fb95b -- stage-1` is empty and stage-2/ has no .git or symlinks.
3. Review against the FULL SPECIFICATIONS and ARCHITECT DECISIONS below; if you find any other defect, report it.
4. Optional directional harness: from /Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs run `.venv/bin/python -m harness run --track tablekeeper --repo ../band-work/result-graded --stage 2 --out ../band-work/checks/graded-s2-review-r2-<n>`. Clean up containers and images afterwards.
Do not modify code. Post ACCEPT, or REJECT with a numbered list of concrete defects (spec section, input, expected vs actual), addressed to me (the architect) in this room.

=====================================================================
STAGE 2 SPECIFICATION (verbatim, tablekeeper/spec/stage-2.md)
=====================================================================
# Tablekeeper — Stage 2: online booking and combined tables

The stage-1 requirements continue to apply, with the additions below. Numbered section
references such as §5 and §7 refer to `stage-1.md`.

Diners can search, book and manage reservations in a browser. Restaurants can offer
approved pairs of tables for larger parties.

The following screens must be reachable by URL. Other screens must be reachable through
the UI. Server-side and client-side rendering are both permitted.

| Route | Screen |
|---|---|
| `/` | Search and availability grid |
| `/signup` | Signup |
| `/login` | Login |
| `/lookup` | Look up a reservation by reference |

A screen route returns HTML; §3.4's `application/json` convention is about the API, and does
not govern the routes in the table above.

## Competing clients and uncertain outcomes

The UI must handle responses arriving out of order and connections failing after submission.

- If search A starts before search B but finishes after it, the grid, table labels and
  booking form must describe B. A late response must not restore A's results.
- If another client takes a table after the form opens, a `409 table_unavailable` response
  shows `booking-error` and refreshes availability. Preserve the selected form and its
  inputs so the diner can change their choice. Do not show a confirmation for that attempt.
- If a booking response is lost, including after the booking commits, show nonempty
  `booking-uncertain` text, without `booking-error` or a new confirmation. The unchanged
  form must retry with the same idempotency key and body. A successful retry removes the
  uncertainty/error elements and shows the original reference. A confirmed rejection
  uses `booking-error`.

These rules apply to combination bookings too. No background polling, live updates,
cross-tab storage synchronization, or recovery across a page reload is required. The server
remains authoritative; the browser must not manufacture a successful result from cached data.

The UI must expose the `data-testid` attributes listed below for integration testing.
Additional elements are permitted, and the visual implementation is the team's choice subject
to the product-quality requirements below.

## Product and visual direction

The browser experience must feel like a coherent, presentation-ready restaurant product, not a
test harness with controls attached. Aim for a warm, confident hospitality character. The search,
availability and booking flow should have an obvious visual hierarchy; a diner should be able to
scan dates, times, party size and table choices without having to interpret raw API data. Combined
tables should read as intentional seating options, not as concatenated technical identifiers.

Use a consistent visual system for typography, spacing, colour, controls and feedback. Primary
actions must be easy to identify. Available, unavailable, selected, loading, successful, refused
and uncertain states must be visually distinct as well as satisfying the behavioural requirements
below. Use human-readable restaurant and table labels prominently; expose technical identifiers
only where they help the user.

The required flows must remain clear and usable at a 375 CSS-pixel viewport and at conventional
desktop widths, without horizontal page scrolling. Inputs need visible labels, keyboard focus must
be apparent, and text and controls need sufficient contrast. Provide considered empty, loading and
error states, and keep navigation consistent across the required routes. A custom illustration,
brand asset or exact visual match to a reference is not required.

## Signup and login

| `data-testid` | Element |
|---|---|
| `signup-email`, `signup-password`, `signup-display-name` | Inputs |
| `signup-submit` | Button |
| `login-email`, `login-password`, `login-submit` | Inputs and button |
| `auth-error` | Error message. Present only when there is one |
| `current-user` | Visible on every screen when signed in. Text contains the display name |
| `logout-button` | Button |

## Search and availability grid — `/`

| `data-testid` | Element |
|---|---|
| `restaurant-select` | Selects a restaurant. Option values are restaurant ids |
| `date-input` | Date, value `YYYY-MM-DD` |
| `party-size-input` | Number |
| `search-button` | Runs the search |
| `availability-grid` | Container for the results |
| `slot-{table_id}-{HH:MM}` | One cell per table per slot, e.g. `slot-t_2-19:00` |
| `no-slots` | Shown instead of the grid when the day has no slots |

Each cell carries `data-available="true"` or `data-available="false"`. A cell is `true` exactly
when its `table_id` is in that slot's `available_table_ids` from `GET /availability` for the party
size that was searched, and `false` otherwise. Clicking an available cell
opens the booking form for that table and slot. Clicking an unavailable cell does nothing.
Booking requires a signed-in user: clicking an available cell while signed out shows `auth-error`
or navigates to `/login`, your choice.

## Booking form

| `data-testid` | Element |
|---|---|
| `booking-form` | Container |
| `booking-summary` | Text contains the table label and the local start time |
| `booking-party-size` | Number input, pre-filled from the search |
| `booking-submit` | Button |
| `booking-error` | Error message, when the booking fails |

Keep the booking form on screen after success. Submitting it again without changing a
field must return the same `confirmation-reference`, without `booking-error` or another
booking. Changing a field makes the next submission a new booking request. Retries follow §7.

## Confirmation

Shown after a successful booking.

| `data-testid` | Element |
|---|---|
| `confirmation` | Container |
| `confirmation-reference` | Text is exactly the reference, no surrounding words |
| `confirmation-details` | Text contains the restaurant name, table label and local start time |

## Lookup — `/lookup`

| `data-testid` | Element |
|---|---|
| `lookup-reference-input`, `lookup-submit` | Input and button |
| `reservation-detail` | Container, shown when found |
| `reservation-status` | Text is exactly `confirmed` or `cancelled` |
| `reservation-cancel-button` | Cancels. Absent once cancelled |
| `reservation-error` | Shown when not found, or when a cancel is refused |

## Existing clients after an upgrade

A stage-2 service must accept an export produced by the same team's stage-1 service. A
browser signed in before that export/import upgrade must remain signed in afterwards.
A retained booking reference still works through the lookup screen. A booking whose response
was lost before export remains retryable after import with the same body and key; the UI
must recover the original confirmation. These requirements apply when import completes
between browser requests; migration during an in-flight request is not required. No page
reload or new screen is required. The form and pending retry identity must survive the upgrade.

## Combined tables

A party may book two tables that the restaurant has declared combinable. The booking
occupies both tables for its full duration.
Existing single-table request formats remain supported.

## Model

The restaurant fixture gains one field:

```json
{
  "id": "r_anker",
  "combinable": [ ["t_1", "t_2"], ["t_2", "t_3"] ],
  ...
}
```

Each entry is an unordered pair of table ids in that restaurant. **Pairs only** — never three or
more. A pair not listed cannot be combined, whatever the table sizes are. Combining is not
transitive: `[t_1,t_2]` and `[t_2,t_3]` do not make `{t_1,t_3}` bookable.

A combination's capacity is the sum of its tables' capacities.

Seeded `reservations` are `confirmed` unless they carry a `status` of `cancelled`, and may hold
either `table_id` or `table_ids`.

## API

### `GET /availability`

Slots gain `available_options`. `available_table_ids` stays exactly as it was — single tables
only.

```json
{
  "slots": [
    {
      "starts_at_local": "2026-09-24T19:00",
      "starts_at": "2026-09-24T19:00:00+02:00",
      "available_table_ids": ["t_3"],
      "available_options": [
        { "table_ids": ["t_3"], "capacity": 4 },
        { "table_ids": ["t_1", "t_2"], "capacity": 6 }
      ]
    }
  ]
}
```

`available_options` lists every single table and every declared pair with
`capacity >= party_size` and no overlapping confirmed reservation on any member. Singles first in
fixture order, then pairs in `combinable` order. `table_ids` within a pair is in `combinable`
order.

### `POST /reservations`

The body takes `table_ids` instead of `table_id`:

```json
{ "restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
  "starts_at_local": "2026-09-24T19:00", "party_size": 6 }
```

`table_id` is still accepted and means a set of one. Sending both is 422 `validation_failed`.

Responses always carry `table_ids`. They also carry `table_id` **when the set has exactly one
member**, and omit it otherwise.

| Case | Response |
|---|---|
| The pair is not in `combinable` | 422 `combination_not_allowed` |
| More than two tables | 422 `combination_not_allowed` |
| Any table in the set is taken for an overlapping interval | 409 `table_unavailable` |
| `party_size` exceeds the combination's summed capacity | 422 `party_exceeds_capacity` |
| Duplicate table id in the set | 422 `validation_failed` |

`PATCH /reservations/{reference}` accepts `table_ids` under the same rules. Cancelling frees every
table in the set.

## UI

The availability grid gains combination cells, shown when a declared pair is available for the
searched party size:

| `data-testid` | Element |
|---|---|
| `slot-{t_a}+{t_b}-{HH:MM}` | A combination cell, e.g. `slot-t_1+t_2-19:00`. Ids in `combinable` order. Carries `data-available` like a single cell |
| `confirmation-tables` | Text contains every table label in the reservation |
| `reservation-tables` | On the lookup screen. Same |

`booking-summary` must name every table in the selection. A single-table booking's cell testid,
confirmation and lookup are unchanged.

Atomic reservation moves from stage 1 also accept `table_ids` per move. No table may
belong to overlapping resulting bookings. The existing browser recovery and original-receipt
requirements also apply to combined-table bookings.

## Concurrent bookings and amendments

Concurrent requests must produce the same results as executing them one at a time in some
order, and the requirements above hold at every read.

=====================================================================
STAGE 1 SPECIFICATION (verbatim, tablekeeper/spec/stage-1.md — still applies)
=====================================================================
# Tablekeeper — Stage 1: reservations

This stage defines the initial service and its API.

Build from the supplied requirements. Source code, API documentation and schemas from
existing products in this domain must not be used.

## 1. Scope

Diners can search restaurant availability, book a table and receive a confirmation
reference. They can cancel or amend their bookings, including changing several bookings
together. Each restaurant has its own table capacities, opening hours and cancellation policy.
Only the HTTP API is required.

Two `confirmed` reservations must never occupy the same table at overlapping times,
including during concurrent requests. Occupancy is the half-open interval
`[starts_at, starts_at + reservation_duration)`. A 90-minute booking at 19:00 therefore
does not overlap a booking starting at 20:30. Retries and rejected requests must not
create duplicate or partial bookings.

## 2. Delivery and deployment

Deliver an HTTP service, a `Dockerfile` and a `RUN.md` with a command that builds and
starts the service without manual setup. Language, framework and storage are unrestricted.
A `docker-compose.yml` is optional.

The submission is a containerized HTTP service, not a Python package. Python is not
required in the implementation. TypeScript/JavaScript, Go, Rust, Java, Python and any
other language are equally valid. The harness builds the submitted `Dockerfile`, starts
the resulting image and tests only its HTTP behavior; it does not import or execute the
submission's source files on the judge host.

The image must run on its own with `-e PORT=<port>` and a port mapping. Runtime networking
has no outbound access. All runtime dependencies, initialization and seed data must work
within that single container. Compose configuration is not used to start the service.

### Resource limits

The service must operate within these limits:

| Limit | Value |
|---|---|
| CPU | 2 vCPU |
| Memory | 2 GiB |
| Start to first healthy response | 60 s |
| Concurrent requests | up to 50 in flight |
| Per-request timeout | 5 s (10 s for `POST /_test/reset`) |
| Outbound network | available during `docker build`, **none at run time** |
| Disk | ephemeral; state need not survive a container restart |

Runtime assets and dependencies must be included in the image. This includes fonts,
scripts and stylesheets; external services are unavailable at runtime.

## 3. Runtime contract

### 3.1 Listening

Listen on `0.0.0.0` using the `PORT` environment variable, default `8080`.

### 3.2 Health

```http
GET /health  ->  200  {"status": "ok"}
```

Return 200 once the service and its data store can serve requests, within 60 seconds
of container start. Non-200 responses are permitted before the service is ready.

### 3.3 Reset and seed

```http
POST /_test/reset
Content-Type: application/json

{ ...fixture... }

->  204 No Content
```

Replace all service state with the fixture in the request body (§4). When reset returns
204, subsequent requests must see only that fixture. Repeated resets are supported.
This test endpoint must be enabled in the delivered image and requires no authentication.

### 3.4 Conventions

- Requests and responses are `application/json; charset=utf-8`.
- Timestamps in responses are RFC 3339 with an explicit offset, e.g. `2026-09-24T19:00:00+02:00`.
- Unknown fields in a request body are ignored, never an error.
- Unknown query parameters are ignored.
- IDs are opaque strings of at most 64 characters. Their format is yours. This limit
  also applies to IDs supplied in reset fixtures.

## 4. Model

Restaurants and tables are supplied through `POST /_test/reset` only. Restaurant and
table creation endpoints are out of scope.

| Field | On | Meaning |
|---|---|---|
| `timezone` | Restaurant | IANA zone name, e.g. `Europe/Berlin`. All of the restaurant's times are local to this |
| `slot_minutes` | Restaurant | Bookings start on a grid of this many minutes from opening time |
| `reservation_duration_minutes` | Restaurant | How long every reservation occupies its table |
| `cancellation_cutoff_minutes` | Restaurant | A booking cannot be cancelled or changed within this many minutes of its start |
| `opening_hours` | Restaurant | Per weekday. A day with no entry is closed |
| `capacity` | Table | Maximum party size |

### Fixture format

```json
{
  "users": [
    { "id": "u_ada", "email": "ada@example.com",
      "password": "correct horse", "display_name": "Ada" }
  ],
  "restaurants": [
    {
      "id": "r_anker",
      "name": "Zum Anker",
      "timezone": "Europe/Berlin",
      "slot_minutes": 30,
      "reservation_duration_minutes": 90,
      "cancellation_cutoff_minutes": 120,
      "opening_hours": [
        { "weekday": "thu", "opens": "18:00", "closes": "23:00" },
        { "weekday": "fri", "opens": "18:00", "closes": "23:30" }
      ],
      "tables": [
        { "id": "t_1", "label": "1", "capacity": 2 },
        { "id": "t_2", "label": "2", "capacity": 4 }
      ]
    }
  ],
  "reservations": []
}
```

- `weekday` is one of `mon tue wed thu fri sat sun`.
- `opens` and `closes` are local `HH:MM`, 24-hour. `closes` is always later than `opens` on the
  same local day — opening hours never cross midnight.
- Seeded users must be able to log in with the given password immediately.
- `reservations` may seed confirmed bookings, with the same fields as a `POST /reservations`
  body plus `id`, `reference` and `user_id`.

Fixtures may use any calendar date. A booking must not be rejected solely because its
start is in the past; the cancellation and amendment cutoff rules still apply.

## 5. Errors

Every 4xx and 5xx response carries this body:

```json
{ "error": { "code": "table_unavailable", "message": "human readable, any wording" } }
```

Use the specified HTTP status and `code`. The human-readable `message` may use any wording.
Endpoint-specific errors are listed with each endpoint.

| Status | `code` | When |
|---|---|---|
| 400 | `malformed_request` | Unparseable body, or a field of the wrong JSON type |
| 400 | `missing_idempotency_key` | Required `Idempotency-Key` header absent or empty |
| 401 | `unauthenticated` | Missing, malformed or unknown bearer token |
| 403 | `forbidden` | Authenticated, but not permitted to touch this resource |
| 404 | `not_found` | No such resource, or not visible to this caller |
| 409 | `idempotency_key_reuse` | Key already used by this caller with a different request body |
| 422 | `validation_failed` | A required field or query parameter is missing, or a stated rule is violated with no more specific code |

A field of the correct JSON type with an invalid format or out-of-range value gives
422 `validation_failed`, unless an endpoint specifies a different error. This includes
invalid dates, negative counts and values exceeding a stated maximum or length. In addition:

- Endpoint-specific field rules take precedence: invalid `party_size` values (including strings
  and booleans) and `starts_at_local` strings that are not a bare local `YYYY-MM-DDTHH:MM` are
  422 `validation_failed`. Other wrong JSON types follow the rule below.
- An integer-valued **query parameter** is written as plain decimal digits: `1e9`, `4.0` and `+4`
  are 422 `validation_failed` whatever their numeric value.
- Reserve 400 `malformed_request` for a body that does not parse or a field of the wrong type.

Shared ranges, enforced on every endpoint that takes them:

| Field | Valid | Otherwise |
|---|---|---|
| `Idempotency-Key` | 1 to 255 characters | 422 `validation_failed` |

Requests must not produce 5xx responses, including under concurrent load.

## 6. Authentication

Authentication supports signup and login. Email verification, password reset, refresh
tokens and role-management endpoints are out of scope. Permissions specified elsewhere
in these requirements still apply.

```http
POST /auth/signup
{ "email": "a@example.com", "password": "correct horse", "display_name": "Ada" }

->  201  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

```http
POST /auth/login
{ "email": "a@example.com", "password": "correct horse" }

->  200  { "user_id": "u_1", "display_name": "Ada", "token": "..." }
```

| Case | Response |
|---|---|
| Email already registered | 409 `email_taken` |
| Password shorter than 8 characters | 422 `validation_failed` |
| `email` not of the form `local@domain` | 422 `validation_failed` |
| Wrong password or unknown email on login | 401 `unauthenticated` |

Every other endpoint requires a bearer token, except `/health`, `/_test/reset`, the two above, and
the three public endpoints named at the top of §8 — `GET /restaurants`, `GET /restaurants/{id}` and
`GET /availability`:

```http
Authorization: Bearer <token>
```

Tokens do not expire. An account may have multiple valid tokens and concurrent sessions.

Passwords must be stored using a password-hashing function such as bcrypt, scrypt or
Argon2, or an equivalent. Plaintext password storage is not permitted.

## 7. Idempotency

Two write paths require an idempotency key: **`POST /reservations`** (§8) and
**`POST /reservation-moves`** (§11).

```http
Idempotency-Key: <client-chosen string, 1..255 characters>
```

The key is scoped to **the authenticated user**. Two different users may use the same key string
with no interaction between them.

A replay means the same user sending the **same method, the same path and the same body**. The
same key with the same body on a different path is a different request, not a replay, and must
succeed normally.

After the body has been parsed as a JSON object and the caller authenticated, idempotency
is resolved before endpoint-specific field validation or current-resource checks. Thus a
used key with a different JSON body returns `409 idempotency_key_reuse` even when that new
body would otherwise be invalid.

| Situation | Response |
|---|---|
| Header absent or empty | 400 `missing_idempotency_key` |
| First use of the key | The normal response, **201** |
| Replay: same key, same body | **200**, body identical to the original response as a JSON value |
| Same key, different body | 409 `idempotency_key_reuse` |
| Key reused after the original request failed with 4xx | Treated as a first use |

"Same body" means the same JSON value after parsing — key order and whitespace do not matter.

For concurrent identical requests with an unused key, exactly one returns 201.
The others return 200 with the same body. The operation takes effect only once.

A successful replay returns the original response, even after the resource changes or
is cancelled. It makes no further state changes.

## 8. API

`GET /restaurants`, `GET /restaurants/{id}` and `GET /availability` are **public** — no bearer
token. Everything else needs one. Diners browse before they sign in.

### `GET /restaurants`

```json
{ "restaurants": [ { "id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin" } ] }
```

### `GET /restaurants/{id}`

The restaurant with its `slot_minutes`, `reservation_duration_minutes`,
`cancellation_cutoff_minutes`, `opening_hours` and `tables`, in the fixture's shape. 404 if
unknown.

### `GET /availability`

```http
GET /availability?restaurant_id=r_anker&date=2026-09-24&party_size=4
```

All three parameters are required; a missing one is 422 `validation_failed`. `date` is a local
calendar date at the restaurant.

```json
{
  "restaurant_id": "r_anker",
  "date": "2026-09-24",
  "timezone": "Europe/Berlin",
  "slots": [
    { "starts_at_local": "2026-09-24T18:00",
      "starts_at": "2026-09-24T18:00:00+02:00",
      "available_table_ids": ["t_2"] }
  ]
}
```

`starts_at_local` is the full `YYYY-MM-DDTHH:MM` and goes into `POST /reservations` unchanged.

A slot appears for every `slot_minutes` step from `opens` such that
`slot + reservation_duration_minutes <= closes`. `available_table_ids` lists the tables of that
restaurant with `capacity >= party_size` and no overlapping confirmed reservation, in fixture
order. A slot with no available table still appears, with an empty list.

A closed day returns `"slots": []`.

### `POST /reservations`

`Idempotency-Key` is required; see §7.

```http
POST /reservations
Authorization: Bearer <token>
Idempotency-Key: 2f9c1a...

{ "restaurant_id": "r_anker", "table_id": "t_2",
  "starts_at_local": "2026-09-24T19:00", "party_size": 4 }
```

`starts_at_local` is wall-clock at the restaurant, with no offset and no `Z`. Resolve it against
the restaurant's `timezone`.

```json
201
{
  "reservation_id": "res_7",
  "reference": "K3P7QW",
  "restaurant_id": "r_anker",
  "table_id": "t_2",
  "party_size": 4,
  "status": "confirmed",
  "starts_at_local": "2026-09-24T19:00",
  "starts_at": "2026-09-24T19:00:00+02:00",
  "ends_at": "2026-09-24T20:30:00+02:00",
  "created_at": "2026-09-21T11:04:03+00:00"
}
```

`reference` is 6 to 12 characters of `A-Z0-9`, unique across all reservations, and never changes.

| Case | Response |
|---|---|
| The table is taken for an overlapping interval | 409 `table_unavailable` |
| `starts_at_local` is not on the slot grid | 422 `not_on_slot_grid` |
| Slot outside opening hours, or the reservation would end after `closes` | 422 `outside_opening_hours` |
| `party_size` exceeds the table's `capacity` | 422 `party_exceeds_capacity` |
| `party_size` below 1, or not an integer | 422 `validation_failed` |
| `starts_at_local` is a local time that does not exist (see §9) | 422 `invalid_local_time` |
| Unknown restaurant, unknown table, or the table belongs to another restaurant | 404 `not_found` |

### `GET /reservations`

The caller's reservations, `starts_at` descending, confirmed and cancelled alike.
Return `200` with `{"reservations": [...]}`; each entry has the same shape as the
create response. An empty list is `{"reservations": []}`.

### `GET /reservations/{reference}`

One reservation. **404 if it is not the caller's** — do not leak the existence of other people's
bookings.

### `POST /reservations/{reference}/cancel`

```json
200
{ "reference": "K3P7QW", "status": "cancelled", ... }
```

Frees the table immediately: the next `GET /availability` must offer that slot again.

| Case | Response |
|---|---|
| Already cancelled | 200 with the current state — cancelling twice is not an error |
| Now is within `cancellation_cutoff_minutes` of `starts_at`, or later | 409 `cutoff_passed` |
| Not the caller's reservation | 404 `not_found` |

### `PATCH /reservations/{reference}`

Change the time, the table or the party size. Any subset of `table_id`, `starts_at_local`,
`party_size`. No idempotency key is required here.

Validation is identical to `POST /reservations`, and the same cutoff rule as cancel applies
(409 `cutoff_passed`), measured against the **current** start time. A cancelled reservation is
409 `reservation_cancelled`. A successful amendment releases the old slot and reserves the
new one together. A failed amendment leaves the original booking and its occupancy unchanged.

`reference` and `reservation_id` survive a change.

## 9. Time and DST

Local dates and times follow the restaurant's `timezone`, including daylight-saving transitions.

**Spring forward.** Local times in the skipped hour do not exist. They never appear in
availability, and booking one is 422 `invalid_local_time`.

**Fall back.** Local times in the repeated hour occur twice. **Always resolve to the first
occurrence — the one before the clocks change.** The slot appears once in availability, and the
second occurrence is not bookable.

`reservation_duration_minutes` is **absolute time**, not wall-clock. A 90-minute reservation
starting at 01:30 on a fall-back night ends 90 real minutes later, and its local `ends_at` will
read 02:00, not 03:00.

The transitions that must be handled:

| Zone | Spring forward | Fall back |
|---|---|---|
| `Europe/Berlin` | 2026-03-29, 02:00 → 03:00 | 2026-10-25, 03:00 → 02:00 |
| `America/New_York` | 2026-03-08, 02:00 → 03:00 | 2026-11-01, 02:00 → 01:00 |

Offsets must follow the IANA rules for the specified zone and date.

## 10. Export and import

The service must support `GET /_test/export` and `POST /_test/import`. Like reset, these
are unauthenticated test endpoints.
Exports may contain credentials and session tokens; handle them as private test artifacts.
Return 200 from export with a JSON object containing `track: "tablekeeper"`,
`format_version: 1` and `state` (an implementation-defined JSON object). The state format
is opaque to the caller and must be accepted unchanged by import.

Import takes that entire object and atomically replaces the service's state, returning
204. It must accept an unchanged export produced by this service. No dependency on the
source process, files, volume, port or network address is allowed. Import is replacement,
not merge; repeating it restores the exported state without duplicating anything. Invalid
JSON follows §5; missing fields, wrong track/version or an invalid state give 422
`validation_failed` without changing the destination. Test control calls have a 10-second
timeout. Export is an atomic, read-only snapshot; subsequent source writes do not change it.

Preserve accounts and hashed-password login, existing bearer tokens, fixture configuration,
reservations, references, all completed idempotent request bodies and original responses.
Identities, statuses and timestamps must not be regenerated. Failed request keys remain
reusable. Existing receipts, references, tokens and retries must remain valid after import;
replacing the state with a fresh fixture does not satisfy this requirement. Import removes
all previous destination data and credentials. Reset continues to clear all state, including
imported state. State need not survive an abrupt container restart.

## 11. Atomic reservation moves

A diner may change several bookings in one request.

`POST /reservation-moves` requires authentication and an idempotency key. Body:

```json
{"moves": [{"reference": "BOOK01", "table_id": "t_2"},
           {"reference": "BOOK02", "table_id": "t_1"}]}
```

`moves` contains 1..8 objects with distinct string references. Invalid shape or duplicate
references gives 422 `validation_failed`. Every booking must belong to the caller and the
same restaurant. Unknown/another owner's reference gives 404 `not_found`; different
restaurants give 422 `validation_failed`. No token gives 401.

Each item accepts the ordinary PATCH fields `table_id`, `starts_at_local`, `party_size`;
omitted fields retain their current values and unknown fields are ignored. The booking's
identity, owner and creation time never change. Cancelled bookings give 409
`reservation_cancelled`. Each booking's existing cutoff applies. Non-occupancy errors use
ordinary amendment codes and take precedence in input order, with cutoff errors preceding
other changes for that booking. An overlap among resulting bookings or with an unlisted
booking gives 409 `table_unavailable`. Unchanged listed bookings retain their occupancy.

Either every move commits or nothing changes: occupancy, reservation records and retry
keys. On success return 201 with `{"reservations": [...]}` in input order, including
unchanged items.
Replays return that original response with 200, even after amendments or cancellations.
No-op moves retain all existing values. Export/import preserves successful batch receipts
as well as the resulting bookings. No batch UI is required.

=====================================================================
ARCHITECT DECISIONS — STAGE 2 (verbatim, decisions/stage-2.md)
=====================================================================
# Stage 2 — architect decisions

Spec: `tablekeeper/spec/stage-2.md` on top of `stage-1.md`. All stage-1 decisions (`decisions/stage-1.md`, D1–D13) still apply unless changed here.
Deliverable: `stage-2/` in `/Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded` — created by the architect by copying `stage-1/` (no nested `.git`; copy committed separately), then extended. `stage-1/` is not modified.

## E1 Delivery of the UI
- Same single Python process serves the API and the UI. UI is a small vanilla-JS single-page app (no build step, no framework fetched at runtime) plus one CSS file, served from the image (`/assets/app.js`, `/assets/app.css`). No CDN, no web fonts from the network: use a system font stack (a serif display stack for headings, e.g. `Georgia, "Iowan Old Style", serif`, and a system sans stack for body) or fonts bundled in the image.
- `GET /`, `/signup`, `/login`, `/lookup` return the same HTML shell, `200`, `Content-Type: text/html; charset=utf-8`; the client router renders the screen from `location.pathname` and uses `history.pushState` for in-app navigation. Every other unknown path keeps the stage-1 JSON 404.
- The HTML/JS/CSS routes need no auth. `GET /reservations` etc. remain JSON API routes; the UI lives only on the four routes above plus `/assets/*`.

## E2 Session in the browser
- After signup/login the token, user id and display name are kept in `localStorage` (key e.g. `tk.session`) and in memory; every API call sends `Authorization: Bearer`. Logout clears it client-side (no server endpoint; tokens never expire). After signup/login navigate to `/`.
- `current-user` (containing the display name) and `logout-button` are in a header present on every screen while signed in. The header also has nav links: Search (`/`), Look up (`/lookup`), Sign in / Sign up when signed out.
- A 401 from the API on a signed-in call clears the session and shows `auth-error`.

## E3 Search and grid
- Search sends `GET /availability` with a monotonically increasing request sequence number; a response is applied only if it belongs to the latest search issued (older responses are dropped, and the previous request is aborted with `AbortController`). The grid, labels and any open booking form context always describe the latest search.
- Grid layout: rows = seating options, columns = slot times (`HH:MM` of `starts_at_local`). Single-table rows for **every** table of the restaurant in fixture order (cell `data-available` true iff the table id is in that slot's `available_table_ids`). Then one row per declared `combinable` pair whose summed capacity >= searched party size, in `combinable` order, labelled by human table labels joined with " + " (e.g. "Table 1 + Table 2 · seats 6"); its cells are `true` iff that pair appears in the slot's `available_options`. Cell testids: `slot-{table_id}-{HH:MM}` and `slot-{t_a}+{t_b}-{HH:MM}` (ids in `combinable` order).
- Grid must remain usable at 375 px without horizontal *page* scrolling: the grid itself may scroll horizontally inside its own container (`overflow-x:auto`), the page must not.
- `no-slots` replaces the grid when the response has no slots. Loading and error states are shown distinctly.
- Clicking an unavailable cell does nothing. Clicking an available cell while signed out shows `auth-error` (with a link to `/login`) and does not navigate.

## E4 Booking form, idempotency and uncertain outcomes
- Clicking an available cell opens `booking-form` with `booking-summary` (restaurant name, all table labels, local start `HH:MM` and date) and `booking-party-size` pre-filled from the search.
- The request body sent is: single table → `{"restaurant_id","table_id","starts_at_local","party_size"}`; pair → `{"restaurant_id","table_ids":[a,b],"starts_at_local","party_size"}` (so single-table bodies are byte-for-byte compatible with stage-1 receipts).
- The form owns an idempotency key (`crypto.randomUUID()` or equivalent) bound to its current body. The key is regenerated only when the body changes (different cell, or party size edited). Re-submitting an unchanged form reuses the key → server replay → same `confirmation-reference`, no `booking-error`, no new booking.
- Outcomes:
  - 201/200 → hide `booking-uncertain` and `booking-error`, show `confirmation` (`confirmation-reference` exactly the reference; `confirmation-details` with restaurant name, table label(s), local start; `confirmation-tables` with every table label). Booking form stays on screen.
  - 409 `table_unavailable` → show `booking-error`, no confirmation for this attempt, re-run availability for the current search, keep the form open with its selection and inputs.
  - Any other 4xx → show `booking-error` with the message; no confirmation.
  - Network failure / no response / 5xx → show non-empty `booking-uncertain`, no `booking-error`, no new confirmation; keep form, body and key unchanged so pressing `booking-submit` retries identically.
- Previous confirmation (if any) is hidden while a *new* (changed) request is pending/refused, so a confirmation never describes a different attempt.
- Responses are rendered from `table_ids` if present, else `[table_id]` (stage-1 receipts carry only `table_id`).
- All of this state is in memory; it survives an export/import on the server because tokens and receipts are preserved (no reload needed).

## E5 Lookup
- `/lookup` requires sign-in (API is caller-scoped). Signed out → `reservation-error` explaining to sign in.
- Found → `reservation-detail` with `reservation-status` exactly `confirmed`/`cancelled`, `reservation-tables` with all table labels (fetch restaurant via `GET /restaurants/{id}` for labels), restaurant name, local time. `reservation-cancel-button` present only when confirmed. Not found / refused cancel (e.g. `cutoff_passed`) → `reservation-error`.

## E6 API — combined tables
- Fixture `combinable`: optional array (default `[]`) of 2-element arrays of distinct string table ids belonging to that restaurant; no duplicate pairs (unordered). Anything else → 422 on reset/import. `GET /restaurants/{id}` includes `combinable` as given.
- Internally every reservation stores `table_ids` (list). Responses always carry `table_ids`; carry `table_id` iff exactly one table. Stored idempotency receipts are replayed verbatim (stage-1 receipts keep their stage-1 shape).
- POST/PATCH/moves table selection validation order (after body/type checks of D2):
  1. Both `table_id` and `table_ids` present → 422 `validation_failed`.
  2. `table_ids` not an array, or any element not a string → 400 `malformed_request`.
  3. `table_ids` empty → 422 `validation_failed`; duplicate ids → 422 `validation_failed`; more than two → 422 `combination_not_allowed`.
  4. Any id unknown in the restaurant → 404 `not_found`.
  5. Two ids whose unordered pair is not in `combinable` → 422 `combination_not_allowed`.
  6. Then the D5 rule chain (`invalid_local_time` → `outside_opening_hours` → `not_on_slot_grid`) → `party_exceeds_capacity` (against summed capacity) → 409 `table_unavailable` if any member overlaps a confirmed reservation (excluding the reservation itself on PATCH/moves).
- The stored `table_ids` order: for a pair, `combinable` order; response `table_ids` uses the same order.
- Availability: `available_table_ids` unchanged. `available_options`: singles with `capacity >= party_size` and free, in fixture order, then declared pairs with summed capacity >= party_size and both members free, in `combinable` order, each `{ "table_ids": [...], "capacity": n }`.
- Moves: each item may carry `table_id` or `table_ids` (same rules); overlap check covers every member table of every resulting booking.
- Seeded reservations may carry `table_id` or `table_ids` (both → 422); `status` defaults to `confirmed`.

## E7 Upgrade compatibility
- Export stays `{"track":"tablekeeper","format_version":1,"state":{...}}`. Stage-2 state adds `"schema": 2`; import accepts states without `schema` (stage-1 exports from our own stage-1 service) by converting each reservation's `table_id` to `table_ids` and defaulting every restaurant's `combinable` to `[]`. All tokens, users, references, receipts (verbatim) and counters are preserved.
- After importing a stage-1 export, a retry of a stage-1 request (same key, same body with `table_id`) replays the original receipt with 200.

## E8 Concurrency
- Same single global lock as D1 around every read-check-write; combination bookings check and insert all member tables in one critical section.

## E9 Process
- The architect has already copied `stage-1/` → `stage-2/` (no `.git`). The developer builds from the specs and these decisions only, and does not open shipped tests or run the harness. Self-test API changes with a script and the UI with a headless browser script of their own (Playwright is available in `/Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs/.venv`) kept under `stage-2/selftest/`.
- The release-verifier runs `harness run --stage 2 --mode isolated`; stage-2/ must claim stage 2 and must not pass stage 3.

## E10 Further UI/API decisions
- Grid cells carry no `disabled`/`aria-disabled`; availability is expressed by `data-available` and an `aria-label`, so a click on a taken cell is a harmless no-op rather than a blocked action.
- A 401 on the lookup screen is shown in `reservation-error` (the lookup screen's error element); a 401 during booking shows `auth-error`.
- Table labels of up to 3 characters display as "Table <label>"; longer labels display verbatim. The raw label is always contained in the text.
- On PATCH, the "both `table_id` and `table_ids` present" 422 is evaluated together with the D7 body type checks, i.e. before `reservation_cancelled`/`cutoff_passed`. On moves items it is the first field error of that item, after cancelled → cutoff (spec §11 requires cutoff errors first in moves).

## E11 Stage-1 deferrals (D14) resolved in stage 2
- Response timestamps always use a 4-digit zero-padded year (`%04d`), and offsets that are not whole minutes are rendered rounded to the minute so the result is valid RFC 3339 `±HH:MM`.
- JSON bodies with integers beyond Python's default 4300-digit limit must not be 400: parse them (e.g. `sys.set_int_max_str_digits(0)` at startup, or a parse hook) and validate as usual, so an absurd `party_size` is 422 `validation_failed` and an absurd fixture number is handled per D13 (200/204 or 422). Never 5xx, and a request must still finish within 5 s.

## E12 Clarification of E11 (developer question on fe7547c)
- A huge but well-formed positive integer `party_size` is a valid integer ≥ 1, so the stage-1 rule applies: it is 422 `party_exceeds_capacity` (after the slot rule chain), not `validation_failed`. E11's "422 `validation_failed`" example is superseded; the only E11 requirement is that such bodies are never 400 or 5xx. `validation_failed` stays reserved for `party_size` that is not an integer or is < 1.

=====================================================================
ARCHITECT DECISIONS — STAGE 1 (verbatim, decisions/stage-1.md; still apply unless changed by stage 2)
=====================================================================
# Stage 1 — architect decisions

Spec: `tablekeeper/spec/stage-1.md` (copied in full into every handoff).
Deliverable: `stage-1/` in the result repository `/Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded` — `Dockerfile`, `RUN.md`, source. No UI (that belongs to stage 2).

These resolve points the spec leaves open. Where the spec is explicit, the spec wins.

## D1 Stack and concurrency
- Python 3.12 on `python:3.12-slim`. HTTP: Starlette or FastAPI on uvicorn, **one worker process**.
- `tzdata` pip package installed in the image so `zoneinfo` never depends on host or OS zone files.
- All state in memory, in one process. Every read-check-write sequence (overlap check + insert, amend, cancel, moves, idempotency record, signup email check, reset, import, export snapshot) runs under one global lock so no two mutations interleave. No `await` inside a critical section unless the lock is an asyncio lock held across it.
- Password hashing (`hashlib.scrypt`, n=2**14, r=8, p=1, 16-byte random salt) runs **outside** the global lock in a thread pool, so 50 concurrent requests stay under 5 s. Reset hashes seeded users' passwords before taking the lock and must finish under 10 s.

## D2 Request processing order (all authenticated write endpoints)
1. Route match (unknown route → 404 `not_found`; known path with wrong method → 405 `method_not_allowed`, same error envelope).
2. Bearer token → 401 `unauthenticated`.
3. Body parses as a JSON object → else 400 `malformed_request` (arrays, scalars and invalid JSON included).
4. Endpoints requiring `Idempotency-Key`: absent/empty → 400 `missing_idempotency_key`; longer than 255 chars → 422 `validation_failed`.
5. Idempotency lookup (replay 200 / reuse 409) — before any field validation.
6. Field type checks → 400 `malformed_request` (except `party_size`, which is always 422 when invalid, and `moves` shape, which is 422).
7. Required-field and format checks → 422 `validation_failed`.
8. Resource checks and business rules (order per endpoint below).

## D3 Idempotency
- Record key = (user_id, method + path, key). The same key on a different path never conflicts.
- Stored value: canonical JSON of the parsed body (sorted keys, no whitespace) + status + original response body.
- Only successful (2xx) outcomes are stored. A 4xx/5xx leaves no record, so the key is reusable.
- Concurrent requests with the same (user, path, key) are serialised per key (in-flight lock/future): the first executes, the rest then see the stored record → 200 with the identical body (or 409 if their body differs).

## D4 Time
- "Now" is the real system clock (UTC). Fixtures in the past are bookable; cutoff uses real now.
- Cutoff: cancel/amend/move is rejected with 409 `cutoff_passed` when `now >= starts_at - cancellation_cutoff_minutes`. Cancelling an already-cancelled booking returns 200 before the cutoff check.
- `starts_at_local` must match `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$` and be a valid calendar date/time; else 422 `validation_failed`.
- Resolution: nonexistent (spring-forward gap) → 422 `invalid_local_time`. Ambiguous (fall-back) → first occurrence (`fold=0`).
- Duration is absolute: `ends_at = starts_at + duration` in UTC, then shown in the restaurant zone.
- Response timestamps: `YYYY-MM-DDTHH:MM:SS±HH:MM`, never `Z`; `created_at` is in UTC as `+00:00`.

## D5 Slot grid and opening hours
- The opening-hours entry is chosen by the weekday of the **local date** of `starts_at_local`.
- Grid: candidate wall-clock times are `opens + k*slot_minutes` (k ≥ 0) on that local date. `starts_at_local` not equal to one of them (including before `opens` on an open day) → precedence below.
- A slot is valid when its absolute start + duration <= the absolute instant of `closes` on that local date (closes resolved with fold=0; if `closes` falls in a gap, use the instant the clock jumps to).
- Availability lists every grid time that exists locally, satisfies the closes rule, once (fall-back duplicates appear once), in time order. Closed day → `"slots": []`.
- POST/PATCH rule order after the reservation's restaurant and table are resolved:
  `invalid_local_time` → closed day or before `opens` or ends after `closes` → `outside_opening_hours` → off-grid → `not_on_slot_grid` → `party_exceeds_capacity` → `table_unavailable`.
  (Concretely: if the day is closed, or the time is before opens, or start+duration > closes → `outside_opening_hours`; otherwise, if not on the grid → `not_on_slot_grid`.)

## D6 Validation details
- Signup: `email`, `password`, `display_name` are required strings (wrong type 400, missing 422). Email must be `local@domain` with exactly one `@`, both parts non-empty, no whitespace → else 422. Emails are compared case-insensitively. Password < 8 characters → 422. Empty `display_name` → 422. Duplicate email → 409 `email_taken`.
- Login: wrong type 400, missing 422, bad credentials 401 `unauthenticated`. Every login issues a new random token (≥ 32 bytes, url-safe); old tokens stay valid.
- `party_size`: must be a JSON integer (not bool, not float, not string, not null) ≥ 1 → else 422 `validation_failed`.
- `restaurant_id`, `table_id`, `starts_at_local`: wrong JSON type (including null) → 400; missing on POST → 422.
- Availability query: `restaurant_id`, `date`, `party_size` required (422). `party_size` digits only (`^[0-9]+$`) and ≥ 1; `date` a valid `YYYY-MM-DD` → else 422. Unknown restaurant → 404 `not_found`.
- IDs (including fixture IDs) > 64 characters → 422.
- `403 forbidden` is not used: other users' reservations are 404 per spec.

## D7 Reservations
- `reservation_id`: `res_<n>` (monotonic counter). `reference`: 8 chars from `A-Z0-9`, random, unique across all reservations ever (including imported/seeded), never changes.
- Response shape exactly the spec's create response (all 10 fields) for create, get, list, cancel, patch and moves.
- `GET /reservations`: caller's own, `starts_at` descending, ties by `created_at` then `reservation_id` descending.
- `GET /reservations/{reference}`, cancel and PATCH look up by `reference` (case-sensitive) and return 404 when not the caller's.
- PATCH order: 404 not caller's → body type errors 400 → 409 `reservation_cancelled` → 409 `cutoff_passed` (against the **current** start) → 422 field format/party_size → 404 table not in the reservation's restaurant → rule chain D5 → 409 `table_unavailable` (overlap ignores the reservation itself). An empty or no-op PATCH runs the same checks and returns 200 with the unchanged reservation. Success → 200 with the updated reservation; `created_at`, `reference`, `reservation_id` unchanged.
- Overlap: same table, both `confirmed`, `[a.start, a.end)` ∩ `[b.start, b.end)` non-empty using absolute instants.

## D8 Atomic moves (`POST /reservation-moves`)
- Processing order after D2 steps 1–5:
  1. `moves` must be an array of 1..8 objects, each with a string `reference`, references distinct → else 422 `validation_failed`.
  2. In input order: reference unknown or not the caller's → 404 `not_found`.
  3. All bookings in the same restaurant → else 422 `validation_failed`.
  4. In input order, for each item: 409 `reservation_cancelled`, then 409 `cutoff_passed`, then the item's field errors (400 type / 422 format / 404 table / D5 rule chain except overlap). First error wins. These checks apply to every listed item, including no-op items.
  5. Build the resulting set; overlap among the resulting listed bookings, or between any of them and an unlisted confirmed booking → 409 `table_unavailable`.
  6. Commit all changes at once; store the idempotency record in the same critical section. Response 201 `{"reservations":[...]}` in input order including unchanged items.
- Any error → nothing changes and no idempotency record is stored.

## D9 Reset, export, import
- Reset fixture: `users`, `restaurants`, `reservations` arrays (missing array → empty). Malformed JSON → 400; structurally invalid fixture → 422 with no state change. Seeded reservations take `status` if given (default `confirmed`), `created_at` if given (default now); `ends_at`/`starts_at` are computed. Reset clears users, tokens, reservations, idempotency records and counters.
- Export: `{"track":"tablekeeper","format_version":1,"state":{...}}` containing users (with password hashes), tokens, restaurants, reservations, idempotency records (full original responses), counters, and the set of issued references. Taken under the global lock as a deep copy.
- Import: validate everything first (track == "tablekeeper", `format_version` is integer 1 and not bool, `state` object with the expected structure) → else 422 with no change; then replace state atomically → 204.

## D10 Delivery
- `stage-1/Dockerfile` builds offline-runnable image; `CMD` listens on `0.0.0.0:${PORT:-8080}`.
- `stage-1/RUN.md`: `docker build -t tablekeeper-stage1 stage-1 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1`.
- No `.git` directory inside `stage-1/`; no symlinks.

## D11 Process
- The developer builds from the written spec and these decisions only; they do not read the shipped test files or run the harness.
- The reviewer reviews the committed revision against the full spec and these decisions and may run the service.
- The release-verifier runs `python -m harness run --track tablekeeper --repo /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded --stage 1 --mode isolated` itself and reads the output.

## D12 Fixture/import value validation (added after review r1)
- Seeded and imported reservation `reference` must match `^[A-Z0-9]{6,12}$` and be unique → else 422 `validation_failed`, no state change.
- ~~D12 caps 1..1440 / 0..525600~~ — **superseded by D13** (the spec states no maximum; a 10-year cutoff is legitimate).
- Any fixture or import content that cannot be processed (wrong JSON types anywhere, unhashable ids, unparseable or out-of-range timestamps) → 422 `validation_failed` with no state change. `/_test/reset` and `/_test/import` never return 5xx.
- Reset body that is not a JSON object → 400; empty body → treated as `{}`. IDs over 64 characters → 422.

## D13 Numeric bounds and string safety (added after review r2; supersedes the D12 caps)
- `cancellation_cutoff_minutes`: any integer >= 0, **no upper bound**. `slot_minutes` and `reservation_duration_minutes`: any integer >= 1, **no upper bound**. Non-integers, bools, negatives (and 0 for slot/duration) → 422.
- Large values must never cause a 5xx: do cutoff and duration arithmetic in integer epoch seconds (not `timedelta`, which overflows), treat a cutoff instant before the representable range as "cutoff already passed", skip availability slots or reject bookings (422 `outside_opening_hours`) whose end is not representable, and reject seeded reservations whose times are not representable (422).
- Strings containing a lone surrogate code point (not encodable as UTF-8) in any signup, login, fixture or import field → 422 `validation_failed` with no state change (login with such a password may stay 401). In addition, responses are encoded so that encoding can never fail (`ensure_ascii=True`).

## D14 Review r1 non-blocking notes (cf904c2 accepted by review)
- Years below 1000 in response timestamps (`strftime("%Y")` not zero-padded; pre-1893 LMT offsets truncated to whole minutes) and JSON integers over Python's 4300-digit limit giving 400 instead of 422 are **deferred**: neither affects a spec-listed behaviour (the spec's DST dates and fixtures are 2026; no 5xx occurs). They are carried as known issues into the next stage that touches time formatting or body parsing and are not grounds for rejecting stage 1.

## Status
- Stage 1 ACCEPTED at cf904c2: review r1 ACCEPT, release-verifier r1 ACCEPT (harness isolated: 120/120 stage-1, claimed stage 1; report checks/graded-s1-release-r1-1/report.json).
