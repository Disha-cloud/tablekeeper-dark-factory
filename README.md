# Tablekeeper

Tablekeeper is a restaurant reservation service. Diners search availability, book a table and
get a confirmation reference. They can then cancel or amend that booking, or move several
bookings together atomically. Each restaurant has its own tables, opening hours, slot grid and
cancellation policy. Two confirmed reservations can never hold the same table at overlapping
times, even under concurrent requests. Retries and rejected requests never create duplicate
or partial bookings.

The service was built in four stages. Each stage is a complete, independently buildable
folder (`stage-1/` … `stage-4/`), and every stage includes everything before it. A four-seat
agent factory produced all the code (see [FACTORY.md](FACTORY.md)).

## Presentation

- [Presentation slides](https://drive.google.com/file/d/1ep1vJUw_nLvg-NmDnZ1Hw8Xb0EB_yn6d/view?usp=sharing)
- [Presentation and demo video](https://drive.google.com/file/d/1cjMDOAg_PpCGJWwZcF0THoYp4QxtEdTh/view?usp=sharing)

## Repository layout

| Path | Contents |
|---|---|
| `stage-1/` … `stage-4/` | One deliverable per stage: `Dockerfile`, `RUN.md`, `app.py`, `server.py`, `requirements.txt`, `selftest/`, plus `static/` (browser UI) from stage 2 on |
| `decisions/stage-N.md` | The architect's written decisions on points the stage spec leaves open (D1–D14, E1–E12, F1–F14, G1–G10) |
| `handoffs/` | The full handoff sent to each seat for each step (developer, reviewer, release-verifier, plus Stage 2's fix round) |
| `mandates/` | The four seat mandates (architect, developer, reviewer, release-verifier) |
| `room.json` | Full, unfiltered export of the BAND room `tablekeeper-graded` in which the factory ran |

## Build and run

Every stage follows the same pattern (from that stage's `RUN.md`). Run it from the
repository root:

```
docker build -t tablekeeper-stage1 stage-1 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage1
docker build -t tablekeeper-stage2 stage-2 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage2
docker build -t tablekeeper-stage3 stage-3 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage3
docker build -t tablekeeper-stage4 stage-4 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage4
```

- The service listens on `0.0.0.0:$PORT` (default 8080). `GET /health` returns `200 {"status":"ok"}`.
- No setup is needed to run the service or pass the harness, and the service makes no outbound
  network calls at run time. From stage 2 on, all HTML, JS and CSS are served from the image,
  and the UI uses system fonts only.
- The service starts **empty by design**: there are no restaurants or users until a fixture is
  loaded (`GET /restaurants` returns `{"restaurants":[]}`). To try it by hand, seed demo data
  first; see [Seed demo data for manual testing](#seed-demo-data-for-manual-testing).
- From stage 2 on, the browser UI is at `/` (search and availability grid), `/signup`, `/login`
  and `/lookup`.
- Stack: Python 3.12 on `python:3.12-slim`. The app is a plain ASGI app (`app.py`, standard
  library only) served by uvicorn with one worker (`server.py`). Its only dependencies are
  `uvicorn` and `tzdata`. All state is held in memory, and every read-check-write runs under
  one global lock (decision D1).

### Seed demo data for manual testing

**To seed demo data for manual testing**, run this once after the container is up (port 8080),
before deciding that the service is empty or broken. It works for every stage and should print
`204`:

```
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8080/_test/reset \
  -H 'Content-Type: application/json' \
  -d '{
  "users": [
    {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}
  ],
  "restaurants": [
    {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
     "slot_minutes": 30, "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
     "opening_hours": [
       {"weekday": "mon", "opens": "17:00", "closes": "23:00"},
       {"weekday": "tue", "opens": "17:00", "closes": "23:00"},
       {"weekday": "wed", "opens": "17:00", "closes": "23:00"},
       {"weekday": "thu", "opens": "17:00", "closes": "23:00"},
       {"weekday": "fri", "opens": "17:00", "closes": "23:00"},
       {"weekday": "sat", "opens": "12:00", "closes": "23:00"},
       {"weekday": "sun", "opens": "12:00", "closes": "22:00"}
     ],
     "tables": [
       {"id": "t_1", "label": "Window 1", "capacity": 2},
       {"id": "t_2", "label": "Booth 2", "capacity": 4},
       {"id": "t_3", "label": "Booth 3", "capacity": 4}
     ],
     "combinable": [["t_2", "t_3"]]}
  ],
  "reservations": [
    {"id": "res_demo1", "reference": "DEMO0001", "user_id": "u_ada", "restaurant_id": "r_anker",
     "table_id": "t_2", "party_size": 4, "starts_at_local": "2027-06-18T19:00"}
  ]
}'
```

`POST /_test/reset` **replaces all in-memory state** with this fixture, so anything booked
before it is gone. You can re-run it at any time to start over. What to try once it is seeded:

- **API (all stages):** `GET /restaurants` lists Zum Anker. Log in with
  `POST /auth/login` `{"email":"ada@example.com","password":"correct horse"}`, then
  `GET /reservations/DEMO0001` with `Authorization: Bearer <token>` returns Ada's booking.
  Stage 1 is API only.
- **Browser UI (stage 2 on):** open http://localhost:8080/, choose Zum Anker, the date
  2027-06-18 and a party of 4. From 18:00 to 20:00, Booth 2 (`t_2`) is taken by `DEMO0001`, so only
  Booth 3 is offered. For a party of 8, the only option is Booth 2 + Booth 3 combined (stage 2
  adds combined tables), and it is unavailable from 18:00 to 20:00 for the same reason.
- **Look up and cancel:** sign in at `/login` as `ada@example.com` / `correct horse`, then
  enter `DEMO0001` at `/lookup`.

### Self-tests

Each stage ships the developer's own self-tests. They need the container running on port 8080.

| Stage | Commands |
|---|---|
| 1 | `python3 stage-1/selftest/run.py` |
| 2 | `python3 stage-2/selftest/run.py` (API) · `python stage-2/selftest/ui.py` (Playwright UI) · `python3 stage-2/selftest/upgrade.py` (stage-1 export → stage 2) |
| 3 | `python3 stage-3/selftest/run.py` (stage 1/2 API regression) · `python3 stage-3/selftest/s3.py` (stage 3 API) · `python stage-3/selftest/ui.py` · `python3 stage-3/selftest/upgrade.py` (from stage-1/2 images on ports 8081/8082) |
| 4 | `python3 stage-4/selftest/run.py` · `python3 stage-4/selftest/s3.py` · `python3 stage-4/selftest/s4.py` (planner vs. brute force, closures, series amend, schema 4) · `python stage-4/selftest/ui.py` · `python3 stage-4/selftest/upgrade.py` (from stage-1/2/3 images on ports 8081–8083) |

### Harness verification

From `dark-factory-wearedevs/` with its virtualenv active:

```
python -m harness run --track tablekeeper --repo ../band-work/result-graded --all
python -m harness check ../band-work/result-graded --track tablekeeper
```

The final `--all` run (`band-work/checks/graded-all-final2/`, revision `b7e5a9f`) passed
every stage. Each folder claimed its own stage (`highest_contiguous` 1, 2, 3, 4) with
`share` 1.0. Counts are in [FACTORY.md §3](FACTORY.md#3-measured-cost-and-time-per-stage).

## What each stage adds

### Stage 1: reservations API (`stage-1/`, accepted at `cf904c2`)
- Restaurants, tables, opening hours, slot grid and availability search.
- Signup and login with bearer tokens.
- Reservations: create, list, get, cancel, and amend with `PATCH`.
- Idempotency keys on writes: a replay returns 200 with the original receipt, and reusing a
  key with a different body returns 409.
- DST-correct local-time handling. A time in the spring-forward gap returns 422
  `invalid_local_time`, and an ambiguous fall-back time resolves to the first occurrence.
- Atomic multi-booking moves (`POST /reservation-moves`).
- `/_test/reset`, export and import.
- No UI.

### Stage 2: online booking UI and combined tables (`stage-2/`, accepted at `74fb95b`)
- A browser UI with a search and availability grid, booking form, confirmation, signup, login
  and lookup with cancel. It is vanilla JS and CSS served from the image.
- The UI drops out-of-order search responses. A booking keeps its own idempotency key, so a
  lost response can be retried safely and shows a distinct "uncertain" state.
- Restaurants can declare `combinable` table pairs. A booking can use `table_ids` (a pair),
  availability gains `available_options`, and capacity is the sum of both tables.
- Export schema 2. A stage-1 export imports cleanly: tokens, references and receipts still work.

### Stage 3: policies, explanations, history, recurring series (`stage-3/`, accepted at `ba2bcfc`)
- Managers publish dated booking policies (`POST/GET /restaurants/{id}/policies`). Each booking
  records the `accepted_terms` it was made under and carries a `revision`.
- `PATCH` accepts `expected_revision` (409 `stale_revision` on mismatch).
- Availability with `explain=true` shows the `capacity` and `no_overlap` rule results for each
  table.
- Per-reservation `history` and `decision` endpoints, plus a restaurant `revision` counter.
- Recurring reservations (`POST /series`, `GET /series/{id}`).
- Export schema 3, which imports stage-1 and stage-2 exports.

### Stage 4: seating changes after a closure, recurring amendments (`stage-4/`, accepted at `d6266e7`)
- A manager can preview a replan when a table closes (`POST /restaurants/{id}/replans`). An
  exact planner minimises moved bookings, then unused seats, then option rank.
- `.../replans/{plan_id}/apply` applies the plan atomically. It records `reassigned` history,
  and closures block the closed table everywhere.
- Series amendment (`POST /series/{id}/amend`) changes the local time from a given occurrence
  onward.
- Export schema 4, which imports schemas 1–4.
