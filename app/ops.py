"""Running the station day to day (build plan P1): the exceptions work list, pick waves, end-of-day scan forms
and pickups, the results page, cycle counts and tracking. Each reads only what it needs: exceptions and
waves use indexed queries, end of day keeps a small set of labels waiting for a scan form."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo
from . import db, packaging, shipping
from .config import settings
from .orderinfo import exception_of
from .pipeline import Refuse, _get, _lk, summary_row, view
from .services import odoo


# ------------------------------------------------------------ exceptions work list (P1 #4)
def exceptions(limit=200):
    """Open exceptions, oldest first: released and failed claims, flagged orders, rejected tracking."""
    now = datetime.now(timezone.utc)
    rows = []
    for o in db.list_orders(exceptions=True, limit=limit):
        since = o.get("released_at") or o.get("updated_at") or o.get("created_at")
        age_h = (now - datetime.fromisoformat(since.replace("Z", "+00:00"))).total_seconds() / 3600 if since else None
        rows.append(dict(summary_row(o), reason=exception_of(o), owner=o.get("exception_owner") or "",
                         age_hours=round(age_h, 1) if age_h is not None else None))
    return sorted(rows, key=lambda r: -(r["age_hours"] or 0))


def assign_exception(oid, owner):
    with _lk(oid):
        o = _get(oid)
        o["exception_owner"] = (owner or "").strip()
        db.log(oid, "exception_owner", f"Owner: {o['exception_owner'] or 'nobody'}")
        return view(db.save_order(o))


def resolve_exception(oid, note):
    if not (note or "").strip():
        raise Refuse(400, "Say what was done, so the next person knows")
    with _lk(oid):
        o = _get(oid)
        if not exception_of(o):
            raise Refuse(409, "This order has no open exception")
        o["exception_resolved"] = {"note": note.strip(), "at": db.now_iso()}
        if o.get("writeback_error") is None:
            o.pop("attention", None)
        db.log(oid, "exception_resolved", note.strip())
        return view(db.save_order(o))


# ------------------------------------------------------------ pick waves (P1 #2)
def _cutoffs():
    return sorted((int(t.split(":")[0]), int(t.split(":")[1])) for t in settings.wave_times)


def wave_for(iso):
    """The wave an order claimed at `iso` goes on: the first cutoff at or after the claim, in WAVE_TZ;
    after the day's last cutoff it goes on the next day's first wave."""
    tz = ZoneInfo(settings.wave_tz)
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
    for h, m in _cutoffs():
        cut = t.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= cut:
            return cut
    h, m = _cutoffs()[0]
    return (t + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)


def waves():
    """Orders still to pick, grouped by wave, each with one combined pick list sorted by shelf."""
    groups = defaultdict(list)
    for o in db.list_orders(statuses=("queued", "picking"), oldest_first=True, limit=2000):
        groups[wave_for(o.get("claimed_at") or o["created_at"])].append(o)
    out = []
    for cut, orders in sorted(groups.items()):
        lines = {}
        for o in orders:
            for p in o["pick"]:
                todo = p["need"] - len(p["picked"])
                if todo <= 0:
                    continue
                line = lines.setdefault(p["sku"], {"sku": p["sku"], "title": p["title"], "bin": p["bin"],
                                                   "glass": p.get("glass"), "qty": 0, "orders": []})
                line["qty"] += todo
                line["orders"].append({"name": o["name"], "qty": todo})
        out.append({"wave": cut.isoformat(), "label": cut.strftime("%a %d %b, %H:%M"),
                    "orders": [summary_row(o) for o in orders],
                    "pick_list": sorted(lines.values(), key=lambda l: (l["bin"], l["sku"]))})
    return out


# ------------------------------------------------------------ end of day: scan forms and pickups (P1 #3)
def end_of_day_status():
    pending = db.kv_get("scan_pending") or {}
    forms = (db.kv_get("scan_forms") or [])[-30:]
    return {"pending": {c: len(ids) for c, ids in pending.items() if ids}, "forms": list(reversed(forms)),
            "pickups": list(reversed((db.kv_get("pickups") or [])[-30:]))}


def create_scan_form(carrier):
    """One scan form per carrier per day, for every label bought since the last form."""
    ids = (db.kv_get("scan_pending") or {}).get(carrier) or []
    orders = [o for o in (db.get_order(i) for i in ids) if o and o.get("label")]
    if not orders:
        raise Refuse(409, f"No {carrier} labels are waiting for a scan form")
    try:
        form = shipping.end_of_day(carrier, [o["label"] for o in orders])
    except shipping.ShippingError as e:
        raise Refuse(502, str(e))
    rec = {"id": form["id"], "url": form.get("url"), "carrier": carrier, "at": db.now_iso(),
           "parcels": [{"oid": o["id"], "order": o["name"], "tracking": o["label"]["tracking"],
                        "service": o["label"]["service"], "weight_oz": o.get("weight_oz")} for o in orders]}
    db.kv_update("scan_forms", lambda forms: (forms + [rec])[-365:], [])
    done = {o["id"] for o in orders}
    db.kv_update("scan_pending", lambda d: {**d, carrier: [x for x in d.get(carrier, []) if x not in done]}, {})
    for o in orders:
        db.log(o["id"], "scan_form", f"On {carrier} scan form {rec['id']}")
    return rec


def book_pickup(carrier, start, end):
    """Book the carrier to collect the parcels on its latest scan form."""
    forms = [f for f in (db.kv_get("scan_forms") or []) if f["carrier"] == carrier]
    if not forms:
        raise Refuse(409, f"Make today's {carrier} scan form first")
    labels = [o["label"] for o in (db.get_order(p["oid"]) for p in forms[-1]["parcels"]) if o and o.get("label")]
    if not labels:
        raise Refuse(409, "The last scan form's labels were all voided")
    try:
        got = shipping.pickup(carrier, (start, end), labels)
    except shipping.ShippingError as e:
        raise Refuse(502, f"Pickup not booked: {e}")
    rec = {"id": got["id"], "confirmation": got.get("confirmation"), "carrier": carrier, "start": start, "end": end,
           "parcels": len(labels), "at": db.now_iso()}
    db.kv_update("pickups", lambda p: (p + [rec])[-365:], [])
    return rec


def track(oid):
    o = _get(oid)
    if not o.get("label"):
        raise Refuse(409, "No label yet")
    try:
        t = shipping.track(o["label"])
    except shipping.ShippingError as e:
        raise Refuse(502, str(e))
    with _lk(oid):
        o = _get(oid)
        o["tracking_status"] = t
        db.save_order(o)
    return t


# ------------------------------------------------------------ results page (P1 #5)
def _hours(a, b):
    return (datetime.fromisoformat(b.replace("Z", "+00:00")) - datetime.fromisoformat(a.replace("Z", "+00:00"))).total_seconds() / 3600


def results(days=30):
    """Whether the lane is worth running, over orders created in the last `days` days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    orders = [o for o in db.list_orders(since=since, kind="order", limit=50_000)]
    eligible = [o for o in orders if o.get("checks") and all(c["ok"] for c in o["checks"] if c["key"] != "reserve")]
    claimed = [o for o in orders if o.get("claimed_day")]
    shipped = [o for o in claimed if o["status"] == "shipped" and o.get("shipped_at") and o.get("created_at")]
    ship_hours = [_hours(o["created_at"], o["shipped_at"]) for o in shipped]
    cat = packaging.catalog()
    pack_cost = [o["package"].get("cost_each", cat.get(o["package"].get("scanned"), {}).get("cost", 0))
                 for o in shipped if (o.get("package") or {}).get("scanned")]
    postage = [float(o["label"]["rate"]) for o in shipped if o.get("label")]
    released = Counter((o.get("release_reason") or "Unknown").split(". ")[0].split(" (")[0]
                       for o in claimed if o["status"] == "released")
    kept = Counter(c["key"] for o in orders if not o.get("claimed_day") for c in (o.get("checks") or []) if not c["ok"])
    return {
        "days": days, "orders_seen": len(orders), "eligible": len(eligible), "claimed": len(claimed),
        "shipped": len(shipped),
        "median_ship_hours": round(median(ship_hours), 1) if ship_hours else None,
        "ufsi_baseline_hours": settings.ufsi_baseline_hours,
        "release_reasons": released.most_common(), "kept_by_ufsi_reasons": kept.most_common(),
        "avg_packaging_cost": round(sum(pack_cost) / len(pack_cost), 2) if pack_cost else None,
        "avg_postage": round(sum(postage) / len(postage), 2) if postage else None,
    }


# ------------------------------------------------------------ cycle counts (P1 #6)
def cycle_count(sku, counted, note=""):
    """Log a physical count against Odoo's on-hand. Odoo stays the record; the variance is what's logged."""
    if not odoo.product(sku):
        raise Refuse(404, f"Unknown SKU {sku}")
    try:
        counted = int(counted)
    except (TypeError, ValueError):
        raise Refuse(400, "The count must be a whole number")
    if counted < 0:
        raise Refuse(400, "The count can't be negative")
    system = odoo.on_hand(sku)
    rec = {"sku": sku, "counted": counted, "system": system, "variance": counted - system,
           "note": (note or "").strip(), "at": db.now_iso()}
    db.kv_update("cycle_counts", lambda rows: (rows + [rec])[-1000:], [])
    db.log(f"stock:{sku}", "cycle_count", f"Counted {counted}, Odoo says {system} (variance {rec['variance']:+d})")
    return rec


def cycle_counts(limit=100):
    return list(reversed((db.kv_get("cycle_counts") or [])[-limit:]))
