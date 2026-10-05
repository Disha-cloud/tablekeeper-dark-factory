"""Black-box self-test against a running container (default http://127.0.0.1:8080)."""
import http.client
import json
import os
import sys
import threading
import time
import uuid

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
FAILS = []
STATUSES = []


def call(method, path, body=None, token=None, headers=None, raw=None):
    c = http.client.HTTPConnection(HOST, PORT, timeout=15)
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    data = raw
    if body is not None:
        data = json.dumps(body)
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    txt = r.read()
    c.close()
    STATUSES.append(r.status)
    try:
        js = json.loads(txt) if txt else None
    except ValueError:
        js = None
    return r.status, js


def check(name, cond, info=""):
    if not cond:
        FAILS.append(name)
        print("FAIL", name, info)


def expect(name, res, status, code=None):
    s, js = res
    ok = s == status and (code is None or (js and js.get("error", {}).get("code") == code))
    check(name, ok, "got %s %s" % (s, js))
    return js


def fixture(extra_res=None, cutoff=120):
    hours = [{"weekday": d, "opens": "18:00", "closes": "23:00"}
             for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")]
    return {
        "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse",
                   "display_name": "Ada"}],
        "restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": cutoff, "opening_hours": hours,
             "tables": [{"id": "t_1", "label": "1", "capacity": 2},
                        {"id": "t_2", "label": "2", "capacity": 4}]},
            {"id": "r_ny", "name": "NY", "timezone": "America/New_York",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 0,
             "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:30"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
             "tables": [{"id": "n_1", "label": "1", "capacity": 4}]},
            {"id": "r_be", "name": "BE", "timezone": "Europe/Berlin",
             "slot_minutes": 30, "reservation_duration_minutes": 90,
             "cancellation_cutoff_minutes": 0,
             "opening_hours": [{"weekday": d, "opens": "00:00", "closes": "23:30"}
                               for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")],
             "tables": [{"id": "b_1", "label": "1", "capacity": 4}]},
        ],
        "reservations": extra_res or [],
    }


def reset(fx=None):
    return call("POST", "/_test/reset", fx if fx is not None else fixture())


def signup(email="a@example.com"):
    s, js = call("POST", "/auth/signup", {"email": email, "password": "password1", "display_name": "A"})
    assert s == 201, (s, js)
    return js["token"], js["user_id"]


def book(tok, local="2099-09-24T19:00", table="t_2", party=4, rid="r_anker", key=None):
    return call("POST", "/reservations",
                {"restaurant_id": rid, "table_id": table, "starts_at_local": local, "party_size": party},
                tok, {"Idempotency-Key": key or uuid.uuid4().hex})


def parallel(fn, n):
    out = [None] * n

    def w(i):
        try:
            out[i] = fn(i)
        except Exception as e:  # noqa
            out[i] = ("EXC", repr(e))
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def t_basic():
    expect("health", call("GET", "/health"), 200)
    expect("reset", reset(), 204)
    s, js = call("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
    check("seed login", s == 200 and js["user_id"] == "u_ada", js)
    expect("bad login", call("POST", "/auth/login", {"email": "ada@example.com", "password": "x"}), 401, "unauthenticated")
    expect("signup short pw", call("POST", "/auth/signup", {"email": "x@y.z", "password": "short", "display_name": "x"}), 422, "validation_failed")
    expect("signup bad email", call("POST", "/auth/signup", {"email": "xy", "password": "password1", "display_name": "x"}), 422, "validation_failed")
    tok, uid = signup()
    expect("email taken", call("POST", "/auth/signup", {"email": "A@example.com", "password": "password1", "display_name": "x"}), 409, "email_taken")
    expect("no token", call("GET", "/reservations"), 401, "unauthenticated")
    expect("unknown route", call("GET", "/nope"), 404, "not_found")
    expect("bad method", call("DELETE", "/reservations"), 405, "method_not_allowed")
    expect("malformed", call("POST", "/auth/login", raw="{nope"), 400, "malformed_request")
    s, js = call("GET", "/restaurants")
    check("list restaurants", s == 200 and js["restaurants"][0]["id"] == "r_anker", js)
    s, js = call("GET", "/restaurants/r_anker")
    check("restaurant detail", s == 200 and js["tables"][1]["capacity"] == 4, js)
    expect("restaurant 404", call("GET", "/restaurants/zzz"), 404, "not_found")
    # availability
    s, js = call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24&party_size=4")
    check("avail slots", s == 200 and js["slots"][0]["starts_at_local"] == "2099-09-24T18:00"
          and js["slots"][0]["available_table_ids"] == ["t_2"] and len(js["slots"]) == 8, js)
    check("avail offset", js["slots"][0]["starts_at"] == "2099-09-24T18:00:00+02:00", js)
    expect("avail missing", call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24"), 422, "validation_failed")
    expect("avail 1e9", call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24&party_size=1e9"), 422, "validation_failed")
    expect("avail +4", call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24&party_size=%2B4"), 422, "validation_failed")
    expect("avail bad date", call("GET", "/availability?restaurant_id=r_anker&date=2099-02-30&party_size=4"), 422, "validation_failed")
    # booking
    s, js = book(tok)
    check("book 201", s == 201 and js["status"] == "confirmed" and js["ends_at"] == "2099-09-24T20:30:00+02:00"
          and len(js["reference"]) == 8 and js["created_at"].endswith("+00:00"), js)
    ref = js["reference"]
    expect("book overlap", book(tok, "2099-09-24T20:00"), 409, "table_unavailable")
    expect("book adjacent", book(tok, "2099-09-24T20:30"), 201)
    expect("off grid", book(tok, "2099-09-24T19:10", "t_1", 2), 422, "not_on_slot_grid")
    expect("before open", book(tok, "2099-09-24T17:30", "t_1", 2), 422, "outside_opening_hours")
    expect("ends after close", book(tok, "2099-09-24T22:00", "t_1", 2), 422, "outside_opening_hours")
    expect("capacity", book(tok, "2099-09-24T19:00", "t_1", 3), 422, "party_exceeds_capacity")
    expect("party str", book(tok, party="4"), 422, "validation_failed")
    expect("party bool", book(tok, party=True), 422, "validation_failed")
    expect("party 0", book(tok, party=0), 422, "validation_failed")
    expect("bad local", book(tok, "2099-09-24T19:00:00"), 422, "validation_failed")
    expect("unknown table", book(tok, table="zz"), 404, "not_found")
    expect("unknown rest", book(tok, rid="zz"), 404, "not_found")
    expect("restaurant_id type", call("POST", "/reservations", {"restaurant_id": 5, "table_id": "t_1", "starts_at_local": "x", "party_size": 1}, tok, {"Idempotency-Key": "k"}), 400, "malformed_request")
    expect("missing key", call("POST", "/reservations", {}, tok), 400, "missing_idempotency_key")
    s, js = call("GET", "/reservations/" + ref, token=tok)
    check("get own", s == 200 and js["reference"] == ref, js)
    tok2, _ = signup("b@example.com")
    expect("get other", call("GET", "/reservations/" + ref, token=tok2), 404, "not_found")
    expect("cancel other", call("POST", "/reservations/%s/cancel" % ref, token=tok2), 404, "not_found")
    s, js = call("GET", "/reservations", token=tok)
    check("list desc", s == 200 and [r["starts_at_local"] for r in js["reservations"]] ==
          ["2099-09-24T20:30", "2099-09-24T19:00"], js)
    # patch
    s, js = call("PATCH", "/reservations/" + ref, {"party_size": 2}, tok)
    check("patch party", s == 200 and js["party_size"] == 2 and js["reference"] == ref, js)
    expect("patch conflict", call("PATCH", "/reservations/" + ref, {"starts_at_local": "2099-09-24T20:30"}, tok), 409, "table_unavailable")
    s, js = call("GET", "/reservations/" + ref, token=tok)
    check("patch unchanged after fail", js["starts_at_local"] == "2099-09-24T19:00", js)
    s, js = call("PATCH", "/reservations/" + ref, {"table_id": "t_1", "starts_at_local": "2099-09-24T20:30"}, tok)
    check("patch move", s == 200 and js["table_id"] == "t_1", js)
    expect("patch type", call("PATCH", "/reservations/" + ref, {"table_id": 3}, tok), 400, "malformed_request")
    # cancel
    s, js = call("POST", "/reservations/%s/cancel" % ref, token=tok)
    check("cancel", s == 200 and js["status"] == "cancelled", js)
    expect("cancel twice", call("POST", "/reservations/%s/cancel" % ref, token=tok), 200)
    expect("patch cancelled", call("PATCH", "/reservations/" + ref, {"party_size": 1}, tok), 409, "reservation_cancelled")
    s, js = call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24&party_size=1")
    sl = [x for x in js["slots"] if x["starts_at_local"] == "2099-09-24T20:30"][0]
    check("slot freed", "t_1" in sl["available_table_ids"], sl)
    # cutoff
    reset(fixture(cutoff=10 ** 9))
    tok, _ = signup()
    s, js = book(tok, "2099-09-24T19:00")
    ref = js["reference"]
    expect("cutoff cancel", call("POST", "/reservations/%s/cancel" % ref, token=tok), 409, "cutoff_passed")
    expect("cutoff patch", call("PATCH", "/reservations/" + ref, {}, tok), 409, "cutoff_passed")
    # past booking allowed
    reset()
    tok, _ = signup()
    expect("past booking", book(tok, "2001-09-24T19:00"), 201)


def t_idem():
    reset()
    tok, _ = signup()
    tok2, _ = signup("b@example.com")
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": "2099-09-24T19:00", "party_size": 4}
    s1, j1 = call("POST", "/reservations", body, tok, {"Idempotency-Key": "K1"})
    check("idem first", s1 == 201, j1)
    s2, j2 = call("POST", "/reservations", dict(reversed(list(body.items()))), tok, {"Idempotency-Key": "K1"})
    check("idem replay", s2 == 200 and j2 == j1, (s2, j2))
    call("POST", "/reservations/%s/cancel" % j1["reference"], token=tok)
    s2, j2 = call("POST", "/reservations", body, tok, {"Idempotency-Key": "K1"})
    check("idem replay after cancel", s2 == 200 and j2 == j1)
    expect("idem reuse", call("POST", "/reservations", dict(body, party_size=2), tok, {"Idempotency-Key": "K1"}), 409, "idempotency_key_reuse")
    expect("idem reuse invalid body", call("POST", "/reservations", {"x": 1}, tok, {"Idempotency-Key": "K1"}), 409, "idempotency_key_reuse")
    expect("idem other user", call("POST", "/reservations", body, tok2, {"Idempotency-Key": "K1"}), 201)
    expect("idem key long", call("POST", "/reservations", body, tok, {"Idempotency-Key": "x" * 256}), 422, "validation_failed")
    # failed 4xx key reusable
    bad = dict(body, party_size=9)
    expect("idem fail", call("POST", "/reservations", bad, tok, {"Idempotency-Key": "K2"}), 422, "party_exceeds_capacity")
    expect("idem reusable", call("POST", "/reservations", dict(body, starts_at_local="2099-09-25T19:00"), tok, {"Idempotency-Key": "K2"}), 201)
    # concurrency identical
    reset()
    tok, _ = signup()
    outs = parallel(lambda i: call("POST", "/reservations", body, tok, {"Idempotency-Key": "CC"}), 50)
    codes = sorted(o[0] for o in outs)
    check("idem concurrent", codes == [200] * 49 + [201], codes)
    bodies = {json.dumps(o[1], sort_keys=True) for o in outs}
    check("idem concurrent same body", len(bodies) == 1)
    s, js = call("GET", "/reservations", token=tok)
    check("idem concurrent single booking", len(js["reservations"]) == 1, js)


def t_double_booking():
    reset()
    toks = [signup("u%d@example.com" % i)[0] for i in range(10)]
    outs = parallel(lambda i: book(toks[i % 10], "2099-09-24T19:00"), 50)
    codes = [o[0] for o in outs]
    check("double booking one winner", codes.count(201) == 1 and codes.count(409) == 49, sorted(codes))
    # overlapping different starts, many
    reset()
    toks = [signup("u%d@example.com" % i)[0] for i in range(10)]
    starts = ["18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00"]
    outs = parallel(lambda i: book(toks[i % 10], "2099-09-24T" + starts[i % 7]), 50)
    ok = [o[1] for o in outs if o[0] == 201]
    iv = sorted((r["starts_at"], r["ends_at"]) for r in ok)
    check("no overlap among winners", all(iv[i][1] <= iv[i + 1][0] for i in range(len(iv) - 1)), iv)
    check("only 201/409", all(o[0] in (201, 409) for o in outs), [o[0] for o in outs])
    # concurrent signup same email
    outs = parallel(lambda i: call("POST", "/auth/signup", {"email": "same@x.io", "password": "password1", "display_name": "s"}), 20)
    codes = sorted(o[0] for o in outs)
    check("signup race", codes == [201] + [409] * 19, codes)


def t_dst():
    reset()
    tok, _ = signup()
    # Berlin spring forward 2026-03-29 02:00 -> 03:00
    s, js = call("GET", "/availability?restaurant_id=r_be&date=2026-03-29&party_size=1")
    loc = [x["starts_at_local"][11:] for x in js["slots"]]
    check("berlin gap hidden", "02:00" not in loc and "02:30" not in loc and "01:30" in loc and "03:00" in loc, loc)
    sl = {x["starts_at_local"][11:]: x["starts_at"] for x in js["slots"]}
    check("berlin offsets", sl["01:30"].endswith("+01:00") and sl["03:00"].endswith("+02:00"), sl)
    expect("berlin gap book", book(tok, "2026-03-29T02:30", "b_1", 1, "r_be"), 422, "invalid_local_time")
    # Berlin fall back 2026-10-25 03:00 -> 02:00; 02:00-02:59 repeated
    s, js = call("GET", "/availability?restaurant_id=r_be&date=2026-10-25&party_size=1")
    loc = [x["starts_at_local"][11:] for x in js["slots"]]
    check("berlin repeated once", loc.count("02:00") == 1 and loc.count("02:30") == 1, loc)
    sl = {x["starts_at_local"][11:]: x["starts_at"] for x in js["slots"]}
    check("berlin first occurrence", sl["02:00"].endswith("+02:00") and sl["03:00"].endswith("+01:00"), sl)
    s, js = book(tok, "2026-10-25T02:30", "b_1", 1, "r_be")
    check("berlin fold book", s == 201 and js["starts_at"] == "2026-10-25T02:30:00+02:00"
          and js["ends_at"] == "2026-10-25T03:00:00+01:00", js)
    s, js = book(tok, "2026-10-25T01:30", "b_1", 1, "r_be")
    check("absolute duration", s == 201 and js["ends_at"] == "2026-10-25T02:00:00+02:00" or
          s == 409, (s, js))
    # NY
    s, js = call("GET", "/availability?restaurant_id=r_ny&date=2026-03-08&party_size=1")
    loc = [x["starts_at_local"][11:] for x in js["slots"]]
    check("ny gap hidden", "02:00" not in loc and "02:30" not in loc and "03:00" in loc, loc)
    expect("ny gap book", book(tok, "2026-03-08T02:00", "n_1", 1, "r_ny"), 422, "invalid_local_time")
    s, js = call("GET", "/availability?restaurant_id=r_ny&date=2026-11-01&party_size=1")
    sl = {x["starts_at_local"][11:]: x["starts_at"] for x in js["slots"]}
    loc = [x["starts_at_local"][11:] for x in js["slots"]]
    check("ny repeated once", loc.count("01:00") == 1 and loc.count("01:30") == 1, loc)
    check("ny first occurrence", sl["01:30"].endswith("-04:00") and sl["02:00"].endswith("-05:00"), sl)
    s, js = book(tok, "2026-11-01T01:30", "n_1", 1, "r_ny")
    check("ny fold book", s == 201 and js["ends_at"] == "2026-11-01T02:00:00-05:00", js)


def t_moves():
    reset()
    tok, _ = signup()
    tok2, _ = signup("b@example.com")
    r1 = book(tok, "2099-09-24T19:00", "t_1", 2)[1]["reference"]
    r2 = book(tok, "2099-09-24T19:00", "t_2", 2)[1]["reference"]
    r3 = book(tok, "2099-09-24T21:30", "t_2", 2)[1]["reference"]  # ends 23:00
    other = book(tok2, "2099-09-25T19:00", "t_2", 2)[1]["reference"]
    H = lambda k: {"Idempotency-Key": k}  # noqa
    swap = {"moves": [{"reference": r1, "table_id": "t_2"}, {"reference": r2, "table_id": "t_1"}]}
    s, js = call("POST", "/reservation-moves", swap, tok, H("M1"))
    check("swap ok", s == 201 and [x["table_id"] for x in js["reservations"]] == ["t_2", "t_1"]
          and js["reservations"][0]["reference"] == r1, (s, js))
    s2, j2 = call("POST", "/reservation-moves", swap, tok, H("M1"))
    check("moves replay", s2 == 200 and j2 == js, (s2, j2))
    expect("moves different body", call("POST", "/reservation-moves", {"moves": [{"reference": r1}]}, tok, H("M1")), 409, "idempotency_key_reuse")
    # atomic failure: second move conflicts -> first not applied
    bad = {"moves": [{"reference": r1, "starts_at_local": "2099-09-24T21:00", "table_id": "t_1"},
                     {"reference": r3, "table_id": "t_1", "starts_at_local": "2099-09-24T21:30"}]}
    # r1 -> t_1 21:00-22:30, r3 -> t_1 21:30-23:00 overlap
    expect("moves overlap", call("POST", "/reservation-moves", bad, tok, H("M2")), 409, "table_unavailable")
    s, js = call("GET", "/reservations/" + r1, token=tok)
    check("moves atomic", js["table_id"] == "t_2" and js["starts_at_local"] == "2099-09-24T19:00", js)
    # failed key reusable
    good = {"moves": [{"reference": r1, "starts_at_local": "2099-09-24T21:00"}]}
    expect("moves reuse after fail", call("POST", "/reservation-moves", good, tok, H("M2")), 409, "table_unavailable")  # r3 on t_2 21:30 overlaps
    good = {"moves": [{"reference": r1, "starts_at_local": "2099-09-24T18:00"}]}
    expect("moves reuse key", call("POST", "/reservation-moves", good, tok, H("M2")), 201)
    expect("moves other owner", call("POST", "/reservation-moves", {"moves": [{"reference": other}]}, tok, H("M3")), 404, "not_found")
    expect("moves dup", call("POST", "/reservation-moves", {"moves": [{"reference": r1}, {"reference": r1}]}, tok, H("M4")), 422, "validation_failed")
    expect("moves empty", call("POST", "/reservation-moves", {"moves": []}, tok, H("M5")), 422, "validation_failed")
    expect("moves no token", call("POST", "/reservation-moves", good, None, H("M6")), 401, "unauthenticated")
    expect("moves no key", call("POST", "/reservation-moves", good, tok), 400, "missing_idempotency_key")
    # no-op move
    s, js = call("POST", "/reservation-moves", {"moves": [{"reference": r3}]}, tok, H("M7"))
    check("moves noop", s == 201 and js["reservations"][0]["reference"] == r3, (s, js))
    # moves concurrency: two users racing for same table
    reset()
    toks = [signup("u%d@example.com" % i)[0] for i in range(2)]
    refs = [book(toks[i], "2099-09-24T%s" % ("18:00" if i == 0 else "21:00"), "t_1", 2)[1]["reference"] for i in range(2)]
    outs = parallel(lambda i: call("POST", "/reservation-moves", {"moves": [{"reference": refs[i % 2], "table_id": "t_2", "starts_at_local": "2099-09-24T19:00"}]}, toks[i % 2], H("R%d" % i)), 50)
    winners = {i % 2 for i, o in enumerate(outs) if o[0] == 201}
    check("moves race single winning user", len(winners) == 1 and all(o[0] in (201, 409) for o in outs), [o[0] for o in outs])
    s, a = call("GET", "/reservations/" + refs[0], token=toks[0])
    s, b = call("GET", "/reservations/" + refs[1], token=toks[1])
    check("moves race no overlap", not (a["table_id"] == b["table_id"] == "t_2" and a["starts_at_local"] == b["starts_at_local"]), (a, b))


def t_export():
    reset()
    tok, uid = signup()
    s, js = book(tok, "2099-09-24T19:00", key="EK")
    ref = js["reference"]
    call("POST", "/reservation-moves", {"moves": [{"reference": ref, "party_size": 3}]}, tok, {"Idempotency-Key": "EM"})
    s, exp = call("GET", "/_test/export")
    check("export shape", s == 200 and exp["track"] == "tablekeeper" and exp["format_version"] == 1)
    reset()
    expect("token gone after reset", call("GET", "/reservations", token=tok), 401)
    expect("import", call("POST", "/_test/import", exp), 204)
    s, js = call("GET", "/reservations/" + ref, token=tok)
    check("import token+res", s == 200 and js["party_size"] == 3, (s, js))
    body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": "2099-09-24T19:00", "party_size": 4}
    s, js = call("POST", "/reservations", body, tok, {"Idempotency-Key": "EK"})
    check("import idem replay", s == 200 and js["reference"] == ref, (s, js))
    s, js = call("POST", "/reservation-moves", {"moves": [{"reference": ref, "party_size": 3}]}, tok, {"Idempotency-Key": "EM"})
    check("import moves replay", s == 200, (s, js))
    s, js = call("POST", "/auth/login", {"email": "a@example.com", "password": "password1"})
    check("import login", s == 200 and js["user_id"] == uid, js)
    expect("import twice", call("POST", "/_test/import", exp), 204)
    s, exp2 = call("GET", "/_test/export")
    check("export idempotent", exp2 == exp)
    expect("import bad track", call("POST", "/_test/import", dict(exp, track="x")), 422, "validation_failed")
    expect("import bad version", call("POST", "/_test/import", dict(exp, format_version=True)), 422, "validation_failed")
    expect("import bad state", call("POST", "/_test/import", dict(exp, state=[])), 422, "validation_failed")
    expect("import missing", call("POST", "/_test/import", {}), 422, "validation_failed")
    expect("import junk", call("POST", "/_test/import", raw="{"), 400, "malformed_request")
    s, js = call("GET", "/reservations/" + ref, token=tok)
    check("failed import keeps state", s == 200)
    # snapshot isolation
    call("POST", "/reservations/%s/cancel" % ref, token=tok)
    check("export is snapshot", exp["state"]["reservations"][0]["status"] == "confirmed")


def t_robust():
    big = fixture()
    big["restaurants"][0]["cancellation_cutoff_minutes"] = 10 ** 30
    big["restaurants"][0]["slot_minutes"] = 10 ** 25
    big["restaurants"][0]["reservation_duration_minutes"] = 10 ** 25
    expect("big numbers reset", reset(big), 204)
    tok, _ = signup()
    s, js = call("GET", "/availability?restaurant_id=r_anker&date=2099-09-24&party_size=1")
    check("big avail", s == 200 and js["slots"] == [], (s, js))
    expect("big book", book(tok), 422, "outside_opening_hours")
    big = fixture()
    big["restaurants"][0]["cancellation_cutoff_minutes"] = 10 ** 30
    big["restaurants"][0]["reservation_duration_minutes"] = 60
    reset(big)
    tok, _ = signup()
    s, js = book(tok)
    expect("big cutoff cancel", call("POST", "/reservations/%s/cancel" % js["reference"], token=tok), 409, "cutoff_passed")
    bad = fixture()
    bad["restaurants"][0]["reservation_duration_minutes"] = 10 ** 30
    bad["reservations"] = [{"id": "x", "reference": "ABCDEF", "user_id": "u_ada", "restaurant_id": "r_anker",
                            "table_id": "t_1", "starts_at_local": "2099-09-24T19:00", "party_size": 1}]
    expect("unrepresentable seed", reset(bad), 422, "validation_failed")
    # lone surrogates
    expect("surrogate signup", call("POST", "/auth/signup", raw='{"email":"a@b.c","password":"pass\\ud800word","display_name":"x"}'), 422, "validation_failed")
    expect("surrogate login", call("POST", "/auth/login", raw='{"email":"a@b.c","password":"pass\\ud800word"}'), 401, "unauthenticated")
    sf = fixture()
    sf["restaurants"][0]["name"] = "bad\ud800"
    s, js = call("POST", "/_test/reset", raw=json.dumps(sf))
    check("surrogate fixture", s == 422, (s, js))
    s, js = call("POST", "/_test/reset", raw='{"users":[{"id":"\\ud800","email":"a@b.c","password":"x"}]}')
    check("surrogate fixture id", s == 422, (s, js))
    s, js = call("POST", "/reservations", raw='{"restaurant_id":"\\ud800","table_id":"t","starts_at_local":"x","party_size":1}',
                 token=tok, headers={"Idempotency-Key": "ék"})
    check("surrogate booking no 5xx", s < 500, (s, js))
    # garbage fixtures
    for name, fx in [("arr", []), ("users str", {"users": "x"}), ("res bad", {"reservations": [1]}),
                     ("unhashable", {"restaurants": [{"id": [1]}]}), ("tz", {"restaurants": [{"id": "a", "name": "n", "timezone": "Nope/Zone", "slot_minutes": 1, "reservation_duration_minutes": 1}]}),
                     ("bool", {"restaurants": [{"id": "a", "name": "n", "timezone": "UTC", "slot_minutes": True, "reservation_duration_minutes": 1}]}),
                     ("longid", {"users": [{"id": "x" * 65, "email": "a@b.c", "password": "x"}]})]:
        s, js = call("POST", "/_test/reset", fx)
        check("garbage fixture " + name, s in (400, 422), (s, js))
    reset()
    s, js = call("POST", "/_test/reset", raw="")
    check("empty reset", s == 204, s)
    reset()
    for p in ["/availability?restaurant_id=r_anker&date=9999-12-31&party_size=1",
              "/availability?restaurant_id=r_anker&date=0001-01-01&party_size=1",
              "/availability?restaurant_id=r_anker&date=2026-03-29&party_size=" + "9" * 5000]:
        s, js = call("GET", p)
        check("edge avail no 5xx " + p[:60], s < 500, (s, js))


def t_load():
    reset()
    outs = parallel(lambda i: call("POST", "/auth/signup", {"email": "l%d@x.io" % i, "password": "password1", "display_name": "L"}), 50)
    t0 = time.time()
    check("50 signups", all(o[0] == 201 for o in outs), [o[0] for o in outs][:5])
    outs = parallel(lambda i: call("POST", "/auth/login", {"email": "l%d@x.io" % i, "password": "password1"}), 50)
    check("50 logins", all(o[0] == 200 for o in outs))
    check("login latency", time.time() - t0 < 5, time.time() - t0)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_"):
            t0 = time.time()
            n0 = len(FAILS)
            fn()
            print("%-18s %s (%.1fs)" % (name, "ok" if len(FAILS) == n0 else "FAILED", time.time() - t0))
    check("no 5xx anywhere", not any(s >= 500 for s in STATUSES), [s for s in STATUSES if s >= 500])
    print("FAILURES:", FAILS if FAILS else "none", "; requests:", len(STATUSES))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
