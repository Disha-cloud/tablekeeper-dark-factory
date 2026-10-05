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
