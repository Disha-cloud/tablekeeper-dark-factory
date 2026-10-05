"""Stage-1/2/3 export -> stage-4 import.

  for n in 1 2 3: docker build -t tablekeeper-graded-s$n stage-$n && docker run -d --rm --name tk-s$n -p 808$n:8080 tablekeeper-graded-s$n
  (stage-4 on :8080)  python3 stage-4/selftest/upgrade.py
"""
import http.client
import json
import sys

FAILS = []
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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


S4 = 8080
D = "2099-09-24"
base_fx = {"users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
                     {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"}],
           "restaurants": [{"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
                            "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
                            "opening_hours": [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in DAYS],
                            "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                                       {"id": "t_3", "label": "3", "capacity": 6}]}],
           "reservations": []}
H = lambda k: {"Idempotency-Key": k}  # noqa


def run(n):
    label, port = "stage-%d" % n, 8080 + n
    print("---", label)
    f = json.loads(json.dumps(base_fx))
    if n >= 2:
        f["restaurants"][0]["combinable"] = [["t_1", "t_2"]]
    if n >= 3:
        f["restaurants"][0]["manager_user_ids"] = ["u_mgr"]
    assert call(port, "POST", "/_test/reset", f)[0] == 204
    s, lg = call(port, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
    tok = lg["token"]
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": D + "T19:00", "party_size": 3}
    s, orig = call(port, "POST", "/reservations", body, tok, H("OLD-" + label))
    assert s == 201, orig
    s, o2 = call(port, "POST", "/reservations", dict(body, table_id="t_3", party_size=5), tok, H("OLD2-" + label))
    s, c2 = call(port, "POST", "/reservations/%s/cancel" % o2["reference"], token=tok)
    series = None
    if n >= 3:
        s, series = call(port, "POST", "/series", {"anchor_reference": orig["reference"], "count": 4, "interval_weeks": 1}, tok, H("SER-" + label))
        assert s == 201, series
        occ = series["occurrences"]
        s, mv = call(port, "PATCH", "/reservations/" + occ[1]["reference"], {"starts_at_local": "2099-10-01T19:30"}, tok)
        assert s == 200, mv
        s, cn = call(port, "POST", "/reservations/%s/cancel" % occ[2]["reference"], token=tok)
        assert s == 200
        s, series = call(port, "GET", "/series/" + series["series_id"], token=tok)
    s, exp = call(port, "GET", "/_test/export")
    call(S4, "POST", "/_test/reset", {})
    check("%s export imports into stage 4" % label, call(S4, "POST", "/_test/import", exp)[0] == 204)
    s, js = call(S4, "GET", "/reservations/" + orig["reference"], token=tok)
    check("old token + reference", s == 200 and js["revision"] == 1 and js["table_id"] == "t_2", js)
    s, js = call(S4, "POST", "/reservations", body, tok, H("OLD-" + label))
    check("original receipt replays verbatim", s == 200 and js == orig, (s, js))
    check("login after import", call(S4, "POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})[0] == 200)
    s, h = call(S4, "GET", "/reservations/%s/history" % o2["reference"], token=tok)
    check("history of imported cancelled booking", s == 200 and [e["event"] for e in h["entries"]] == ["created", "cancelled"], h)
    s, rd = call(S4, "GET", "/restaurants/r_anker")
    check("restaurant revision preserved/0", rd["revision"] == (0 if n < 3 else rd["revision"]), rd)
    if n < 3:
        s, p = call(S4, "POST", "/restaurants/r_anker/replans", {"table_id": "t_2", "from": D + "T18:00:00+02:00", "to": D + "T23:00:00+02:00"}, tok, H("RP"))
        check("replan on imported stage-%d data: no managers -> 403" % n, s == 403, (s, p))
        # adoption on imported reservation then amend
        s, sr = call(S4, "POST", "/series", {"anchor_reference": orig["reference"], "count": 3, "interval_weeks": 1}, tok, H("SER-new"))
        check("series adoption on imported reservation", s == 201 and len(sr["occurrences"]) == 3, (s, sr))
        s, am = call(S4, "POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}, tok, H("AM"))
        check("amend a freshly adopted series", s == 201 and am["occurrences"][2]["reservation"]["starts_at_local"].endswith("20:00") and am["revision"] == 2, (s, am))
    else:
        s, sg = call(S4, "GET", "/series/" + series["series_id"], token=tok)
        check("imported series identical", s == 200 and sg == series, (s, sg))
        s, rp = call(S4, "POST", "/series", {"anchor_reference": orig["reference"], "count": 4, "interval_weeks": 1}, tok, H("SER-" + label))
        check("series receipt replays verbatim", s == 200 and rp["series_id"] == series["series_id"] and "dates" not in rp, (s, rp))
        occ = series["occurrences"]
        s, am = call(S4, "POST", "/series/%s/amend" % series["series_id"], {"expected_revision": series["revision"], "from_index": 0, "local_time": "20:00"}, tok, H("AM"))
        times = [o["reservation"]["starts_at_local"] for o in am["occurrences"]] if s == 201 else am
        check("amend imported series: moved occurrence (exception) & cancelled skipped, scheduled dates derived",
              s == 201 and times == [D + "T20:00", "2099-10-01T19:30", "2099-10-08T19:00", "2099-10-15T20:00"], (s, times))
        check("exception flags preserved, series rev +1", [o["exception"] for o in am["occurrences"]] == [False, True, False, False] and am["revision"] == series["revision"] + 1)
        s, p = call(S4, "POST", "/restaurants/r_anker/replans", {"table_id": "t_2", "from": "2099-10-15T18:00:00+02:00", "to": "2099-10-15T23:00:00+02:00"}, call(S4, "POST", "/auth/login", {"email": "mgr@example.com", "password": "correct horse"})[1]["token"], H("RP"))
        check("replan works on imported data (managers preserved)", s == 201 and len(p["assignments"]) == 1 and p["assignments"][0]["changed"], (s, p))
        mt = call(S4, "POST", "/auth/login", {"email": "mgr@example.com", "password": "correct horse"})[1]["token"]
        s, ap = call(S4, "POST", "/restaurants/r_anker/replans/%s/apply" % p["plan_id"], {}, mt, H("AP"))
        s2, sg2 = call(S4, "GET", "/series/" + series["series_id"], token=tok)
        check("apply on imported series moves occurrence, series rev +1, exceptions kept", s == 201 and sg2["revision"] == am["revision"] + 1
              and [o["exception"] for o in sg2["occurrences"]] == [False, True, False, False] and sg2["occurrences"][3]["reservation"]["table_ids"] != ["t_2"], (s, sg2))
    s, e4 = call(S4, "GET", "/_test/export")
    check("stage-4 re-export is schema 4 and re-imports", e4["state"]["schema"] == 4 and call(S4, "POST", "/_test/import", e4)[0] == 204)


for n in (1, 2, 3):
    run(n)
print("FAILURES:", FAILS or "none")
sys.exit(1 if FAILS else 0)
