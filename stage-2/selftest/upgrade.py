"""Stage-1 export -> stage-2 import. Needs stage-1 image on :8081 and stage-2 on :8080.
  docker build -t tablekeeper-graded-s1 stage-1
  docker run -d --rm --name tk-s1 -p 8081:8080 tablekeeper-graded-s1
"""
import http.client
import json
import sys

FAILS = []


def call(port, method, path, body=None, token=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body) if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    t = r.read()
    c.close()
    return r.status, (json.loads(t) if t else None)


def check(name, cond, info=""):
    print("ok  " if cond else "FAIL", name, "" if cond else info)
    if not cond:
        FAILS.append(name)


S1, S2 = 8081, 8080
fx = {"users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
      "restaurants": [{"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
                       "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                       "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
                       "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4}]}],
      "reservations": []}
assert call(S1, "POST", "/_test/reset", fx)[0] == 204
s, login = call(S1, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
tok = login["token"]
body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": "2099-09-24T19:00", "party_size": 4}
s, orig = call(S1, "POST", "/reservations", body, tok, {"Idempotency-Key": "OLDKEY"})
check("s1 booking", s == 201 and "table_ids" not in orig, orig)
s, orig2 = call(S1, "POST", "/reservations", dict(body, table_id="t_1", party_size=2), tok, {"Idempotency-Key": "OLDKEY2"})
s, mv = call(S1, "POST", "/reservation-moves", {"moves": [{"reference": orig2["reference"], "starts_at_local": "2099-09-25T19:00"}]}, tok, {"Idempotency-Key": "OLDMOVE"})
check("s1 move", s == 201, mv)
s, exp = call(S1, "GET", "/_test/export")
check("s1 export", s == 200 and "schema" not in exp["state"])
call(S2, "POST", "/_test/reset", {})
check("s2 import", call(S2, "POST", "/_test/import", exp)[0] == 204)
s, js = call(S2, "GET", "/reservations/" + orig["reference"], token=tok)
check("old token + old ref", s == 200 and js["table_ids"] == ["t_2"] and js["table_id"] == "t_2", (s, js))
s, js = call(S2, "POST", "/reservations", body, tok, {"Idempotency-Key": "OLDKEY"})
check("replay stage-1 key -> 200 original receipt", s == 200 and js == orig, (s, js))
s, js = call(S2, "POST", "/reservation-moves", {"moves": [{"reference": orig2["reference"], "starts_at_local": "2099-09-25T19:00"}]}, tok, {"Idempotency-Key": "OLDMOVE"})
check("replay stage-1 move -> 200 original", s == 200 and js == mv, (s, js))
s, js = call(S2, "POST", "/reservations", dict(body, party_size=3), tok, {"Idempotency-Key": "OLDKEY"})
check("reuse w/ other body 409", s == 409, (s, js))
s, js = call(S2, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
check("login after import", s == 200 and js["user_id"] == "u_ada", js)
s, js = call(S2, "GET", "/restaurants/r_anker")
check("combinable default", js.get("combinable") == [], js)
s, js = call(S2, "POST", "/reservations", dict(body, starts_at_local="2099-09-24T19:30"), tok, {"Idempotency-Key": "NEW"})
check("occupancy preserved (409)", s == 409, (s, js))
s, e2 = call(S2, "GET", "/_test/export")
check("s2 export has schema 2", e2["state"].get("schema") == 2)
check("s2 re-import own export", call(S2, "POST", "/_test/import", e2)[0] == 204)
bad = json.loads(json.dumps(exp)); bad["state"]["schema"] = 9
check("bad schema 422", call(S2, "POST", "/_test/import", bad)[0] == 422)
print("FAILURES:", FAILS or "none")
sys.exit(1 if FAILS else 0)
