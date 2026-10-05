"""Stage-3 API selftest against a running container (default http://127.0.0.1:8080)."""
import copy
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
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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
        print("FAIL", name, str(info)[:600])


def expect(name, res, status, code=None):
    s, js = res
    ok = s == status and (code is None or (js and js.get("error", {}).get("code") == code))
    check(name, ok, "got %s %s" % (s, js))
    return js


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


def hours(o="18:00", c="23:00"):
    return [{"weekday": d, "opens": o, "closes": c} for d in DAYS]


def fixture():
    return {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}],
        "restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "opening_hours": hours(),
             "manager_user_ids": ["u_mgr"],
             "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                        {"id": "t_3", "label": "3", "capacity": 6}],
             "combinable": [["t_1", "t_2"], ["t_2", "t_3"]]},
            {"id": "r_be", "name": "Berlin24", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 0, "opening_hours": hours("00:00", "23:30"),
             "tables": [{"id": "b_1", "label": "1", "capacity": 4}]},
            {"id": "r_ny", "name": "NY24", "timezone": "America/New_York", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 0, "opening_hours": hours("00:00", "23:30"),
             "tables": [{"id": "n_1", "label": "1", "capacity": 4}]},
        ],
        "reservations": []}


def reset(fx=None):
    s, _ = call("POST", "/_test/reset", fx if fx is not None else fixture())
    assert s == 204, s
    toks = {}
    for k, e in (("ada", "ada@example.com"), ("mgr", "mgr@example.com"), ("bob", "bob@example.com")):
        s, js = call("POST", "/auth/login", {"email": e, "password": "correct horse"})
        toks[k] = js["token"]
    return toks


def H(k=None):
    return {"Idempotency-Key": k or uuid.uuid4().hex}


def book(tok, local, tids=("t_2",), party=2, rid="r_anker", key=None):
    b = {"restaurant_id": rid, "starts_at_local": local, "party_size": party}
    if len(tids) == 1:
        b["table_id"] = tids[0]
    else:
        b["table_ids"] = list(tids)
    return call("POST", "/reservations", b, tok, H(key))


def policy(eff="2099-03-01", slot=30, dur=120, cutoff=60, caps=None, hrs=None):
    return {"effective_from": eff, "slot_minutes": slot, "reservation_duration_minutes": dur,
            "cancellation_cutoff_minutes": cutoff,
            "opening_hours": hrs if hrs is not None else [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in DAYS],
            "capacities": caps or {"t_1": 2, "t_2": 4, "t_3": 6}}


def pub(tok, p, rid="r_anker", key=None):
    return call("POST", "/restaurants/%s/policies" % rid, p, tok, H(key))


def rrev(rid="r_anker"):
    return call("GET", "/restaurants/" + rid)[1]["revision"]


def T0():
    return {"policy_version": 0, "slot_minutes": 30, "reservation_duration_minutes": 90,
            "cancellation_cutoff_minutes": 120, "opening_hours": hours(),
            "capacities": {"t_1": 2, "t_2": 4, "t_3": 6}}


def t_explain():
    t = reset()
    D = "2099-09-24"
    book(t["ada"], D + "T19:00", ("t_2",), 3)
    s, plain = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=3" % D)
    check("no explain key by default", all("explain" not in sl for sl in plain["slots"]), plain["slots"][0])
    s, ex = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=3&explain=true" % D)
    check("explain 200", s == 200, ex)
    for bad in ("false", "1", "", "True", "TRUE", "yes"):
        expect("explain=%r" % bad, call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=3&explain=%s" % (D, bad)), 422, "validation_failed")
    ok = True
    for sl, pl in zip(ex["slots"], plain["slots"]):
        ids = [e["table_id"] for e in sl["explain"]]
        ok &= ids == ["t_1", "t_2", "t_3"]
        ok &= [e["table_id"] for e in sl["explain"] if e["available"]] == sl["available_table_ids"] == pl["available_table_ids"]
        for e in sl["explain"]:
            ok &= [r["rule"] for r in e["rules"]] == ["capacity", "no_overlap"] and e["policy_version"] == 0
            ok &= e["available"] == all(r["holds"] for r in e["rules"])
    check("explain invariants", ok, ex["slots"][0])
    sl = [x for x in ex["slots"] if x["starts_at_local"].endswith("19:30")][0]
    e = {x["table_id"]: x for x in sl["explain"]}
    check("t_1 capacity fails, overlap ok", e["t_1"]["rules"] == [{"rule": "capacity", "holds": False}, {"rule": "no_overlap", "holds": True}], e["t_1"])
    check("t_2 overlap fails, capacity ok", e["t_2"]["rules"] == [{"rule": "capacity", "holds": True}, {"rule": "no_overlap", "holds": False}], e["t_2"])
    sl = [x for x in ex["slots"] if x["starts_at_local"].endswith("19:00")][0]
    book(t["ada"], D + "T19:00", ("t_1",), 2)
    s, ex2 = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=7&explain=true" % D)
    sl = ex2["slots"][0]
    check("slot with no table still appears with full explain", sl["available_table_ids"] == [] and len(sl["explain"]) == 3
          and all(not x["available"] and x["rules"][0]["holds"] is False for x in sl["explain"]), sl)
    s, closed = call("GET", "/restaurants/r_be")
    fx = fixture()
    fx["restaurants"][0]["opening_hours"] = [{"weekday": "mon", "opens": "18:00", "closes": "23:00"}]
    reset(fx)
    s, js = call("GET", "/availability?restaurant_id=r_anker&date=2099-09-26&party_size=2&explain=true")  # a Saturday
    check("closed day slots []", s == 200 and js["slots"] == [], js)


def t_policy_validation():
    t = reset()
    ok = policy()
    expect("no token", call("POST", "/restaurants/r_anker/policies", ok, None, H()), 401, "unauthenticated")
    expect("missing key", call("POST", "/restaurants/r_anker/policies", ok, t["mgr"]), 400, "missing_idempotency_key")
    expect("not object", call("POST", "/restaurants/r_anker/policies", raw="[1]", token=t["mgr"], headers=H()), 400, "malformed_request")
    expect("unknown restaurant", pub(t["mgr"], ok, "zz"), 404, "not_found")
    expect("non-manager", pub(t["ada"], ok), 403, "forbidden")
    expect("unknown restaurant before 403", pub(t["ada"], ok, "zz"), 404, "not_found")
    expect("403 before 422", pub(t["ada"], {}), 403, "forbidden")
    bads = {
        "missing field": {k: v for k, v in ok.items() if k != "capacities"},
        "empty body": {},
        "bad date": dict(ok, effective_from="2099-02-30"),
        "date format": dict(ok, effective_from="2099-2-3"),
        "date type": dict(ok, effective_from=20990301),
        "slot 0": dict(ok, slot_minutes=0), "slot 1441": dict(ok, slot_minutes=1441),
        "slot bool": dict(ok, slot_minutes=True), "slot float": dict(ok, slot_minutes=30.0), "slot str": dict(ok, slot_minutes="30"),
        "dur 0": dict(ok, reservation_duration_minutes=0), "dur 1441": dict(ok, reservation_duration_minutes=1441),
        "cutoff -1": dict(ok, cancellation_cutoff_minutes=-1), "cutoff 10081": dict(ok, cancellation_cutoff_minutes=10081),
        "cutoff bool": dict(ok, cancellation_cutoff_minutes=False),
        "hours dup": dict(ok, opening_hours=[{"weekday": "mon", "opens": "10:00", "closes": "12:00"}, {"weekday": "mon", "opens": "13:00", "closes": "14:00"}]),
        "hours order": dict(ok, opening_hours=[{"weekday": "mon", "opens": "12:00", "closes": "10:00"}]),
        "hours fmt": dict(ok, opening_hours=[{"weekday": "mon", "opens": "9:00", "closes": "10:00"}]),
        "hours weekday": dict(ok, opening_hours=[{"weekday": "xyz", "opens": "09:00", "closes": "10:00"}]),
        "hours type": dict(ok, opening_hours="x"),
        "caps missing table": dict(ok, capacities={"t_1": 2, "t_2": 4}),
        "caps extra table": dict(ok, capacities={"t_1": 2, "t_2": 4, "t_3": 6, "t_4": 1}),
        "caps 0": dict(ok, capacities={"t_1": 0, "t_2": 4, "t_3": 6}),
        "caps 101": dict(ok, capacities={"t_1": 2, "t_2": 101, "t_3": 6}),
        "caps bool": dict(ok, capacities={"t_1": True, "t_2": 4, "t_3": 6}),
        "caps list": dict(ok, capacities=[2, 4, 6]),
    }
    for name, b in bads.items():
        expect("422 " + name, pub(t["mgr"], b), 422, "validation_failed")
    expect("no versions allocated", call("GET", "/restaurants/r_anker/policies"), 200)
    check("no versions after failures", call("GET", "/restaurants/r_anker/policies")[1] == {"policies": []})
    check("restaurant revision untouched by failures", rrev() == 0)
    s, p1 = pub(t["mgr"], dict(ok, extra="ignored", unknown=[1]), key="P1")
    check("201 v1", s == 201 and p1["policy_version"] == 1 and p1["restaurant_id"] == "r_anker" and "extra" not in p1
          and p1["capacities"] == ok["capacities"] and p1["effective_from"] == ok["effective_from"], p1)
    s, p1b = pub(t["mgr"], dict(ok, extra="ignored", unknown=[1]), key="P1")
    check("replay 200 identical, no version", s == 200 and p1b == p1 and len(call("GET", "/restaurants/r_anker/policies")[1]["policies"]) == 1, (s, p1b))
    expect("reuse different body", pub(t["mgr"], policy(eff="2099-04-01"), key="P1"), 409, "idempotency_key_reuse")
    expect("replay by non-manager still 403 w/ new key", pub(t["ada"], ok), 403)
    s, p2 = pub(t["mgr"], policy(eff="2099-04-01"))
    check("v2", s == 201 and p2["policy_version"] == 2, p2)
    s, lst = call("GET", "/restaurants/r_anker/policies")
    check("public list in publication order", s == 200 and [p["policy_version"] for p in lst["policies"]] == [1, 2], lst)
    s, det = call("GET", "/restaurants/r_anker")
    check("detail original config", det["reservation_duration_minutes"] == 90 and det["manager_user_ids"] == ["u_mgr"] and det["revision"] == 2, det)
    expect("policies unknown restaurant", call("GET", "/restaurants/zz/policies"), 404, "not_found")
    # empty opening hours allowed
    expect("empty hours ok", pub(t["mgr"], policy(eff="2099-05-01", hrs=[])), 201)
    # fixture validation
    fx = fixture()
    fx["restaurants"][0]["manager_user_ids"] = "u_mgr"
    expect("bad manager_user_ids fixture", call("POST", "/_test/reset", fx), 422, "validation_failed")


def t_policy_selection():
    t = reset()
    expect("v1 out of order (later date first)", pub(t["mgr"], policy(eff="2099-03-01", dur=120)), 201)
    expect("v2 earlier date", pub(t["mgr"], policy(eff="2099-02-01", dur=60, slot=60)), 201)
    s, v3 = pub(t["mgr"], policy(eff="2099-03-01", dur=45, caps={"t_1": 1, "t_2": 1, "t_3": 1}))
    check("v3 same date", v3["policy_version"] == 3)
    def terms_on(date, party=1):
        s, js = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=%d&explain=true" % (date, party))
        return js
    a = terms_on("2099-01-15")
    check("before any policy -> 0", a["slots"][0]["explain"][0]["policy_version"] == 0 and len(a["slots"]) == 8, a["slots"][0])
    a = terms_on("2099-02-10")
    check("v2 slots hourly, 60 min", a["slots"][0]["explain"][0]["policy_version"] == 2 and [x["starts_at_local"][11:] for x in a["slots"]][:3] == ["18:00", "19:00", "20:00"], a["slots"][:2])
    a = terms_on("2099-03-01")
    check("same-date supersession picks v3", a["slots"][0]["explain"][0]["policy_version"] == 3, a["slots"][0]["explain"][0])
    a = terms_on("2099-03-20", 2)
    check("v3 capacities (1)", a["slots"][0]["available_table_ids"] == [] and a["slots"][0]["explain"][0]["rules"][0]["holds"] is False, a["slots"][0])
    s, r = book(t["ada"], "2099-02-10T19:00", ("t_2",), 2)
    check("booking under v2", s == 201 and r["accepted_terms"]["policy_version"] == 2 and r["ends_at"] == "2099-02-10T20:00:00+01:00", r)
    expect("v2 grid 60", book(t["ada"], "2099-02-10T19:30", ("t_3",), 2), 422, "not_on_slot_grid")
    s, r3 = book(t["ada"], "2099-03-10T19:00", ("t_2",), 1)
    check("booking under v3", s == 201 and r3["accepted_terms"]["capacities"] == {"t_1": 1, "t_2": 1, "t_3": 1} and r3["accepted_terms"]["policy_version"] == 3, r3)
    expect("v3 capacity enforced", book(t["ada"], "2099-03-10T20:00", ("t_2",), 2), 422, "party_exceeds_capacity")
    expect("pair capacity summed from policy (1+1 < 3)", book(t["ada"], "2099-03-10T20:00", ("t_1", "t_2"), 3), 422, "party_exceeds_capacity")
    s, p = book(t["ada"], "2099-03-10T20:00", ("t_1", "t_2"), 2)
    check("pair capacity ok under policy", s == 201, p)
    # accepted terms snapshot unchanged by later publication
    before = call("GET", "/reservations/" + r3["reference"], token=t["ada"])[1]
    pub(t["mgr"], policy(eff="2099-03-05", dur=30, caps={"t_1": 9, "t_2": 9, "t_3": 9}))
    after = call("GET", "/reservations/" + r3["reference"], token=t["ada"])[1]
    check("accepted terms unchanged after publication", before == after, (before, after))
    s, dec = call("GET", "/reservations/%s/decision" % r3["reference"], token=t["ada"])
    check("decision", s == 200 and dec == {"reference": r3["reference"], "revision": 1, "accepted_terms": r3["accepted_terms"]}, dec)
    # past effective date allowed, never retroactive
    expect("past effective_from allowed", pub(t["mgr"], policy(eff="2001-01-01", dur=60)), 201)
    check("existing booking unaffected by past policy", call("GET", "/reservations/" + r3["reference"], token=t["ada"])[1] == before)
    # closed day under a policy
    pub(t["mgr"], policy(eff="2099-06-01", hrs=[{"weekday": "mon", "opens": "10:00", "closes": "12:00"}]))
    s, js = call("GET", "/availability?restaurant_id=r_anker&date=2099-06-02&party_size=1")  # Tuesday
    check("policy closes the day", js["slots"] == [], js)
    # cutoff uses accepted terms
    s, rc = book(t["ada"], "2099-02-11T19:00", ("t_3",), 2)
    pub(t["mgr"], policy(eff="2099-02-11", cutoff=10000 if False else 10080, dur=30))
    check("cancel uses accepted cutoff (120 min, far future) -> ok", call("POST", "/reservations/%s/cancel" % rc["reference"], token=t["ada"])[0] == 200)


def t_history():
    t = reset()
    s, r = book(t["ada"], "2099-09-24T19:00", ("t_2",), 4, key="H1")
    ref = r["reference"]
    s, h = call("GET", "/reservations/%s/history" % ref, token=t["ada"])
    e = h["entries"]
    check("created entry", s == 200 and h["reference"] == ref and len(e) == 1 and e[0]["seq"] == 1 and e[0]["event"] == "created"
          and e[0]["changes"] == [{"field": "table_id", "from": None, "to": "t_2"},
                                  {"field": "starts_at_local", "from": None, "to": "2099-09-24T19:00"},
                                  {"field": "party_size", "from": None, "to": 4}]
          and e[0]["revision"] == 1 and e[0]["accepted_terms"] == T0() and e[0]["at"].endswith(("+01:00", "+02:00", "+00:00")), h)
    book(t["ada"], "2099-09-24T19:00", ("t_2",), 4, key="H1")
    check("replay records nothing", len(call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]) == 1)
    s, p = call("PATCH", "/reservations/" + ref, {"table_id": "t_2", "party_size": 4, "starts_at_local": "2099-09-24T19:00", "junk": 1}, t["ada"])
    check("no-op patch 200 unchanged", s == 200 and p["revision"] == 1, p)
    check("no-op patch no entry", len(call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]) == 1)
    s, p = call("PATCH", "/reservations/" + ref, {"party_size": 3, "table_id": "t_3", "starts_at_local": "2099-09-24T20:00"}, t["ada"])
    check("real patch rev 2", s == 200 and p["revision"] == 2, p)
    e = call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]
    check("changed entry order+fields", e[1]["seq"] == 2 and e[1]["event"] == "changed" and e[1]["changes"] == [
        {"field": "table_id", "from": "t_2", "to": "t_3"}, {"field": "starts_at_local", "from": "2099-09-24T19:00", "to": "2099-09-24T20:00"},
        {"field": "party_size", "from": 4, "to": 3}] and e[1]["revision"] == 2, e[1])
    call("PATCH", "/reservations/" + ref, {"party_size": 2}, t["ada"])
    e = call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]
    check("only changed field named", e[2]["changes"] == [{"field": "party_size", "from": 3, "to": 2}], e[2])
    s, c = call("POST", "/reservations/%s/cancel" % ref, token=t["ada"])
    check("cancel rev 4", c["revision"] == 4, c)
    call("POST", "/reservations/%s/cancel" % ref, token=t["ada"])
    e = call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]
    check("cancelled entry, empty changes, nothing follows", len(e) == 4 and e[3]["event"] == "cancelled" and e[3]["changes"] == [] and e[3]["revision"] == 4, e)
    check("seq 1..n", [x["seq"] for x in e] == [1, 2, 3, 4])
    check("history keeps old terms", all(x["accepted_terms"] == T0() for x in e))
    # access
    expect("history other user", call("GET", "/reservations/%s/history" % ref, token=t["bob"]), 404, "not_found")
    expect("history no token", call("GET", "/reservations/%s/history" % ref), 404, "not_found")
    expect("history bad token", call("GET", "/reservations/%s/history" % ref, token="nope"), 404, "not_found")
    expect("history unknown", call("GET", "/reservations/ZZZZZZ/history", token=t["ada"]), 404, "not_found")
    expect("decision no token", call("GET", "/reservations/%s/decision" % ref), 404, "not_found")
    expect("decision other user", call("GET", "/reservations/%s/decision" % ref, token=t["bob"]), 404, "not_found")
    expect("manager can't read diner history", call("GET", "/reservations/%s/history" % ref, token=t["mgr"]), 404, "not_found")
    s, d = call("GET", "/reservations/%s/decision" % ref, token=t["ada"])
    check("decision after cancel", s == 200 and d["revision"] == 4, d)
    # combined history
    s, pr = book(t["ada"], "2099-09-25T19:00", ("t_1", "t_2"), 5)
    h = call("GET", "/reservations/%s/history" % pr["reference"], token=t["ada"])[1]["entries"]
    check("pair created uses table_ids", h[0]["changes"][0] == {"field": "table_ids", "from": None, "to": ["t_1", "t_2"]}, h[0])
    s, p = call("PATCH", "/reservations/" + pr["reference"], {"table_ids": ["t_2", "t_1"]}, t["ada"])
    check("reversed pair is a no-op", s == 200 and p["revision"] == 1, p)
    s, p = call("PATCH", "/reservations/" + pr["reference"], {"table_id": "t_3", "party_size": 5}, t["ada"])
    h = call("GET", "/reservations/%s/history" % pr["reference"], token=t["ada"])[1]["entries"]
    check("pair->single uses table_ids lists", h[1]["changes"][0] == {"field": "table_ids", "from": ["t_1", "t_2"], "to": ["t_3"]}, h[1])
    s, p = call("PATCH", "/reservations/" + pr["reference"], {"table_ids": ["t_3", "t_2"], "party_size": 6}, t["ada"])
    h = call("GET", "/reservations/%s/history" % pr["reference"], token=t["ada"])[1]["entries"]
    check("single->pair + party", s == 200 and h[2]["changes"][0] == {"field": "table_ids", "from": ["t_3"], "to": ["t_2", "t_3"]}
          and h[2]["changes"][1] == {"field": "party_size", "from": 5, "to": 6}, h[2:])
    # seeded
    fx = fixture()
    fx["reservations"] = [{"id": "s1", "reference": "SEED0001", "user_id": "u_ada", "restaurant_id": "r_anker", "table_id": "t_2",
                           "starts_at_local": "2099-09-24T19:00", "party_size": 2},
                          {"id": "s2", "reference": "SEED0002", "user_id": "u_ada", "restaurant_id": "r_anker", "table_ids": ["t_1", "t_2"],
                           "starts_at_local": "2099-09-25T19:00", "party_size": 5, "status": "cancelled", "created_at": "2026-01-01T10:00:00Z"}]
    t = reset(fx)
    h = call("GET", "/reservations/SEED0001/history", token=t["ada"])[1]["entries"]
    check("seeded: one created entry", len(h) == 1 and h[0]["event"] == "created" and h[0]["revision"] == 1 and h[0]["accepted_terms"] == T0(), h)
    h = call("GET", "/reservations/SEED0002/history", token=t["ada"])[1]["entries"]
    check("seeded cancelled: created + cancelled same instant, rev 1", [x["event"] for x in h] == ["created", "cancelled"]
          and h[0]["at"] == h[1]["at"] and h[1]["revision"] == 1 and h[0]["changes"][0]["field"] == "table_ids", h)
    s, r = call("GET", "/reservations/SEED0001", token=t["ada"])
    check("seeded rev 1 terms 0", r["revision"] == 1 and r["accepted_terms"] == T0(), r)


def t_revisions_and_patch_order():
    t = reset()
    check("restaurant rev 0 after reset", rrev() == 0)
    s, r = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2, key="RV1")
    ref = r["reference"]
    check("rev after create", rrev() == 1)
    book(t["ada"], "2099-09-24T19:00", ("t_2",), 2, key="RV1")
    check("replay no bump", rrev() == 1)
    book(t["ada"], "2099-09-24T19:30", ("t_2",), 2)
    check("failed create no bump", rrev() == 1)
    call("PATCH", "/reservations/" + ref, {}, t["ada"])
    call("PATCH", "/reservations/" + ref, {"party_size": 9}, t["ada"])
    check("noop/failed patch no bump", rrev() == 1)
    expect("expected_revision str", call("PATCH", "/reservations/" + ref, {"expected_revision": "1"}, t["ada"]), 422, "validation_failed")
    for bad in (True, 1.0, None, 0, -1):
        expect("expected_revision %r" % (bad,), call("PATCH", "/reservations/" + ref, {"expected_revision": bad}, t["ada"]), 422, "validation_failed")
    expect("stale", call("PATCH", "/reservations/" + ref, {"expected_revision": 5, "party_size": 3}, t["ada"]), 409, "stale_revision")
    expect("stale before validation", call("PATCH", "/reservations/" + ref, {"expected_revision": 2, "party_size": 99}, t["ada"]), 409, "stale_revision")
    expect("type 400 before stale", call("PATCH", "/reservations/" + ref, {"expected_revision": 2, "table_id": 5}, t["ada"]), 400, "malformed_request")
    s, p = call("PATCH", "/reservations/" + ref, {"expected_revision": 1, "party_size": 3}, t["ada"])
    check("expected_revision matches", s == 200 and p["revision"] == 2 and rrev() == 2, p)
    expect("expected_revision is stale now", call("PATCH", "/reservations/" + ref, {"expected_revision": 1, "party_size": 2}, t["ada"]), 409, "stale_revision")
    call("POST", "/reservations/%s/cancel" % ref, token=t["ada"])
    call("POST", "/reservations/%s/cancel" % ref, token=t["ada"])
    check("cancel bumps once", rrev() == 3 and call("GET", "/reservations/" + ref, token=t["ada"])[1]["revision"] == 3)
    expect("cancelled patch stale first", call("PATCH", "/reservations/" + ref, {"expected_revision": 1}, t["ada"]), 409, "stale_revision")
    expect("cancelled patch", call("PATCH", "/reservations/" + ref, {"expected_revision": 3}, t["ada"]), 409, "reservation_cancelled")
    # cutoff beats field errors, no-op still needs editable booking
    fx = fixture()
    fx["restaurants"][0]["cancellation_cutoff_minutes"] = 10 ** 9
    t = reset(fx)
    s, r = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2)
    expect("noop still cutoff", call("PATCH", "/reservations/" + r["reference"], {}, t["ada"]), 409, "cutoff_passed")
    # concurrency: same expected_revision
    t = reset()
    s, r = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2)
    ref = r["reference"]
    outs = parallel(lambda i: call("PATCH", "/reservations/" + ref, {"expected_revision": 1, "party_size": 1 + (i % 4)}, t["ada"]), 50)
    ok = [o for o in outs if o[0] == 200]
    stale = [o for o in outs if o[0] == 409]
    check("concurrent same revision: one real change wins", len(ok) >= 1 and all(o[0] in (200, 409) for o in outs), [o[0] for o in outs])
    final = call("GET", "/reservations/" + ref, token=t["ada"])[1]
    check("exactly one revision bump (others stale or no-op)", final["revision"] == 2, final)
    h = call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]
    check("history matches revision", len(h) == 2, len(h))
    # policy-driven amendment adopts new terms
    t = reset()
    pub(t["mgr"], policy(eff="2099-10-01", dur=60, cutoff=30, caps={"t_1": 2, "t_2": 3, "t_3": 6}))
    s, r = book(t["ada"], "2099-09-24T19:00", ("t_2",), 4)
    s, p = call("PATCH", "/reservations/" + r["reference"], {"starts_at_local": "2099-10-02T19:00"}, t["ada"])
    check("amend into new policy: capacity 3 < 4", s == 422 and p["error"]["code"] == "party_exceeds_capacity", p)
    s, p = call("PATCH", "/reservations/" + r["reference"], {"starts_at_local": "2099-10-02T19:00", "party_size": 3}, t["ada"])
    check("amend adopts terms+end", s == 200 and p["accepted_terms"]["policy_version"] == 1 and p["ends_at"] == "2099-10-02T20:00:00+02:00" and p["revision"] == 2, p)
    h = call("GET", "/reservations/%s/history" % r["reference"], token=t["ada"])[1]["entries"]
    check("history entries keep their own terms", h[0]["accepted_terms"]["policy_version"] == 0 and h[1]["accepted_terms"]["policy_version"] == 1)
    # F13: later policy never breaks a no-op
    pub(t["mgr"], policy(eff="2099-10-01", hrs=[], dur=60))
    s, p = call("PATCH", "/reservations/" + r["reference"], {"party_size": 3}, t["ada"])
    check("no-op survives closing policy", s == 200 and p["revision"] == 2, p)


def t_series():
    t = reset()
    D = "2099-09-24"
    s, a = book(t["ada"], D + "T19:00", ("t_2",), 3, key="A1")
    ref = a["reference"]
    expect("series no token", call("POST", "/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, None, H()), 401, "unauthenticated")
    expect("series missing key", call("POST", "/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, t["ada"]), 400, "missing_idempotency_key")
    for name, b in {"count 1": {"count": 1}, "count 13": {"count": 13}, "count bool": {"count": True}, "count str": {"count": "3"},
                    "interval 0": {"interval_weeks": 0}, "interval 5": {"interval_weeks": 5}, "interval float": {"interval_weeks": 1.0},
                    "no anchor": {"anchor_reference": None}}.items():
        body = dict({"anchor_reference": ref, "count": 3, "interval_weeks": 1}, **b)
        expect("422 " + name, call("POST", "/series", body, t["ada"], H()), 422, "validation_failed")
    expect("unknown anchor", call("POST", "/series", {"anchor_reference": "NOPE", "count": 3, "interval_weeks": 1}, t["ada"], H()), 404, "not_found")
    expect("other owner's anchor", call("POST", "/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, t["bob"], H()), 404, "not_found")
    r0 = rrev()
    body = {"anchor_reference": ref, "count": 4, "interval_weeks": 2, "junk": 1}
    s, sr = call("POST", "/series", body, t["ada"], H("S1"))
    check("series 201 shape", s == 201 and sr["revision"] == 1 and sr["interval_weeks"] == 2 and len(sr["occurrences"]) == 4
          and [o["index"] for o in sr["occurrences"]] == [0, 1, 2, 3] and all(o["exception"] is False for o in sr["occurrences"])
          and sr["occurrences"][0]["reference"] == ref and sr["occurrences"][0]["reservation"] == a, (s, sr))
    dates = [o["reservation"]["starts_at_local"] for o in sr["occurrences"]]
    check("dates every 2 weeks", dates == ["2099-09-24T19:00", "2099-10-08T19:00", "2099-10-22T19:00", "2099-11-05T19:00"], dates)
    check("distinct refs, rev 1, terms, tables", len({o["reference"] for o in sr["occurrences"]}) == 4
          and all(o["reservation"]["revision"] == 1 and o["reservation"]["table_ids"] == ["t_2"] and o["reservation"]["party_size"] == 3 for o in sr["occurrences"]))
    check("restaurant revision +1 once", rrev() == r0 + 1, (r0, rrev()))
    s, rp = call("POST", "/series", body, t["ada"], H("S1"))
    check("replay 200 identical, no counters", s == 200 and rp == sr and rrev() == r0 + 1, (s, rp))
    expect("already in series", call("POST", "/series", {"anchor_reference": ref, "count": 3, "interval_weeks": 1}, t["ada"], H()), 409, "already_in_series")
    sid = sr["series_id"]
    s, g = call("GET", "/series/" + sid, token=t["ada"])
    check("GET series same shape", s == 200 and g == sr, g)
    expect("GET series other user", call("GET", "/series/" + sid, token=t["bob"]), 404, "not_found")
    expect("GET series no token", call("GET", "/series/" + sid), 404, "not_found")
    expect("GET series unknown", call("GET", "/series/zzz", token=t["ada"]), 404, "not_found")
    s, lst = call("GET", "/reservations", token=t["ada"])
    check("occurrences in ordinary list", len(lst["reservations"]) == 4)
    h = call("GET", "/reservations/%s/history" % sr["occurrences"][2]["reference"], token=t["ada"])[1]["entries"]
    check("occurrence has created history", len(h) == 1 and h[0]["event"] == "created")
    check("anchor history unchanged", len(call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]) == 1)
    s, av = call("GET", "/availability?restaurant_id=r_anker&date=2099-10-08&party_size=3")
    check("occurrence occupies table", "t_2" not in [x for x in av["slots"][2]["available_table_ids"]], av["slots"][2])
    # exception rules
    o1 = sr["occurrences"][1]["reference"]
    call("PATCH", "/reservations/" + o1, {}, t["ada"])
    check("no-op patch: series rev unchanged", call("GET", "/series/" + sid, token=t["ada"])[1]["revision"] == 1)
    call("PATCH", "/reservations/" + o1, {"party_size": 99}, t["ada"])
    check("failed patch: series rev unchanged", call("GET", "/series/" + sid, token=t["ada"])[1]["revision"] == 1)
    s, p = call("PATCH", "/reservations/" + o1, {"party_size": 2}, t["ada"])
    g = call("GET", "/series/" + sid, token=t["ada"])[1]
    check("real patch -> exception + series rev 2", g["revision"] == 2 and g["occurrences"][1]["exception"] is True and g["occurrences"][2]["exception"] is False
          and g["occurrences"][1]["reference"] == o1 and g["occurrences"][1]["reservation"]["party_size"] == 2, g)
    call("PATCH", "/reservations/" + o1, {"party_size": 3}, t["ada"])
    o2 = sr["occurrences"][2]["reference"]
    call("POST", "/reservations/%s/cancel" % o2, token=t["ada"])
    call("POST", "/reservations/%s/cancel" % o2, token=t["ada"])
    g = call("GET", "/series/" + sid, token=t["ada"])[1]
    check("cancel: +1 once, no exception, retained", g["revision"] == 4 and g["occurrences"][2]["exception"] is False
          and g["occurrences"][2]["reservation"]["status"] == "cancelled" and len(g["occurrences"]) == 4, g)
    call("POST", "/reservations/%s/cancel" % ref, token=t["ada"])
    g = call("GET", "/series/" + sid, token=t["ada"])[1]
    check("cancelling anchor keeps siblings", g["occurrences"][0]["reservation"]["status"] == "cancelled" and g["occurrences"][3]["reservation"]["status"] == "confirmed" and g["revision"] == 5, g)
    # cancelled anchor
    s, b2 = book(t["ada"], "2099-12-01T19:00", ("t_3",), 2)
    call("POST", "/reservations/%s/cancel" % b2["reference"], token=t["ada"])
    expect("cancelled anchor", call("POST", "/series", {"anchor_reference": b2["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H()), 409, "reservation_cancelled")
    # cutoff
    fx = fixture()
    fx["restaurants"][0]["cancellation_cutoff_minutes"] = 10 ** 9
    t = reset(fx)
    s, b3 = book(t["ada"], D + "T19:00", ("t_2",), 2)
    expect("anchor cutoff", call("POST", "/series", {"anchor_reference": b3["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H()), 409, "cutoff_passed")
    # atomic failure: third occurrence conflicts
    t = reset()
    s, b4 = book(t["ada"], D + "T19:00", ("t_2",), 2)
    book(t["bob"], "2099-10-08T19:30", ("t_2",), 2)  # blocks occurrence 2 (interval 1 -> 10-08 is index 2)
    r0 = rrev()
    n0 = len(call("GET", "/reservations", token=t["ada"])[1]["reservations"])
    expect("conflict rejects whole adoption", call("POST", "/series", {"anchor_reference": b4["reference"], "count": 5, "interval_weeks": 1}, t["ada"], H("F1")), 409, "table_unavailable")
    check("nothing created or counted", rrev() == r0 and len(call("GET", "/reservations", token=t["ada"])[1]["reservations"]) == n0)
    check("anchor not adopted after failure", call("GET", "/reservations/%s/history" % b4["reference"], token=t["ada"])[0] == 200)
    expect("same key reusable after failure", call("POST", "/series", {"anchor_reference": b4["reference"], "count": 5, "interval_weeks": 1}, t["ada"], H("F1")), 409, "table_unavailable")
    expect("shorter series ok", call("POST", "/series", {"anchor_reference": b4["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H("F2")), 201)
    # per-occurrence policy
    t = reset()
    pub(t["mgr"], policy(eff="2099-01-12", dur=120, cutoff=15, caps={"t_1": 2, "t_2": 3, "t_3": 6}))
    s, b5 = book(t["ada"], "2099-01-05T19:00", ("t_2",), 3)
    s, sr = call("POST", "/series", {"anchor_reference": b5["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    o = sr["occurrences"]
    check("per-occurrence policy", s == 201 and o[0]["reservation"]["accepted_terms"]["policy_version"] == 0 and o[1]["reservation"]["accepted_terms"]["policy_version"] == 1
          and o[1]["reservation"]["ends_at"] == "2099-01-12T21:00:00+01:00" and o[0]["reservation"]["ends_at"] == "2099-01-05T20:30:00+01:00", sr)
    t = reset()
    pub(t["mgr"], policy(eff="2099-01-12", caps={"t_1": 2, "t_2": 3, "t_3": 6}))
    s, b6 = book(t["ada"], "2099-01-05T19:00", ("t_2",), 4)
    expect("later occurrence fails capacity under its policy", call("POST", "/series", {"anchor_reference": b6["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H()), 422, "party_exceeds_capacity")
    # combination anchor
    t = reset()
    s, b7 = book(t["ada"], D + "T19:00", ("t_1", "t_2"), 5)
    s, sr = call("POST", "/series", {"anchor_reference": b7["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    check("pair series keeps selection", s == 201 and sr["occurrences"][1]["reservation"]["table_ids"] == ["t_1", "t_2"] and "table_id" not in sr["occurrences"][1]["reservation"], sr)


def t_series_dst():
    fx = fixture()
    t = reset(fx)
    s, a = book(t["ada"], "2027-03-21T02:30", ("b_1",), 2, "r_be")
    check("anchor ok", s == 201, a)
    r0 = rrev("r_be")
    expect("gap rejects whole adoption", call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H()), 422, "invalid_local_time")
    check("nothing survived", rrev("r_be") == r0 and len(call("GET", "/reservations", token=t["ada"])[1]["reservations"]) == 1)
    s, a2 = book(t["ada"], "2027-10-24T02:30", ("b_1",), 2, "r_be")
    s, sr = call("POST", "/series", {"anchor_reference": a2["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    o = sr["occurrences"][1]["reservation"]
    check("fold -> first occurrence", s == 201 and o["starts_at_local"] == "2027-10-31T02:30" and o["starts_at"] == "2027-10-31T02:30:00+02:00"
          and o["ends_at"] == "2027-10-31T03:00:00+01:00", sr)
    s, a3 = book(t["ada"], "2027-03-07T02:30", ("n_1",), 2, "r_ny")
    expect("NY gap (03-14)", call("POST", "/series", {"anchor_reference": a3["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H()), 422, "invalid_local_time")
    s, a4 = book(t["ada"], "2027-10-31T01:30", ("n_1",), 2, "r_ny")
    s, sr = call("POST", "/series", {"anchor_reference": a4["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    check("NY fold first occurrence", s == 201 and sr["occurrences"][1]["reservation"]["starts_at"] == "2027-11-07T01:30:00-04:00", sr)


def t_moves():
    t = reset()
    D = "2099-09-24"
    s, a = book(t["ada"], D + "T19:00", ("t_1",), 2)
    s, b = book(t["ada"], D + "T19:00", ("t_2",), 2)
    s, c = book(t["ada"], D + "T21:00", ("t_3",), 2)
    A, B, C = a["reference"], b["reference"], c["reference"]
    r0 = rrev()
    mv = lambda moves, k=None: call("POST", "/reservation-moves", {"moves": moves}, t["ada"], H(k))  # noqa
    expect("moves expected_revision str", mv([{"reference": A, "expected_revision": "1"}]), 422, "validation_failed")
    expect("moves stale", mv([{"reference": A, "expected_revision": 2, "table_id": "t_3"}]), 409, "stale_revision")
    expect("moves bad order: second stale", mv([{"reference": A, "table_id": "t_3"}, {"reference": B, "expected_revision": 9}]), 409, "stale_revision")
    check("failures changed nothing", rrev() == r0 and call("GET", "/reservations/" + A, token=t["ada"])[1]["revision"] == 1)
    s, js = mv([{"reference": A, "table_id": "t_2"}, {"reference": B, "table_id": "t_1"}], "MV1")
    check("swap ok", s == 201 and [x["table_ids"] for x in js["reservations"]] == [["t_2"], ["t_1"]] and [x["revision"] for x in js["reservations"]] == [2, 2], js)
    check("restaurant rev +1 once", rrev() == r0 + 1)
    s, rp = mv([{"reference": A, "table_id": "t_2"}, {"reference": B, "table_id": "t_1"}], "MV1")
    check("replay 200 same, no counters", s == 200 and rp == js and rrev() == r0 + 1)
    s, js = mv([{"reference": A, "table_id": "t_2"}, {"reference": C, "expected_revision": 1}], "MV2")
    check("all no-ops: nothing changes, rev kept", s == 201 and all(x["revision"] in (2, 1) for x in js["reservations"]) and rrev() == r0 + 1, js)
    h = call("GET", "/reservations/%s/history" % C, token=t["ada"])[1]["entries"]
    check("no-op item no history", len(h) == 1)
    s, js = mv([{"reference": A, "table_id": "t_2", "party_size": 1}, {"reference": C, "table_ids": ["t_2", "t_3"], "party_size": 5}])
    check("mixed: noop + real change", s == 409 or s == 201, (s, js))
    # series interplay
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ("t_2",), 2)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    refs = [o["reference"] for o in sr["occurrences"]]
    s, js = call("POST", "/reservation-moves", {"moves": [{"reference": refs[1], "table_id": "t_3", "party_size": 2}, {"reference": refs[2], "table_id": "t_3"}, {"reference": refs[0]}]}, t["ada"], H())
    g = call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]
    check("moves: series rev +1 once, changed occurrences are exceptions", s == 201 and g["revision"] == 2 and [o["exception"] for o in g["occurrences"]] == [False, True, True], (s, g["revision"], [o["exception"] for o in g["occurrences"]]))
    s, js = call("POST", "/reservation-moves", {"moves": [{"reference": refs[0], "table_id": "t_3"}, {"reference": refs[1], "table_id": "t_1", "party_size": 99}]}, t["ada"], H())
    g2 = call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]
    check("failed batch: series untouched", s == 422 and g2 == g, (s, g2))
    # amend under policy in moves
    t = reset()
    pub(t["mgr"], policy(eff="2099-10-01", dur=60, caps={"t_1": 2, "t_2": 4, "t_3": 6}))
    s, a = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2)
    s, js = call("POST", "/reservation-moves", {"moves": [{"reference": a["reference"], "starts_at_local": "2099-10-02T19:00"}]}, t["ada"], H())
    check("moves adopt policy", s == 201 and js["reservations"][0]["accepted_terms"]["policy_version"] == 1 and js["reservations"][0]["ends_at"] == "2099-10-02T20:00:00+02:00", js)


def t_export_roundtrip():
    t = reset()
    s, a = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2, key="X1")
    pub(t["mgr"], policy(eff="2099-10-01", dur=60), key="XP")
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 2}, t["ada"], H("XS"))
    call("PATCH", "/reservations/" + sr["occurrences"][1]["reference"], {"party_size": 3}, t["ada"])
    s, exp = call("GET", "/_test/export")
    check("schema 4", exp["state"]["schema"] == 4)
    expected = {
        "series": call("GET", "/series/" + sr["series_id"], token=t["ada"])[1],
        "hist": call("GET", "/reservations/%s/history" % a["reference"], token=t["ada"])[1],
        "list": call("GET", "/reservations", token=t["ada"])[1],
        "policies": call("GET", "/restaurants/r_anker/policies")[1],
        "rev": rrev(),
        "dec": call("GET", "/reservations/%s/decision" % sr["occurrences"][2]["reference"], token=t["ada"])[1]}
    reset()
    expect("import", call("POST", "/_test/import", exp), 204)
    got = {
        "series": call("GET", "/series/" + sr["series_id"], token=t["ada"])[1],
        "hist": call("GET", "/reservations/%s/history" % a["reference"], token=t["ada"])[1],
        "list": call("GET", "/reservations", token=t["ada"])[1],
        "policies": call("GET", "/restaurants/r_anker/policies")[1],
        "rev": rrev(),
        "dec": call("GET", "/reservations/%s/decision" % sr["occurrences"][2]["reference"], token=t["ada"])[1]}
    for k in expected:
        check("round trip " + k, expected[k] == got[k], (expected[k], got[k]))
    s, rp = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 2}, t["ada"], H("XS"))
    check("series replay after import", s == 200 and rp == sr, s)
    s, rp = pub(t["mgr"], policy(eff="2099-10-01", dur=60), key="XP")
    check("policy replay after import", s == 200)
    s, ex2 = call("GET", "/_test/export")
    check("re-export identical", ex2 == exp)
    s, p = pub(t["mgr"], policy(eff="2099-11-01", dur=60))
    check("next policy version continues", p["policy_version"] == 2, p)
    bad = copy.deepcopy(exp)
    bad["state"]["series"][0]["references"] = ["NOPE"]
    expect("invalid series import 422", call("POST", "/_test/import", bad), 422, "validation_failed")
    check("failed import kept state", call("GET", "/series/" + sr["series_id"], token=t["ada"])[0] == 200)


def t_concurrency():
    t = reset()
    toks = [t["ada"], t["bob"]]
    # 50-way booking race on one table, then series races
    outs = parallel(lambda i: book(toks[i % 2], "2099-09-24T19:00", ("t_2",), 2), 50)
    check("race one winner", sorted(o[0] for o in outs).count(201) == 1 and all(o[0] in (201, 409) for o in outs), [o[0] for o in outs])
    check("restaurant revision == committed ops", rrev() == 1)
    t = reset()
    s, a = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2)
    # concurrent series adoption of one anchor with different keys: one wins
    outs = parallel(lambda i: call("POST", "/series", {"anchor_reference": a["reference"], "count": 4, "interval_weeks": 1}, t["ada"], H()), 20)
    codes = sorted(o[0] for o in outs)
    check("concurrent adoption one winner", codes == [201] + [409] * 19, codes)
    check("no extra reservations", len(call("GET", "/reservations", token=t["ada"])[1]["reservations"]) == 4)
    # two users racing series that overlap on the same table
    t = reset()
    s, a1 = book(t["ada"], "2099-09-24T19:00", ("t_2",), 2)
    s, a2 = book(t["bob"], "2099-10-08T19:00", ("t_2",), 2)
    outs = parallel(lambda i: call("POST", "/series", {"anchor_reference": (a1 if i % 2 == 0 else a2)["reference"], "count": 3, "interval_weeks": 1}, (t["ada"] if i % 2 == 0 else t["bob"]), H()), 20)
    check("series race no 5xx", all(o[0] in (201, 409) for o in outs), [o[0] for o in outs])
    s, av = call("GET", "/availability?restaurant_id=r_anker&date=2099-10-08&party_size=1")
    allr = call("GET", "/reservations", token=t["ada"])[1]["reservations"] + call("GET", "/reservations", token=t["bob"])[1]["reservations"]
    conf = [r for r in allr if r["status"] == "confirmed" and r["table_ids"] == ["t_2"]]
    bad = [(x["starts_at"], y["starts_at"]) for i, x in enumerate(conf) for y in conf[i + 1:] if x["starts_at"] < y["ends_at"] and y["starts_at"] < x["ends_at"]]
    check("no overlapping occupancy", not bad, bad[:2])
    # policies publish race (manager)
    outs = parallel(lambda i: pub(t["mgr"], policy(eff="2099-12-%02d" % (1 + i % 28), dur=30 + i % 5)), 40)
    check("policy publication race", all(o[0] == 201 for o in outs) and sorted(o[1]["policy_version"] for o in outs) == list(range(1, 41)), [o[0] for o in outs])


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_"):
            t0 = time.time()
            n0 = len(FAILS)
            try:
                fn()
            except Exception as e:  # noqa
                import traceback
                traceback.print_exc()
                FAILS.append(name + " EXC " + repr(e))
            print("%-26s %s (%.1fs)" % (name, "ok" if len(FAILS) == n0 else "FAILED", time.time() - t0))
    check("no 5xx anywhere", not any(s >= 500 for s in STATUSES), [s for s in STATUSES if s >= 500])
    print("FAILURES:", FAILS if FAILS else "none", "; requests:", len(STATUSES))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
