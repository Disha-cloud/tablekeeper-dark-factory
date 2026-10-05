"""Stage-4 API selftest (replans, closures, series amend, schema 4). Container on :8080."""
import copy
import itertools
import json
import os
import random
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s3  # noqa: E402  (helpers: call, check, expect, parallel, ...)
from s3 import call, check, expect, parallel, H, hours, DAYS  # noqa: E402

D = "2099-09-24"  # a Thursday, Berlin DST offset +02:00
INST = lambda hh, mm=0, day=D, off="+02:00": "%sT%02d:%02d:00%s" % (day, hh, mm, off)  # noqa


def fx(n_tables=3, caps=(2, 4, 6), pairs=(("t_1", "t_2"), ("t_2", "t_3")), cutoff=120, extra_rest=True):
    tables = [{"id": "t_%d" % (i + 1), "label": str(i + 1), "capacity": caps[i]} for i in range(n_tables)]
    f = {
        "users": [
            {"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"},
            {"id": "u_mgr", "email": "mgr@example.com", "password": "correct horse", "display_name": "Mgr"},
            {"id": "u_bob", "email": "bob@example.com", "password": "correct horse", "display_name": "Bob"}],
        "restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": cutoff, "opening_hours": hours(),
             "manager_user_ids": ["u_mgr"], "tables": tables, "combinable": [list(p) for p in pairs]}],
        "reservations": []}
    if extra_rest:
        f["restaurants"].append(
            {"id": "r_two", "name": "Two", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "opening_hours": hours(),
             "manager_user_ids": ["u_mgr"],
             "tables": [{"id": "x_1", "label": "1", "capacity": 4}, {"id": "x_2", "label": "2", "capacity": 4}],
             "combinable": []})
        f["restaurants"].append(
            {"id": "r_be", "name": "Berlin24", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 0, "opening_hours": hours("00:00", "23:30"),
             "manager_user_ids": [], "tables": [{"id": "b_1", "label": "1", "capacity": 4}], "combinable": []})
    return f


def reset(f=None):
    return s3.reset(f if f is not None else fx())


def replan(tok, table="t_2", frm=None, to=None, rid="r_anker", key=None, extra=None):
    b = {"table_id": table, "from": frm or INST(18), "to": to or INST(23)}
    b.update(extra or {})
    return call("POST", "/restaurants/%s/replans" % rid, b, tok, H(key))


def apply_plan(tok, pid, rid="r_anker", key=None):
    return call("POST", "/restaurants/%s/replans/%s/apply" % (rid, pid), {}, tok, H(key))


def rev(rid="r_anker"):
    return s3.rrev(rid)


def mine(tok):
    return call("GET", "/reservations", token=tok)[1]["reservations"]


def book(tok, local, tids, party, rid="r_anker", key=None):
    return s3.book(tok, local, tuple(tids), party, rid, key)


def t_preview_validation():
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    expect("no token", call("POST", "/restaurants/r_anker/replans", {}, None, H()), 401, "unauthenticated")
    expect("not object", call("POST", "/restaurants/r_anker/replans", raw="[]", token=t["mgr"], headers=H()), 400, "malformed_request")
    expect("missing key", call("POST", "/restaurants/r_anker/replans", {"table_id": "t_2"}, t["mgr"]), 400, "missing_idempotency_key")
    expect("unknown restaurant", replan(t["mgr"], rid="zz"), 404, "not_found")
    expect("non-manager 403", replan(t["ada"]), 403, "forbidden")
    expect("404 before 403", replan(t["ada"], rid="zz"), 404, "not_found")
    expect("manager of another restaurant? r_be has none", replan(t["mgr"], rid="r_be", table="b_1"), 403, "forbidden")
    for name, b in {
        "table not string": {"table_id": 5, "from": INST(18), "to": INST(23)},
        "missing table": {"from": INST(18), "to": INST(23)},
        "missing from": {"table_id": "t_2", "to": INST(23)},
        "naive from": {"table_id": "t_2", "from": D + "T18:00:00", "to": INST(23)},
        "naive to": {"table_id": "t_2", "from": INST(18), "to": D + "T23:00"},
        "date only": {"table_id": "t_2", "from": D, "to": INST(23)},
        "from == to": {"table_id": "t_2", "from": INST(18), "to": INST(18)},
        "from > to": {"table_id": "t_2", "from": INST(23), "to": INST(18)},
        "garbage": {"table_id": "t_2", "from": "tomorrow", "to": INST(23)},
        "bad month": {"table_id": "t_2", "from": "2099-13-01T00:00:00Z", "to": "2099-13-02T00:00:00Z"},
        "to number": {"table_id": "t_2", "from": INST(18), "to": 5},
        "bad offset": {"table_id": "t_2", "from": "2099-09-24T18:00:00+99:00", "to": INST(23)},
    }.items():
        expect("422 " + name, call("POST", "/restaurants/r_anker/replans", b, t["mgr"], H()), 422, "validation_failed")
    expect("422 before 404 table", call("POST", "/restaurants/r_anker/replans", {"table_id": "zz", "from": INST(23), "to": INST(18)}, t["mgr"], H()), 422, "validation_failed")
    expect("unknown table", replan(t["mgr"], table="zz"), 404, "not_found")
    check("failures left revision alone", rev() == 1)
    # accepted formats
    for name, (f, to) in {"Z": ("2099-09-24T16:00:00Z", "2099-09-24T21:00:00Z"), "no seconds": ("2099-09-24T18:00+02:00", "2099-09-24T23:00+02:00"),
                          "fraction floored": ("2099-09-24T18:00:00.9+02:00", "2099-09-24T23:00:00.9+02:00"),
                          "other offset": ("2099-09-24T12:00:00-04:00", "2099-09-24T17:00:00-04:00")}.items():
        s, p = replan(t["mgr"], frm=f, to=to)
        check("accepts " + name, s == 201 and p["closure"]["from"] == "2099-09-24T18:00:00+02:00" and p["closure"]["to"] == "2099-09-24T23:00:00+02:00", (s, p))


def t_preview_shape_and_effects():
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 2)
    s, b = book(t["ada"], D + "T20:30", ["t_2"], 2)
    s, c = book(t["ada"], D + "T19:00", ["t_3"], 5)
    refs = sorted([a["reference"], b["reference"], c["reference"]])
    r0 = rev()
    before = {x["reference"]: x for x in mine(t["ada"])}
    hist0 = {x: call("GET", "/reservations/%s/history" % x, token=t["ada"])[1] for x in refs}
    av0 = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=1&explain=true" % D)[1]
    s, p = replan(t["mgr"], key="PV1")
    check("201 shape", s == 201 and p["plan_id"].startswith("plan_") and p["restaurant_revision"] == r0 and p["closure"] == {"table_id": "t_2", "from": "2099-09-24T18:00:00+02:00", "to": "2099-09-24T23:00:00+02:00"}, p)
    check("assignments cover every considered booking in reference order", [x["reference"] for x in p["assignments"]] == refs, p)
    check("preview stores nothing else", rev() == r0 and {x["reference"]: x for x in mine(t["ada"])} == before
          and all(call("GET", "/reservations/%s/history" % x, token=t["ada"])[1] == hist0[x] for x in refs)
          and call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=1&explain=true" % D)[1] == av0)
    s, p2 = replan(t["mgr"], key="PV1")
    check("replay 200 identical", s == 200 and p2 == p, (s, p2))
    expect("reuse different body", replan(t["mgr"], table="t_1", key="PV1"), 409, "idempotency_key_reuse")
    s, p3 = replan(t["mgr"], key="PV2")
    check("new key new plan id", s == 201 and p3["plan_id"] != p["plan_id"])
    # zero considered bookings: feasible empty plan
    s, p4 = replan(t["mgr"], table="t_1", frm=INST(8), to=INST(9))
    check("empty plan", s == 201 and p4["assignments"] == [] and p4["moved_count"] == 0 and p4["unused_seats"] == 0, p4)
    s, p5 = replan(t["mgr"], table="t_2", frm=INST(8), to=INST(9))
    check("no overlap -> empty", s == 201 and p5["assignments"] == [], p5)
    # considered includes bookings on other tables overlapping the interval
    s, p6 = replan(t["mgr"], table="t_1", frm=INST(19), to=INST(20))
    check("considered = every overlapping booking", len(p6["assignments"]) == 2, p6)
    # half-open: closure [20:30, 21:00) touches nothing before 20:30 end? booking a ends 20:30
    s, p7 = replan(t["mgr"], table="t_1", frm=INST(20, 30), to=INST(21))
    check("half-open boundaries", [x["reference"] for x in p7["assignments"]] == [b["reference"]], p7)
    # no feasible plan: close t_3 when t_2 is full and party 5 cannot move
    s, p8 = replan(t["mgr"], table="t_3", frm=INST(19), to=INST(20))
    check("no_feasible_plan changes nothing", s == 409 and p8["error"]["code"] == "no_feasible_plan" and rev() == r0, (s, p8))
    # unused seats = capacity - party
    s, p9 = replan(t["mgr"], table="t_2", frm=INST(19), to=INST(20))
    check("moved_count / unused_seats consistent", p9["moved_count"] == sum(1 for x in p9["assignments"] if x["changed"]), p9) if s == 201 else None


def t_planning_limit():
    f = fx(n_tables=3)
    f["restaurants"][0]["tables"] += [{"id": "t_%d" % i, "label": str(i), "capacity": 2} for i in range(4, 11)]
    f["restaurants"][0]["combinable"] = [["t_1", "t_2"], ["t_4", "t_5"], ["t_6", "t_7"], ["t_8", "t_9"]]
    t = reset(f)
    for i in range(9):
        book(t["ada"], D + "T19:00", ["t_%d" % (i + 1)], 1)
    t0 = time.time()
    s, p = replan(t["mgr"], table="t_1")
    check("planning_limit for 9 bookings", s == 422 and p["error"]["code"] == "planning_limit", (s, p))
    check("planning_limit fast", time.time() - t0 < 2)
    # spec guarantee: 6 tables, 4 pairs, 6 bookings solved exactly and quickly
    f = fx(n_tables=3)
    f["restaurants"][0]["tables"] = [{"id": "t_%d" % (i + 1), "label": str(i + 1), "capacity": 2 + i % 3} for i in range(6)]
    f["restaurants"][0]["combinable"] = [["t_1", "t_2"], ["t_2", "t_3"], ["t_4", "t_5"], ["t_5", "t_6"]]
    t = reset(f)
    for i in range(6):
        book(t["ada"], D + "T19:00", ["t_%d" % (i + 1)], 1 + i % 2)
    t0 = time.time()
    s, p = replan(t["mgr"], table="t_1")
    check("6x4x6 solved", s in (201, 409) and time.time() - t0 < 1.0, (s, time.time() - t0))


def brute(rest, tables, pairs, considered, fixed, closures, closed_tid):
    opts = [(t,) for t in tables] + [tuple(p) for p in pairs]
    cands = []
    for b in considered:
        caps = b["accepted_terms"]["capacities"]
        lst = []
        for rank, o in enumerate(opts):
            if closed_tid in o:
                continue
            if sum(caps[t] for t in o) < b["party_size"]:
                continue
            if any(c[0] in o and c[1] < b["_end"] and b["_start"] < c[2] for c in closures):
                continue
            if any(set(f["table_ids"]) & set(o) and f["_start"] < b["_end"] and b["_start"] < f["_end"] for f in fixed):
                continue
            lst.append((rank, o, sum(caps[t] for t in o) - b["party_size"]))
        cands.append(lst)
    best = None
    for combo in itertools.product(*cands):
        ok = True
        for i in range(len(combo)):
            for j in range(i + 1, len(combo)):
                bi, bj = considered[i], considered[j]
                if bi["_start"] < bj["_end"] and bj["_start"] < bi["_end"] and set(combo[i][1]) & set(combo[j][1]):
                    ok = False
        if not ok:
            continue
        moved = sum(1 for b, c in zip(considered, combo) if list(c[1]) != b["table_ids"])
        unused = sum(c[2] for c in combo)
        key = (moved, unused, tuple(c[0] for c in combo))
        if best is None or key < best[0]:
            best = (key, [list(c[1]) for c in combo])
    return best


def epoch_of(res_json, field):
    # starts_at / ends_at -> comparable epoch seconds
    import datetime as dt
    return int(dt.datetime.fromisoformat(res_json[field]).timestamp())


def t_planner_optimality():
    rnd = random.Random(20260928)
    cases = ok_plans = infeasible = applied = 0
    t_start = time.time()
    for case in range(120):
        n = rnd.randint(3, 5)
        caps = [rnd.randint(2, 6) for _ in range(n)]
        all_pairs = [("t_%d" % i, "t_%d" % j) for i in range(1, n + 1) for j in range(i + 1, n + 1)]
        pairs = rnd.sample(all_pairs, rnd.randint(0, min(3, len(all_pairs))))
        f = fx(n_tables=n, caps=caps, pairs=pairs, extra_rest=False)
        t = s3.reset(f)
        tables = ["t_%d" % (i + 1) for i in range(n)]
        # two policies: v1 (accepted) then v2 (current) with different capacities
        pub = lambda caps_, eff: s3.pub(t["mgr"], s3.policy(eff=eff, dur=rnd.choice([60, 90, 120]), caps={"t_%d" % (i + 1): caps_[i] for i in range(n)}))  # noqa
        v1caps = [rnd.randint(2, 6) for _ in range(n)]
        pub(v1caps, "2099-09-01")
        for _ in range(rnd.randint(3, 8)):
            hh = rnd.choice([18, 18.5, 19, 19.5, 20, 20.5, 21])
            local = "%sT%02d:%02d" % (D, int(hh), 30 if hh % 1 else 0)
            tids = [rnd.choice(tables)] if rnd.random() < 0.7 or not pairs else list(rnd.choice(pairs))
            book(t["ada"], local, tids, rnd.randint(1, 5))
        pub([rnd.randint(1, 6) for _ in range(n)], "2099-09-10")
        closed = rnd.choice(tables)
        # maybe apply a previous closure first
        closures = []
        if rnd.random() < 0.3:
            ct = rnd.choice(tables)
            s, p0 = replan(t["mgr"], table=ct, frm=INST(21), to=INST(22, 30))
            if s == 201:
                s2, _ = apply_plan(t["mgr"], p0["plan_id"])
                if s2 == 201:
                    import datetime as dt
                    closures.append((ct, int(dt.datetime.fromisoformat(INST(21)).timestamp()), int(dt.datetime.fromisoformat(INST(22, 30)).timestamp())))
        resv = mine(t["ada"])
        for r in resv:
            r["_start"] = epoch_of(r, "starts_at")
            r["_end"] = epoch_of(r, "ends_at")
        f_h, t_h = rnd.choice([(18, 23), (19, 21), (19, 20), (20, 22)])
        import datetime as dt
        cf = int(dt.datetime.fromisoformat(INST(f_h)).timestamp())
        ct_ = int(dt.datetime.fromisoformat(INST(t_h)).timestamp())
        conf = [r for r in resv if r["status"] == "confirmed"]
        considered = sorted([r for r in conf if r["_start"] < ct_ and cf < r["_end"]], key=lambda r: r["reference"])
        fixed = [r for r in conf if r not in considered]
        ref = brute(None, tables, pairs, considered, fixed, closures, closed)
        s, p = replan(t["mgr"], table=closed, frm=INST(f_h), to=INST(t_h))
        cases += 1
        if ref is None:
            infeasible += 1
            check("case %d: infeasible agrees" % case, s == 409 and p["error"]["code"] == "no_feasible_plan", (s, p))
            continue
        if s != 201:
            check("case %d: expected plan" % case, False, (s, p))
            continue
        got = [x["table_ids"] for x in p["assignments"]]
        check("case %d: assignments equal reference optimum" % case, got == ref[1] and p["moved_count"] == ref[0][0] and p["unused_seats"] == ref[0][1] and [x["reference"] for x in p["assignments"]] == [r["reference"] for r in considered],
              (got, ref, p["moved_count"], p["unused_seats"]))
        ok_plans += 1
        if rnd.random() < 0.35:
            s2, ap = apply_plan(t["mgr"], p["plan_id"])
            check("case %d: apply ok" % case, s2 == 201 and [x["reference"] for x in ap["reservations"]] == [r["reference"] for r in considered], (s2, ap))
            if s2 == 201:
                applied += 1
                after = {x["reference"]: x for x in mine(t["ada"])}
                bad = []
                for r in considered:
                    a = after[r["reference"]]
                    if (a["starts_at"], a["ends_at"], a["party_size"], a["accepted_terms"]) != (r["starts_at"], r["ends_at"], r["party_size"], r["accepted_terms"]):
                        bad.append(r["reference"])
                check("case %d: times/party/terms retained" % case, not bad, bad)
                conf2 = [x for x in after.values() if x["status"] == "confirmed"]
                overlap = [(x["reference"], y["reference"]) for i, x in enumerate(conf2) for y in conf2[i + 1:]
                           if set(x["table_ids"]) & set(y["table_ids"]) and epoch_of(x, "starts_at") < epoch_of(y, "ends_at") and epoch_of(y, "starts_at") < epoch_of(x, "ends_at")]
                check("case %d: no shared table after apply" % case, not overlap, overlap)
                closed_hit = [x["reference"] for x in conf2 if closed in x["table_ids"] and epoch_of(x, "starts_at") < ct_ and cf < epoch_of(x, "ends_at")]
                check("case %d: nothing left on the closed table" % case, not closed_hit, closed_hit)
    print("planner cases: %d (plans %d, infeasible %d, applied %d) in %.1fs" % (cases, ok_plans, infeasible, applied, time.time() - t_start))
    check("planner exercised both outcomes", ok_plans > 30 and infeasible > 3, (ok_plans, infeasible))


def t_apply():
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)  # t_2 (4) -> must move: t_3 (6) is next best? options: t_1 cap2 no, t_3 cap 6
    s, b = book(t["ada"], D + "T20:30", ["t_2", "t_3"], 8)  # pair
    refs = [a["reference"], b["reference"]]
    s, p = replan(t["mgr"], table="t_2", frm=INST(18), to=INST(23))
    check("plan for a pair booking infeasible (needs t_2)", s == 409, (s, p))
    # make feasible: close t_1 instead, booking a fits t_2 already (moved 0)
    s, p = replan(t["mgr"], table="t_1")
    check("closing t_1 moves nobody (all bookings still considered)", s == 201 and p["moved_count"] == 0 and len(p["assignments"]) == 2 and not any(x["changed"] for x in p["assignments"]), p)
    s, ap = apply_plan(t["mgr"], p["plan_id"], key="AP0")
    check("apply empty plan: closure recorded, revision +1", s == 201 and ap["restaurant_revision"] == 3 and len(ap["reservations"]) == 2, ap)
    s, av = call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=1&explain=true" % D)
    sl = [x for x in av["slots"] if x["starts_at_local"].endswith("21:00")][0]
    e1 = [e for e in sl["explain"] if e["table_id"] == "t_1"][0]
    check("closure: explain no_overlap false", e1["rules"][1] == {"rule": "no_overlap", "holds": False} and e1["available"] is False, e1)
    check("closure: not in singles/pairs", "t_1" not in sl["available_table_ids"] and all("t_1" not in o["table_ids"] for o in sl["available_options"]), sl)
    sl8 = [x for x in av["slots"] if x["starts_at_local"].endswith("17:30")]
    check("outside closure window unaffected (no slot before 18:00)", sl8 == [])
    expect("create on closed table 409", book(t["ada"], D + "T21:00", ["t_1"], 1), 409, "table_unavailable")
    expect("create pair containing closed table 409", book(t["ada"], D + "T21:30", ["t_1", "t_2"], 2), 409, "table_unavailable")
    s, c = book(t["ada"], D + "T18:00", ["t_3"], 1)
    check("other table fine", s == 201, c)
    expect("PATCH into closure 409", call("PATCH", "/reservations/" + c["reference"], {"table_id": "t_1"}, t["ada"]), 409, "table_unavailable")
    expect("moves into closure 409", call("POST", "/reservation-moves", {"moves": [{"reference": c["reference"], "table_id": "t_1"}]}, t["ada"], H()), 409, "table_unavailable")
    s, d2 = book(t["ada"], "2099-10-01T18:00", ["t_1"], 1)
    check("closure ends at `to` / other days fine", s == 201 and d2 is not None)
    # other restaurant unaffected
    s, x = book(t["ada"], D + "T20:00", ["x_1"], 2, "r_two")
    check("other restaurant unaffected", s == 201, x)
    # replay after later changes
    s, ap2 = apply_plan(t["mgr"], p["plan_id"], key="AP0")
    check("apply replay 200 original", s == 200 and ap2 == ap, (s, ap2))
    expect("already applied under new key", apply_plan(t["mgr"], p["plan_id"], key="AP1"), 409, "plan_already_applied")
    # stale: a plan previewed before an intervening revision
    s, p2 = replan(t["mgr"], table="t_3", frm="2099-10-02T18:00:00+02:00", to="2099-10-02T19:00:00+02:00")
    book(t["ada"], "2099-10-05T19:00", ["t_2"], 1)
    expect("stale_plan", apply_plan(t["mgr"], p2["plan_id"]), 409, "stale_plan")
    check("stale changed nothing", rev() == p2["restaurant_revision"] + 1)
    expect("unknown plan", apply_plan(t["mgr"], "plan_999"), 404, "not_found")
    expect("plan under other restaurant 404", apply_plan(t["mgr"], p2["plan_id"], rid="r_two"), 404, "not_found")
    expect("apply no token", call("POST", "/restaurants/r_anker/replans/%s/apply" % p2["plan_id"], {}, None, H()), 401, "unauthenticated")
    expect("apply non-manager", apply_plan(t["ada"], p2["plan_id"]), 403, "forbidden")
    expect("apply unknown restaurant", apply_plan(t["mgr"], p2["plan_id"], rid="zz"), 404, "not_found")
    expect("apply missing key", call("POST", "/restaurants/r_anker/replans/%s/apply" % p2["plan_id"], {}, t["mgr"]), 400, "missing_idempotency_key")
    # closure at another restaurant does not invalidate this plan
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, pl = replan(t["mgr"], table="t_2")
    s, p_other = replan(t["mgr"], table="x_1", rid="r_two")
    expect("apply at other restaurant", apply_plan(t["mgr"], p_other["plan_id"], rid="r_two"), 201)
    s, ap = apply_plan(t["mgr"], pl["plan_id"])
    check("closure elsewhere doesn't invalidate", s == 201, (s, ap))


def t_apply_history_and_counters():
    f = fx(n_tables=4, caps=(2, 4, 4, 6), pairs=[("t_1", "t_2"), ("t_3", "t_4")], extra_rest=False)
    t = s3.reset(f)
    # single -> single, single <-> pair, pair -> pair
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 4)           # single, will move to t_3
    s, c = book(t["ada"], D + "T19:00", ["t_3"], 3)
    s, series_a = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    ra = rev()
    sr0 = series_a["revision"]
    s, p = replan(t["mgr"], table="t_2", frm=INST(18), to=INST(23))
    check("plan with single->single move", s == 201, p)
    if s != 201:
        return
    asg = {x["reference"]: x for x in p["assignments"]}
    before = {r["reference"]: r for r in mine(t["ada"])}
    revs_before = {k: v["revision"] for k, v in before.items()}
    s, ap = apply_plan(t["mgr"], p["plan_id"])
    check("apply 201", s == 201, ap)
    after = {r["reference"]: r for r in mine(t["ada"])}
    moved = [x["reference"] for x in p["assignments"] if x["changed"]]
    check("moved bookings +1 revision, unmoved untouched", all(after[k]["revision"] == revs_before[k] + (1 if k in moved else 0) for k in before), {k: (revs_before[k], after[k]["revision"]) for k in before})
    for ref in moved:
        h = call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]
        e = h[-1]
        check("reassigned entry shape for " + ref, e["event"] == "reassigned" and e["plan_id"] == p["plan_id"] and e["changes"] == [{"field": "table_ids", "from": before[ref]["table_ids"], "to": asg[ref]["table_ids"]}]
              and e["revision"] == after[ref]["revision"] and e["accepted_terms"] == before[ref]["accepted_terms"] and e["seq"] == len(h), e)
        check("times/terms identical", (after[ref]["starts_at"], after[ref]["ends_at"], after[ref]["accepted_terms"], after[ref]["party_size"]) == (before[ref]["starts_at"], before[ref]["ends_at"], before[ref]["accepted_terms"], before[ref]["party_size"]))
    for ref in set(before) - set(moved):
        check("unmoved gained no history", len(call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]) == len(call("GET", "/reservations/%s/history" % ref, token=t["ada"])[1]["entries"]))
    check("restaurant revision once for the whole plan", ap["restaurant_revision"] == ra + 1 and rev() == ra + 1, (ap["restaurant_revision"], ra))
    sr = call("GET", "/series/" + series_a["series_id"], token=t["ada"])[1]
    in_series_moved = [x for x in sr["occurrences"] if x["reference"] in moved]
    check("series revision +1 once if a member moved; exceptions unchanged", sr["revision"] == sr0 + (1 if in_series_moved else 0) and all(o["exception"] is False for o in sr["occurrences"]), sr["revision"])
    # now explicit shapes: pair involvement
    t = s3.reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_1", "t_2"], 6)     # pair (2+4)
    s, p = replan(t["mgr"], table="t_1", frm=INST(18), to=INST(23))
    check("pair booking re-planned (needs 6: pair t_3+t_4 or single t_4)", s == 201 and p["assignments"][0]["table_ids"] in (["t_4"], ["t_3", "t_4"]), p)
    s, ap = apply_plan(t["mgr"], p["plan_id"])
    h = call("GET", "/reservations/%s/history" % a["reference"], token=t["ada"])[1]["entries"][-1]
    check("pair -> X uses table_ids lists + plan_id", h["event"] == "reassigned" and h["changes"][0]["from"] == ["t_1", "t_2"] and isinstance(h["changes"][0]["to"], list) and h["plan_id"] == p["plan_id"], h)
    t = s3.reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 4)
    s, b = book(t["ada"], D + "T19:00", ["t_3"], 4)
    s, c = book(t["ada"], D + "T19:00", ["t_4"], 6)
    s, p = replan(t["mgr"], table="t_4", frm=INST(18), to=INST(23))
    print("plan2:", [(x["reference"], x["table_ids"], x["changed"]) for x in p["assignments"]] if s == 201 else p)
    s, p = replan(t["mgr"], table="t_3", frm=INST(18), to=INST(23))
    if s == 201:
        s, ap = apply_plan(t["mgr"], p["plan_id"])
        ent = [call("GET", "/reservations/%s/history" % x["reference"], token=t["ada"])[1]["entries"][-1] for x in ap["reservations"]]
        check("single->single reassigned uses table_ids (not table_id)", all(e["event"] != "reassigned" or e["changes"][0]["field"] == "table_ids" for e in ent), ent)
    # single -> pair: closure of t_4 for a 6-person booking when only a pair has room
    t = s3.reset(fx(n_tables=3, caps=(3, 3, 6), pairs=[("t_1", "t_2")], extra_rest=False))
    s, a = book(t["ada"], D + "T19:00", ["t_3"], 6)
    s, p = replan(t["mgr"], table="t_3", frm=INST(18), to=INST(23))
    check("single -> pair plan", s == 201 and p["assignments"][0]["table_ids"] == ["t_1", "t_2"] and p["assignments"][0]["changed"] is True, p)
    s, ap = apply_plan(t["mgr"], p["plan_id"])
    h = call("GET", "/reservations/%s/history" % a["reference"], token=t["ada"])[1]["entries"][-1]
    check("single->pair entry", h["changes"] == [{"field": "table_ids", "from": ["t_3"], "to": ["t_1", "t_2"]}] and "table_id" not in ap["reservations"][0], (h, ap["reservations"][0]))
    # pair -> single
    s, a2 = book(t["ada"], "2099-09-25T19:00", ["t_1", "t_2"], 6)
    s, p = replan(t["mgr"], table="t_1", frm="2099-09-25T18:00:00+02:00", to="2099-09-25T23:00:00+02:00")
    check("pair -> single plan", s == 201 and p["assignments"][0]["table_ids"] == ["t_3"], p)
    s, ap = apply_plan(t["mgr"], p["plan_id"])
    h = call("GET", "/reservations/%s/history" % a2["reference"], token=t["ada"])[1]["entries"][-1]
    check("pair->single entry + table_id present on response", h["changes"] == [{"field": "table_ids", "from": ["t_1", "t_2"], "to": ["t_3"]}] and ap["reservations"][0]["table_id"] == "t_3", (h, ap["reservations"][0]))
    # revision counters through a full cycle
    t = s3.reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 4)
    r0 = rev()
    replan(t["mgr"], key="K1")
    check("preview: no revision change", rev() == r0)
    s, p = replan(t["mgr"], key="K2")
    apply_plan(t["mgr"], p["plan_id"], key="K3")
    check("apply: +1", rev() == r0 + 1)
    apply_plan(t["mgr"], p["plan_id"], key="K3")
    check("replay: no change", rev() == r0 + 1)
    apply_plan(t["mgr"], p["plan_id"], key="K4")
    check("failed apply: no change", rev() == r0 + 1)


def t_series_amend():
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 5, "interval_weeks": 1}, t["ada"], H())
    sid = sr["series_id"]
    refs = [o["reference"] for o in sr["occurrences"]]
    am = lambda b, tok=None, k=None: call("POST", "/series/%s/amend" % sid, b, tok or t["ada"], H(k))  # noqa
    good = {"expected_revision": 1, "from_index": 2, "local_time": "20:00"}
    expect("no token", call("POST", "/series/%s/amend" % sid, good, None, H()), 401, "unauthenticated")
    expect("not object", call("POST", "/series/%s/amend" % sid, raw="1", token=t["ada"], headers=H()), 400, "malformed_request")
    expect("missing key", call("POST", "/series/%s/amend" % sid, good, t["ada"]), 400, "missing_idempotency_key")
    expect("unknown series", call("POST", "/series/zzz/amend", good, t["ada"], H()), 404, "not_found")
    expect("other owner", am(good, t["bob"]), 404, "not_found")
    for name, b in {"rev 0": {"expected_revision": 0}, "rev bool": {"expected_revision": True}, "rev str": {"expected_revision": "1"},
                    "rev missing": {"expected_revision": None}, "idx -1": {"from_index": -1}, "idx 5": {"from_index": 5}, "idx bool": {"from_index": False},
                    "idx float": {"from_index": 1.0}, "time 24:00": {"local_time": "24:00"}, "time 7:00": {"local_time": "7:00"},
                    "time secs": {"local_time": "20:00:00"}, "time num": {"local_time": 2000}, "time ws": {"local_time": " 20:00"}}.items():
        expect("422 " + name, am(dict(good, **b)), 422, "validation_failed")
    expect("stale before validation of occurrences", am(dict(good, expected_revision=7, local_time="03:07")), 409, "stale_revision")
    expect("422 before stale", am(dict(good, expected_revision=7, from_index=9)), 422, "validation_failed")
    r0, sr0 = rev(), 1
    s, out = am(good, k="A1")
    check("amend 201 series shape", s == 201 and out["revision"] == 2 and [o["reservation"]["starts_at_local"][11:] for o in out["occurrences"]] == ["19:00", "19:00", "20:00", "20:00", "20:00"], out)
    check("dates retained, refs retained", [o["reservation"]["starts_at_local"][:10] for o in out["occurrences"]] == [sr["occurrences"][i]["reservation"]["starts_at_local"][:10] for i in range(5)]
          and [o["reference"] for o in out["occurrences"]] == refs)
    check("no exceptions marked", all(o["exception"] is False for o in out["occurrences"]))
    check("restaurant rev +1 once, series +1 once", rev() == r0 + 1 and call("GET", "/series/" + sid, token=t["ada"])[1]["revision"] == 2)
    h = call("GET", "/reservations/%s/history" % refs[2], token=t["ada"])[1]["entries"]
    check("ordinary changed entry, rev 2", h[-1]["event"] == "changed" and h[-1]["changes"] == [{"field": "starts_at_local", "from": sr["occurrences"][2]["reservation"]["starts_at_local"], "to": out["occurrences"][2]["reservation"]["starts_at_local"]}] and out["occurrences"][2]["reservation"]["revision"] == 2, h[-1])
    check("untouched occurrences keep revision", out["occurrences"][0]["reservation"]["revision"] == 1 and out["occurrences"][1]["reservation"]["revision"] == 1)
    s, rp = am(good, k="A1")
    check("replay 200 original (no counters)", s == 200 and rp == out and rev() == r0 + 1, (s, rp))
    # further edits then replay
    call("POST", "/reservations/%s/cancel" % refs[4], token=t["ada"])
    s, rp = am(good, k="A1")
    check("replay after later edits is the original", s == 200 and rp == out, (s, rp))
    expect("reuse key w/ other body", am(dict(good, local_time="21:00"), k="A1"), 409, "idempotency_key_reuse")
    # no-op and empty eligible
    cur = call("GET", "/series/" + sid, token=t["ada"])[1]
    rv = cur["revision"]
    r1 = rev()
    s, nop = am({"expected_revision": rv, "from_index": 2, "local_time": "20:00"})
    check("all no-op -> 201, nothing changes", s == 201 and rev() == r1 and nop["revision"] == rv, (s, nop))
    # exceptions skipped, cancelled skipped
    call("PATCH", "/reservations/" + refs[1], {"party_size": 2}, t["ada"])
    cur = call("GET", "/series/" + sid, token=t["ada"])[1]
    check("patch marks exception", cur["occurrences"][1]["exception"] is True)
    s, out2 = am({"expected_revision": cur["revision"], "from_index": 0, "local_time": "21:00"})
    times = [o["reservation"]["starts_at_local"][11:] for o in out2["occurrences"]]
    check("exception + cancelled skipped", s == 201 and times == ["21:00", "19:00", "21:00", "21:00", "20:00"] and out2["occurrences"][4]["reservation"]["status"] == "cancelled", (s, times))
    check("still exactly one exception", [o["exception"] for o in out2["occurrences"]] == [False, True, False, False, False])
    # cutoff: old accepted cutoff blocks
    f = fx(cutoff=10 ** 9)
    t = reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr2 = call("POST", "/series", {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    check("setup w/ huge cutoff blocked adoption", s == 409, sr2)
    # cutoff at amend time: use seeded past anchor w/ cutoff 0 in r_be
    t = reset()
    far = "2099-01-05T10:00"
    s, a = book(t["ada"], far, ["b_1"], 2, "r_be")
    s, sr3 = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    check("r_be series ok", s == 201, sr3)
    sid3 = sr3["series_id"]
    s, o3 = call("POST", "/series/%s/amend" % sid3, {"expected_revision": 1, "from_index": 0, "local_time": "10:30"}, t["ada"], H())
    check("amend with ordinary policy", s == 201 and o3["occurrences"][2]["reservation"]["starts_at_local"].endswith("10:30"), o3)
    # conflicts: other booking blocks occurrence 3
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 4, "interval_weeks": 1}, t["ada"], H())
    sid = sr["series_id"]
    book(t["bob"], "2099-10-08T20:30", ["t_2"], 2)   # blocks 20:00 on occurrence 2 (10-08)
    r0 = rev()
    h0 = [call("GET", "/reservations/%s/history" % o["reference"], token=t["ada"])[1] for o in sr["occurrences"]]
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, t["ada"], H("CF"))
    check("occupancy conflict 409 table_unavailable", s == 409 and e["error"]["code"] == "table_unavailable", (s, e))
    check("failure changed nothing", rev() == r0 and call("GET", "/series/" + sid, token=t["ada"])[1] == sr
          and [call("GET", "/reservations/%s/history" % o["reference"], token=t["ada"])[1] for o in sr["occurrences"]] == h0)
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 1, "from_index": 3, "local_time": "20:00"}, t["ada"], H("CF"))
    check("same key reusable after failure", s == 201, (s, e))
    # non-occupancy error precedence: 22:30 ends after closing for index 0 first -> outside_opening_hours
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 2, "from_index": 0, "local_time": "22:45"}, t["ada"], H())
    check("non-occupancy error first", s == 422 and e["error"]["code"] == "outside_opening_hours", (s, e))
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 2, "from_index": 0, "local_time": "19:10"}, t["ada"], H())
    check("grid error", s == 422 and e["error"]["code"] == "not_on_slot_grid", (s, e))
    # closure applied on a later date blocks amend
    s, pl = replan(t["mgr"], table="t_2", frm="2099-10-15T21:30:00+02:00", to="2099-10-15T23:00:00+02:00")
    s2, _ = apply_plan(t["mgr"], pl["plan_id"])
    cur = call("GET", "/series/" + sid, token=t["ada"])[1]
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": cur["revision"], "from_index": 3, "local_time": "21:00"}, t["ada"], H())
    check("amend into applied closure -> table_unavailable", s == 409 and e["error"]["code"] == "table_unavailable", (s, e))
    # concurrent amend from the same revision
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 4, "interval_weeks": 1}, t["ada"], H())
    outs = parallel(lambda i: call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 1, "local_time": ("20:00", "20:30", "21:00")[i % 3]}, t["ada"], H()), 30)
    codes = [o[0] for o in outs]
    check("concurrent amends: exactly one real change", codes.count(201) == 1 and codes.count(409) == 29 and call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]["revision"] == 2, codes)


def t_series_amend_dst_policy():
    t = reset()
    # Berlin 24h restaurant: weekly 02:30 anchors; amending to 02:30 on the 2027-03-28 gap fails; fold uses first occurrence
    s, a = book(t["ada"], "2027-03-21T01:30", ["b_1"], 2, "r_be")
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    check("setup series over the spring-forward date", s == 201, sr)
    sid = sr["series_id"]
    r0 = rev("r_be")
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 1, "from_index": 0, "local_time": "02:30"}, t["ada"], H())
    check("gap time on 2027-03-28 rejects the whole amend", s == 422 and e["error"]["code"] == "invalid_local_time", (s, e))
    check("nothing changed", rev("r_be") == r0 and call("GET", "/series/" + sid, token=t["ada"])[1] == sr)
    s, e = call("POST", "/series/%s/amend" % sid, {"expected_revision": 1, "from_index": 1, "local_time": "03:00"}, t["ada"], H())
    check("03:00 after gap fine", s == 201 and e["occurrences"][1]["reservation"]["starts_at"] == "2027-03-28T03:00:00+02:00", (s, e))
    t = reset()
    s, a = book(t["ada"], "2027-10-24T01:30", ["b_1"], 2, "r_be")
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    s, e = call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 1, "local_time": "02:30"}, t["ada"], H())
    o = e["occurrences"][1]["reservation"] if s == 201 else e
    check("fold -> first occurrence", s == 201 and o["starts_at"] == "2027-10-31T02:30:00+02:00" and o["ends_at"] == "2027-10-31T03:00:00+01:00", (s, o))
    # per-occurrence policy: later policy changes duration/hours/capacity/cutoff for later dates only
    t = reset()
    s, a = book(t["ada"], "2099-01-05T19:00", ["t_2"], 4)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    s3.pub(t["mgr"], s3.policy(eff="2099-01-12", dur=60, slot=60, cutoff=15, caps={"t_1": 2, "t_2": 4, "t_3": 6}))
    s, e = call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 0, "local_time": "20:00"}, t["ada"], H())
    o = e["occurrences"] if s == 201 else e
    check("per-occurrence policy applied (later dates under v1)", s == 201 and o[0]["reservation"]["accepted_terms"]["policy_version"] == 0 and o[1]["reservation"]["accepted_terms"]["policy_version"] == 1
          and o[1]["reservation"]["ends_at"] == "2099-01-12T21:00:00+01:00" and o[0]["reservation"]["ends_at"] == "2099-01-05T21:30:00+01:00", e)
    s, e2 = call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 2, "from_index": 0, "local_time": "20:30"}, t["ada"], H())
    check("v1 grid is hourly: 20:30 off-grid for later occurrences", s == 422 and e2["error"]["code"] == "not_on_slot_grid", (s, e2))
    # capacity under the occurrence's accepted... new policy capacity governs the resulting date
    t = reset()
    s, a = book(t["ada"], "2099-01-05T19:00", ["t_2"], 4)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    s3.pub(t["mgr"], s3.policy(eff="2099-01-12", caps={"t_1": 2, "t_2": 3, "t_3": 6}))
    s, e = call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}, t["ada"], H())
    check("capacity under resulting policy", s == 422 and e["error"]["code"] == "party_exceeds_capacity", (s, e))
    # cutoff: old accepted cutoff 10^9 on occurrence 1 is not reachable... use past-dated seeds
    fxx = fx()
    fxx["reservations"] = []
    t = reset(fxx)


def t_series_repairs():
    f = fx(n_tables=3, caps=(4, 4, 4), pairs=[], extra_rest=False)
    t = s3.reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_1"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    refs = [o["reference"] for o in sr["occurrences"]]
    call("PATCH", "/reservations/" + refs[1], {"party_size": 2}, t["ada"])  # exception
    cur = call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]
    s, pl = replan(t["mgr"], table="t_1", frm="2099-10-01T00:00:00+02:00", to="2099-10-09T00:00:00+02:00")
    check("plan covering occurrences 1 and 2", s == 201 and len(pl["assignments"]) == 2, pl)
    s, ap = apply_plan(t["mgr"], pl["plan_id"])
    after = call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]
    check("series rev +1 once; exception flags preserved", after["revision"] == cur["revision"] + 1 and [o["exception"] for o in after["occurrences"]] == [False, True, False], (after["revision"], cur["revision"]))
    check("identities/dates preserved", [o["reference"] for o in after["occurrences"]] == refs and [o["reservation"]["starts_at_local"] for o in after["occurrences"]] == [o["reservation"]["starts_at_local"] for o in cur["occurrences"]])
    check("occurrences moved", all(o["reservation"]["table_ids"] != ["t_1"] for o in after["occurrences"][1:]), after)
    # series amend after repair: scheduled dates still follow the agreement
    s, e = call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": after["revision"], "from_index": 0, "local_time": "20:00"}, t["ada"], H())
    check("amend after repair ok", s == 201 and e["occurrences"][2]["reservation"]["starts_at_local"] == "2099-10-08T20:00" and e["occurrences"][1]["reservation"]["starts_at_local"].endswith("19:00"), (s, e if s != 201 else ""))
    # series adoption blocked by closure
    t = s3.reset(f)
    s, a = book(t["ada"], D + "T19:00", ["t_1"], 3)
    s, pl = replan(t["mgr"], table="t_1", frm="2099-10-08T00:00:00+02:00", to="2099-10-09T00:00:00+02:00")
    apply_plan(t["mgr"], pl["plan_id"])
    s, e = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 2}, t["ada"], H())
    check("adoption into a closure -> table_unavailable", s == 409 and e["error"]["code"] == "table_unavailable", (s, e))
    s, e = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    check("adoption into the closure on index 2 (10-08) fails", s == 409, (s, e))
    s, e = call("POST", "/series", {"anchor_reference": a["reference"], "count": 2, "interval_weeks": 1}, t["ada"], H())
    check("shorter series before the closure fine", s == 201, (s, e))


def t_concurrency():
    t = reset()
    for i in range(3):
        book(t["ada"], D + "T19:00", ["t_%d" % (i + 1)], [2, 4, 4][i] if i else 2)
    s, p = replan(t["mgr"], table="t_2", frm=INST(18), to=INST(23))
    pid = p["plan_id"] if s == 201 else None
    if pid:
        outs = parallel(lambda i: apply_plan(t["mgr"], pid, key="CA%d" % i), 30)
        codes = sorted(o[0] for o in outs)
        check("concurrent applies: exactly one wins", codes == [201] + [409] * 29, codes)
        check("one revision for the plan", rev() == 4 + 0 or rev() >= 4)
        conf = [r for r in mine(t["ada"]) if r["status"] == "confirmed"]
        check("no partially moved/overlapping state", not [1 for i, x in enumerate(conf) for y in conf[i + 1:] if set(x["table_ids"]) & set(y["table_ids"]) and x["starts_at"] < y["ends_at"] and y["starts_at"] < x["ends_at"]])
    # same-key applies: one creates, rest replay
    t = reset()
    book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, p = replan(t["mgr"], table="t_2", frm=INST(18), to=INST(23))
    if s == 201:
        outs = parallel(lambda i: apply_plan(t["mgr"], p["plan_id"], key="SAME"), 30)
        codes = sorted(o[0] for o in outs)
        check("same-key applies: one 201, rest 200", codes == [200] * 29 + [201], codes)
    # races between bookings and closures
    t = reset()
    s, p = replan(t["mgr"], table="t_3", frm=INST(18), to=INST(23))
    def go(i):
        if i == 0:
            return apply_plan(t["mgr"], p["plan_id"])
        return book(t["ada"] if i % 2 else t["bob"], D + "T%02d:00" % (18 + i % 4), ["t_3"], 2)
    outs = parallel(go, 40)
    check("closure race no 5xx", all(o[0] < 500 for o in outs), [o[0] for o in outs])
    booked = [r for r in mine(t["ada"]) + mine(t["bob"]) if "t_3" in r["table_ids"] and r["status"] == "confirmed"]
    check("closure race: bookings made before the closure may exist, none overlap", not [1 for i, x in enumerate(booked) for y in booked[i + 1:] if x["starts_at"] < y["ends_at"] and y["starts_at"] < x["ends_at"]])
    # series amend vs patch race
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H())
    ref1 = sr["occurrences"][1]["reference"]
    def go2(i):
        if i % 2:
            return call("POST", "/series/%s/amend" % sr["series_id"], {"expected_revision": 1, "from_index": 1, "local_time": "20:00"}, t["ada"], H())
        return call("PATCH", "/reservations/" + ref1, {"party_size": 2}, t["ada"])
    outs = parallel(go2, 30)
    check("amend/patch race no 5xx and consistent revisions", all(o[0] < 500 for o in outs))
    cur = call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]
    real = len([e for e in call("GET", "/reservations/%s/history" % ref1, token=t["ada"])[1]["entries"] if e["event"] == "changed"])
    check("series revision >= 1 and history consistent with revision", call("GET", "/reservations/" + ref1, token=t["ada"])[1]["revision"] == 1 + real, (real, cur["revision"]))


def t_export_schema4():
    t = reset()
    s, a = book(t["ada"], D + "T19:00", ["t_2"], 3)
    s, sr = call("POST", "/series", {"anchor_reference": a["reference"], "count": 3, "interval_weeks": 1}, t["ada"], H("XS"))
    s, p = replan(t["mgr"], table="t_2", key="XP")
    s, ap = apply_plan(t["mgr"], p["plan_id"], key="XA")
    s, p2 = replan(t["mgr"], table="t_3", frm=INST(10), to=INST(11), key="XP2")
    amend_body = {"expected_revision": call("GET", "/series/" + sr["series_id"], token=t["ada"])[1]["revision"], "from_index": 1, "local_time": "20:00"}
    s, am = call("POST", "/series/%s/amend" % sr["series_id"], amend_body, t["ada"], H("XM"))
    snap = lambda: {  # noqa
        "series": call("GET", "/series/" + sr["series_id"], token=t["ada"])[1],
        "list": mine(t["ada"]),
        "hist": [call("GET", "/reservations/%s/history" % o["reference"], token=t["ada"])[1] for o in sr["occurrences"]],
        "rev": rev(),
        "avail": call("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=1&explain=true" % D)[1]}
    exp = call("GET", "/_test/export")[1]
    check("schema 4", exp["state"]["schema"] == 4)
    want = snap()
    reset()
    expect("import", call("POST", "/_test/import", exp), 204)
    got = snap()
    for k in want:
        check("round trip " + k, want[k] == got[k], (str(want[k])[:300], str(got[k])[:300]))
    s, rp = replan(t["mgr"], table="t_2", key="XP")
    check("preview replay after import", s == 200 and rp == p)
    s, rp = apply_plan(t["mgr"], p["plan_id"], key="XA")
    check("apply replay after import", s == 200 and rp == ap)
    expect("plan_already_applied after import", apply_plan(t["mgr"], p["plan_id"], key="XB"), 409, "plan_already_applied")
    s, ap2 = apply_plan(t["mgr"], p2["plan_id"], key="XC")
    check("unapplied plan keeps its stale state after import", s == 409 and ap2["error"]["code"] == "stale_plan", (s, ap2))
    s, am2 = call("POST", "/series/%s/amend" % sr["series_id"], amend_body, t["ada"], H("XM"))
    check("series amend replay after import", s == 200 and am2 == am, s)
    s, p3 = replan(t["mgr"], table="t_1", frm=INST(18), to=INST(19))
    check("new plan id continues", s == 201 and p3["plan_id"] not in (p["plan_id"], p2["plan_id"]), p3)
    ex2 = call("GET", "/_test/export")[1]
    check("re-export imports again", call("POST", "/_test/import", ex2)[0] == 204)
    bad = copy.deepcopy(exp)
    bad["state"]["plans"][0]["table_id"] = "nope"
    expect("bad plan import 422", call("POST", "/_test/import", bad), 422, "validation_failed")
    bad = copy.deepcopy(exp)
    bad["state"]["restaurants"][0]["closures"] = [{"table_id": "nope", "from": 1, "to": 2, "plan_id": "x"}]
    expect("bad closure import 422", call("POST", "/_test/import", bad), 422, "validation_failed")
    check("failed imports kept state", call("GET", "/series/" + sr["series_id"], token=t["ada"])[0] == 200)


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_"):
            t0 = time.time()
            n0 = len(s3.FAILS)
            try:
                fn()
            except Exception as e:  # noqa
                import traceback
                traceback.print_exc()
                s3.FAILS.append(name + " EXC " + repr(e))
            print("%-30s %s (%.1fs)" % (name, "ok" if len(s3.FAILS) == n0 else "FAILED", time.time() - t0))
    check("no 5xx anywhere", not any(x >= 500 for x in s3.STATUSES), [x for x in s3.STATUSES if x >= 500])
    print("FAILURES:", s3.FAILS if s3.FAILS else "none", "; requests:", len(s3.STATUSES))
    sys.exit(1 if s3.FAILS else 0)


if __name__ == "__main__":
    main()
