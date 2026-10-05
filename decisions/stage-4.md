# Stage 4 — architect decisions

Spec: `tablekeeper/spec/stage-4.md` on top of stages 1–3. All earlier decisions (D1–D14, E1–E12, F1–F14) still apply unless changed here.
Deliverable: `stage-4/` in `/Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded` — created by the architect by copying `stage-3/` (no nested `.git`; copy committed separately), then extended. `stage-1/`, `stage-2/`, `stage-3/` are not modified.

## G1 Restaurant revision
- F7 already matches the stage-4 definition (0 after reset; +1 per new booking, real amendment, cancel, policy publication, series adoption, moves batch with a real change, plan application, series amendment with a real change). Previews, no-ops, failures and replays never change it. Plan responses expose it as `restaurant_revision`.

## G2 Replan preview — `POST /restaurants/{id}/replans`
- Order: 401 → 400 body not an object → idempotency key checks → replay (200 original) / 409 reuse → 404 unknown restaurant → 403 non-manager → 422 field validation (`table_id` string required; `from`, `to` required RFC 3339 strings with explicit offset — `Z` or `±HH:MM`, seconds optional, no naive times; `from < to`) → 404 table not in restaurant → 422 `planning_limit` → 409 `no_feasible_plan` → 201.
- Considered bookings: every **confirmed** booking at this restaurant whose `[starts_at, ends_at)` overlaps `[from, to)` — on any table, not only the closed one. Fixed bookings: all other confirmed bookings at the restaurant.
- Options for a considered booking: every single table, then every declared pair (in `combinable` order); option rank = index in that list (singles in fixture order from 0, then pairs). An option is feasible for booking b when: (a) summed capacity under **b's accepted_terms.capacities** >= party size; (b) no member is the proposed closed table (all considered bookings overlap the closure); (c) no member is covered by a previously applied closure overlapping b's interval; (d) no member overlaps a fixed booking. Cutoffs are ignored.
- A plan assigns one feasible option per considered booking with no two overlapping considered bookings sharing a table. Choose lexicographic minimum of (number of bookings whose table **set** changes, total unused seats = Σ(option capacity under the booking's own terms − party size), rank vector ordered by ascending reference). Exact search via backtracking/branch-and-bound over bookings in reference order.
- `planning_limit` (422) only when considered bookings > 8 or the restaurant has more than 12 tables + pairs combined; everything up to the spec's guaranteed size (6 tables, 4 pairs, 6 bookings) must be solved exactly, well under 1 s.
- 201 body: `{"plan_id","restaurant_revision","closure":{"table_id","from","to"},"assignments":[{"reference","table_ids","changed"}],"moved_count","unused_seats"}`; `from`/`to` echoed normalised as RFC 3339 in the restaurant's timezone offset; assignments in ascending reference order; `table_ids` in declared combination order. Zero considered bookings is a feasible empty plan.
- The plan is stored (restaurant id, closure, assignments, the restaurant revision at preview, applied=false). Nothing else changes; restaurant revision unchanged.

## G3 Apply — `POST /restaurants/{id}/replans/{plan_id}/apply`
- Order: 401 → 400 body → key checks → replay (200 original, even after later changes) / 409 reuse → 404 unknown restaurant → 403 non-manager → 404 plan unknown or of another restaurant → 409 `plan_already_applied` (applied under another key) → 409 `stale_plan` (restaurant revision ≠ revision at preview) → apply.
- Apply atomically under the global lock: record the closure (table, interval, plan_id); for each considered booking whose set changed: set `table_ids`, revision +1, append history `{"event":"reassigned","plan_id",…,"changes":[{"field":"table_ids","from":[…],"to":[…]}]}` with unchanged accepted_terms, start, end — the `reassigned` change is **always** `table_ids` with complete before/after lists, even single→single (unlike PATCH `changed` entries, which keep `table_id` for single→single per stage 3); unmoved bookings untouched; each series with ≥1 moved member: series revision +1 (exception flags unchanged); restaurant revision +1; mark plan applied; store receipt.
- 201 body: `{"plan_id","restaurant_revision","reservations":[…]}` with every considered booking (current state) in reference order.
- Closures block their table for `[from,to)`: availability (singles and pairs containing it), `explain` `no_overlap` false, and 409 `table_unavailable` on create, PATCH, moves, series adoption and series amend. Closures at another restaurant never affect this one.

## G4 Series amend — `POST /series/{series_id}/amend`
- Order: 401 → 400 body → key checks → replay / 409 reuse → 404 series unknown or not the caller's → 422 (`expected_revision` positive integer, `from_index` integer 0..count-1, `local_time` matching `^([01]\d|2[0-3]):[0-5]\d$`; booleans invalid; missing → 422) → 409 `stale_revision` (vs series revision) → per eligible occurrence in index order: no-op check (resulting `starts_at_local` equals current → skip, no checks), else 409 `cutoff_passed` (old accepted cutoff) → resulting date's policy: `invalid_local_time` → `outside_opening_hours` → `not_on_slot_grid` → `party_exceeds_capacity` → then occupancy over all resulting bookings (other bookings, unchanged occurrences, applied closures) → 409 `table_unavailable`.
- Eligible: index ≥ from_index, status confirmed, `exception` false. Resulting start = the occurrence's **scheduled local date** (anchor's local date at adoption + index × interval_weeks × 7 days, stored per occurrence) at `local_time`, fold=0.
- Success (201, current series response): each changed occurrence gets new start/end/terms, revision +1, one `changed` history entry; series revision +1 and restaurant revision +1 once if anything changed; exceptions not marked. All-no-op or empty eligible set → 201 with no counter changes. Failure changes nothing and stores no receipt.

## G5 Export/import
- State gains `"schema": 4` with closures, plans (incl. applied flag and the key/receipt) and per-occurrence scheduled dates. Import accepts schemas 1–4. For schema-3 series, derive scheduled dates as: date of occurrence 1's `created` history entry `starts_at_local` + (i−1) × interval_weeks × 7 days. Legacy restaurants have no closures/plans.

## G6 UI
- No new screens. Availability grid, confirmation and lookup always render from server data, so an applied plan (moved tables, closures) is reflected on the next fetch. No stage-2/3 UI regression.

## G7 Process
- The architect has already copied `stage-3/` → `stage-4/`. The developer builds from specs and decisions only, does not open shipped tests or run the harness, self-tests under `stage-4/selftest/` (API incl. planner optimality on small brute-force-checked cases, UI regression, upgrade from stage-1/2/3 images).
- Release-verifier runs `harness run --stage 4 --mode isolated` and `--all`; stage-4/ must claim 4.

## G8 Further decisions
- Closure instants with fractional seconds are accepted and floored to whole seconds.
- Plan ids are `plan_<n>` from an exported/imported counter.
- Preview field-type problems (non-string `table_id`, missing `from`/`to`) are 422 per G2.
- Imported schema-4 closures/plans referencing an unknown restaurant or table → 422.
- Restaurants imported from stage-1/2 exports have no managers, so replans on them return 403 until a reset/fixture names managers.

## G9 Replan instant representability
- During G2 validation (before planning, before allocating a plan id or storing anything), each instant must render in the restaurant's timezone without error and with a whole-minute UTC offset (valid RFC 3339). Otherwise → 422 `validation_failed`. A preview that fails for any reason allocates no plan id and stores no plan. Build the full 201 body before committing the plan.

## G10 Developer decisions accepted by the architect (d6266e7)
- A closure window with zero considered bookings gives a feasible empty plan (201, `assignments: []`, `moved_count` 0, `unused_seats` 0); applying it records the closure and increments the restaurant revision once.
- The planner has a 4-second wall-clock guard; if exceeded (never within the guaranteed size) → 422 `planning_limit`, nothing stored.
- Closure instants with fractional seconds are floored to whole seconds (as G8).
- A series amend whose eligible set is empty or all no-op returns 201 and stores its idempotency receipt (it is a successful write), so a replay returns 200; no counters change.
- Imported schema-4 plans whose assignments name unknown reservation references → 422 with no state change.
- Restaurants imported from stage-1/2 exports have `manager_user_ids: []`, so replans there return 403 (as G8).

## Status
- Stage 4 ACCEPTED at d6266e7: review r1 ACCEPT (no defects; independent brute force 420 cases, 0 mismatches), release-verifier r1 ACCEPT (harness isolated: stage-1 120/120, stage-2 25/25, stage-3 7/7, stage-4 6/6, claimed stage 4; `--all`: stage-1/2/3/4 claim 1/2/3/4; reports checks/graded-s4-release-r1-1 and -all).
