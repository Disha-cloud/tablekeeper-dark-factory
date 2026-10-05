"""Stage-1 / stage-2 export -> stage-3 import.

  docker build -t tablekeeper-graded-s1 stage-1 && docker run -d --rm --name tk-s1 -p 8081:8080 tablekeeper-graded-s1
  docker build -t tablekeeper-graded-s2 stage-2 && docker run -d --rm --name tk-s2 -p 8082:8080 tablekeeper-graded-s2
  (stage-3 on :8080)  python3 stage-3/selftest/upgrade.py
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
    print("ok  " if cond else "FAIL", name, "" if cond else str(info)[:500])
    if not cond:
        FAILS.append(name)


S3 = 8080
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
fx = {"users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
      "restaurants": [{"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
                       "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                       "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in DAYS],
                       "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                                  {"id": "t_3", "label": "3", "capacity": 4}]}],
      "reservations": []}


def run(label, port):
    print("---", label)
    f = json.loads(json.dumps(fx))
    if label == "stage-2":
        f["restaurants"][0]["combinable"] = [["t_1", "t_2"]]
    assert call(port, "POST", "/_test/reset", f)[0] == 204
    s, lg = call(port, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
    tok = lg["token"]
    body = {"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": "2099-09-24T19:00", "party_size": 3}
    s, orig = call(port, "POST", "/reservations", body, tok, {"Idempotency-Key": "OLD-" + label})
    assert s == 201, orig
    s, orig2 = call(port, "POST", "/reservations", dict(body, table_id="t_2", party_size=2), tok, {"Idempotency-Key": "OLD2-" + label})
    s, can = call(port, "POST", "/reservations/%s/cancel" % orig2["reference"], token=tok)
    s, mv = call(port, "POST", "/reservation-moves", {"moves": [{"reference": orig["reference"], "starts_at_local": "2099-09-25T19:00"}]}, tok, {"Idempotency-Key": "MV-" + label})
    s, orig3 = call(port, "POST", "/reservations", dict(body, table_id="t_1", party_size=2, starts_at_local="2099-10-01T19:00"), tok, {"Idempotency-Key": "OLD3-" + label})
    if label == "stage-2":
        s, pair = call(port, "POST", "/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"], "starts_at_local": "2099-11-05T19:00", "party_size": 5}, tok, {"Idempotency-Key": "PAIR"})
        assert s == 201, pair
    s, exp = call(port, "GET", "/_test/export")
    assert s == 200
    call(S3, "POST", "/_test/reset", {})
    check("%s export imports into stage 3" % label, call(S3, "POST", "/_test/import", exp)[0] == 204)
    s, js = call(S3, "GET", "/reservations/" + orig["reference"], token=tok)
    check("old token + reference work", s == 200 and js["revision"] == 1 and js["accepted_terms"]["policy_version"] == 0
          and js["starts_at_local"] == "2099-09-25T19:00" and js["table_id"] == "t_3", js)
    s, js = call(S3, "POST", "/reservations", body, tok, {"Idempotency-Key": "OLD-" + label})
    check("original receipt replays verbatim (200)", s == 200 and js == orig and "revision" not in js, (s, js))
    s, js = call(S3, "POST", "/reservation-moves", {"moves": [{"reference": orig["reference"], "starts_at_local": "2099-09-25T19:00"}]}, tok, {"Idempotency-Key": "MV-" + label})
    check("move receipt replays verbatim", s == 200 and js == mv, (s, js))
    s, js = call(S3, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
    check("login after import", s == 200)
    s, h = call(S3, "GET", "/reservations/%s/history" % orig2["reference"], token=tok)
    check("cancelled legacy booking: synthesized created+cancelled", s == 200 and [e["event"] for e in h["entries"]] == ["created", "cancelled"]
          and h["entries"][0]["revision"] == 1 and h["entries"][1]["revision"] == 1 and h["entries"][0]["at"] == h["entries"][1]["at"], h)
    s, h = call(S3, "GET", "/reservations/%s/history" % orig["reference"], token=tok)
    check("history of imported booking", s == 200 and len(h["entries"]) == 1 and h["entries"][0]["event"] == "created"
          and h["entries"][0]["changes"][0] == {"field": "table_id", "from": None, "to": "t_3"}, h)
    s, d = call(S3, "GET", "/reservations/%s/decision" % orig["reference"], token=tok)
    check("decision of imported booking", s == 200 and d["revision"] == 1 and d["accepted_terms"]["capacities"] == {"t_1": 2, "t_2": 4, "t_3": 4}, d)
    expect_noauth = call(S3, "GET", "/reservations/%s/history" % orig["reference"])[0]
    check("history 404 without token", expect_noauth == 404)
    s, rd = call(S3, "GET", "/restaurants/r_anker")
    check("restaurant revision 0, no managers", rd["revision"] == 0 and rd["manager_user_ids"] == [], rd)
    s, av = call(S3, "GET", "/availability?restaurant_id=r_anker&date=2099-10-01&party_size=2&explain=true")
    check("occupancy preserved + explain works", av["slots"][2]["available_table_ids"] == ["t_2", "t_3"] if label == "stage-1" else "t_1" not in av["slots"][2]["available_table_ids"], av["slots"][2])
    s, sr = call(S3, "POST", "/series", {"anchor_reference": orig3["reference"], "count": 3, "interval_weeks": 1}, tok, {"Idempotency-Key": "SER-" + label})
    check("series adoption on imported reservation", s == 201 and len(sr["occurrences"]) == 3 and sr["occurrences"][0]["reservation"]["revision"] == 1
          and sr["occurrences"][1]["reservation"]["starts_at_local"] == "2099-10-08T19:00", (s, sr))
    s, ch = call(S3, "PATCH", "/reservations/" + orig["reference"], {"party_size": 2, "expected_revision": 1}, tok)
    check("amend imported booking", s == 200 and ch["revision"] == 2, ch)
    if label == "stage-2":
        s, js = call(S3, "POST", "/reservations", {"restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"], "starts_at_local": "2099-11-05T19:00", "party_size": 5}, tok, {"Idempotency-Key": "PAIR"})
        check("pair receipt replay verbatim", s == 200 and js == pair, (s, js))
        s, h = call(S3, "GET", "/reservations/%s/history" % pair["reference"], token=tok)
        check("pair history uses table_ids", h["entries"][0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}, h)
        s, d = call(S3, "GET", "/restaurants/r_anker")
        check("combinable preserved", d["combinable"] == [["t_1", "t_2"]], d)
    s, e3 = call(S3, "GET", "/_test/export")
    check("stage-3 re-export is schema 3 and re-imports", e3["state"]["schema"] == 3 and call(S3, "POST", "/_test/import", e3)[0] == 204)


run("stage-1", 8081)
run("stage-2", 8082)
print("FAILURES:", FAILS or "none")
sys.exit(1 if FAILS else 0)
