# Stage 3 — architect decisions

Spec: `tablekeeper/spec/stage-3.md` on top of `stage-2.md` and `stage-1.md`. All earlier decisions (`decisions/stage-1.md` D1–D14, `decisions/stage-2.md` E1–E12) still apply unless changed here.
Deliverable: `stage-3/` in `/Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded` — created by the architect by copying `stage-2/` (no nested `.git`; copy committed separately), then extended. `stage-1/` and `stage-2/` are not modified.

## F1 Policy model and selection
- Each restaurant keeps policy 0 = its fixture rules (`slot_minutes`, `reservation_duration_minutes`, `cancellation_cutoff_minutes`, `opening_hours`, `capacities` built from table capacities in fixture order) with no `effective_from` (applies to every date before any published policy's date).
- Published policies are immutable and stored in publication order with `policy_version` 1, 2, … per restaurant.
- Selection for a local date `d`: among published policies with `effective_from <= d`, the greatest `effective_from`, ties → greatest `policy_version`; if none, policy 0.
- Everything date-dependent uses the selected policy of the booking's **local start date**: slot grid, opening hours, duration (end time), capacity rule (single and summed pair capacity), availability (`available_table_ids`, `available_options`, `explain`), booking/amendment/series validation. Cancellation and amendment cutoff use the booking's **accepted** terms.
- `GET /restaurants/{id}` keeps returning the original fixture configuration (plus `combinable`, plus a `revision` field — see F7).

## F2 `POST /restaurants/{id}/policies`
- Processing order: route → 401 → 400 body not a JSON object → 400 `missing_idempotency_key` / 422 key length → idempotency replay/409 (scope: user + method + full path) → 404 unknown restaurant → 403 `forbidden` if caller not in `manager_user_ids` → 422 validation → commit.
- Validation (all → 422 `validation_failed`, no version allocated): all six fields required; `effective_from` a real `YYYY-MM-DD` string; `slot_minutes`, `reservation_duration_minutes` integers 1..1440; `cancellation_cutoff_minutes` integer 0..10080; booleans/floats are not integers; `opening_hours` array of `{weekday, opens, closes}` per stage 1 (`HH:MM`, `closes > opens`), no duplicate weekday (empty array allowed = closed every day); `capacities` object whose keys are exactly the restaurant's table ids, values integers 1..100. Wrong JSON type for a field here is 422 (policy rule: "invalid policy is 422").
- 201 response: `{"restaurant_id", "policy_version", "effective_from", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours", "capacities"}` (normalised; unknown fields dropped). Replay → 200 identical body.
- `GET /restaurants/{id}/policies` public: `{"policies":[...]}` same objects, publication order, no policy 0. Unknown restaurant → 404.
- Fixture `manager_user_ids`: optional array of strings (default `[]`), ids ≤ 64 chars; may name users not in the fixture. Wrong shape → 422 on reset/import.

## F3 Accepted terms and revision
- `accepted_terms` = `{"policy_version", "slot_minutes", "reservation_duration_minutes", "cancellation_cutoff_minutes", "opening_hours", "capacities"}` of the selected policy at the time of acceptance (no `effective_from`). `capacities` is the full table→capacity map in fixture table order.
- Every reservation response (create, get, list, cancel, patch, moves, series) carries `revision` and `accepted_terms`. Stored idempotency receipts are replayed verbatim (older receipts have neither field).
- Revision rules: create → 1; real amendment → +1; cancel → +1 (repeated cancel no change); no-op PATCH → unchanged. Seeded and legacy-imported reservations → revision 1, terms of policy 0.
- Real amendment: check old accepted cutoff (D4), then validate all resulting fields against the policy of the resulting start date, then atomically set new terms, new end time, revision+1, append history.
- No-op detection: resulting table **set**, `starts_at_local` and `party_size` equal to current (a reversed pair is the same set). A no-op still requires a confirmed booking outside its cutoff (409 `reservation_cancelled` / `cutoff_passed`), returns 200 with the unchanged booking and records nothing.

## F4 PATCH order with `expected_revision`
404 not caller's → body type checks incl. both-present (E10) → `expected_revision` present but not a positive integer (bool, float, string, null, ≤ 0) → 422 `validation_failed` → present and ≠ current revision → 409 `stale_revision` → 409 `reservation_cancelled` → 409 `cutoff_passed` → field format → table/combination → D5 chain against resulting date's policy → capacity → 409 `table_unavailable`. All under the global lock so two concurrent amendments with one revision cannot both make a real change.

## F5 Explain
- `explain` query param: absent → stage-1/2 shape exactly (no `explain` key). Present and exactly `true` → every slot gets `explain`. Any other value (incl. `false`, `1`, `True`, empty) → 422.
- `explain` lists every table of the restaurant in fixture order: `{"table_id", "policy_version", "available", "rules":[{"rule":"capacity","holds"},{"rule":"no_overlap","holds"}]}`. `capacity` uses the selected policy's capacity; `no_overlap` uses confirmed reservations overlapping the slot interval computed with the selected policy's duration.

## F6 History and decision
- Each reservation stores an append-only list of entries `{"seq","at","event","changes","revision","accepted_terms"}`; `seq` from 1 step 1; `at` is the commit instant rendered in the **restaurant's timezone** offset (RFC 3339, seconds precision). Two writes in the same second keep `seq` order.
- `created`: changes `table_id` (or `table_ids` for a pair, `to` = list in combination order), `starts_at_local`, `party_size`, all `from: null`. `changed`: only changed fields in order table field, `starts_at_local`, `party_size`; use `table_id` when both before and after are single tables, otherwise `table_ids` with full before/after lists. `cancelled`: `changes: []`.
- Seeded reservations: one `created` entry at `created_at` (revision 1, policy-0 terms); if seeded `cancelled`, an additional `cancelled` entry at the same instant, revision stays 1. Legacy imports (stage-1/2 states) get the same synthesized history.
- `GET /reservations/{reference}/history` → `{"reference","entries":[...]}`; `GET /reservations/{reference}/decision` → `{"reference","revision","accepted_terms"}`. Both: not the caller's, unknown, or **no/invalid token** → 404 `not_found` (never 401).
- Replays record nothing.

## F7 Restaurant revision
- Each restaurant has a `revision` integer, 0 after reset/fixture load, exposed as `revision` in `GET /restaurants/{id}`. It increases by exactly 1 per committed, non-replayed operation that changes that restaurant's bookings or policies: reservation create, real amendment, cancel (not repeated), policy publication, series adoption (once for the whole series), moves batch (once for the batch, only when at least one booking really changed). Failed operations, no-ops and replays leave it unchanged. Preserved by export/import.

## F8 Series (`POST /series`, `GET /series/{id}`)
- Processing order: 401 → 400 body → idempotency key checks → replay/409 → 422 field validation (`anchor_reference` missing or not a string; `count` integer 2..12; `interval_weeks` integer 1..4; bools/floats/strings invalid) → 404 anchor unknown/not caller's → 409 `reservation_cancelled` → 409 `already_in_series` → 409 `cutoff_passed` (anchor's accepted cutoff) → occurrences 1..count-1 in index order, each validated as an ordinary new booking with the anchor's table selection and party size at anchor local date + i×interval_weeks×7 days, same local clock time, under that date's policy: `invalid_local_time` → `outside_opening_hours` → `not_on_slot_grid` → `combination_not_allowed`/`party_exceeds_capacity` → 409 `table_unavailable`. First failing occurrence decides; nothing is committed on any failure.
- On success, in one critical section: create occurrences (new ids, references, revision 1, their own terms, `created` history, `created_at` now), mark the anchor as in the series (anchor record unchanged otherwise), series `revision` 1, restaurant revision +1, store idempotency receipt. 201 body: `{"series_id","revision","interval_weeks","count","anchor_reference","occurrences":[{"index","reference","exception","reservation"}]}`.
- Series revision: +1 per real PATCH of an occurrence (which also sets `exception: true` permanently), +1 per cancel of an occurrence (not repeated, does not set exception), +1 once per moves batch that really changes ≥1 of its occurrences (each changed occurrence becomes an exception). Failures, no-ops and replays change nothing.
- `GET /series/{id}`: owner only; other user, unknown or no token → 404. Returns the same shape with current reservation states.

## F9 Moves under policies
- Per item, in input order: `expected_revision` present but not a positive integer → 422 `validation_failed` → present and ≠ current revision → 409 `stale_revision` → 409 `reservation_cancelled` → 409 `cutoff_passed` (old accepted cutoff) → field errors (both-present first, then types, formats, tables, D5 chain against the resulting date's policy, capacity). Then the occupancy check over all resulting bookings → 409 `table_unavailable`. Each real change uses F3 amendment semantics. Failure changes nothing.
- On success: each really changed booking gets revision +1, new terms/end time and one `changed` history entry; no-op items keep everything; restaurant revision +1 once (if anything changed); each affected series revision +1 once and each changed occurrence becomes a permanent exception. Replays change nothing.

## F10 Export/import
- State gains `"schema": 3`. Import accepts schema 3, schema 2 (stage-2 exports) and no schema (stage-1 exports): legacy reservations get revision 1, policy-0 terms and synthesized history (F6); restaurants get `manager_user_ids: []`, no policies, revision 0; no series. All tokens, users, references and receipts preserved verbatim.

## F11 UI
- No new screens. The grid keeps stage-2 rules and reads availability from the API (which now honours policies). The UI must tolerate the new response fields. No regressions in any stage-2 UI behaviour.

## F12 Process
- The architect has already copied `stage-2/` → `stage-3/` (no `.git`). The developer builds from specs and decisions only, does not open shipped tests or run the harness, and self-tests under `stage-3/selftest/` (API, UI regression, upgrade from stage-1 and stage-2 images).
- The release-verifier runs `harness run --stage 3 --mode isolated` and `--all`; stage-3/ must claim 3 and must not pass stage 4.

## F13 Further decisions
- A no-op PATCH or moves item (F3 no-op definition) skips opening-hours, capacity and overlap checks against the current policy (it still needs format-valid input, a confirmed booking and an unpassed cutoff), so publishing a later policy can never make an unchanged booking fail.
- `GET /restaurants/{id}` also shows `manager_user_ids` (fixture shape). Policy POST/GET responses include `restaurant_id`.
- Series response carries `count` and `anchor_reference`; an occurrence date beyond the representable calendar → 422 `outside_opening_hours`.
- UI shows a combination row whenever the API offers that pair in `available_options` for any slot, even if fixture capacities alone would hide it (policies change capacities).

## F14 Developer decisions accepted by the architect (ba2bcfc)
- Moves occupancy: really changed bookings are conflict-checked against every other confirmed booking, including no-op listed items, which keep their existing occupancy in the resulting set (spec: "Unchanged listed bookings retain their occupancy").
- `GET /series/{id}` returns 404 (never 401) for a missing or invalid token, consistent with the history/decision rule.
- Policy `effective_from` is stored as a validated zero-padded `YYYY-MM-DD` and compared as such.
- Import validates schema-3 history (sequential `seq`) and series links; corrupt state → 422 with no change.
- Policy 0 keeps the fixture's unbounded numeric values (D13); the stage-3 ranges (1..1440, 0..10080, 1..100) apply to published policies only.
