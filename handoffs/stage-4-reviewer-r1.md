@chiraanth/reviewer HANDOFF — Review graded Stage 4 revision d6266e7 (tablekeeper: seating changes after a table closure, recurring amendments)

TASK
Independently review commit d6266e7 on main in the Git repo /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded (same machine as you). The code is in stage-4/: Dockerfile, .dockerignore, requirements.txt, server.py, app.py, static/ (unchanged from stage-3), RUN.md, selftest/run.py (stage-1/2 regression), selftest/s3.py (stage-3 regression), selftest/s4.py (stage 4), selftest/ui.py (UI regression), selftest/upgrade.py (stage-1/2/3 images → stage-4). stage-1/ must be unchanged since cf904c2, stage-2/ since 74fb95b, stage-3/ since ba2bcfc.
- Review against the FULL SPECIFICATIONS and ARCHITECT DECISIONS below — not against the developer's summary. Where a spec is explicit it wins over the decisions. G10 records the developer's own decisions that I accepted.
- Read the stage-4/app.py diff against stage-3/app.py in full, plus enough surrounding code to judge it. Build and run the image yourself (`cd /Users/chiraanths/Desktop/lockin/Band-hack/band-work/result-graded && docker build -t tablekeeper-graded-s4-review stage-4 && docker run --rm -d --cpus 2 --memory 2g -e PORT=18084 -p 18084:18084 tablekeeper-graded-s4-review`) and probe it with your own requests.
- Pay particular attention to:
  - Replan preview: G2 error order (401/400/key/replay/404/403/422/404 table/planning_limit/no_feasible_plan); instant validation incl. offsets, `from < to`, G9 representability; considered = every confirmed booking overlapping `[from,to)` on any table (half-open boundaries); feasibility under each booking's OWN accepted_terms capacities, previous closures, fixed bookings, closed table; cutoffs ignored; exact optimum by (moved count, unused seats, rank vector by ascending reference) — verify with your own independent brute force on random small cases up to 6 tables / 4 pairs / 6 bookings; response shape; preview changes nothing (no closure, revision, history) and is fast.
  - Apply: G3 order; stale_plan on any intervening restaurant revision (but not from another restaurant's activity); plan_already_applied under another key; replay 200 even after later changes; atomic; closure recorded; moved bookings revision +1 and exactly one `reassigned` history entry with `plan_id` and a `table_ids` change with complete lists for EVERY shape — single→single, single→pair, pair→single and pair→pair (the developer's selftest does not cover pair→pair; please construct one); accepted terms, start and end unchanged; unmoved bookings untouched; series revision +1 once per plan with exceptions preserved; restaurant revision +1 once; concurrent applies never partial.
  - Closures afterwards: availability singles and pairs, explain `no_overlap` false, 409 `table_unavailable` on create, PATCH, moves, series adoption and series amend; other restaurants unaffected; confirmation/lookup UI reflect moved tables.
  - Series amend: G4 order; 422 cases (booleans, ranges, `local_time` format); stale_revision before any cutoff/booking check; eligible set (≥ from_index, confirmed, not exception); scheduled local dates (not current moved dates); no-op; per-occurrence policy; DST gap/fold; occupancy vs other bookings, unchanged occurrences, closures and each other; non-occupancy errors in index order before table_unavailable; success counters (reservation, series, restaurant once), no exceptions marked; all-no-op/empty → 201 without counter changes; replay; concurrency from one revision.
  - Export/import: schema-4 round trip including plans/closures/receipts; import of real stage-1, stage-2 and stage-3 exports (build those images yourself), incl. imported series with moved and cancelled occurrences; replan and amend on imported data.
  - No regression in stage-1/2/3 API or UI behaviour; no 5xx under any input or 50-way concurrency; RUN.md/Dockerfile; no .git/symlinks.
- You may also run the harness for evidence: from /Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs run `.venv/bin/python -m harness run --track tablekeeper --repo ../band-work/result-graded --stage 4 --out ../band-work/checks/graded-s4-review-r1-<n>` (new --out each time). Directional only; the graded suite is larger. Clean up containers and images afterwards.
- Do not modify code. Post your verdict in this room addressed to me (the architect): ACCEPT, or REJECT with a numbered list of specific, concrete defects (spec section, input, expected vs actual). I route fixes back to the developer.

=====================================================================
STAGE 4 SPECIFICATION (verbatim, tablekeeper/spec/stage-4.md)
=====================================================================
# Tablekeeper — Stage 4: seating changes and recurring amendments

Extends all earlier stages, including stage-1 atomic reservation moves, stage-2 table
combinations and stage-3 policies and recurring agreements. All earlier requirements apply.

## Seating changes after a table closure

When a table becomes unavailable, a manager can review a proposed seating arrangement
before applying it. Customers must keep their booking times, party sizes and accepted terms.
No new screens are required. Existing availability, confirmation and lookup screens must
reflect an applied plan.

`POST /restaurants/{id}/replans` requires a manager and an idempotency key. Body:

```json
{"table_id": "t_2", "from": "2026-09-28T18:00:00+02:00",
 "to": "2026-09-28T23:00:00+02:00"}
```

The instants have explicit offsets and `from < to`; invalid interval is 422
`validation_failed`, unknown table 404. The proposed closure is the half-open interval
`[from,to)`. Consider every confirmed booking at this restaurant overlapping that interval.
Other bookings retain their assignments.
Planning must support up to 6 tables, 4 declared pairs and 6 considered bookings; larger
inputs may return 422 `planning_limit`. Each considered booking must retain its reference,
owner, party size, start, end and accepted terms. Assign it a single or a declared pair with
enough capacity under **its own accepted terms**, without conflicts with fixed bookings,
other assignments, previously applied closures or the proposed closure. Diners' cancellation
cutoffs do not prevent an operator repair. No booking may disappear or be cancelled.

Among feasible plans minimize, in order:

1. Number of bookings whose table set changes.
2. Total unused seats across all considered bookings (capacity minus party size).
3. The vector of option ranks in ascending reservation-reference order. Singles are ranked
   first in fixture order, then pairs in declared order, starting at 0.

Returns 201:

```json
{"plan_id": "opaque", "restaurant_revision": 4,
 "closure": {"table_id": "t_2", "from": "...", "to": "..."},
 "assignments": [{"reference": "ABC12345", "table_ids": ["t_1"], "changed": true}],
 "moved_count": 1, "unused_seats": 0}
```

Assignments include every considered booking in reference order. A restaurant revision starts
at 0 after reset and increments once for each successful new booking, real amendment,
cancellation, policy publication or plan application. No-op writes, failures, previews and
replays do not increment it. Preview stores only a plan: no closure, occupancy, reservation
revision or history changes. No feasible plan gives 409 `no_feasible_plan`, changing nothing.

`POST /restaurants/{id}/replans/{plan_id}/apply`, body `{}`, requires a manager and an
idempotency key. Return 201 with `{"plan_id": "...", "restaurant_revision": 5,
"reservations": [...]}`; reservations include every considered booking in reference order.
Unknown plan or one from another restaurant is 404. Any intervening restaurant revision
invalidates the plan: 409 `stale_plan`, changing nothing. A plan already applied under a
different key gives 409 `plan_already_applied`; replay of the successful key returns the
original response with 200, even after later changes. Application is atomic.

Application records the closure and all assignments together. Each moved booking increments
its revision once and gains one `reassigned` history entry with a `table_ids` change and
`plan_id`; accepted terms and times remain identical. Unmoved bookings gain nothing. The
restaurant revision increments once for the **whole plan**. Closures thereafter exclude
singles and pairs from availability and reject creates/amendments with 409 `table_unavailable`.
In explanations, `no_overlap` is false for a closure as for a conflicting booking.

Concurrent applications must not leave partially moved bookings. A closure at another
restaurant does not invalidate this plan.

## Amend recurring reservations

`POST /series/{series_id}/amend` is an owner-only idempotent write. Unknown or another owner's
series is 404; no token is 401. Body:

```json
{"expected_revision": 3, "from_index": 2, "local_time": "20:00"}
```

Revision must be a positive integer; from_index an integer in 0..count-1; local_time exactly
HH:MM in 00:00..23:59. Booleans are invalid integers. Invalid input gives 422
`validation_failed`; a mismatched series revision gives 409 `stale_revision` before any
occurrence's cutoff or booking validation. Unknown fields are ignored.

Consider indices at or after from_index, excluding cancelled occurrences and those marked
exception. Change their clock time on their original scheduled local dates, retaining each
reference, owner, party size and current table selection. A change with identical
resulting fields is a no-op and retains its terms. Each real change checks its old accepted
cutoff, then adopts the policy for its resulting start date, just like an individual PATCH.

The resulting occurrences must not conflict with unchanged occurrences, other bookings
or applied closures. On failure, histories, idempotency records and all revisions remain
unchanged. Non-occupancy errors take precedence in occurrence-index order; otherwise an
occupancy conflict returns `table_unavailable`.

On success return 201 with the current series response. Each changed occurrence gains one
ordinary changed history entry and one reservation revision. The series and restaurant
revisions each increase once for the entire operation if anything changed. Series amendments
do not mark exceptions. All-no-op or empty eligible sets succeed without changing revisions.
Replay returns the original response with 200 even after further edits or cancellations.

Seating repairs may move series occurrences. They preserve their exception flags, scheduled
dates, identities and accepted terms. Each affected series revision increases once per plan
application if at least one member moved.
Concurrent amendments from the same expected revision may not both make a real change.

A stage-4 service must accept exports produced by the same team's stages 1–3. These
operations must support imported series, including moved and cancelled occurrences.
Earlier booking and series receipts, histories and retries remain valid.

=====================================================================
STAGE 3 SPECIFICATION (verbatim, tablekeeper/spec/stage-3.md — still applies)
=====================================================================
# Tablekeeper — Stage 3: booking policies, history and recurring reservations

The requirements from stages 1 and 2 continue to apply, with the additions below.
Numbered section references such as §5 and §7 refer to `stage-1.md`.

Restaurants can publish dated booking policies. Diners can see why a table is unavailable,
view their reservation history and arrange recurring bookings.

## Availability explanations

Whether a table is available for a slot is decided by two rules, each independent of the other:

| Rule | Holds when |
|---|---|
| `capacity` | `party_size` is at most the table's `capacity` |
| `no_overlap` | no confirmed reservation on that table overlaps the slot's interval |

A table is available exactly when both hold. `available_table_ids` is unchanged in meaning.

```http
GET /availability?restaurant_id=r_anker&date=2026-09-24&party_size=4&explain=true
```

`explain` is optional. Its only accepted value is `true`; any other value, including `false`,
`1` and the empty string, is 422 `validation_failed`. **Without it the response keeps stage
1's shape** — no explanation fields appear. Published policies can change the slot values.

With it, every slot carries one further field:

```json
{ "starts_at_local": "2026-09-24T18:00",
  "starts_at": "2026-09-24T18:00:00+02:00",
  "available_table_ids": ["t_2"],
  "explain": [
    { "table_id": "t_1", "policy_version": 0, "available": false,
      "rules": [ { "rule": "capacity", "holds": false },
                 { "rule": "no_overlap", "holds": true } ] },
    { "table_id": "t_2", "policy_version": 0, "available": true,
      "rules": [ { "rule": "capacity", "holds": true },
                 { "rule": "no_overlap", "holds": true } ] }
  ] }
```

1. **Every table of the restaurant appears exactly once**, available or not, in fixture order —
   the same order `available_table_ids` uses.
2. **Both rules are reported for every table**, in the order above. A rule that holds is
   reported holding; a table excluded by both reports both false. No rule may be omitted.
3. **`available` is true exactly when both rules hold**, and the `table_id`s whose `available`
   is true are exactly `available_table_ids`, in the same order.
4. A closed day still returns `"slots": []`, and a slot with no available table still appears —
   now with a full `explain` for every table.

## Reservation history

```http
GET /reservations/{reference}/history
```

The reservation's own record, oldest first. Only its owner may read it; anyone else, signed in
or not, gets the same 404 `not_found` that §8 gives for a reservation that is not theirs. A
cancelled reservation still has its history.

The example below shows the event fields; every entry also carries `revision` and
`accepted_terms` as specified under “Policies and accepted terms”.

```json
{ "reference": "ABC12345",
  "entries": [
    { "seq": 1, "at": "2026-09-17T12:00:00+02:00", "event": "created",
      "changes": [ { "field": "table_id", "from": null, "to": "t_2" },
                   { "field": "starts_at_local", "from": null, "to": "2026-09-24T19:00" },
                   { "field": "party_size", "from": null, "to": 4 } ] },
    { "seq": 2, "at": "2026-09-17T12:05:00+02:00", "event": "changed",
      "changes": [ { "field": "table_id", "from": "t_2", "to": "t_3" } ] },
    { "seq": 3, "at": "2026-09-17T12:09:00+02:00", "event": "cancelled", "changes": [] } ]
}
```

1. **`seq` starts at 1 and increases by exactly 1**, so the order is total even when two writes
   land in the same second. Entries are returned in `seq` order, which is also `at` order.
2. **`created` names all three fields**, each with `"from": null`.
3. **`changed` names only the fields that actually changed**, in the order `table_id`,
   `starts_at_local`, `party_size`. A `PATCH` that sets a field to the value it already has
   changed nothing: it still succeeds, and it records **no entry at all**.
4. **`cancelled` carries an empty `changes`**, and nothing follows it.
5. Replaying an idempotent `POST /reservations` records nothing — a replay returns the original
   response and does not re-run the operation (§7).

## Existing screens

No new screens are required for explanations or history. The availability grid continues
to follow the stage-2 rules.

## Policies and accepted terms

Restaurants may now declare `manager_user_ids` in their reset fixture (default `[]`). Only
these users may publish policies. Unknown restaurant is 404;
an authenticated non-manager is 403 `forbidden`; no token is 401. This extends stage 1's
minimal permissions; managers do not gain access to other diners' private lookup/history.

`POST /restaurants/{id}/policies` requires an idempotency key, with stage 1's replay rules.
It accepts a **complete policy**, not a patch:

```json
{
  "effective_from": "2026-09-28",
  "slot_minutes": 30,
  "reservation_duration_minutes": 120,
  "cancellation_cutoff_minutes": 60,
  "opening_hours": [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
  "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}
}
```

Returns 201 with the supplied policy plus `policy_version`, an integer starting at 1 and
increasing by one per restaurant. Failed writes and replays allocate no version. Policy 0
is the original fixture's rules and applies before any published policy. Policies are
immutable. Publication order may differ from effective-date order. For a booking's **local
start date**, choose the greatest `effective_from` not later than that date; ties choose
the greatest `policy_version`. A new same-date policy supersedes the old one for future
decisions, without changing any accepted reservation. Effective dates may be in the past;
publication never retroactively edits a booking.

All fields above are required. `effective_from` is an actual `YYYY-MM-DD` date; grid and
duration are integers 1..1440; cutoff is an integer 0..10080; booleans are not integers.
Opening hours follow stage 1 and contain no duplicate weekdays. `capacities` names **exactly**
the restaurant's table ids with integer capacities 1..100. Invalid policy is 422
`validation_failed`, with no version or state change. Table ids, labels, timezone and
declared combinations cannot be changed by a policy. Unknown fields are ignored.

`GET /restaurants/{id}/policies` is public and returns `{"policies": [...]}` in publication
order, omitting policy 0. The ordinary restaurant detail still returns its original fixture
configuration. Availability and booking decisions use the selected policy, not that detail.
With `explain=true`, each table explanation additionally identifies its `policy_version`.

Every reservation response gains `revision` (1 at creation) and `accepted_terms`:

```json
{"policy_version": 0, "slot_minutes": 30,
 "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
 "opening_hours": [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}],
 "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}
```

These are a snapshot of the entire selected policy, excluding `effective_from`. Seeded
bookings start at revision 1 under policy 0. Responses to old idempotency keys remain the
original response, including the original revision and terms.

- A policy publication does not change existing bookings, their end times, or their history.
- Cancel checks the accepted cutoff, against the current start.
- A real diner amendment (time, tables or party size) checks the old accepted cutoff first,
  then validates **all** resulting fields against the policy applicable to the resulting start
  date. It atomically replaces accepted terms and end time and increments revision once.
- A no-op amendment retains terms, end time and revision and records no history. It still
  requires a confirmed, editable booking.
- Failed amendments change nothing. Cancel increments revision once; repeated cancel does not.
- `PATCH` optionally accepts `expected_revision`. A positive integer differing from the current
  revision gives 409 `stale_revision` before cutoff/validation; invalid type/range gives 422.
  Omission retains stage 1 semantics. Two concurrent amendments using one revision: at most one
  real change succeeds. Unrelated unknown fields remain ignored.

Each history entry additionally carries the reservation's resulting `revision` and complete
`accepted_terms`. Old entries never acquire newer terms. `GET /reservations/{reference}/decision`
returns `{"reference": "...", "revision": 1, "accepted_terms": {...}}` for the current booking,
including after cancellation, with history's owner-only 404 rule. History and decision return
404 even without authentication, resolving the exception to stage 1's general 401 rule.

## Recurring reservations

`POST /series` adopts an existing reservation as occurrence zero of a recurring agreement.
An idempotency key is required. Body:

```json
{"anchor_reference": "ABC12345", "count": 8, "interval_weeks": 1}
```

The anchor must belong to the caller, be confirmed and satisfy its accepted cancellation
cutoff. Unknown or another owner's anchor gives 404 `not_found`; cancelled gives 409
`reservation_cancelled`; already adopted gives 409 `already_in_series`. `count` is an integer
2..12 including the anchor; `interval_weeks` is an integer 1..4. Invalid values, including
booleans, give 422 `validation_failed`. No token gives 401.

Occurrence zero is the anchor itself: its reference, identity, revision, terms, history,
timestamps and original idempotent response remain unchanged. Occurrence i starts on the
anchor's local calendar date plus i × interval_weeks × 7 days, at the same local clock time.
Each generated occurrence independently selects its date's policy, including duration and
capacity, and obeys ordinary opening, DST and occupancy rules. A nonexistent local time
rejects the entire adoption with `invalid_local_time`; repeated times use stage 1's first
occurrence rule. Generated occurrences use the anchor's party size and table selection.
No partial series, reservations, histories, counters or idempotency claim survive failure.
The first failing occurrence in index order determines the ordinary booking error.

Return 201:

```json
{"series_id": "opaque", "revision": 1, "interval_weeks": 1,
 "occurrences": [{"index": 0, "reference": "ABC12345", "exception": false,
                  "reservation": {"...": "ordinary reservation response"}}]}
```

The array includes all count occurrences in index order. Each has a distinct ordinary
reservation reference; references and indices never change when dates or tables change.
Occurrences appear in ordinary reservation lists, occupy tables, and have ordinary histories.
`GET /series/{series_id}` returns this shape with current reservation states. Only the owner
may read it: another user or no token gives 404 `not_found`.

A real individual PATCH permanently marks that occurrence as `exception: true` and increments
the series revision once; a no-op or failure changes neither. Cancellation increments the
series revision once, retaining the cancelled occurrence, but does not mark it as an
exception; repeated cancel does nothing.
Cancelling the anchor does not cancel its siblings. Ordinary cutoff and revision checks still
apply. Adoption increments the restaurant revision once for the whole operation. Replays
return the original series response, even after later changes, and change no counter.
Series creation adds one idempotent write path. Unknown fields are ignored.

A stage-3 service must accept exports produced by the same team's stage-1 or stage-2
service. Adoption must work on reservations imported this way. Existing confirmation links,
sessions and original booking retries remain valid.

## Combined-table history

Stage 3's accepted terms apply to combinations too; capacity is the sum of the **selected
policy's** capacities. In history, retain stage-3 fields for single-to-single operations.
For a creation of a pair, replace the `table_id` change by `table_ids` (from null to the pair).
For a change involving a pair, use `table_ids` (complete before/after lists) instead of
`table_id`. Table-set order is the declared combination order. A reversed input pair names
the same set and is not an amendment on its own. Policy selection, revision and replay rules
are unchanged.

## Collective moves under policies and agreements

Each real change in `POST /reservation-moves` uses individual PATCH semantics: check the
old accepted cutoff, then adopt the resulting date's policy. Per-move `expected_revision`
is optional and follows PATCH validation and stale-revision rules. A no-op retains its
terms and history. All resulting bookings must satisfy amendment and occupancy rules;
failure leaves every booking unchanged. Every changed booking gains one revision and
changed history entry; the restaurant revision increases once for the whole batch.
Each affected series revision increases once,
and each changed series occurrence becomes a permanent diner exception. A
failed batch or replay changes no revisions, histories or exception flags.

=====================================================================
STAGE 2 SPECIFICATION (verbatim, tablekeeper/spec/stage-2.md — still applies)
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
ARCHITECT DECISIONS — STAGE 4 (verbatim, decisions/stage-4.md)
=====================================================================
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

=====================================================================
ARCHITECT DECISIONS — STAGE 3 (verbatim, decisions/stage-3.md; still apply unless changed)
=====================================================================
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

## Status
- Stage 3 ACCEPTED at ba2bcfc: review r1 ACCEPT (no defects), release-verifier r1 ACCEPT (harness isolated: stage-1 120/120, stage-2 25/25, stage-3 7/7, claimed stage 3; `--all`: stage-1/2/3 claim 1/2/3; reports checks/graded-s3-release-r1-1 and -all).

=====================================================================
ARCHITECT DECISIONS — STAGE 2 (verbatim, decisions/stage-2.md; still apply unless changed)
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

## Status
- Stage 2 ACCEPTED at 74fb95b: review r1 REJECT (stray "null" in header) → fix round 1 → review r2 ACCEPT, release-verifier r1 ACCEPT (harness isolated: stage-1 120/120, stage-2 25/25, claimed stage 2; stage-1 still claims 1; reports checks/graded-s2-release-r1-1 and -s1).

=====================================================================
ARCHITECT DECISIONS — STAGE 1 (verbatim, decisions/stage-1.md; still apply unless changed)
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
