# Tablekeeper stage 4: seating changes and recurring amendments

Build and start (no manual setup, no network needed at run time; all HTML, JS and CSS are
served from the image, with system fonts only):

```
docker build -t tablekeeper-stage4 stage-4 && docker run --rm -e PORT=8080 -p 8080:8080 tablekeeper-stage4
```

Run from the repository root. The service listens on `0.0.0.0:$PORT` (default 8080).

- Browser UI: `/` (search and availability grid), `/signup`, `/login`, `/lookup`.
- API: see the stage 1-4 specifications; `GET /health` returns `200 {"status":"ok"}`.

## Seed demo data for manual testing

The service starts **empty by design**: no restaurants and no users (`GET /restaurants` returns
`{"restaurants":[]}`). This is not needed for the harness. **To seed demo data for manual
testing**, run this once after the container is up, before deciding that the service is empty
or broken. It should print `204`:

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

This **replaces all in-memory state**, so anything booked before it is gone. Then:

- Open http://localhost:8080/, choose Zum Anker, the date 2027-06-18 and a party of 4. From 18:00
  to 20:00, Booth 2 (`t_2`) is taken by `DEMO0001`, so only Booth 3 is offered. For a party of 8,
  the only option is Booth 2 + Booth 3 combined, and it is unavailable from 18:00 to 20:00 for the
  same reason.
- Sign in at `/login` as `ada@example.com` / `correct horse`, then look up (and cancel)
  `DEMO0001` at `/lookup`.
- API: log in with `POST /auth/login` `{"email":"ada@example.com","password":"correct horse"}`,
  then `GET /reservations/DEMO0001` with `Authorization: Bearer <token>`.

Self-tests (need the container running on port 8080):

- Stage 1/2 API regression: `python3 stage-4/selftest/run.py`
- Stage 3 API regression: `python3 stage-4/selftest/s3.py`
- Stage 4 API (planner vs. brute force, closures, series amend, schema 4): `python3 stage-4/selftest/s4.py`
- UI regression (headless browser, Playwright for Python): `python stage-4/selftest/ui.py`
- Upgrade from the stage-1/2/3 images (ports 8081-8083, see the script header): `python3 stage-4/selftest/upgrade.py`
