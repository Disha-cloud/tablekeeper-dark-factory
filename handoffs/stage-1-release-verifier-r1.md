@chiraanth/release-verifier HANDOFF — Release verification of graded Stage 1 revision cf904c2 (accepted by review)

TASK
Independently verify commit cf904c2 on main in /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded (same machine) before it is accepted as final. The service is in stage-1/: Dockerfile, .dockerignore, RUN.md, requirements.txt, server.py, app.py, selftest/run.py. Later commits on main only add files under decisions/ and handoffs/. Run every check yourself and read the output. Do not rely on any reported pass, including the reviewer's.

Checks to run, in order:
1. `git -C /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded status` and `git log --oneline -5`. HEAD must contain cf904c2, `git diff cf904c2 HEAD -- stage-1` must be empty, and there must be no uncommitted changes under stage-1/. stage-1/ must contain no .git directory and no symlinks (`find stage-1 -name .git -o -type l`).
2. Clean build exactly as RUN.md says, using `docker build --no-cache -t tablekeeper-stage1 stage-1` from the repo root. Start the container with only `-e PORT=<port>` and a port mapping (e.g. `docker run --rm -d -e PORT=18090 -p 18090:18090 tablekeeper-stage1`). Confirm `GET /health` returns 200 `{"status":"ok"}` within 60 s.
3. Run the shipped harness in ISOLATED mode, which is how judging runs: no outbound network, 2 vCPU, 2 GiB. From /Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs:
   `.venv/bin/python -m harness run --track tablekeeper --repo ../band-work/result-graded --stage 1 --mode isolated --out ../band-work/checks/graded-s1-release-r1-1`
   (use a fresh --out directory if that one exists). Required: the stage-1 suite all passed, and the output ends with "claimed stage: 1". The stage-2 suite is expected to fail because stage 1 does not implement stage 2. Only report it if stage 2 unexpectedly passes.
4. Run the layout check: `.venv/bin/python -m harness check ../band-work/result-graded --track tablekeeper`. Report its output. Room-log and mandate findings about files the human adds at submission (room.json, mandate harness/model lines) are outside this stage's scope. List them, but do not reject the stage for them. Reject only for findings about stage-1/.
5. Run the developer's selftest against your running container: `HOST=127.0.0.1 PORT=<port> python3 stage-1/selftest/run.py`. It must finish with 0 failures. Also check `docker logs` for the container shows no tracebacks.
6. Stop your containers and remove the images you built.

Known, deliberately deferred (decision D14, not grounds for rejection): response timestamps for years below 1000 are not zero-padded, and JSON integers over 4300 digits give 400 rather than 422.

Post ACCEPT or REJECT to me, the architect, in this room. Include the claimed-stage line, the pass/fail counts, the report path, and for any failure the exact failing check and its output.

=====================================================================
FULL SPECIFICATION (verbatim, tablekeeper/spec/stage-1.md)
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
ARCHITECT DECISIONS (verbatim, decisions/stage-1.md in the result repo; the spec wins where it is explicit)
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
