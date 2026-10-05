"""Headless-browser self-test of the UI (Playwright for Python).

  /Users/chiraanths/Desktop/lockin/Band-hack/dark-factory-wearedevs/.venv/bin/python stage-2/selftest/ui.py

Needs the stage-2 container on http://127.0.0.1:8080.
"""
import json
import os
import re
import sys
import traceback
import urllib.request

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE", "http://127.0.0.1:8080")
SHOTS = os.environ.get("SHOTS", "/tmp/tk-shots")
os.makedirs(SHOTS, exist_ok=True)
FAILS = []
SEEN = set()
ALL_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def api(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            t = r.read()
            return r.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        t = e.read()
        return e.code, (json.loads(t) if t else None)


def fixture():
    hours = [{"weekday": d, "opens": "18:00", "closes": "23:00"} for d in ALL_DAYS]
    return {
        "users": [{"id": "u_ada", "email": "ada@example.com", "password": "correct horse", "display_name": "Ada"}],
        "restaurants": [
            {"id": "r_anker", "name": "Zum Anker", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120, "opening_hours": hours,
             "tables": [{"id": "t_1", "label": "1", "capacity": 2}, {"id": "t_2", "label": "2", "capacity": 4},
                        {"id": "t_3", "label": "3", "capacity": 4}],
             "combinable": [["t_1", "t_2"], ["t_2", "t_3"]]},
            {"id": "r_nord", "name": "Bistro Nord", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 120,
             "opening_hours": [{"weekday": d, "opens": "17:00", "closes": "22:00"} for d in ("mon", "tue", "wed", "thu", "fri")],
             "tables": [{"id": "n_a", "label": "Window", "capacity": 2}, {"id": "n_b", "label": "Terrace", "capacity": 6}]},
            {"id": "r_cut", "name": "Cutoff Cafe", "timezone": "Europe/Berlin", "slot_minutes": 30,
             "reservation_duration_minutes": 90, "cancellation_cutoff_minutes": 10 ** 9, "opening_hours": hours,
             "tables": [{"id": "c_1", "label": "9", "capacity": 4}]},
        ],
        "reservations": [{"id": "seed1", "reference": "CUTREF01", "user_id": "u_ada", "restaurant_id": "r_cut",
                          "table_id": "c_1", "starts_at_local": "2099-09-24T19:00", "party_size": 2}],
    }


def step(name):
    def deco(fn):
        def run(*a, **k):
            try:
                fn(*a, **k)
                print("ok  ", name)
            except Exception as e:  # noqa
                FAILS.append(name)
                print("FAIL", name, "->", repr(e)[:400])
                traceback.print_exc(limit=3)
        run.step_name = name
        return run
    return deco


def tid(page, t):
    SEEN.add(re.sub(r"slot-.*", "slot-*", t))
    return page.locator('[data-testid="%s"]' % t)


def search(page, rid, date, party):
    tid(page, "restaurant-select").select_option(rid)
    tid(page, "date-input").fill(date)
    tid(page, "party-size-input").fill(str(party))
    tid(page, "search-button").click()


def cell(page, t, hhmm):
    return page.locator('[data-testid="slot-%s-%s"]' % (t, hhmm))


def no_hscroll(page, label):
    w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
    assert w[0] <= w[1] and w[2] <= w[1], "horizontal page scroll on %s: %s" % (label, w)


def ui_login(page, email="ada@example.com", pw="correct horse"):
    page.goto(BASE + "/login")
    tid(page, "login-email").fill(email)
    tid(page, "login-password").fill(pw)
    tid(page, "login-submit").click()
    expect(tid(page, "current-user")).to_contain_text("Ada")


def main():
    assert api("POST", "/_test/reset", fixture())[0] == 204
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        page.set_default_timeout(6000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("request", lambda r: errors.append("EXTERNAL " + r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)

        @step("routes return HTML shell; unknown path is JSON 404")
        def t_routes():
            for path in ("/", "/signup", "/login", "/lookup"):
                r = page.request.get(BASE + path)
                assert r.status == 200 and r.headers["content-type"].startswith("text/html"), (path, r.status, r.headers)
            r = page.request.get(BASE + "/nope")
            assert r.status == 404 and r.json()["error"]["code"] == "not_found"
        t_routes()

        @step("signed-out screens: nav, no current-user, no auth-error")
        def t_signed_out():
            for path in ("/", "/lookup", "/login", "/signup"):
                page.goto(BASE + path)
                expect(page.locator("nav a", has_text="Look up")).to_be_visible()
                assert page.locator('[data-testid="current-user"]').count() == 0
                assert page.locator('[data-testid="auth-error"]').count() == 0, path
        t_signed_out()

        @step("signup + login testids, auth-error appears only on failure")
        def t_auth():
            page.goto(BASE + "/signup")
            tid(page, "signup-display-name").fill("Grace")
            tid(page, "signup-email").fill("grace@example.com")
            tid(page, "signup-password").fill("short")
            tid(page, "signup-submit").click()
            expect(tid(page, "auth-error")).to_be_visible()
            tid(page, "signup-password").fill("longenough1")
            tid(page, "signup-submit").click()
            expect(tid(page, "current-user")).to_contain_text("Grace")
            assert page.url.rstrip("/") == BASE, page.url
            assert page.locator('[data-testid="auth-error"]').count() == 0
            for path in ("/lookup", "/login", "/signup", "/"):
                page.goto(BASE + path)
                expect(tid(page, "current-user")).to_contain_text("Grace")
            tid(page, "logout-button").click()
            assert page.locator('[data-testid="current-user"]').count() == 0
            page.goto(BASE + "/signup")
            tid(page, "signup-display-name").fill("Dup")
            tid(page, "signup-email").fill("grace@example.com")
            tid(page, "signup-password").fill("longenough1")
            tid(page, "signup-submit").click()
            expect(tid(page, "auth-error")).to_contain_text("already")
            page.goto(BASE + "/login")
            tid(page, "login-email").fill("ada@example.com")
            tid(page, "login-password").fill("wrong password")
            tid(page, "login-submit").click()
            expect(tid(page, "auth-error")).to_be_visible()
            tid(page, "login-password").fill("correct horse")
            tid(page, "login-submit").click()
            expect(tid(page, "current-user")).to_contain_text("Ada")
            tid(page, "logout-button").click()
        t_auth()

        D1 = "2099-09-24"

        @step("signed-out: grid matches API; unavailable click no-op; available click -> auth-error, no navigation")
        def t_signed_out_grid():
            page.goto(BASE)
            search(page, "r_anker", D1, 4)
            expect(tid(page, "availability-grid")).to_be_visible()
            s, av = api("GET", "/availability?restaurant_id=r_anker&date=%s&party_size=4" % D1)
            for sl in av["slots"]:
                hh = sl["starts_at_local"][11:]
                for t in ("t_1", "t_2", "t_3"):
                    want = "true" if t in sl["available_table_ids"] else "false"
                    assert cell(page, t, hh).get_attribute("data-available") == want, (t, hh)
                for o in sl["available_options"]:
                    if len(o["table_ids"]) == 2:
                        assert cell(page, "+".join(o["table_ids"]), hh).get_attribute("data-available") == "true"
            # party 4: pair rows exist (6 and 8 seats) and singles with capacity 2 are unavailable
            assert cell(page, "t_1", "19:00").get_attribute("data-available") == "false"
            cell(page, "t_1", "19:00").click()
            assert page.locator('[data-testid="booking-form"]').count() == 0
            assert page.locator('[data-testid="auth-error"]').count() == 0
            cell(page, "t_2", "19:00").click()
            expect(tid(page, "auth-error")).to_be_visible()
            assert page.url.rstrip("/") == BASE
            assert page.locator('[data-testid="booking-form"]').count() == 0
        t_signed_out_grid()

        @step("no-slots on a closed day")
        def t_no_slots():
            page.goto(BASE)
            search(page, "r_nord", "2099-10-04", 2)  # a Sunday: Bistro Nord is closed
            expect(tid(page, "no-slots")).to_be_visible()
            assert page.locator('[data-testid="availability-grid"]').count() == 0
            search(page, "r_nord", "2099-10-05", 2)  # Monday
            expect(tid(page, "availability-grid")).to_be_visible()
            assert page.locator('[data-testid="no-slots"]').count() == 0
        t_no_slots()

        ui_login(page)

        @step("single booking: form, summary, confirmation, resubmit same ref, changed cell new ref")
        def t_single():
            page.goto(BASE)
            search(page, "r_anker", D1, 4)
            cell(page, "t_2", "19:00").click()
            expect(tid(page, "booking-form")).to_be_visible()
            expect(tid(page, "booking-summary")).to_contain_text("2")
            expect(tid(page, "booking-summary")).to_contain_text("19:00")
            expect(tid(page, "booking-summary")).to_contain_text("Zum Anker")
            expect(tid(page, "booking-party-size")).to_have_value("4")
            tid(page, "booking-submit").click()
            expect(tid(page, "confirmation")).to_be_visible()
            ref = tid(page, "confirmation-reference").inner_text()
            assert re.fullmatch(r"[A-Z0-9]{6,12}", ref), ref
            expect(tid(page, "confirmation-details")).to_contain_text("Zum Anker")
            expect(tid(page, "confirmation-details")).to_contain_text("19:00")
            expect(tid(page, "confirmation-tables")).to_contain_text("2")
            expect(tid(page, "booking-form")).to_be_visible()
            for _ in range(2):
                tid(page, "booking-submit").click()
                expect(tid(page, "confirmation-reference")).to_have_text(ref)
                assert page.locator('[data-testid="booking-error"]').count() == 0
            s, lst = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            tok = lst["token"]
            s, mine = api("GET", "/reservations", token=tok)
            assert len([r for r in mine["reservations"] if r["starts_at_local"] == D1 + "T19:00" and r["restaurant_id"] == "r_anker"]) == 1, mine
            # a different cell is a new booking -> new reference
            cell(page, "t_3", "19:00").click()
            expect(tid(page, "booking-summary")).to_contain_text("3")
            assert page.locator('[data-testid="confirmation"]').count() == 0, "stale confirmation must be hidden"
            tid(page, "booking-submit").click()
            expect(tid(page, "confirmation")).to_be_visible()
            ref2 = tid(page, "confirmation-reference").inner_text()
            assert ref2 != ref
            # editing a field makes the next submission a new request (own t_3 booking now conflicts)
            tid(page, "booking-party-size").fill("3")
            assert page.locator('[data-testid="confirmation"]').count() == 0
            tid(page, "booking-submit").click()
            expect(tid(page, "booking-error")).to_be_visible()
            assert page.locator('[data-testid="confirmation"]').count() == 0
            expect(tid(page, "booking-form")).to_be_visible()
            expect(tid(page, "booking-party-size")).to_have_value("3")
            s, mine = api("GET", "/reservations", token=tok)
            assert len([r for r in mine["reservations"] if r["starts_at_local"] == D1 + "T19:00" and r["restaurant_id"] == "r_anker"]) == 2
        t_single()

        D2 = "2099-09-25"

        @step("409 table_unavailable: booking-error, form kept, availability refreshed")
        def t_409():
            page.goto(BASE)
            search(page, "r_anker", D2, 2)
            cell(page, "t_3", "21:00").click()
            expect(tid(page, "booking-form")).to_be_visible()
            tid(page, "booking-party-size").fill("3")
            s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            req = urllib.request.Request(BASE + "/reservations", method="POST", data=json.dumps(
                {"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": D2 + "T21:00", "party_size": 2}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + lg["token"], "Idempotency-Key": "other-client"})
            assert urllib.request.urlopen(req).status == 201
            tid(page, "booking-submit").click()
            expect(tid(page, "booking-error")).to_be_visible()
            assert page.locator('[data-testid="confirmation"]').count() == 0
            expect(tid(page, "booking-form")).to_be_visible()
            expect(tid(page, "booking-summary")).to_contain_text("21:00")
            expect(tid(page, "booking-party-size")).to_have_value("3")
            expect(cell(page, "t_3", "21:00")).to_have_attribute("data-available", "false")
            # pick another table: error clears on a fresh submission that succeeds
            cell(page, "t_1", "21:00").click()
            tid(page, "booking-party-size").fill("2")
            tid(page, "booking-submit").click()
            expect(tid(page, "confirmation")).to_be_visible()
            assert page.locator('[data-testid="booking-error"]').count() == 0
        t_409()

        def hold_lose(page_, state):
            def handler(route):
                req = route.request
                if req.method == "POST" and state.get("lose"):
                    state["lose"] = False
                    route.fetch()  # the server commits ...
                    route.abort("connectionreset")  # ... but the response is lost
                else:
                    route.continue_()
            page_.route("**/reservations", handler)

        D3 = "2099-09-26"
        lost = {"lose": False}
        hold_lose(page, lost)

        @step("lost response (single): uncertain, retry same key -> original reference")
        def t_lost_single():
            page.goto(BASE)
            search(page, "r_anker", D3, 2)
            cell(page, "t_3", "18:00").click()
            lost["lose"] = True
            tid(page, "booking-submit").click()
            expect(tid(page, "booking-uncertain")).to_be_visible()
            assert tid(page, "booking-uncertain").inner_text().strip() != ""
            assert page.locator('[data-testid="booking-error"]').count() == 0
            assert page.locator('[data-testid="confirmation"]').count() == 0
            s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            s, mine = api("GET", "/reservations", token=lg["token"])
            made = [r for r in mine["reservations"] if r["starts_at_local"] == D3 + "T18:00"]
            assert len(made) == 1, "server committed once"
            tid(page, "booking-submit").click()
            expect(tid(page, "confirmation-reference")).to_have_text(made[0]["reference"])
            assert page.locator('[data-testid="booking-uncertain"]').count() == 0
            assert page.locator('[data-testid="booking-error"]').count() == 0
            s, mine = api("GET", "/reservations", token=lg["token"])
            assert len([r for r in mine["reservations"] if r["starts_at_local"] == D3 + "T18:00"]) == 1
        t_lost_single()

        @step("combination: cells, summary names both tables, lost response retry, lookup tables")
        def t_combo():
            page.goto(BASE)
            search(page, "r_anker", "2099-09-27", 6)
            expect(tid(page, "availability-grid")).to_be_visible()
            assert cell(page, "t_1+t_2", "19:00").get_attribute("data-available") == "true"
            assert cell(page, "t_2+t_3", "19:00").get_attribute("data-available") == "true"
            assert cell(page, "t_2", "19:00").get_attribute("data-available") == "false"  # 4 seats < 6
            assert page.locator('[data-testid="slot-t_1+t_3-19:00"]').count() == 0, "undeclared pair"
            cell(page, "t_1+t_2", "19:00").click()
            expect(tid(page, "booking-summary")).to_contain_text("Table 1 + Table 2")
            expect(tid(page, "booking-summary")).to_contain_text("19:00")
            lost["lose"] = True
            tid(page, "booking-submit").click()
            expect(tid(page, "booking-uncertain")).to_be_visible()
            assert page.locator('[data-testid="booking-error"]').count() == 0
            tid(page, "booking-submit").click()
            expect(tid(page, "confirmation")).to_be_visible()
            ref = tid(page, "confirmation-reference").inner_text()
            expect(tid(page, "confirmation-tables")).to_contain_text("1")
            expect(tid(page, "confirmation-tables")).to_contain_text("2")
            expect(tid(page, "confirmation-details")).to_contain_text("Table 1")
            expect(tid(page, "confirmation-details")).to_contain_text("Table 2")
            s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            s, mine = api("GET", "/reservations", token=lg["token"])
            assert len([r for r in mine["reservations"] if r["starts_at_local"] == "2099-09-27T19:00"]) == 1
            # members are now taken in the grid
            tid(page, "search-button").click()
            expect(cell(page, "t_1+t_2", "19:00")).to_have_attribute("data-available", "false")
            expect(cell(page, "t_2+t_3", "19:00")).to_have_attribute("data-available", "false")
            # lookup shows both tables
            page.goto(BASE + "/lookup")
            tid(page, "lookup-reference-input").fill(ref)
            tid(page, "lookup-submit").click()
            expect(tid(page, "reservation-detail")).to_be_visible()
            expect(tid(page, "reservation-tables")).to_contain_text("Table 1")
            expect(tid(page, "reservation-tables")).to_contain_text("Table 2")
            expect(tid(page, "reservation-status")).to_have_text("confirmed")
        t_combo()

        @step("combination 409 keeps form and refreshes")
        def t_combo_409():
            page.goto(BASE)
            search(page, "r_anker", "2099-09-28", 6)
            cell(page, "t_2+t_3", "20:00").click()
            s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            req = urllib.request.Request(BASE + "/reservations", method="POST", data=json.dumps(
                {"restaurant_id": "r_anker", "table_id": "t_3", "starts_at_local": "2099-09-28T20:00", "party_size": 2}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + lg["token"], "Idempotency-Key": "k-combo-409"})
            assert urllib.request.urlopen(req).status == 201
            tid(page, "booking-submit").click()
            expect(tid(page, "booking-error")).to_be_visible()
            expect(tid(page, "booking-summary")).to_contain_text("Table 2 + Table 3")
            expect(cell(page, "t_2+t_3", "20:00")).to_have_attribute("data-available", "false")
            assert page.locator('[data-testid="confirmation"]').count() == 0
        t_combo_409()

        @step("out-of-order searches: late response for A never replaces B")
        def t_ooo():
            held = []

            def handler(route):
                if "restaurant_id=r_anker" in route.request.url and state["hold"]:
                    held.append(route)
                else:
                    route.continue_()
            state = {"hold": True}
            page.route("**/availability*", handler)
            page.goto(BASE)
            search(page, "r_anker", "2099-09-29", 2)  # A: held
            page.wait_for_timeout(300)
            search(page, "r_nord", "2099-09-29", 2)  # B: fast (Tuesday)
            expect(page.locator('[data-testid="slot-n_a-17:00"]')).to_be_visible()
            state["hold"] = False
            for r in held:
                try:
                    r.continue_()
                except Exception:
                    pass
            page.wait_for_timeout(800)
            assert page.locator('[data-testid="slot-t_2-19:00"]').count() == 0, "late A restored its results"
            assert page.locator('[data-testid="slot-n_b-17:00"]').count() == 1
            expect(tid(page, "availability-grid")).to_contain_text("Bistro Nord")
            expect(tid(page, "availability-grid")).to_contain_text("Terrace")
            cell(page, "n_b", "17:00").click()
            expect(tid(page, "booking-summary")).to_contain_text("Terrace")
            expect(tid(page, "booking-summary")).to_contain_text("Bistro Nord")
            page.unroute("**/availability*", handler)
        t_ooo()

        @step("lookup: found, cancel, not found, refused cancel, signed out")
        def t_lookup():
            s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
            body = {"restaurant_id": "r_anker", "table_id": "t_2", "starts_at_local": "2099-10-01T19:00", "party_size": 3}
            req = urllib.request.Request(BASE + "/reservations", method="POST", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + lg["token"], "Idempotency-Key": "lk1"})
            ref = json.loads(urllib.request.urlopen(req).read())["reference"]
            page.goto(BASE + "/lookup")
            tid(page, "lookup-reference-input").fill(ref)
            tid(page, "lookup-submit").click()
            expect(tid(page, "reservation-status")).to_have_text("confirmed")
            expect(tid(page, "reservation-tables")).to_contain_text("2")
            assert page.locator('[data-testid="reservation-error"]').count() == 0
            tid(page, "reservation-cancel-button").click()
            expect(tid(page, "reservation-status")).to_have_text("cancelled")
            assert page.locator('[data-testid="reservation-cancel-button"]').count() == 0
            s, av = api("GET", "/availability?restaurant_id=r_anker&date=2099-10-01&party_size=3")
            assert "t_2" in [sl for sl in av["slots"] if sl["starts_at_local"].endswith("19:00")][0]["available_table_ids"]
            tid(page, "lookup-reference-input").fill("NOSUCH99")
            tid(page, "lookup-submit").click()
            expect(tid(page, "reservation-error")).to_be_visible()
            assert page.locator('[data-testid="reservation-detail"]').count() == 0
            tid(page, "lookup-reference-input").fill("CUTREF01")
            tid(page, "lookup-submit").click()
            expect(tid(page, "reservation-detail")).to_be_visible()
            expect(tid(page, "reservation-tables")).to_contain_text("9")
            tid(page, "reservation-cancel-button").click()
            expect(tid(page, "reservation-error")).to_be_visible()
            expect(tid(page, "reservation-status")).to_have_text("confirmed")
            expect(tid(page, "reservation-cancel-button")).to_be_visible()
        t_lookup()

        @step("signed-out lookup shows reservation-error")
        def t_lookup_out():
            tid(page, "logout-button").click()
            page.goto(BASE + "/lookup")
            tid(page, "lookup-reference-input").fill("CUTREF01")
            tid(page, "lookup-submit").click()
            expect(tid(page, "reservation-error")).to_be_visible()
        t_lookup_out()
        ui_login(page)

        @step("export/import between browser actions: still signed in, pending lost booking recovers original confirmation")
        def t_upgrade():
            for stage1_shape in (False, True):
                day = "2099-10-0%d" % (7 if stage1_shape else 6)
                page.goto(BASE)
                search(page, "r_anker", day, 2)
                cell(page, "t_3", "18:30").click()
                lost["lose"] = True
                tid(page, "booking-submit").click()
                expect(tid(page, "booking-uncertain")).to_be_visible()
                s, exp = api("GET", "/_test/export")
                if stage1_shape:  # downgrade the export to the stage-1 shape
                    st = exp["state"]
                    st.pop("schema", None)
                    for r in st["restaurants"]:
                        r.pop("combinable", None)
                    for r in st["reservations"]:
                        r["table_id"] = r.pop("table_ids")[0]
                    for rec in st["idempotency"]:
                        resp = rec["response"]
                        if isinstance(resp, dict) and "table_ids" in resp:
                            resp.pop("table_ids")
                assert api("POST", "/_test/import", exp)[0] == 204
                expect(tid(page, "current-user")).to_contain_text("Ada")
                tid(page, "booking-submit").click()
                expect(tid(page, "confirmation")).to_be_visible()
                ref = tid(page, "confirmation-reference").inner_text()
                s, lg = api("POST", "/auth/login", {"email": "ada@example.com", "password": "correct horse"})
                s, mine = api("GET", "/reservations", token=lg["token"])
                made = [r for r in mine["reservations"] if r["starts_at_local"] == day + "T18:30"]
                assert len(made) == 1 and made[0]["reference"] == ref, (made, ref)
                assert page.locator('[data-testid="booking-uncertain"]').count() == 0
                expect(tid(page, "confirmation-tables")).to_contain_text("3")
                # retained reference still works through the lookup screen after the upgrade
                page.goto(BASE + "/lookup")
                tid(page, "lookup-reference-input").fill(ref)
                tid(page, "lookup-submit").click()
                expect(tid(page, "reservation-status")).to_have_text("confirmed")
        t_upgrade()

        @step("no horizontal page scroll at 375px and desktop on every screen/state; labels present")
        def t_viewports():
            tid(page, "logout-button").click()
            assert api("POST", "/_test/reset", fixture())[0] == 204
            ui_login(page)
            for vw, vh in ((375, 812), (1280, 800)):
                page.set_viewport_size({"width": vw, "height": vh})
                for path in ("/", "/signup", "/login", "/lookup"):
                    page.goto(BASE + path)
                    page.wait_for_timeout(100)
                    no_hscroll(page, "%s@%d" % (path, vw))
                    unlabeled = page.evaluate("""() => [...document.querySelectorAll('input,select')].filter(
                        e => !document.querySelector('label[for="' + e.id + '"]')).map(e => e.id)""")
                    assert not unlabeled, ("unlabeled controls", path, unlabeled)
                page.goto(BASE)
                search(page, "r_anker", "2099-11-0%d" % (2 if vw == 375 else 3), 6)
                expect(tid(page, "availability-grid")).to_be_visible()
                no_hscroll(page, "grid@%d" % vw)
                cell(page, "t_1+t_2", "19:00").click()
                expect(tid(page, "booking-form")).to_be_visible()
                no_hscroll(page, "form@%d" % vw)
                tid(page, "booking-submit").click()
                expect(tid(page, "confirmation")).to_be_visible()
                no_hscroll(page, "confirmation@%d" % vw)
                page.screenshot(path="%s/booking-%d.png" % (SHOTS, vw), full_page=True)
                page.goto(BASE + "/lookup")
                tid(page, "lookup-reference-input").fill("CUTREF01")
                tid(page, "lookup-submit").click()
                expect(tid(page, "reservation-detail")).to_be_visible()
                tid(page, "reservation-cancel-button").click()
                expect(tid(page, "reservation-error")).to_be_visible()
                no_hscroll(page, "lookup@%d" % vw)
                page.screenshot(path="%s/lookup-%d.png" % (SHOTS, vw), full_page=True)
                page.goto(BASE)
                search(page, "r_nord", "2099-10-04", 2)
                expect(tid(page, "no-slots")).to_be_visible()
                no_hscroll(page, "no-slots@%d" % vw)
                # a refused booking state (stale selection) still fits
                page.goto(BASE + "/login")
                tid(page, "login-submit").click()
                expect(tid(page, "auth-error")).to_be_visible()
                no_hscroll(page, "auth-error@%d" % vw)
        t_viewports()

        @step("keyboard focus is visible and no external requests / JS errors")
        def t_focus():
            page.goto(BASE + "/login")
            page.keyboard.press("Tab")
            page.keyboard.press("Tab")
            ol = page.evaluate("getComputedStyle(document.activeElement).outlineStyle + ' ' + getComputedStyle(document.activeElement).outlineWidth")
            assert "none" not in ol, ol
            assert not errors, errors
        t_focus()

        browser.close()

    needed = {"signup-email", "signup-password", "signup-display-name", "signup-submit", "login-email", "login-password",
              "login-submit", "auth-error", "current-user", "logout-button", "restaurant-select", "date-input",
              "party-size-input", "search-button", "availability-grid", "slot-*", "no-slots", "booking-form",
              "booking-summary", "booking-party-size", "booking-submit", "booking-error", "booking-uncertain",
              "confirmation", "confirmation-reference", "confirmation-details", "confirmation-tables",
              "lookup-reference-input", "lookup-submit", "reservation-detail", "reservation-status",
              "reservation-cancel-button", "reservation-error", "reservation-tables"}
    print("FAILURES:", FAILS or "none")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
