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
