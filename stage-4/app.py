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
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

sys.set_int_max_str_digits(1000000)
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
LOCAL_TIME_RE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


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
    secs = int(round(dtv.utcoffset().total_seconds() / 60.0)) * 60
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return "%s%02d:%02d" % (sign, secs // 3600, (secs % 3600) // 60)


def fmt_dt(d):
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (d.year, d.month, d.day, d.hour, d.minute, d.second)


def fmt_epoch(e, tz):
    d = to_local(e, tz)
    return fmt_dt(d) + fmt_offset(d)


def fmt_utc(e):
    return fmt_dt(to_local(e, UTC)) + "+00:00"


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


def parse_hours(hours, unique=False):
    """-> (normalised list, by-weekday windows). Raises Invalid."""
    if not isinstance(hours, list):
        raise Invalid("opening_hours")
    out = []
    by = {}
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
        if unique and WEEKDAYS.index(wd) in by:
            raise Invalid("duplicate weekday")
        out.append({"weekday": wd, "opens": op, "closes": cl})
        by.setdefault(WEEKDAYS.index(wd), []).append((o, c))
    for v in by.values():
        v.sort()
    return out, by


class Policy:
    """Immutable booking rules. Policy 0 is the fixture; others are published."""

    def __init__(self, version, eff, slot, dur, cutoff, hours, by_wd, caps):
        self.version = version
        self.eff = eff
        self.slot = slot
        self.dur = dur
        self.cutoff = cutoff
        self.hours = hours
        self.by_wd = by_wd
        self.caps = caps

    def terms(self):
        return {"policy_version": self.version, "slot_minutes": self.slot,
                "reservation_duration_minutes": self.dur,
                "cancellation_cutoff_minutes": self.cutoff,
                "opening_hours": copy.deepcopy(self.hours),
                "capacities": dict(self.caps)}

    def to_json(self, rid):
        out = {"restaurant_id": rid, "policy_version": self.version,
               "effective_from": self.eff, "slot_minutes": self.slot,
               "reservation_duration_minutes": self.dur,
               "cancellation_cutoff_minutes": self.cutoff,
               "opening_hours": copy.deepcopy(self.hours), "capacities": dict(self.caps)}
        return out

    def capacity(self, ids):
        return sum(self.caps[t] for t in ids)

    def check_slot(self, tz, parts):
        """Rule chain D5 up to (not including) capacity. Returns start epoch."""
        y, mo, d, h, mi = parts
        try:
            e = local_to_epoch(y, mo, d, h, mi, tz)
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
            ce = closes_epoch(y, mo, d, c // 60, c % 60, tz)
        except Unrep:
            raise err(422, "outside_opening_hours")
        if e + self.dur * 60 > ce:
            raise err(422, "outside_opening_hours")
        if (minute - o) % self.slot:
            raise err(422, "not_on_slot_grid")
        return e

    def slots(self, tz, y, mo, d):
        out = []
        seen = set()
        wd = dt.date(y, mo, d).weekday()
        for o, c in self.by_wd.get(wd, ()):
            try:
                ce = closes_epoch(y, mo, d, c // 60, c % 60, tz)
            except Unrep:
                continue
            m = o
            while m < c:
                try:
                    e = local_to_epoch(y, mo, d, m // 60, m % 60, tz)
                except Unrep:
                    e = None
                if e is not None and e + self.dur * 60 <= ce and m not in seen:
                    seen.add(m)
                    out.append((m, e))
                m += self.slot
        out.sort()
        return out


def parse_policy(body, rest):
    """Validate a complete published policy (422 on any problem)."""
    def bad(msg):
        return err(422, "validation_failed", msg)
    for k in ("effective_from", "slot_minutes", "reservation_duration_minutes",
              "cancellation_cutoff_minutes", "opening_hours", "capacities"):
        if k not in body:
            raise bad("%s is required" % k)
    eff = body["effective_from"]
    m = DATE_RE.fullmatch(eff) if isinstance(eff, str) else None
    if not m:
        raise bad("effective_from must be YYYY-MM-DD")
    try:
        dt.date(*(int(x) for x in m.groups()))
    except ValueError:
        raise bad("effective_from is not a real date")
    slot = body["slot_minutes"]
    dur = body["reservation_duration_minutes"]
    cutoff = body["cancellation_cutoff_minutes"]
    if not (is_int(slot) and 1 <= slot <= 1440):
        raise bad("slot_minutes must be an integer 1..1440")
    if not (is_int(dur) and 1 <= dur <= 1440):
        raise bad("reservation_duration_minutes must be an integer 1..1440")
    if not (is_int(cutoff) and 0 <= cutoff <= 10080):
        raise bad("cancellation_cutoff_minutes must be an integer 0..10080")
    try:
        hours, by = parse_hours(body["opening_hours"], unique=True)
    except Invalid:
        raise bad("invalid opening_hours")
    caps = body["capacities"]
    if not isinstance(caps, dict) or set(caps) != set(rest.table_by_id):
        raise bad("capacities must name exactly the restaurant's tables")
    for v in caps.values():
        if not (is_int(v) and 1 <= v <= 100):
            raise bad("capacities must be integers 1..100")
    ordered = {t["id"]: caps[t["id"]] for t in rest.tables}
    return Policy(0, eff, slot, dur, cutoff, hours, by, ordered)


class Rest:
    def __init__(self, d, importing=False):
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
        slot = d.get("slot_minutes")
        dur = d.get("reservation_duration_minutes")
        cutoff = d.get("cancellation_cutoff_minutes", 0)
        if not (is_int(slot) and slot >= 1):
            raise Invalid("slot_minutes")
        if not (is_int(dur) and dur >= 1):
            raise Invalid("reservation_duration_minutes")
        if not (is_int(cutoff) and cutoff >= 0):
            raise Invalid("cancellation_cutoff_minutes")
        hours, by_wd = parse_hours(d.get("opening_hours", []))
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
        comb = d.get("combinable", [])
        if not isinstance(comb, list):
            raise Invalid("combinable")
        self.combinable = []
        self.pairs = {}
        for p in comb:
            if not (isinstance(p, list) and len(p) == 2 and isinstance(p[0], str)
                    and isinstance(p[1], str) and p[0] != p[1]
                    and p[0] in self.table_by_id and p[1] in self.table_by_id):
                raise Invalid("combinable pair")
            key = frozenset(p)
            if key in self.pairs:
                raise Invalid("duplicate pair")
            self.pairs[key] = [p[0], p[1]]
            self.combinable.append([p[0], p[1]])
        mgr = d.get("manager_user_ids", [])
        if not isinstance(mgr, list):
            raise Invalid("manager_user_ids")
        self.managers = [v_id(x) for x in mgr]
        self.p0 = Policy(0, None, slot, dur, cutoff, hours, by_wd,
                         {t["id"]: t["capacity"] for t in self.tables})
        self.policies = []
        self.revision = 0
        self.closures = []
        if importing:
            rev = d.get("revision", 0)
            if not (is_int(rev) and rev >= 0):
                raise Invalid("revision")
            self.revision = rev
            cls = d.get("closures", [])
            if not isinstance(cls, list):
                raise Invalid("closures")
            for c in cls:
                if not (isinstance(c, dict) and c.get("table_id") in self.table_by_id
                        and is_int(c.get("from")) and is_int(c.get("to")) and c["from"] < c["to"]
                        and isinstance(c.get("plan_id"), str)):
                    raise Invalid("closure")
                self.closures.append({"table_id": c["table_id"], "from": c["from"],
                                      "to": c["to"], "plan_id": c["plan_id"]})
            pols = d.get("policies", [])
            if not isinstance(pols, list):
                raise Invalid("policies")
            for i, pd in enumerate(pols):
                if not isinstance(pd, dict) or pd.get("policy_version") != i + 1 \
                        or not is_int(pd.get("policy_version")):
                    raise Invalid("policy_version")
                try:
                    p = parse_policy(pd, self)
                except ApiError:
                    raise Invalid("policy")
                p.version = i + 1
                self.policies.append(p)

    def to_json(self, export=False):
        p0 = self.p0
        out = {
            "id": self.id, "name": self.name, "timezone": self.timezone,
            "slot_minutes": p0.slot,
            "reservation_duration_minutes": p0.dur,
            "cancellation_cutoff_minutes": p0.cutoff,
            "opening_hours": copy.deepcopy(p0.hours),
            "tables": copy.deepcopy(self.tables),
            "combinable": copy.deepcopy(self.combinable),
            "manager_user_ids": list(self.managers),
            "revision": self.revision,
        }
        if export:
            out["policies"] = [p.to_json(self.id) for p in self.policies]
            out["closures"] = copy.deepcopy(self.closures)
        return out

    def policy_for(self, datestr):
        best = None
        for p in self.policies:
            if p.eff <= datestr and (best is None or (p.eff, p.version) > (best.eff, best.version)):
                best = p
        return best or self.p0

    def resolve(self, ids):
        """Table-selection steps 4-5: unknown ids -> 404, undeclared pair -> 422.
        Returns the ids in canonical (declared) order."""
        for t in ids:
            if t not in self.table_by_id:
                raise err(404, "not_found", "no such table")
        if len(ids) == 2:
            pair = self.pairs.get(frozenset(ids))
            if pair is None:
                raise err(422, "combination_not_allowed", "these tables cannot be combined")
            return list(pair)
        return list(ids)


def date_of(parts):
    return "%04d-%02d-%02d" % parts[:3]


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
        self.series = {}
        self.plans = {}
        self.n_res = 0
        self.n_user = 0
        self.n_series = 0
        self.n_plan = 0

    def index_add(self, r):
        for t in r["tids"]:
            self.by_table.setdefault((r["rid"], t), []).append(r)

    def index_remove(self, r):
        for t in r["tids"]:
            lst = self.by_table.get((r["rid"], t))
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

    def conflict(self, rid, tids, start, end, ignore):
        for t in tids:
            for x in self.by_table.get((rid, t), ()):
                if x["status"] == "confirmed" and x["ref"] not in ignore \
                        and x["start"] < end and start < x["end"]:
                    return True
        rest = self.rests.get(rid)
        if rest is not None:
            for c in rest.closures:
                if c["table_id"] in tids and c["from"] < end and start < c["to"]:
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


def shape_ids(ids):
    """Table-selection step 3 (body-only checks)."""
    if not ids:
        raise err(422, "validation_failed", "table_ids must not be empty")
    if len(set(ids)) != len(ids):
        raise err(422, "validation_failed", "duplicate table ids")
    if len(ids) > 2:
        raise err(422, "combination_not_allowed", "at most two tables can be combined")
    for t in ids:
        check_id_len(t)


def sel_type_checks(body):
    """Table-selection steps 1-2."""
    if "table_id" in body and "table_ids" in body:
        raise err(422, "validation_failed", "send table_id or table_ids, not both")
    if "table_id" in body and not isinstance(body["table_id"], str):
        raise err(400, "malformed_request", "table_id must be a string")
    if "table_ids" in body:
        t = body["table_ids"]
        if not isinstance(t, list) or not all(isinstance(x, str) for x in t):
            raise err(400, "malformed_request", "table_ids must be an array of strings")


def sel_ids(body):
    if "table_ids" in body:
        return list(body["table_ids"])
    if "table_id" in body:
        return [body["table_id"]]
    return None


def created_changes(tids, local, party):
    first = {"field": "table_id", "from": None, "to": tids[0]} if len(tids) == 1 \
        else {"field": "table_ids", "from": None, "to": list(tids)}
    return [first, {"field": "starts_at_local", "from": None, "to": local},
            {"field": "party_size", "from": None, "to": party}]


def diff_changes(old, new):
    ch = []
    if old["tids"] != new["tids"]:
        if len(old["tids"]) == 1 and len(new["tids"]) == 1:
            ch.append({"field": "table_id", "from": old["tids"][0], "to": new["tids"][0]})
        else:
            ch.append({"field": "table_ids", "from": list(old["tids"]), "to": list(new["tids"])})
    if old["local"] != new["local"]:
        ch.append({"field": "starts_at_local", "from": old["local"], "to": new["local"]})
    if old["party"] != new["party"]:
        ch.append({"field": "party_size", "from": old["party"], "to": new["party"]})
    return ch


def valid_terms(t):
    if not isinstance(t, dict):
        raise Invalid("terms")
    if not (is_int(t.get("policy_version")) and t["policy_version"] >= 0
            and is_int(t.get("slot_minutes")) and t["slot_minutes"] >= 1
            and is_int(t.get("reservation_duration_minutes")) and t["reservation_duration_minutes"] >= 1
            and is_int(t.get("cancellation_cutoff_minutes")) and t["cancellation_cutoff_minutes"] >= 0
            and isinstance(t.get("opening_hours"), list) and isinstance(t.get("capacities"), dict)):
        raise Invalid("terms")
    return copy.deepcopy(t)


def valid_hist(h):
    if not isinstance(h, list) or not h:
        raise Invalid("history")
    out = []
    for i, e in enumerate(h):
        if not (isinstance(e, dict) and e.get("seq") == i + 1 and is_int(e.get("seq"))
                and isinstance(e.get("at"), str) and e.get("event") in ("created", "changed", "cancelled", "reassigned")
                and isinstance(e.get("changes"), list) and is_int(e.get("revision"))):
            raise Invalid("history entry")
        ent = {"seq": e["seq"], "at": e["at"], "event": e["event"],
               "changes": copy.deepcopy(e["changes"]), "revision": e["revision"],
               "accepted_terms": valid_terms(e.get("accepted_terms"))}
        if e["event"] == "reassigned":
            if not isinstance(e.get("plan_id"), str):
                raise Invalid("history plan_id")
            ent["plan_id"] = e["plan_id"]
        out.append(ent)
    return out


def build_res(d, rests, importing=False):
    if not isinstance(d, dict):
        raise Invalid("reservation")
    rid_ = v_id(d.get("id"))
    ref = d.get("reference")
    if not isinstance(ref, str) or not REF_RE.fullmatch(ref):
        raise Invalid("reference")
    user = v_id(d.get("user_id"))
    rid = d.get("restaurant_id")
    rest = rests.get(rid) if isinstance(rid, str) else None
    if rest is None or ("table_id" in d and "table_ids" in d):
        raise Invalid("restaurant/table")
    ids = d.get("table_ids") if "table_ids" in d else [d.get("table_id")]
    if not isinstance(ids, list) or not ids or not all(isinstance(t, str) for t in ids):
        raise Invalid("tables")
    try:
        shape_ids(ids)
        tids = rest.resolve(ids)
    except ApiError:
        raise Invalid("tables")
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
    cts = parse_created(d.get("created_at"))
    series_id = series_idx = None
    if importing and "accepted_terms" in d:
        terms = valid_terms(d["accepted_terms"])
        rev = d.get("revision")
        if not (is_int(rev) and rev >= 1):
            raise Invalid("revision")
        hist = valid_hist(d.get("history"))
        series_id = d.get("series_id")
        series_idx = d.get("series_index")
        if series_id is not None and not (isinstance(series_id, str) and is_int(series_idx) and series_idx >= 0):
            raise Invalid("series link")
    else:
        terms = rest.p0.terms()
        rev = 1
        at = fmt_epoch(cts, rest.tz)
        hist = [{"seq": 1, "at": at, "event": "created", "changes": created_changes(tids, local, party),
                 "revision": 1, "accepted_terms": copy.deepcopy(terms)}]
        if status == "cancelled":
            hist.append({"seq": 2, "at": at, "event": "cancelled", "changes": [], "revision": 1,
                         "accepted_terms": copy.deepcopy(terms)})
    end = e + terms["reservation_duration_minutes"] * 60
    if end > MAX_E:
        raise Invalid("end not representable")
    return {"id": rid_, "ref": ref, "user": user, "rid": rid, "tids": tids,
            "party": party, "status": status, "local": local, "start": e,
            "end": end, "cts": cts, "rev": rev, "terms": terms, "hist": hist,
            "series": series_id, "idx": series_idx}


def res_export(r):
    return {"id": r["id"], "reference": r["ref"], "user_id": r["user"],
            "restaurant_id": r["rid"], "table_ids": list(r["tids"]), "party_size": r["party"],
            "status": r["status"], "starts_at_local": r["local"],
            "created_at": fmt_utc(r["cts"]), "revision": r["rev"],
            "accepted_terms": copy.deepcopy(r["terms"]), "history": copy.deepcopy(r["hist"]),
            "series_id": r["series"], "series_index": r["idx"]}


def res_json(r):
    tz = S.rests[r["rid"]].tz
    out = {
        "reservation_id": r["id"], "reference": r["ref"], "restaurant_id": r["rid"],
        "table_ids": list(r["tids"]), "party_size": r["party"], "status": r["status"],
        "starts_at_local": r["local"], "starts_at": fmt_epoch(r["start"], tz),
        "ends_at": fmt_epoch(r["end"], tz), "created_at": fmt_utc(r["cts"]),
        "revision": r["rev"], "accepted_terms": copy.deepcopy(r["terms"]),
    }
    if len(r["tids"]) == 1:
        out["table_id"] = r["tids"][0]
    return out


def build_rests_and_res(rest_list, res_list, importing=False):
    rests = {}
    for x in rest_list:
        r = Rest(x, importing)
        if r.id in rests:
            raise Invalid("duplicate restaurant")
        rests[r.id] = r
    out = []
    refs = set()
    ids = set()
    for x in res_list:
        r = build_res(x, rests, importing)
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
        "restaurants": [r.to_json(export=True) for r in S.rests.values()],
        "reservations": [res_export(r) for r in S.res.values()],
        "idempotency": [
            {"user": k[0], "route": k[1], "key": k[2], "body": v["body"],
             "status": v["status"], "response": copy.deepcopy(v["response"])}
            for k, v in S.idem.items()],
        "series": [{"id": x["id"], "user_id": x["user"], "restaurant_id": x["rid"],
                    "anchor_reference": x["anchor"], "count": x["count"],
                    "interval_weeks": x["interval"], "revision": x["rev"],
                    "references": list(x["refs"]), "exceptions": list(x["exc"]),
                    "dates": list(x["dates"])}
                   for x in S.series.values()],
        "plans": [{"id": p["id"], "restaurant_id": p["rid"], "table_id": p["table_id"],
                   "from": p["from"], "to": p["to"],
                   "assignments": [{"reference": a, "table_ids": list(t)} for a, t in p["assign"]],
                   "restaurant_revision": p["rev"], "applied": p["applied"]}
                  for p in S.plans.values()],
        "counters": {"reservation": S.n_res, "user": S.n_user, "series": S.n_series,
                     "plan": S.n_plan},
        "references": sorted(S.res.keys()),
        "schema": 4,
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
    schema = st.get("schema")
    if "schema" in st and not (is_int(schema) and schema in (1, 2, 3, 4)):
        raise Invalid("schema")
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
    n_series = counters.get("series", 0)
    n_plan = counters.get("plan", 0)
    if not (is_int(n_series) and n_series >= 0 and is_int(n_plan) and n_plan >= 0):
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
    new.rests, reslist = build_rests_and_res(st["restaurants"], st["reservations"], importing=True)
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
    for x in st.get("series", []) if isinstance(st.get("series", []), list) else None:
        if not isinstance(x, dict):
            raise Invalid("series")
        sid = x.get("id")
        refs = x.get("references")
        exc = x.get("exceptions")
        if not (isinstance(sid, str) and isinstance(refs, list) and isinstance(exc, list)
                and len(refs) == len(exc) and all(isinstance(b, bool) for b in exc)
                and is_int(x.get("count")) and x["count"] == len(refs)
                and is_int(x.get("interval_weeks")) and is_int(x.get("revision"))
                and isinstance(x.get("user_id"), str) and isinstance(x.get("restaurant_id"), str)
                and isinstance(x.get("anchor_reference"), str) and sid not in new.series):
            raise Invalid("series")
        for i, ref in enumerate(refs):
            rr = new.res.get(ref) if isinstance(ref, str) else None
            if rr is None or rr["series"] != sid or rr["idx"] != i:
                raise Invalid("series link")
        iv = x["interval_weeks"]
        dates = x.get("dates")
        if dates is None:
            # schema 3: derive scheduled dates from occurrence 1's created entry
            ch = new.res[refs[1]]["hist"][0]["changes"]
            first = [c["to"] for c in ch if c.get("field") == "starts_at_local"]
            if not first or not isinstance(first[0], str):
                raise Invalid("series dates")
            m1 = LOCAL_RE.fullmatch(first[0])
            if not m1 or not 1 <= iv <= 4:
                raise Invalid("series dates")
            d1 = dt.date(int(m1.group(1)), int(m1.group(2)), int(m1.group(3)))
            dates = [(d1 + dt.timedelta(days=(i - 1) * iv * 7)).isoformat() for i in range(len(refs))]
        elif not (isinstance(dates, list) and len(dates) == len(refs)
                  and all(isinstance(q, str) and DATE_RE.fullmatch(q) for q in dates)):
            raise Invalid("series dates")
        new.series[sid] = {"id": sid, "user": x["user_id"], "rid": x["restaurant_id"],
                           "anchor": x["anchor_reference"], "count": x["count"],
                           "interval": iv, "rev": x["revision"],
                           "refs": list(refs), "exc": list(exc), "dates": list(dates)}
    for rr in new.res.values():
        if rr["series"] is not None and rr["series"] not in new.series:
            raise Invalid("series link")
    new.n_res = max(new.n_res, counters["reservation"])
    new.n_user = counters["user"]
    new.n_series = n_series
    plans = st.get("plans", [])
    if not isinstance(plans, list):
        raise Invalid("plans")
    for x in plans:
        if not isinstance(x, dict):
            raise Invalid("plan")
        rr = new.rests.get(x.get("restaurant_id")) if isinstance(x.get("restaurant_id"), str) else None
        asg = x.get("assignments")
        if not (isinstance(x.get("id"), str) and rr is not None and x.get("table_id") in rr.table_by_id
                and is_int(x.get("from")) and is_int(x.get("to")) and x["from"] < x["to"]
                and isinstance(asg, list) and is_int(x.get("restaurant_revision"))
                and isinstance(x.get("applied"), bool) and x["id"] not in new.plans):
            raise Invalid("plan")
        assign = []
        for a in asg:
            if not (isinstance(a, dict) and a.get("reference") in new.res
                    and isinstance(a.get("table_ids"), list) and a["table_ids"]
                    and all(t in rr.table_by_id for t in a["table_ids"])):
                raise Invalid("plan assignment")
            assign.append((a["reference"], list(a["table_ids"])))
        new.plans[x["id"]] = {"id": x["id"], "rid": rr.id, "table_id": x["table_id"],
                              "from": x["from"], "to": x["to"], "assign": assign,
                              "rev": x["restaurant_revision"], "applied": x["applied"]}
    new.n_plan = n_plan
    return new


async def h_import(req):
    global S
    body, _ = parse_obj(req)
    try:
        new = build_import(body)
    except Exception:
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
    if not DIGITS_RE.fullmatch(ps) or len(ps) > 100000 or int(ps) < 1:
        raise err(422, "validation_failed", "party_size must be a positive integer")
    m = DATE_RE.fullmatch(q["date"])
    if not m:
        raise err(422, "validation_failed", "date must be YYYY-MM-DD")
    y, mo, d = (int(x) for x in m.groups())
    try:
        dt.date(y, mo, d)
    except ValueError:
        raise err(422, "validation_failed", "invalid date")
    explain = None
    if "explain" in q:
        if q["explain"] != "true":
            raise err(422, "validation_failed", "explain must be true")
        explain = True
    rest = S.rests.get(q["restaurant_id"])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    party = int(ps)
    pol = rest.policy_for(q["date"])
    slots = []
    for m_, e in pol.slots(rest.tz, y, mo, d):
        end = e + pol.dur * 60
        free = {t["id"] for t in rest.tables
                if not S.conflict(rest.id, (t["id"],), e, end, ())}
        avail = [t["id"] for t in rest.tables
                 if pol.caps[t["id"]] >= party and t["id"] in free]
        options = [{"table_ids": [t["id"]], "capacity": pol.caps[t["id"]]} for t in rest.tables
                   if pol.caps[t["id"]] >= party and t["id"] in free]
        for pair in rest.combinable:
            cap = pol.capacity(pair)
            if cap >= party and pair[0] in free and pair[1] in free:
                options.append({"table_ids": list(pair), "capacity": cap})
        slot = {"starts_at_local": "%04d-%02d-%02dT%02d:%02d" % (y, mo, d, m_ // 60, m_ % 60),
                "starts_at": fmt_epoch(e, rest.tz), "available_table_ids": avail,
                "available_options": options}
        if explain:
            ex = []
            for t in rest.tables:
                cap_ok = party <= pol.caps[t["id"]]
                free_ok = t["id"] in free
                ex.append({"table_id": t["id"], "policy_version": pol.version,
                           "available": cap_ok and free_ok,
                           "rules": [{"rule": "capacity", "holds": cap_ok},
                                     {"rule": "no_overlap", "holds": free_ok}]})
            slot["explain"] = ex
        slots.append(slot)
    return 200, {"restaurant_id": rest.id, "date": q["date"], "timezone": rest.timezone,
                 "slots": slots}


def type_check_strings(body, names):
    for k in names:
        if k in body and not isinstance(body[k], str):
            raise err(400, "malformed_request", "%s must be a string" % k)


def hist_add(r, event, changes, at=None, extra=None):
    tz = S.rests[r["rid"]].tz
    e = {"seq": len(r["hist"]) + 1,
         "at": fmt_epoch(int(time.time()) if at is None else at, tz),
         "event": event, "changes": changes, "revision": r["rev"],
         "accepted_terms": copy.deepcopy(r["terms"])}
    if extra:
        e.update(extra)
    r["hist"].append(e)


def make_res(user, rest, tids, party, local, start, pol):
    now = int(time.time())
    r = {"id": new_res_id(), "ref": new_ref(), "user": user["id"], "rid": rest.id,
         "tids": list(tids), "party": party, "status": "confirmed",
         "local": local, "start": start, "end": start + pol.dur * 60,
         "cts": now, "rev": 1, "terms": pol.terms(), "hist": [], "series": None, "idx": None}
    hist_add(r, "created", created_changes(r["tids"], local, party), at=now)
    S.add_res(r)
    return r


async def h_create(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    names = ("restaurant_id", "starts_at_local")
    type_check_strings(body, names)
    sel_type_checks(body)
    for k in names + ("party_size",):
        if k not in body:
            raise err(422, "validation_failed", "%s is required" % k)
    ids = sel_ids(body)
    if ids is None:
        raise err(422, "validation_failed", "table_id or table_ids is required")
    check_party(body["party_size"])
    parts = parse_local(body["starts_at_local"])
    check_id_len(body["restaurant_id"])
    shape_ids(ids)
    rest = S.rests.get(body["restaurant_id"])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    tids = rest.resolve(ids)
    pol = rest.policy_for(date_of(parts))
    start = pol.check_slot(rest.tz, parts)
    party = body["party_size"]
    if party > pol.capacity(tids):
        raise err(422, "party_exceeds_capacity")
    if S.conflict(rest.id, tids, start, start + pol.dur * 60, ()):
        raise err(409, "table_unavailable")
    r = make_res(user, rest, tids, party, body["starts_at_local"], start, pol)
    rest.revision += 1
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


def opt_own(req):
    """History/decision: any auth failure or foreign reservation is a plain 404."""
    try:
        user = auth(req)
    except ApiError:
        raise err(404, "not_found", "no such reservation")
    return own_res(user, req.params[0])


async def h_history(req):
    r = opt_own(req)
    return 200, {"reference": r["ref"], "entries": copy.deepcopy(r["hist"])}


async def h_decision(req):
    r = opt_own(req)
    return 200, {"reference": r["ref"], "revision": r["rev"],
                 "accepted_terms": copy.deepcopy(r["terms"])}


def cutoff_check(r):
    if time.time() >= r["start"] - r["terms"]["cancellation_cutoff_minutes"] * 60:
        raise err(409, "cutoff_passed")


def series_touch(r, exception):
    sid = r["series"]
    if sid is not None and sid in S.series:
        s_ = S.series[sid]
        s_["rev"] += 1
        if exception:
            s_["exc"][r["idx"]] = True


async def h_cancel(req):
    user = auth(req)
    r = own_res(user, req.params[0])
    if r["status"] == "cancelled":
        return 200, res_json(r)
    cutoff_check(r)
    r["status"] = "cancelled"
    r["rev"] += 1
    hist_add(r, "cancelled", [])
    S.rests[r["rid"]].revision += 1
    series_touch(r, False)
    return 200, res_json(r)


def check_expected_revision(r, body):
    if "expected_revision" in body:
        v = body["expected_revision"]
        if not (is_int(v) and v >= 1):
            raise err(422, "validation_failed", "expected_revision must be a positive integer")
        if v != r["rev"]:
            raise err(409, "stale_revision", "the reservation has changed")


def plan_amend(r, body, types_first):
    rest = S.rests[r["rid"]]

    def types():
        sel_type_checks(body)
        type_check_strings(body, ("starts_at_local",))
    if types_first:
        types()
    check_expected_revision(r, body)
    if r["status"] != "confirmed":
        raise err(409, "reservation_cancelled")
    cutoff_check(r)
    if not types_first:
        types()
    party = r["party"]
    if "party_size" in body:
        check_party(body["party_size"])
        party = body["party_size"]
    local = body.get("starts_at_local", r["local"])
    parts = parse_local(local)
    ids = sel_ids(body)
    if ids is None:
        ids = list(r["tids"])
    else:
        shape_ids(ids)
    tids = rest.resolve(ids)
    if tids == r["tids"] and local == r["local"] and party == r["party"]:
        return {"noop": True, "tids": list(r["tids"]), "local": local, "party": party,
                "start": r["start"], "end": r["end"], "terms": r["terms"]}
    pol = rest.policy_for(date_of(parts))
    start = pol.check_slot(rest.tz, parts)
    if party > pol.capacity(tids):
        raise err(422, "party_exceeds_capacity")
    return {"noop": False, "tids": tids, "local": local, "party": party, "start": start,
            "end": start + pol.dur * 60, "terms": pol.terms()}


def apply_plan(r, p):
    """Commit a real amendment; returns nothing. Caller handles restaurant/series counters."""
    old = {"tids": r["tids"], "local": r["local"], "party": r["party"]}
    if p["tids"] != r["tids"]:
        S.index_remove(r)
        r["tids"] = p["tids"]
        S.index_add(r)
    r["local"] = p["local"]
    r["party"] = p["party"]
    r["start"] = p["start"]
    r["end"] = p["end"]
    r["terms"] = p["terms"]
    r["rev"] += 1
    hist_add(r, "changed", diff_changes(old, {"tids": r["tids"], "local": r["local"], "party": r["party"]}))


async def h_patch(req):
    user = auth(req)
    r = own_res(user, req.params[0])
    body, _ = parse_obj(req)
    p = plan_amend(r, body, True)
    if p["noop"]:
        return 200, res_json(r)
    if S.conflict(r["rid"], p["tids"], p["start"], p["end"], (r["ref"],)):
        raise err(409, "table_unavailable")
    apply_plan(r, p)
    S.rests[r["rid"]].revision += 1
    series_touch(r, True)
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
            if set(a["tids"]) & set(b["tids"]) and a["start"] < b["end"] and b["start"] < a["end"]:
                raise err(409, "table_unavailable")
        if not a["noop"] and S.conflict(ra["rid"], a["tids"], a["start"], a["end"], listed):
            raise err(409, "table_unavailable")
    changed = [(r, p) for r, p in zip(rs, plans) if not p["noop"]]
    for r, p in changed:
        apply_plan(r, p)
    if changed:
        S.rests[rs[0]["rid"]].revision += 1
        touched = {}
        for r, _ in changed:
            if r["series"] is not None and r["series"] in S.series:
                touched.setdefault(r["series"], []).append(r["idx"])
        for sid, idxs in touched.items():
            S.series[sid]["rev"] += 1
            for i in idxs:
                S.series[sid]["exc"][i] = True
    resp = {"reservations": [res_json(r) for r in rs]}
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


# ------------------------------------------------------------- policies ---

async def h_policy_post(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    rest = S.rests.get(req.params[0])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    if user["id"] not in rest.managers:
        raise err(403, "forbidden", "only managers may publish policies")
    pol = parse_policy(body, rest)
    pol.version = len(rest.policies) + 1
    rest.policies.append(pol)
    rest.revision += 1
    resp = pol.to_json(rest.id)
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


async def h_policy_list(req):
    rest = S.rests.get(req.params[0])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    return 200, {"policies": [p.to_json(rest.id) for p in rest.policies]}


# --------------------------------------------------------------- series ---

def series_json(sr):
    occ = []
    for i, ref in enumerate(sr["refs"]):
        occ.append({"index": i, "reference": ref, "exception": sr["exc"][i],
                    "reservation": res_json(S.res[ref])})
    return {"series_id": sr["id"], "revision": sr["rev"], "interval_weeks": sr["interval"],
            "count": sr["count"], "anchor_reference": sr["anchor"], "occurrences": occ}


async def h_series_create(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    ref = body.get("anchor_reference")
    count = body.get("count")
    iv = body.get("interval_weeks")
    if not isinstance(ref, str):
        raise err(422, "validation_failed", "anchor_reference is required")
    if not (is_int(count) and 2 <= count <= 12):
        raise err(422, "validation_failed", "count must be an integer 2..12")
    if not (is_int(iv) and 1 <= iv <= 4):
        raise err(422, "validation_failed", "interval_weeks must be an integer 1..4")
    anchor = S.res.get(ref)
    if anchor is None or anchor["user"] != user["id"]:
        raise err(404, "not_found", "no such reservation")
    if anchor["status"] != "confirmed":
        raise err(409, "reservation_cancelled")
    if anchor["series"] is not None:
        raise err(409, "already_in_series")
    cutoff_check(anchor)
    rest = S.rests[anchor["rid"]]
    y, mo, d, h, mi = parse_local(anchor["local"])
    base = dt.date(y, mo, d)
    pending = []
    for i in range(1, count):
        try:
            nd = base + dt.timedelta(days=i * iv * 7)
        except OverflowError:
            raise err(422, "outside_opening_hours")
        parts = (nd.year, nd.month, nd.day, h, mi)
        pol = rest.policy_for(date_of(parts))
        start = pol.check_slot(rest.tz, parts)
        if anchor["party"] > pol.capacity(anchor["tids"]):
            raise err(422, "party_exceeds_capacity")
        end = start + pol.dur * 60
        if S.conflict(rest.id, anchor["tids"], start, end, ()):
            raise err(409, "table_unavailable")
        for (_, s2, e2, _) in pending:
            if s2 < end and start < e2:
                raise err(409, "table_unavailable")
        pending.append((parts, start, end, pol))
    # commit
    while True:
        S.n_series += 1
        sid = "ser_%d" % S.n_series
        if sid not in S.series:
            break
    refs = [anchor["ref"]]
    for parts, start, end, pol in pending:
        local = "%04d-%02d-%02dT%02d:%02d" % parts
        r = make_res(user, rest, anchor["tids"], anchor["party"], local, start, pol)
        r["series"] = sid
        r["idx"] = len(refs)
        refs.append(r["ref"])
    anchor["series"] = sid
    anchor["idx"] = 0
    S.series[sid] = {"id": sid, "user": user["id"], "rid": rest.id, "anchor": anchor["ref"],
                     "count": count, "interval": iv, "rev": 1, "refs": refs,
                     "exc": [False] * count,
                     "dates": [(base + dt.timedelta(days=i * iv * 7)).isoformat() for i in range(count)]}
    rest.revision += 1
    resp = series_json(S.series[sid])
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


async def h_series_get(req):
    try:
        user = auth(req)
    except ApiError:
        raise err(404, "not_found", "no such series")
    sr = S.series.get(req.params[0])
    if sr is None or sr["user"] != user["id"]:
        raise err(404, "not_found", "no such series")
    return 200, series_json(sr)


# ---------------------------------------------------------- series amend --

async def h_series_amend(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    sr = S.series.get(req.params[0])
    if sr is None or sr["user"] != user["id"]:
        raise err(404, "not_found", "no such series")
    er = body.get("expected_revision")
    fi = body.get("from_index")
    lt = body.get("local_time")
    if not (is_int(er) and er >= 1):
        raise err(422, "validation_failed", "expected_revision must be a positive integer")
    if not (is_int(fi) and 0 <= fi <= sr["count"] - 1):
        raise err(422, "validation_failed", "from_index out of range")
    if not (isinstance(lt, str) and LOCAL_TIME_RE.fullmatch(lt)):
        raise err(422, "validation_failed", "local_time must be HH:MM")
    if er != sr["rev"]:
        raise err(409, "stale_revision", "the series has changed")
    rest = S.rests[sr["rid"]]
    plans = []
    for i in range(fi, sr["count"]):
        r = S.res[sr["refs"][i]]
        if r["status"] != "confirmed" or sr["exc"][i]:
            continue
        local = "%sT%s" % (sr["dates"][i], lt)
        if local == r["local"]:
            continue
        cutoff_check(r)
        parts = parse_local(local)
        pol = rest.policy_for(date_of(parts))
        start = pol.check_slot(rest.tz, parts)
        if r["party"] > pol.capacity(r["tids"]):
            raise err(422, "party_exceeds_capacity")
        plans.append((r, {"noop": False, "tids": list(r["tids"]), "local": local, "party": r["party"],
                          "start": start, "end": start + pol.dur * 60, "terms": pol.terms()}))
    changing = {r["ref"] for r, _ in plans}
    for i, (ra, a) in enumerate(plans):
        for rb, b in plans[i + 1:]:
            if set(a["tids"]) & set(b["tids"]) and a["start"] < b["end"] and b["start"] < a["end"]:
                raise err(409, "table_unavailable")
        if S.conflict(ra["rid"], a["tids"], a["start"], a["end"], changing):
            raise err(409, "table_unavailable")
    for r, p in plans:
        apply_plan(r, p)
    if plans:
        sr["rev"] += 1
        rest.revision += 1
    resp = series_json(sr)
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


# --------------------------------------------------------------- replans --

INSTANT_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2})"
                        r"(?::([0-9]{2})(?:\.[0-9]+)?)?([Zz]|[+-][0-9]{2}:[0-9]{2})")


def parse_instant(v, tz):
    def bad(msg):
        return err(422, "validation_failed", msg)
    m = INSTANT_RE.fullmatch(v) if isinstance(v, str) else None
    if not m:
        raise bad("instants need an explicit offset, e.g. 2026-09-28T18:00:00+02:00")
    y, mo, d, h, mi = (int(m.group(i)) for i in range(1, 6))
    sec = int(m.group(6) or 0)
    off = m.group(7)
    if off in ("Z", "z"):
        offs = 0
    else:
        oh, om = int(off[1:3]), int(off[4:6])
        if oh > 23 or om > 59:
            raise bad("bad offset")
        offs = (oh * 3600 + om * 60) * (1 if off[0] == "+" else -1)
    try:
        dt.datetime(y, mo, d, h, mi, sec)
    except ValueError:
        raise bad("not a valid instant")
    e = calendar.timegm((y, mo, d, h, mi, sec)) - offs
    try:
        loc = to_local(e, tz)
    except (Unrep, OverflowError, ValueError):
        raise bad("instant out of range")
    if int(loc.utcoffset().total_seconds()) % 60:
        raise bad("instant has a non-minute UTC offset in the restaurant timezone")
    return e


def solve_plan(rest, considered, closed_tid, fixed):
    """Exact minimiser of (moved, unused seats, rank vector). None if infeasible."""
    opts = [(t["id"],) for t in rest.tables] + [tuple(p) for p in rest.combinable]
    n = len(considered)
    cand = []
    for b in considered:
        caps = b["terms"]["capacities"]
        lst = []
        for rank, opt in enumerate(opts):
            if closed_tid in opt:
                continue
            cap = sum(caps.get(t, 0) for t in opt)
            if cap < b["party"]:
                continue
            if any(c["table_id"] in opt and c["from"] < b["end"] and b["start"] < c["to"]
                   for c in rest.closures):
                continue
            if any(set(f["tids"]) & set(opt) and f["start"] < b["end"] and b["start"] < f["end"]
                   for f in fixed):
                continue
            lst.append((0 if list(opt) == b["tids"] else 1, cap - b["party"], rank, opt))
        if not lst:
            return None
        lst.sort()
        cand.append(lst)
    ov = [[considered[i]["start"] < considered[j]["end"] and considered[j]["start"] < considered[i]["end"]
           for j in range(n)] for i in range(n)]
    minc = [0] * (n + 1)
    minu = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        minc[i] = minc[i + 1] + min(x[0] for x in cand[i])
        minu[i] = minu[i + 1] + min(x[1] for x in cand[i])
    best = [None]
    chosen = [None] * n
    ranks = [0] * n
    deadline = time.time() + 4.0

    def dfs(i, moved, unused):
        if time.time() > deadline:
            raise TimeoutError()
        if i == n:
            key = (moved, unused, tuple(ranks))
            if best[0] is None or key < best[0][0]:
                best[0] = (key, list(chosen))
            return
        if best[0] is not None:
            bm, bu, br = best[0][0]
            lm, lu = moved + minc[i], unused + minu[i]
            if lm > bm or (lm == bm and lu > bu):
                return
            if lm == bm and lu == bu and tuple(ranks[:i]) > br[:i]:
                return
        for ch, un, rank, opt in cand[i]:
            if any(ov[j][i] and set(chosen[j]) & set(opt) for j in range(i)):
                continue
            chosen[i] = opt
            ranks[i] = rank
            dfs(i + 1, moved + ch, unused + un)
        chosen[i] = None

    try:
        dfs(0, 0, 0)
    except TimeoutError:
        raise err(422, "planning_limit", "the plan is too large to solve")
    if best[0] is None:
        return None
    key, ch = best[0]
    return key[0], key[1], [list(c) for c in ch]


async def h_replan(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    rest = S.rests.get(req.params[0])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    if user["id"] not in rest.managers:
        raise err(403, "forbidden", "only managers may plan seating changes")
    tid = body.get("table_id")
    if not isinstance(tid, str):
        raise err(422, "validation_failed", "table_id is required")
    frm = parse_instant(body.get("from"), rest.tz)
    to = parse_instant(body.get("to"), rest.tz)
    if not frm < to:
        raise err(422, "validation_failed", "from must be before to")
    if tid not in rest.table_by_id:
        raise err(404, "not_found", "no such table")
    confirmed = [r for r in S.res.values() if r["rid"] == rest.id and r["status"] == "confirmed"]
    considered = sorted((r for r in confirmed if r["start"] < to and frm < r["end"]), key=lambda r: r["ref"])
    if len(considered) > 8 or len(rest.tables) + len(rest.combinable) > 12:
        raise err(422, "planning_limit", "too many bookings or tables to plan")
    cons_refs = {r["ref"] for r in considered}
    fixed = [r for r in confirmed if r["ref"] not in cons_refs]
    sol = solve_plan(rest, considered, tid, fixed)
    if sol is None:
        raise err(409, "no_feasible_plan", "no seating arrangement can keep every booking")
    moved, unused, assign = sol
    n = S.n_plan + 1
    while "plan_%d" % n in S.plans:
        n += 1
    pid = "plan_%d" % n
    resp = {"plan_id": pid, "restaurant_revision": rest.revision,
            "closure": {"table_id": tid, "from": fmt_epoch(frm, rest.tz), "to": fmt_epoch(to, rest.tz)},
            "assignments": [{"reference": r["ref"], "table_ids": list(a), "changed": list(a) != r["tids"]}
                            for r, a in zip(considered, assign)],
            "moved_count": moved, "unused_seats": unused}
    S.n_plan = n
    S.plans[pid] = {"id": pid, "rid": rest.id, "table_id": tid, "from": frm, "to": to,
                    "assign": [(r["ref"], list(a)) for r, a in zip(considered, assign)],
                    "rev": rest.revision, "applied": False}
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


async def h_replan_apply(req):
    user = auth(req)
    body, canon = parse_obj(req)
    ik, rec = idem_pre(req, user, canon)
    if rec is not None:
        return 200, rec["response"]
    rest = S.rests.get(req.params[0])
    if rest is None:
        raise err(404, "not_found", "no such restaurant")
    if user["id"] not in rest.managers:
        raise err(403, "forbidden", "only managers may apply seating plans")
    plan = S.plans.get(req.params[1])
    if plan is None or plan["rid"] != rest.id:
        raise err(404, "not_found", "no such plan")
    if plan["applied"]:
        raise err(409, "plan_already_applied", "this plan was already applied")
    if plan["rev"] != rest.revision:
        raise err(409, "stale_plan", "the restaurant changed since the plan was proposed")
    touched = set()
    rs = []
    for ref, tids in plan["assign"]:
        r = S.res[ref]
        rs.append(r)
        if r["tids"] != tids:
            old = list(r["tids"])
            S.index_remove(r)
            r["tids"] = list(tids)
            S.index_add(r)
            r["rev"] += 1
            hist_add(r, "reassigned", [{"field": "table_ids", "from": old, "to": list(tids)}],
                     extra={"plan_id": plan["id"]})
            if r["series"] is not None and r["series"] in S.series:
                touched.add(r["series"])
    for sid in touched:
        S.series[sid]["rev"] += 1
    rest.closures.append({"table_id": plan["table_id"], "from": plan["from"], "to": plan["to"],
                          "plan_id": plan["id"]})
    rest.revision += 1
    plan["applied"] = True
    resp = {"plan_id": plan["id"], "restaurant_revision": rest.revision,
            "reservations": [res_json(r) for r in rs]}
    S.idem[ik] = {"body": canon, "status": 201, "response": resp}
    return 201, copy.deepcopy(resp)


class Raw:
    def __init__(self, body, ctype, cache="no-cache"):
        self.body, self.ctype, self.cache = body, ctype, cache


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def load_static(name):
    with open(os.path.join(STATIC_DIR, name), "rb") as f:
        return f.read()


SHELL = Raw(load_static("index.html"), "text/html; charset=utf-8")
ASSETS = {
    "/assets/app.js": Raw(load_static("app.js"), "text/javascript; charset=utf-8"),
    "/assets/app.css": Raw(load_static("app.css"), "text/css; charset=utf-8"),
    "/assets/favicon.svg": Raw(load_static("favicon.svg"), "image/svg+xml"),
}


async def h_shell(req):
    return 200, SHELL


async def h_asset(req):
    return 200, ASSETS[req.path]


ROUTES = [
    (re.compile(r"/|/signup|/login|/lookup"), {"GET": h_shell}),
    (re.compile(r"/assets/(?:app\.js|app\.css|favicon\.svg)"), {"GET": h_asset}),
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
    (re.compile(r"/reservations/([^/]+)/history"), {"GET": h_history}),
    (re.compile(r"/reservations/([^/]+)/decision"), {"GET": h_decision}),
    (re.compile(r"/reservation-moves"), {"POST": h_moves}),
    (re.compile(r"/restaurants/([^/]+)/policies"), {"POST": h_policy_post, "GET": h_policy_list}),
    (re.compile(r"/restaurants/([^/]+)/replans"), {"POST": h_replan}),
    (re.compile(r"/restaurants/([^/]+)/replans/([^/]+)/apply"), {"POST": h_replan_apply}),
    (re.compile(r"/series/([^/]+)/amend"), {"POST": h_series_amend}),
    (re.compile(r"/series"), {"POST": h_series_create}),
    (re.compile(r"/series/([^/]+)"), {"GET": h_series_get}),
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
    ctype = "application/json; charset=utf-8"
    try:
        req = await read_request(scope, receive)
        (status, payload), _ = await route(req)
        if isinstance(payload, Raw):
            body = payload.body
            ctype = payload.ctype
            extra = [(b"cache-control", payload.cache.encode())]
        else:
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
        headers.append((b"content-type", ctype.encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
