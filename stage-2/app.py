"""Tablekeeper stage 1: reservations API (single-process, in-memory, raw ASGI).

All state lives in one process. Every handler runs its read-check-write
sequence synchronously (no ``await`` between the first read and the last write),
so on the single asyncio event loop no two mutations can interleave. The only
awaits are body reads (before any state is touched) and password hashing, which
runs in a thread pool strictly outside any critical section.
"""
import asyncio
import calendar
import copy
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)
MIN_E = -62135596800 + 3 * 86400
MAX_E = 253402300799 - 3 * 86400
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
POOL = ThreadPoolExecutor(max_workers=4)

LOCAL_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})")
DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
HHMM_RE = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
REF_RE = re.compile(r"[A-Z0-9]{6,12}")
TZ_RE = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")
PW_RE = re.compile(r"scrypt\$16384\$8\$1\$[0-9a-f]{32}\$[0-9a-f]{64}")
DIGITS_RE = re.compile(r"[0-9]+")


class ApiError(Exception):
    def __init__(self, status, code, message=""):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or code.replace("_", " ")


class Invalid(Exception):
    """Fixture/import content that cannot be processed."""


class Unrep(Exception):
    """A time that is not representable."""


def err(status, code, message=""):
    return ApiError(status, code, message)


# ----------------------------------------------------------------- time ----

_tz_cache = {}


def get_tz(name):
    tz = _tz_cache.get(name)
    if tz is None:
        tz = ZoneInfo(name)
        _tz_cache[name] = tz
    return tz


def to_local(e, tz):
    if e < MIN_E or e > MAX_E:
        raise Unrep()
    return (EPOCH + dt.timedelta(seconds=e)).astimezone(tz)


def offset_seconds(e, tz):
    return int(to_local(e, tz).utcoffset().total_seconds())


def local_to_epoch(y, mo, d, h, mi, tz):
    """Epoch seconds of a local wall time (first occurrence if repeated).
    None when the local time does not exist. Raises Unrep / ValueError."""
    naive = dt.datetime(y, mo, d, h, mi)
    ne = calendar.timegm(naive.timetuple())
    off = int(naive.replace(tzinfo=tz).utcoffset().total_seconds())
    e = ne - off
    if e < MIN_E or e > MAX_E:
        raise Unrep()
    loc = to_local(e, tz)
    if (loc.year, loc.month, loc.day, loc.hour, loc.minute) != (y, mo, d, h, mi):
        return None
    return e


def closes_epoch(y, mo, d, h, mi, tz):
    e = local_to_epoch(y, mo, d, h, mi, tz)
    if e is not None:
        return e
    # closing time falls in a spring-forward gap: use the instant the clock jumps
    naive = dt.datetime(y, mo, d, h, mi)
    ne = calendar.timegm(naive.timetuple())
    hi = ne - int(naive.replace(tzinfo=tz).utcoffset().total_seconds())
    lo = hi - 86400
    target = offset_seconds(hi, tz)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if offset_seconds(mid, tz) == target:
            hi = mid
        else:
            lo = mid
    return hi


def fmt_offset(dtv):
    secs = int(dtv.utcoffset().total_seconds())
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return "%s%02d:%02d" % (sign, secs // 3600, (secs % 3600) // 60)


def fmt_epoch(e, tz):
    d = to_local(e, tz)
    return d.strftime("%Y-%m-%dT%H:%M:%S") + fmt_offset(d)


def fmt_utc(e):
    return to_local(e, UTC).strftime("%Y-%m-%dT%H:%M:%S") + "+00:00"


def parse_local(s):
    m = LOCAL_RE.fullmatch(s)
    if not m:
        raise err(422, "validation_failed", "starts_at_local must be YYYY-MM-DDTHH:MM")
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        dt.datetime(y, mo, d, h, mi)
    except ValueError:
        raise err(422, "validation_failed", "starts_at_local is not a valid date/time")
    return y, mo, d, h, mi


# ------------------------------------------------------------- restaurants -

def is_int(x):
    return type(x) is int


def v_id(x):
    if not isinstance(x, str) or not 1 <= len(x) <= 64:
        raise Invalid("bad id")
    return x


class Rest:
    def __init__(self, d):
        if not isinstance(d, dict):
            raise Invalid("restaurant")
        self.id = v_id(d.get("id"))
        self.name = d.get("name")
        if not isinstance(self.name, str):
            raise Invalid("name")
        tzname = d.get("timezone")
        if not isinstance(tzname, str) or not TZ_RE.fullmatch(tzname):
            raise Invalid("timezone")
        try:
            self.tz = get_tz(tzname)
        except Exception:
            raise Invalid("timezone")
        self.timezone = tzname
        self.slot = d.get("slot_minutes")
        self.dur = d.get("reservation_duration_minutes")
        self.cutoff = d.get("cancellation_cutoff_minutes", 0)
        if not (is_int(self.slot) and self.slot >= 1):
            raise Invalid("slot_minutes")
        if not (is_int(self.dur) and self.dur >= 1):
            raise Invalid("reservation_duration_minutes")
        if not (is_int(self.cutoff) and self.cutoff >= 0):
            raise Invalid("cancellation_cutoff_minutes")
        hours = d.get("opening_hours", [])
        if not isinstance(hours, list):
            raise Invalid("opening_hours")
        self.hours = []
        self.by_wd = {}
        for h in hours:
            if not isinstance(h, dict):
                raise Invalid("opening_hours entry")
            wd, op, cl = h.get("weekday"), h.get("opens"), h.get("closes")
            if wd not in WEEKDAYS:
                raise Invalid("weekday")
            mo = HHMM_RE.fullmatch(op) if isinstance(op, str) else None
            mc = HHMM_RE.fullmatch(cl) if isinstance(cl, str) else None
            if not mo or not mc:
                raise Invalid("opens/closes")
            o = int(mo.group(1)) * 60 + int(mo.group(2))
            c = int(mc.group(1)) * 60 + int(mc.group(2))
            if c <= o:
                raise Invalid("closes before opens")
            self.hours.append({"weekday": wd, "opens": op, "closes": cl})
            self.by_wd.setdefault(WEEKDAYS.index(wd), []).append((o, c))
        for v in self.by_wd.values():
            v.sort()
        tables = d.get("tables", [])
        if not isinstance(tables, list):
            raise Invalid("tables")
        self.tables = []
        self.table_by_id = {}
        for t in tables:
            if not isinstance(t, dict):
                raise Invalid("table")
            tid = v_id(t.get("id"))
            label = t.get("label", tid)
            cap = t.get("capacity")
            if not isinstance(label, str) or not (is_int(cap) and cap >= 1):
                raise Invalid("table fields")
            if tid in self.table_by_id:
                raise Invalid("duplicate table")
            tab = {"id": tid, "label": label, "capacity": cap}
            self.tables.append(tab)
            self.table_by_id[tid] = tab

    def to_json(self):
        return {
            "id": self.id, "name": self.name, "timezone": self.timezone,
            "slot_minutes": self.slot,
            "reservation_duration_minutes": self.dur,
            "cancellation_cutoff_minutes": self.cutoff,
            "opening_hours": copy.deepcopy(self.hours),
            "tables": copy.deepcopy(self.tables),
        }

    def check_slot(self, parts):
        """Rule chain D5 up to (not including) capacity. Returns start epoch."""
        y, mo, d, h, mi = parts
        try:
            e = local_to_epoch(y, mo, d, h, mi, self.tz)
        except Unrep:
            raise err(422, "outside_opening_hours")
        if e is None:
            raise err(422, "invalid_local_time", "local time does not exist")
        wd = dt.date(y, mo, d).weekday()
        minute = h * 60 + mi
        win = None
        for o, c in self.by_wd.get(wd, ()):
            if o <= minute:
                win = (o, c)
        if win is None:
            raise err(422, "outside_opening_hours")
        o, c = win
        try:
            ce = closes_epoch(y, mo, d, c // 60, c % 60, self.tz)
        except Unrep:
            raise err(422, "outside_opening_hours")
        if e + self.dur * 60 > ce:
            raise err(422, "outside_opening_hours")
        if (minute - o) % self.slot:
            raise err(422, "not_on_slot_grid")
        return e

    def slots(self, y, mo, d):
        out = []
        seen = set()
        wd = dt.date(y, mo, d).weekday()
        for o, c in self.by_wd.get(wd, ()):
            try:
                ce = closes_epoch(y, mo, d, c // 60, c % 60, self.tz)
            except Unrep:
                continue
            m = o
            while m < c:
                try:
                    e = local_to_epoch(y, mo, d, m // 60, m % 60, self.tz)
                except Unrep:
                    e = None
                if e is not None and e + self.dur * 60 <= ce and m not in seen:
                    seen.add(m)
                    out.append((m, e))
                m += self.slot
        out.sort()
        return out


# ------------------------------------------------------------------ state --

class State:
    def __init__(self):
        self.users = {}
        self.emails = {}
        self.tokens = {}
        self.rests = {}
        self.res = {}
        self.res_ids = set()
        self.by_table = {}
        self.idem = {}
        self.n_res = 0
        self.n_user = 0

    def index_add(self, r):
        self.by_table.setdefault((r["rid"], r["tid"]), []).append(r)

    def index_remove(self, r):
        lst = self.by_table.get((r["rid"], r["tid"]))
        if lst is not None:
            for i, x in enumerate(lst):
                if x is r:
                    del lst[i]
                    break

    def add_res(self, r):
        self.res[r["ref"]] = r
        self.res_ids.add(r["id"])
        self.index_add(r)
        m = re.fullmatch(r"res_([0-9]{1,15})", r["id"])
        if m:
            self.n_res = max(self.n_res, int(m.group(1)))

    def conflict(self, rid, tid, start, end, ignore):
        for x in self.by_table.get((rid, tid), ()):
            if x["status"] == "confirmed" and x["ref"] not in ignore \
                    and x["start"] < end and start < x["end"]:
                return True
        return False


S = State()


def has_surrogate(obj):
    stack = [obj]
    while stack:
        o = stack.pop()
        if isinstance(o, str):
            try:
                o.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(o, dict):
            for k, v in o.items():
                stack.append(k)
                stack.append(v)
        elif isinstance(o, list):
            stack.extend(o)
    return False


def valid_email(e):
    if e.count("@") != 1:
        return False
    local, domain = e.split("@")
    return bool(local) and bool(domain) and not any(c.isspace() for c in e)


def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$%s$%s" % (salt.hex(), h.hex())


def check_pw(stored, pw):
    try:
        p = stored.split("$")
        salt = bytes.fromhex(p[4])
        exp = bytes.fromhex(p[5])
        h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=int(p[1]), r=int(p[2]),
                           p=int(p[3]), dklen=len(exp))
        return hmac.compare_digest(h, exp)
    except Exception:
        return False


def run_pool(fn, *a):
    return asyncio.get_running_loop().run_in_executor(POOL, fn, *a)


def parse_created(v):
    if v is None:
        return int(time.time())
    if not isinstance(v, str):
        raise Invalid("created_at")
    try:
        d = dt.datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        e = int(d.timestamp())
    except Exception:
        raise Invalid("created_at")
    if e < MIN_E or e > MAX_E:
        raise Invalid("created_at")
    return e


def build_res(d, rests):
    if not isinstance(d, dict):
        raise Invalid("reservation")
    rid_ = v_id(d.get("id"))
    ref = d.get("reference")
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise Invalid("reference")
    user = v_id(d.get("user_id"))
    rid = d.get("restaurant_id")
    tid = d.get("table_id")
    rest = rests.get(rid) if isinstance(rid, str) else None
    if rest is None or not isinstance(tid, str) or tid not in rest.table_by_id:
        raise Invalid("restaurant/table")
    party = d.get("party_size")
    if not (is_int(party) and party >= 1):
        raise Invalid("party_size")
    status = d.get("status", "confirmed")
    if status not in ("confirmed", "cancelled"):
        raise Invalid("status")
    local = d.get("starts_at_local")
    if not isinstance(local, str):
        raise Invalid("starts_at_local")
    m = LOCAL_RE.fullmatch(local)
    if not m:
        raise Invalid("starts_at_local")
    try:
        e = local_to_epoch(*(int(x) for x in m.groups()), rest.tz)
    except (Unrep, ValueError):
        raise Invalid("starts_at_local")
    if e is None:
        raise Invalid("starts_at_local")
    end = e + rest.dur * 60
    if end > MAX_E:
        raise Invalid("end not representable")
    cts = parse_created(d.get("created_at"))
    return {"id": rid_, "ref": ref, "user": user, "rid": rid, "tid": tid,
            "party": party, "status": status, "local": local, "start": e,
            "end": end, "cts": cts}


def res_export(r):
    return {"id": r["id"], "reference": r["ref"], "user_id": r["user"],
            "restaurant_id": r["rid"], "table_id": r["tid"], "party_size": r["party"],
            "status": r["status"], "starts_at_local": r["local"],
            "created_at": fmt_utc(r["cts"])}


def res_json(r):
    tz = S.rests[r["rid"]].tz
    return {
        "reservation_id": r["id"], "reference": r["ref"], "restaurant_id": r["rid"],
        "table_id": r["tid"], "party_size": r["party"], "status": r["status"],
        "starts_at_local": r["local"], "starts_at": fmt_epoch(r["start"], tz),
        "ends_at": fmt_epoch(r["end"], tz), "created_at": fmt_utc(r["cts"]),
    }


def build_rests_and_res(rest_list, res_list):
    rests = {}
    for x in rest_list:
        r = Rest(x)
        if r.id in rests:
            raise Invalid("duplicate restaurant")
        rests[r.id] = r
    out = []
    refs = set()
    ids = set()
    for x in res_list:
        r = build_res(x, rests)
        if r["ref"] in refs or r["id"] in ids:
            raise Invalid("duplicate reservation")
        refs.add(r["ref"])
        ids.add(r["id"])
        out.append(r)
    return rests, out


def need_list(d, key):
    v = d.get(key, [])
    if not isinstance(v, list):
        raise Invalid(key)
    return v


def build_users_meta(user_list):
    users = []
    ids = set()
    emails = set()
    for u in user_list:
        if not isinstance(u, dict):
            raise Invalid("user")
        uid = v_id(u.get("id"))
        email = u.get("email")
        if not isinstance(email, str) or not valid_email(email):
            raise Invalid("email")
        name = u.get("display_name", "")
        if not isinstance(name, str):
            raise Invalid("display_name")
        if uid in ids or email.lower() in emails:
            raise Invalid("duplicate user")
        ids.add(uid)
        emails.add(email.lower())
        users.append((uid, email, name, u))
    return users


# ------------------------------------------------------------- http utils --

class Req:
    __slots__ = ("method", "path", "query", "headers", "body", "params")


def parse_obj(req):
    try:
        text = req.body.decode("utf-8")

        def bad_const(c):
            raise ValueError(c)
        obj = json.loads(text, parse_constant=bad_const)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        canon = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (ValueError, RecursionError):
        raise err(400, "malformed_request", "body must be a JSON object")
    return obj, canon


def auth(req):
    h = req.headers.get("authorization")
    if not h:
        raise err(401, "unauthenticated")
    parts = h.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise err(401, "unauthenticated")
    uid = S.tokens.get(parts[1])
    user = S.users.get(uid) if uid is not None else None
    if user is None:
        raise err(401, "unauthenticated")
    return user


def idem_pre(req, user, canon):
    key = req.headers.get("idempotency-key")
    if key is None or key == "":
        raise err(400, "missing_idempotency_key")
    if len(key) > 255:
        raise err(422, "validation_failed", "Idempotency-Key too long")
    ik = (user["id"], req.method + " " + req.path, key)
    rec = S.idem.get(ik)
    if rec is not None:
        if rec["body"] != canon:
            raise err(409, "idempotency_key_reuse")
        return ik, rec
    return ik, None


def check_party(p):
    if not (is_int(p) and p >= 1):
        raise err(422, "validation_failed", "party_size must be an integer >= 1")


def check_id_len(x):
    if len(x) > 64:
        raise err(422, "validation_failed", "id too long")


def new_ref():
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    while True:
        ref = "".join(secrets.choice(alpha) for _ in range(8))
        if ref not in S.res:
            return ref


def new_res_id():
    while True:
        S.n_res += 1
        rid = "res_%d" % S.n_res
        if rid not in S.res_ids:
            return rid


def own_res(user, ref):
    r = S.res.get(ref)
    if r is None or r["user"] != user["id"]:
        raise err(404, "not_found", "no such reservation")
    return r


# --------------------------------------------------------------- handlers --

async def h_health(req):
    return 200, {"status": "ok"}


async def h_reset(req):
    global S
    if req.body.strip() == b"":
        body = {}
    else:
        body, _ = parse_obj(req)
    try:
        if has_surrogate(body):
            raise Invalid("surrogate")
        users = build_users_meta(need_list(body, "users"))
        for _, _, _, u in users:
            if not isinstance(u.get("password"), str):
                raise Invalid("password")
        rests, reslist = build_rests_and_res(need_list(body, "restaurants"),
                                             need_list(body, "reservations"))
    except Invalid:
        raise err(422, "validation_failed", "invalid fixture")
    except (RecursionError, OverflowError, ValueError, TypeError):
        raise err(422, "validation_failed", "invalid fixture")
    hashes = await asyncio.gather(*[run_pool(hash_pw, u["password"]) for _, _, _, u in users])
    new = State()
    for (uid, email, name, _), pw in zip(users, hashes):
        new.users[uid] = {"id": uid, "email": email, "display_name": name, "pw": pw}
        new.emails[email.lower()] = uid
    new.rests = rests
    for r in reslist:
        new.add_res(r)
    S = new
    return 204, None


async def h_export(req):
    st = {
        "users": [dict(u) for u in S.users.values()],
        "tokens": dict(S.tokens),
        "restaurants": [r.to_json() for r in S.rests.values()],
        "reservations": [res_export(r) for r in S.res.values()],
        "idempotency": [
            {"user": k[0], "route": k[1], "key": k[2], "body": v["body"],
             "status": v["status"], "response": copy.deepcopy(v["response"])}
            for k, v in S.idem.items()],
        "counters": {"reservation": S.n_res, "user": S.n_user},
        "references": sorted(S.res.keys()),
    }
    return 200, {"track": "tablekeeper", "format_version": 1, "state": st}


def build_import(body):
    if body.get("track") != "tablekeeper":
        raise Invalid("track")
    fv = body.get("format_version")
    if not is_int(fv) or fv != 1:
        raise Invalid("format_version")
    st = body.get("state")
    if not isinstance(st, dict):
        raise Invalid("state")
    if has_surrogate(st):
        raise Invalid("surrogate")
    for k in ("users", "restaurants", "reservations", "idempotency"):
        if not isinstance(st.get(k), list):
            raise Invalid(k)
    if not isinstance(st.get("tokens"), dict):
        raise Invalid("tokens")
    counters = st.get("counters")
    if not isinstance(counters, dict) or not is_int(counters.get("reservation")) \
            or not is_int(counters.get("user")) or counters["reservation"] < 0 \
            or counters["user"] < 0:
        raise Invalid("counters")
    new = State()
    for _, _, _, u in build_users_meta(st["users"]):
        pw = u.get("pw")
        if not isinstance(pw, str) or not PW_RE.fullmatch(pw):
            raise Invalid("pw")
        new.users[u["id"]] = {"id": u["id"], "email": u["email"],
                              "display_name": u.get("display_name", ""), "pw": pw}
        new.emails[u["email"].lower()] = u["id"]
    for tok, uid in st["tokens"].items():
        if not isinstance(uid, str) or uid not in new.users or not tok:
            raise Invalid("token")
        new.tokens[tok] = uid
    new.rests, reslist = build_rests_and_res(st["restaurants"], st["reservations"])
    for r in reslist:
        new.add_res(r)
    for rec in st["idempotency"]:
        if not isinstance(rec, dict):
            raise Invalid("idempotency")
        u, route, key, b, status = (rec.get(x) for x in
                                    ("user", "route", "key", "body", "status"))
        if not (isinstance(u, str) and isinstance(route, str) and isinstance(key, str)
                and isinstance(b, str) and is_int(status) and status in (200, 201)
                and "response" in rec):
            raise Invalid("idempotency")
        new.idem[(u, route, key)] = {"body": b, "status": status,
                                     "response": rec["response"]}
    new.n_res = max(new.n_res, counters["reservation"])
    new.n_user = counters["user"]
    return new


async def h_import(req):
    global S
    body, _ = parse_obj(req)
    try:
        new = build_import(body)
    except (Invalid, RecursionError, OverflowError, ValueError, TypeError, KeyError):
        raise err(422, "validation_failed", "invalid export")
    S = new
    return 204, None


async def h_signup(req):
    body, _ = parse_obj(req)
    for k in ("email", "password", "display_name"):
        if k in body and not isinstance(body[k], str):
            raise err(400, "malformed_request", "%s must be a string" % k)
    for k in ("email", "password", "display_name"):
        if k not in body:
            raise err(422, "validation_failed", "%s is required" % k)
    email, pw, name = body["email"], body["password"], body["display_name"]
    if has_surrogate([email, pw, name]):
        raise err(422, "validation_failed", "invalid characters")
    if not valid_email(email):
        raise err(422, "validation_failed", "invalid email")
    if len(pw) < 8:
        raise err(422, "validation_failed", "password too short")
    if name == "":
        raise err(422, "validation_failed", "display_name must not be empty")
    if email.lower() in S.emails:
        raise err(409, "email_taken")
    hashed = await run_pool(hash_pw, pw)
    if email.lower() in S.emails:
        raise err(409, "email_taken")
    while True:
        S.n_user += 1
        uid = "u_%d" % S.n_user
        if uid not in S.users:
            break
    S.users[uid] = {"id": uid, "email": email, "display_name": name, "pw": hashed}
    S.emails[email.lower()] = uid
    token = secrets.token_urlsafe(32)
    S.tokens[token] = uid
    return 201, {"user_id": uid, "display_name": name, "token": token}


async def h_login(req):
    body, _ = parse_obj(req)
    for k in ("email", "password"):
        if k in body and not isinstance(body[k], str):
            raise err(400, "malformed_request", "%s must be a string" % k)
    for k in ("email", "password"):
        if k not in body:
            raise err(422, "validation_failed", "%s is required" % k)
    email, pw = body["email"], body["password"]
    if has_surrogate(email):
        raise err(401, "unauthenticated")
    uid = S.emails.get(email.lower())
    user = S.users.get(uid) if uid else None
    stored = user["pw"] if user else None
    if stored is None:
        raise err(401, "unauthenticated")
    ok = await run_pool(check_pw, stored, pw)
    user = S.users.get(uid)
    if not ok or user is None:
        raise err(401, "unauthenticated")
    token = secrets.token_urlsafe(32)
    S.tokens[token] = uid
    return 200, {"user_id": uid, "display_name": user["display_name"], "token": token}


async def h_restaurants(req):
    return 200, {"restaurants": [{"id": r.id, "name": r.name, "timezone": r.timezone}
                                 for r in S.rests.values()]}


async def h_restaurant(req):
    r = S.rests.get(req.params[0])
    if r is None:
        raise err(404, "not_found", "no such restaurant")
    return 200, r.to_json()


async def h_availability(req):
    q = {}
    for k, v in req.query:
        q.setdefault(k, v)
    for k in ("restaurant_id", "date", "party_size"):
        if q.get(k, "") == "":
            raise err(422, "validation_failed", "%s is required" % k)
    ps = q["party_size"]
    if not DIGITS_RE.fullmatch(ps) or len(ps) > 4000 or int(ps) < 1:
        raise err(422, "validation_failed", "party_size must be a positive integer")
    m = DATE_RE.fullmatch(q["date"])
    if not m:
        raise err(422, "validation_failed", "date must be YYYY-MM-DD")
    y, mo, d = (int(x) for x in m.groups())
    try:
        dt.date(y, mo, d)
    except ValueError:
        raise err(422, "validation_failed", "invalid date")
    rest = S.rests.get(q["restaurant_id"])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    party = int(ps)
    slots = []
    for m_, e in rest.slots(y, mo, d):
        end = e + rest.dur * 60
        avail = [t["id"] for t in rest.tables
                 if t["capacity"] >= party and not S.conflict(rest.id, t["id"], e, end, ())]
        slots.append({"starts_at_local": "%04d-%02d-%02dT%02d:%02d" % (y, mo, d, m_ // 60, m_ % 60),
                      "starts_at": fmt_epoch(e, rest.tz), "available_table_ids": avail})
    return 200, {"restaurant_id": rest.id, "date": q["date"], "timezone": rest.timezone,
                 "slots": slots}


def type_check_strings(body, names):
    for k in names:
        if k in body and not isinstance(body[k], str):
            raise err(400, "malformed_request", "%s must be a string" % k)


async def h_create(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    names = ("restaurant_id", "table_id", "starts_at_local")
    type_check_strings(body, names)
    for k in names + ("party_size",):
        if k not in body:
            raise err(422, "validation_failed", "%s is required" % k)
    check_party(body["party_size"])
    parts = parse_local(body["starts_at_local"])
    check_id_len(body["restaurant_id"])
    check_id_len(body["table_id"])
    rest = S.rests.get(body["restaurant_id"])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    table = rest.table_by_id.get(body["table_id"])
    if table is None:
        raise err(404, "not_found", "no such table")
    start = rest.check_slot(parts)
    party = body["party_size"]
    if party > table["capacity"]:
        raise err(422, "party_exceeds_capacity")
    end = start + rest.dur * 60
    if S.conflict(rest.id, table["id"], start, end, ()):
        raise err(409, "table_unavailable")
    r = {"id": new_res_id(), "ref": new_ref(), "user": user["id"], "rid": rest.id,
         "tid": table["id"], "party": party, "status": "confirmed",
         "local": body["starts_at_local"], "start": start, "end": end,
         "cts": int(time.time())}
    S.add_res(r)
    resp = res_json(r)
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


async def h_list(req):
    user = auth(req)
    mine = [r for r in S.res.values() if r["user"] == user["id"]]

    def key(r):
        m = re.fullmatch(r"res_([0-9]+)", r["id"])
        return (r["start"], r["cts"], int(m.group(1)) if m else -1, r["id"])
    mine.sort(key=key, reverse=True)
    return 200, {"reservations": [res_json(r) for r in mine]}


async def h_get(req):
    user = auth(req)
    return 200, res_json(own_res(user, req.params[0]))


def cutoff_check(r, rest):
    if time.time() >= r["start"] - rest.cutoff * 60:
        raise err(409, "cutoff_passed")


async def h_cancel(req):
    user = auth(req)
    r = own_res(user, req.params[0])
    if r["status"] == "cancelled":
        return 200, res_json(r)
    cutoff_check(r, S.rests[r["rid"]])
    r["status"] = "cancelled"
    return 200, res_json(r)


def plan_amend(r, body, types_first):
    rest = S.rests[r["rid"]]
    names = ("table_id", "starts_at_local")
    if types_first:
        type_check_strings(body, names)
    if r["status"] != "confirmed":
        raise err(409, "reservation_cancelled")
    cutoff_check(r, rest)
    if not types_first:
        type_check_strings(body, names)
    party = r["party"]
    if "party_size" in body:
        check_party(body["party_size"])
        party = body["party_size"]
    local = body.get("starts_at_local", r["local"])
    parts = parse_local(local)
    tid = body.get("table_id", r["tid"])
    check_id_len(tid)
    table = rest.table_by_id.get(tid)
    if table is None:
        raise err(404, "not_found", "no such table")
    start = rest.check_slot(parts)
    if party > table["capacity"]:
        raise err(422, "party_exceeds_capacity")
    return {"tid": tid, "local": local, "party": party, "start": start,
            "end": start + rest.dur * 60}


def apply_plan(r, p):
    if p["tid"] != r["tid"]:
        S.index_remove(r)
        r["tid"] = p["tid"]
        S.index_add(r)
    r["local"] = p["local"]
    r["party"] = p["party"]
    r["start"] = p["start"]
    r["end"] = p["end"]


async def h_patch(req):
    user = auth(req)
    r = own_res(user, req.params[0])
    body, _ = parse_obj(req)
    p = plan_amend(r, body, True)
    if S.conflict(r["rid"], p["tid"], p["start"], p["end"], (r["ref"],)):
        raise err(409, "table_unavailable")
    apply_plan(r, p)
    return 200, res_json(r)


async def h_moves(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    moves = body.get("moves")
    if not isinstance(moves, list) or not 1 <= len(moves) <= 8:
        raise err(422, "validation_failed", "moves must be 1..8 objects")
    refs = []
    for m in moves:
        if not isinstance(m, dict) or not isinstance(m.get("reference"), str):
            raise err(422, "validation_failed", "each move needs a string reference")
        refs.append(m["reference"])
    if len(set(refs)) != len(refs):
        raise err(422, "validation_failed", "duplicate references")
    rs = [own_res(user, ref) for ref in refs]
    if len({r["rid"] for r in rs}) != 1:
        raise err(422, "validation_failed", "bookings belong to different restaurants")
    plans = [plan_amend(r, m, False) for r, m in zip(rs, moves)]
    listed = set(refs)
    for i, a in enumerate(plans):
        ra = rs[i]
        for j in range(i + 1, len(plans)):
            b = plans[j]
            if a["tid"] == b["tid"] and a["start"] < b["end"] and b["start"] < a["end"]:
                raise err(409, "table_unavailable")
        if S.conflict(ra["rid"], a["tid"], a["start"], a["end"], listed):
            raise err(409, "table_unavailable")
    for r, p in zip(rs, plans):
        apply_plan(r, p)
    resp = {"reservations": [res_json(r) for r in rs]}
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


ROUTES = [
    (re.compile(r"/health"), {"GET": h_health}),
    (re.compile(r"/_test/reset"), {"POST": h_reset}),
    (re.compile(r"/_test/export"), {"GET": h_export}),
    (re.compile(r"/_test/import"), {"POST": h_import}),
    (re.compile(r"/auth/signup"), {"POST": h_signup}),
    (re.compile(r"/auth/login"), {"POST": h_login}),
    (re.compile(r"/restaurants"), {"GET": h_restaurants}),
    (re.compile(r"/restaurants/([^/]+)"), {"GET": h_restaurant}),
    (re.compile(r"/availability"), {"GET": h_availability}),
    (re.compile(r"/reservations"), {"POST": h_create, "GET": h_list}),
    (re.compile(r"/reservations/([^/]+)"), {"GET": h_get, "PATCH": h_patch}),
    (re.compile(r"/reservations/([^/]+)/cancel"), {"POST": h_cancel}),
    (re.compile(r"/reservation-moves"), {"POST": h_moves}),
]


def dumps(obj):
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def error_body(status, code, message):
    return dumps({"error": {"code": code, "message": message}})


async def read_request(scope, receive):
    req = Req()
    req.method = scope["method"].upper()
    req.path = scope["path"]
    req.query = parse_qsl(scope.get("query_string", b"").decode("latin-1"),
                          keep_blank_values=True)
    hs = {}
    for k, v in scope["headers"]:
        hs.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
    req.headers = hs
    chunks = []
    while True:
        msg = await receive()
        if msg["type"] == "http.disconnect":
            raise asyncio.CancelledError()
        chunks.append(msg.get("body", b""))
        if not msg.get("more_body"):
            break
    req.body = b"".join(chunks)
    req.params = ()
    return req


async def route(req):
    allowed = None
    for pat, methods in ROUTES:
        m = pat.fullmatch(req.path)
        if m:
            h = methods.get(req.method)
            if h is None:
                allowed = ", ".join(sorted(methods))
                break
            req.params = m.groups()
            return await h(req), None
    if allowed:
        raise ApiError(405, "method_not_allowed", "method not allowed")
    raise err(404, "not_found", "no such route")


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    if scope["type"] != "http":
        return
    extra = []
    try:
        req = await read_request(scope, receive)
        (status, payload), _ = await route(req)
        body = b"" if payload is None else dumps(payload)
    except ApiError as e:
        status, body = e.status, error_body(e.status, e.code, e.message)
        if status == 405:
            extra = [(b"allow", b"GET, POST, PATCH")]
    except asyncio.CancelledError:
        return
    except Exception:
        traceback.print_exc(file=sys.stderr)
        status, body = 500, error_body(500, "internal_error", "internal error")
    headers = [(b"content-length", str(len(body)).encode())] + extra
    if status != 204:
        headers.append((b"content-type", b"application/json; charset=utf-8"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
