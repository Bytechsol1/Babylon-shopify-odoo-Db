"""The Babylon lane: claim -> pick -> pack -> weigh -> live check -> label -> close.

Every mutating call holds its order's lock, so a double click or a webhook racing a poll can't claim
twice or buy two labels. Locks come from a fixed pool picked by order id (lock striping), so memory
stays flat however many orders pass through. The locks live in this process: run one uvicorn worker.

Cost model: every order operation touches one order, its lines and its SKUs, never the whole order
table. Lists, counts and searches run as indexed SQL (db.py). Polling asks Shopify only for orders
changed since the last poll and skips the ones whose updatedAt it has already processed."""
import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from . import db, clock, packaging, shipping, zpl
from .config import settings
from .eligibility import evaluate, leaf_needs
from .services import odoo, shopify, deposco
from .shopify import ShopifyError
from .deposco import DeposcoUnavailable

ACTIVE = ("queued", "picking", "packed", "labeled")
DONE = ("shipped", "released", "cancelled")
NOT_OURS = ("ufsi", "shadow", "claim_failed")
POLL_LOOKBACK = timedelta(minutes=15)  # first poll after a fresh start looks this far back
POLL_OVERLAP = timedelta(minutes=2)    # each poll re-reads a little of the last window (clock skew)
LOW_STOCK = 5

_STRIPES = [threading.RLock() for _ in range(256)]


class Refuse(Exception):
    def __init__(self, status, message, checks=None):
        super().__init__(message)
        self.status, self.message, self.checks = status, message, checks


def _lk(oid):
    return _STRIPES[hash(oid) % len(_STRIPES)]


def _get(oid):
    o = db.get_order(oid)
    if not o:
        raise Refuse(404, "Order not found")
    return o


def lines_hash(o):
    key = {"lines": sorted((l["sku"], l["qty"]) for l in o["lines"]),
           "addr": {k: (o["address"].get(k) or "") for k in ("name", "address1", "address2", "city", "state", "zip", "country")}}
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()


def _claimed_today():
    return db.count_claimed_on(clock.now().date().isoformat())


def _build_pick(need, old=None):
    old = {p["sku"]: p["picked"] for p in (old or [])}
    out = []
    for sku, q in need.items():
        p = odoo.product(sku)
        out.append({"sku": sku, "title": p["name"], "need": q, "bin": p["bin"], "glass": p["glass"],
                    "picked": old.get(sku, [])[:q]})
    return out


def _suggest(need):
    items = [(odoo.product(s), q) for s, q in need.items()]
    return dict(packaging.suggest(items), scanned=None, override=False)


def _units(o):
    return [(odoo.product(p["sku"]), p["need"]) for p in o["pick"]]


def _pick_done(o):
    return bool(o["pick"]) and all(len(p["picked"]) >= p["need"] for p in o["pick"])


def _return_package(o):
    """A box taken for an order that won't ship goes back on the packaging count."""
    pkg = o.get("package") or {}
    if pkg.get("scanned") and not o.get("label"):
        packaging.give_back(pkg["scanned"])
        pkg["scanned"] = None


def view(o):
    o = dict(o)
    o["pick_done"] = _pick_done(o)
    o["ready_for_label"] = bool(o["pick_done"] and (o.get("package") or {}).get("scanned") and o.get("weight_oz"))
    return o


def summary_row(o):
    """What a list needs: no lines, pick, label or checks."""
    a = o.get("address") or {}
    return {"id": o["id"], "name": o["name"], "kind": o["kind"], "status": o["status"],
            "created_at": o.get("created_at"), "updated_at": o.get("updated_at"), "claim_route": o.get("claim_route"),
            "units": sum(l["qty"] for l in o.get("lines", [])),
            "address": {k: a.get(k) for k in ("name", "city", "state", "country")}}


def _failed(checks):
    """The checks that failed, for the audit log. Check texts state the rule, so say they failed."""
    bad = [c["text"] for c in checks if not c["ok"]]
    return ("Failed check: " + "; ".join(bad)) if bad else ""


# ------------------------------------------------------------ claim
def ingest_order(gid, trigger="webhook"):
    with _lk(gid):
        existing = db.get_order(gid)
        if existing:
            return existing
        o = shopify.get_order(gid)
        rec = {"id": o["id"], "name": o["name"], "kind": "order", "status": "ufsi", "trigger": trigger,
               "created_at": o["created_at"], "pick": [], "package": None, "weight_oz": None, "label": None,
               "shop_updated": o.get("updated_at")}
        return _decide(rec, o)


def _decide(rec, o):
    """Eligibility, then claim (or log what shadow mode would do). Caller holds the order's lock."""
    gid = rec["id"]
    rec.update(status="ufsi", address=o["address"], shipping_method=o["shipping_method"], lines=o["lines"],
               lines_hash=lines_hash(o), fo_id=o["fo_id"], seen=_signature(o))
    d = evaluate(o, odoo, settings, claimed_today=_claimed_today())
    rec["checks"] = d["checks"]
    if not d["eligible"]:
        db.log(gid, "decision", "Stays with UFSI. " + _failed(d["checks"]))
        _send_to_ufsi_if_routed_here(rec, o)
        return db.save_order(rec)
    if settings.claim_mode != "live":
        rec["status"] = "shadow"
        db.log(gid, "decision", "Shadow mode: would claim for Babylon. Nothing was changed.")
        return db.save_order(rec)
    need = leaf_needs(odoo, o["lines"])
    if not odoo.reserve(gid, need):
        rec["checks"].append({"key": "reserve", "ok": False, "text": "Could not reserve stock in Odoo"})
        db.log(gid, "decision", "Stays with UFSI. Odoo could not reserve the stock.")
        _send_to_ufsi_if_routed_here(rec, o)
        return db.save_order(rec)
    route = "routing"
    try:
        if o["location_id"] != settings.babylon_location_id:
            rec["fo_id"] = shopify.move_fulfillment_order(o["fo_id"], settings.babylon_location_id)
            route = "move"
    except ShopifyError as e:
        odoo.release(gid)
        rec["status"] = "claim_failed"
        db.log(gid, "claim_failed", str(e))
        return db.save_order(rec)
    rec.update(status="queued", claim_route=route, claimed_day=clock.now().date().isoformat(),
               pick=_build_pick(need), package=_suggest(need))
    db.log(gid, "claimed", "Claimed for Babylon" + (" (moved with fulfillmentOrderMove)" if route == "move"
                                                     else " (Shopify routing had already assigned it)"))
    return db.save_order(rec)


def _send_to_ufsi_if_routed_here(rec, o):
    """Shopify's own routing can assign an order straight to Babylon. If Babylon isn't taking it, it has to
    go to UFSI, or nobody ships it. Shadow mode only notes it."""
    if o["location_id"] != settings.babylon_location_id or not o["fo_id"]:
        return
    if settings.claim_mode != "live":
        db.log(rec["id"], "routing", "Shopify routed this order to Babylon. Live mode would move it to UFSI.")
        return
    try:
        rec["fo_id"] = shopify.move_fulfillment_order(o["fo_id"], settings.ufsi_location_id)
    except ShopifyError as e:
        rec["attention"] = f"Shopify routed this order to Babylon and moving it to UFSI failed ({e}). Move it to UFSI by hand."
        db.log(rec["id"], "route_failed", rec["attention"])
        return
    o["location_id"] = settings.ufsi_location_id
    rec["seen"] = _signature(o)  # the move itself is not a change worth re-checking
    rec.pop("attention", None)
    db.log(rec["id"], "routed_to_ufsi", "Shopify had routed it to Babylon. Moved to UFSI so it still ships.")


def _signature(o):
    """Everything eligibility looks at, so a re-check only runs when Shopify actually changed the order."""
    return hashlib.sha1(json.dumps([lines_hash(o), o["financial_status"], o["risk"], o["on_hold"], o["cancelled"],
                                    sorted(o.get("tags", [])), o["source"], o["country"], o["location_id"],
                                    o.get("fo_count"), o.get("fo_request"), o.get("lines_truncated")]).encode()).hexdigest()


def _import_passed_since(created_iso):
    """True once UFSI's hourly :00 import has run since the order was created (it may have the order now)."""
    created = datetime.fromisoformat(created_iso.replace("Z", "+00:00"))
    return created < clock.now().replace(minute=0, second=0, microsecond=0)


def _recheck(o, fresh):
    """An order UFSI kept (or shadow mode would claim) looked at again with Shopify's latest. Claiming is only
    considered until UFSI's next import: after that UFSI may already have it, and taking it could ship it twice."""
    if o["status"] == "ufsi" and fresh["location_id"] == settings.babylon_location_id and settings.claim_mode == "live":
        _send_to_ufsi_if_routed_here(o, fresh)  # declined but sitting at Babylon: hand it over, at any time
        o = db.save_order(o)
    if _import_passed_since(o["created_at"]):
        return o
    promote = o["status"] == "shadow" and settings.claim_mode == "live"
    same = o.get("seen") == _signature(fresh)
    if same and not promote:
        return o
    if promote and same:
        why = "Claims switched on. Checked again before UFSI's import."
    elif o.get("seen"):
        why = "Order changed in Shopify before UFSI's import. Checked again."
    else:
        why = "Checked again with the latest order from Shopify."
    db.log(o["id"], "changed", why)
    return _decide(o, fresh)


def sync_order(gid, fresh=None):
    """Bring a known order in line with Shopify: polling's stand-in for the edit and cancel webhooks."""
    with _lk(gid):
        o = db.get_order(gid)
        if not o or o["kind"] != "order":
            return o
        fresh = fresh or shopify.get_order(gid)
        if o["status"] in ("ufsi", "shadow"):
            _recheck(o, fresh)
        elif fresh["cancelled"] and o["status"] in ACTIVE + ("shipped",):
            handle_cancel(gid)
        elif o["status"] in ACTIVE and lines_hash(fresh) != o["lines_hash"]:
            handle_edit(gid, fresh)
        o = db.get_order(gid)
        o["shop_updated"] = fresh.get("updated_at")
        return db.save_order(o)


def recheck_order(gid):
    return sync_order(gid)


def poll_recent():
    """Takes in new orders and syncs changed ones. Asks Shopify only for orders updated since the last poll
    (all pages), and fetches an order only when its updatedAt moved. The mark advances only when every
    order in the window was handled, so a failure is retried on the next poll instead of lost."""
    started = datetime.now(timezone.utc)
    mark = db.kv_get("poll_since")
    since = (datetime.fromisoformat(mark) if mark else started - POLL_LOOKBACK) - POLL_OVERLAP
    new, failed = 0, False
    for gid, updated in shopify.list_updated_orders(since):
        try:
            o = db.get_order(gid)
            if o is None:
                ingest_order(gid, "poll")
                new += 1
            elif o.get("shop_updated") != updated:
                sync_order(gid)
        except (ShopifyError, Refuse) as e:
            failed = True
            db.log(gid, "sync_failed", getattr(e, "message", str(e)))
    if settings.claim_mode == "live":  # "would claim" orders from a shadow run, still before their import
        for o in db.list_orders(statuses=("shadow",), limit=500):
            if not _import_passed_since(o["created_at"]):
                sync_order(o["id"])
    if not failed:
        db.kv_set("poll_since", started.isoformat())
    return new


def _release_to_ufsi(o, why):
    """Hand a claimed order back. Shopify is moved first: if that fails nothing changes, stock stays held."""
    if o["kind"] == "order" and settings.claim_mode == "live":
        try:
            o["fo_id"] = shopify.move_fulfillment_order(o["fo_id"], settings.ufsi_location_id)
        except ShopifyError as e:
            raise Refuse(502, f"Could not hand the order back to UFSI in Shopify, nothing was changed: {e}")
    _return_package(o)
    odoo.release(o["id"])
    o["status"] = "released"
    db.log(o["id"], "released", why)


def handle_edit(gid, fresh=None):
    with _lk(gid):
        o = db.get_order(gid)
        if not o or o["kind"] != "order":
            return None
        if o["status"] in ("packed", "labeled"):
            o["attention"] = "Order was edited after packing. Check the items before it ships."
            db.log(gid, "edited", o["attention"])
            return db.save_order(o)
        if o["status"] not in ("queued", "picking"):
            return o
        fresh = fresh or shopify.get_order(gid)
        old_need = leaf_needs(odoo, o["lines"])
        odoo.release(gid)  # the edited order is judged on its own, not against its old hold
        d = evaluate(fresh, odoo, settings, check_margin=False)
        o["checks"] = d["checks"]
        need = leaf_needs(odoo, fresh["lines"]) if d["eligible"] else None
        try:
            if not d["eligible"]:
                _release_to_ufsi(o, "Edited order is no longer eligible, released to UFSI. " + _failed(d["checks"]))
                return db.save_order(o)
            if not odoo.reserve(gid, need):
                _release_to_ufsi(o, "Edited order could not be reserved, released to UFSI")
                return db.save_order(o)
        except Refuse as e:  # Shopify refused the hand-back: keep the old hold and flag it for a person
            odoo.reserve(gid, old_need)
            o["attention"] = f"Edited in Shopify and no longer Babylon's, but {e.message}. Hand it to UFSI by hand."
            db.log(gid, "edited", o["attention"])
            return db.save_order(o)
        o.update(lines=fresh["lines"], address=fresh["address"], lines_hash=lines_hash(fresh),
                 pick=_build_pick(need, o["pick"]), package=_suggest(need))
        db.log(gid, "edited", "Edited order re-checked: still eligible, pick list updated")
        return db.save_order(o)


def handle_cancel(gid):
    with _lk(gid):
        o = db.get_order(gid)
        if not o or o["kind"] != "order" or o["status"] not in ACTIVE + ("shipped",) or o.get("cancelled"):
            return o
        if o["status"] == "shipped":
            o["cancelled"] = True
            db.log(gid, "cancelled", "Cancelled after it shipped. Handle as a return or refund.")
        elif o["status"] == "labeled":
            _mark_cancelled_after_label(o)
        else:
            _return_package(o)
            odoo.release(gid)
            o["status"] = "cancelled"
            db.log(gid, "cancelled", "Cancelled in Shopify. Pulled off the pick list.")
        return db.save_order(o)


def _mark_cancelled_after_label(o):
    o["cancelled"] = True
    o["attention"] = "Cancelled after the label was bought. Do not ship. Void the label and put the items back."
    db.log(o["id"], "cancelled", o["attention"])


def void_label(oid):
    """Cancelled after the label: refund the label in EasyPost, free the stock, never ship."""
    with _lk(oid):
        o = _get(oid)
        if o["status"] != "labeled" or not o.get("cancelled"):
            raise Refuse(409, "Only a labeled order that was cancelled in Shopify can be voided")
        try:
            res = shipping.void(o["label"])
        except shipping.ShippingError as e:
            raise Refuse(502, f"{e}. Void it in the EasyPost dashboard, then try again.")
        odoo.release(oid)
        o["status"] = "cancelled"
        o["attention"] = None
        db.log(oid, "voided", f"{res}. Stock hold released, items go back on the shelf.")
        return view(db.save_order(o))


# ------------------------------------------------------------ samples
SAMPLE_FIELDS = ("name", "address1", "city", "state", "zip")


def _qty(v):
    try:
        q = int(v)
    except (TypeError, ValueError):
        raise Refuse(400, f"Quantity must be a whole number, not {v!r}")
    if q < 0 or q > 10_000:
        raise Refuse(400, f"Quantity {q} is out of range")
    return q


def create_sample(data):
    addr = {k: str((data.get("address") or {}).get(k) or "").strip() for k in
            ("name", "address1", "address2", "city", "state", "zip", "phone")}
    missing = [k for k in SAMPLE_FIELDS if not addr[k]]
    if missing:
        raise Refuse(400, "Missing address fields: " + ", ".join(missing))
    addr["country"] = "US"
    lines = []
    for i in data.get("items") or []:
        q = _qty(i.get("qty", 0))
        if q > 0:
            sku = str(i.get("sku") or "")
            lines.append({"sku": sku, "title": (odoo.product(sku) or {}).get("name", sku), "qty": q})
    if not lines:
        raise Refuse(400, "Add at least one item")
    need = leaf_needs(odoo, lines)
    bad = [s for s in need if not odoo.product(s)]
    if bad:
        raise Refuse(400, "Unknown SKU: " + ", ".join(bad))
    sid = f"SMP-{db.next_seq('sample'):04d}"
    while db.get_order(sid):  # samples made before the counter existed
        sid = f"SMP-{db.next_seq('sample'):04d}"
    if not odoo.reserve(sid, need):
        raise Refuse(409, "Not enough stock in Odoo for this sample")
    rec = {"id": sid, "name": sid, "kind": "sample", "status": "queued", "created_at": db.now_iso(),
           "address": addr, "shipping_method": "Ground", "lines": lines, "lines_hash": "", "fo_id": None,
           "checks": [], "pick": _build_pick(need), "package": _suggest(need), "weight_oz": None, "label": None,
           "note": str(data.get("note") or ""), "email": str(data.get("email") or "")}
    db.log(sid, "sample", "Sample entered. It never becomes a Shopify order, so UFSI can't see it.")
    return db.save_order(rec)


# ------------------------------------------------------------ pick / pack
def scan_lot(oid, code):
    with _lk(oid):
        o = _get(oid)
        if o["status"] not in ("queued", "picking"):
            raise Refuse(409, f"Order is {o['status']}. Nothing to scan.")
        lot = odoo.lot(code)
        if not lot:
            raise Refuse(404, f"Unknown lot barcode: {code}")
        item = next((p for p in o["pick"] if p["sku"] == lot["sku"]), None)
        if not item:
            raise Refuse(409, f"{lot['sku']} is not on this order")
        if lot["expired"]:
            raise Refuse(409, f"Lot {lot['lot']} expired on {lot['expiry']}. Set it aside and scan another.")
        if len(item["picked"]) >= item["need"]:
            raise Refuse(409, f"All {item['need']} unit(s) of {item['title']} are already scanned")
        used = sum(1 for x in item["picked"] if x["lot"] == lot["lot"])  # a lot belongs to one SKU
        if used >= lot["qty"]:
            raise Refuse(409, f"Lot {lot['lot']} has no more units in Odoo")
        item["picked"].append({"lot": lot["lot"], "expiry": lot["expiry"]})
        o["status"] = "picking"
        warn = None
        first = odoo.fefo_lots(lot["sku"])
        if first and first[0]["lot"] != lot["lot"] and first[0]["expiry"] < lot["expiry"]:
            warn = f"Older lot {first[0]['lot']} (expires {first[0]['expiry']}) should go first"
            db.log(oid, "fefo_warning", warn)
        db.log(oid, "scan", f"{lot['sku']} lot {lot['lot']}")
        if _pick_done(o):
            db.log(oid, "pick_done", "All units scanned, lots recorded")
        db.save_order(o)
        return view(o), warn


def short_at_pick(oid, sku):
    with _lk(oid):
        o = _get(oid)
        if o["kind"] != "order":
            raise Refuse(409, "A sample can't go back to UFSI. Fix the stock in Odoo and try again.")
        if o["status"] not in ("queued", "picking"):
            raise Refuse(409, f"Order is {o['status']}")
        _release_to_ufsi(o, f"Stock short at pick ({sku}). Released to UFSI so the order still ships complete.")
        return view(db.save_order(o))


def stand_down(oid):
    """Babylon gives the order up before any label. UFSI gets it in Shopify so it still ships, unless the
    live check found it cancelled, in which case nobody ships it."""
    with _lk(oid):
        o = _get(oid)
        if o["status"] not in ("queued", "picking", "packed"):
            raise Refuse(409, f"Order is {o['status']}")
        if o["label"]:
            raise Refuse(409, "The label is already bought")
        checks = (o.get("live_check") or {}).get("checks", [])
        why = _failed(checks) or "Packer stood down"
        if any(c["key"] == "cancel" and not c["ok"] for c in checks):
            _return_package(o)
            odoo.release(oid)
            o["status"] = "cancelled"
            db.log(oid, "stood_down", f"Stood down: cancelled in Shopify. {why}")
        elif o["kind"] == "sample":
            _return_package(o)
            odoo.release(oid)
            o["status"] = "cancelled"
            db.log(oid, "stood_down", f"Sample stood down. {why}")
        else:
            _release_to_ufsi(o, f"Stood down, handed to UFSI so it still ships. {why}")
        return view(db.save_order(o))


def set_package(oid, bin_code):
    bin_code = bin_code.strip().upper()
    if bin_code not in packaging.BINS:
        raise Refuse(404, f"{bin_code} is not a packaging bin")
    with _lk(oid):
        o = _get(oid)
        if o["status"] not in ("queued", "picking", "packed"):
            raise Refuse(409, f"Order is {o['status']}")
        if not _pick_done(o):
            raise Refuse(409, "Scan every unit before choosing the package")
        pkg = o["package"]
        if pkg.get("scanned") == bin_code:
            return view(o)  # the same bin scanned twice is not a second box
        if pkg.get("scanned"):
            packaging.give_back(pkg["scanned"])
        packaging.take(bin_code)
        pkg["scanned"] = bin_code
        pkg["override"] = bin_code != pkg["bin"]  # pkg["bin"] is the suggestion
        pkg["dims"] = packaging.parcel_dims(bin_code, _units(o))
        if pkg["override"]:
            db.log(oid, "package_override", f"Suggested {pkg['bin']}, packer used {bin_code}")
        else:
            db.log(oid, "package", f"{bin_code} scanned")
        if o.get("weight_oz"):
            o["status"] = "packed"
        return view(db.save_order(o))


def set_weight(oid, oz):
    try:
        oz = float(oz)
    except (TypeError, ValueError):
        raise Refuse(400, "Weight must be a number of ounces")
    if not 0 < oz <= 400:
        raise Refuse(400, "Weight must be between 0 and 400 oz (25 lb scale limit)")
    with _lk(oid):
        o = _get(oid)
        if o["status"] not in ("queued", "picking", "packed"):
            raise Refuse(409, f"Order is {o['status']}")
        if not (o.get("package") or {}).get("scanned"):
            raise Refuse(409, "Scan the package bin before weighing")
        if o["label"]:
            raise Refuse(409, "The label is already bought")
        o["weight_oz"] = round(oz, 1)
        o["status"] = "packed"
        db.log(oid, "weight", f"{o['weight_oz']} oz")
        return view(db.save_order(o))


# ------------------------------------------------------------ live check, label, close
def live_check(o):
    """Runs when the packer asks for a label. Fails closed: if we can't verify, no label."""
    fresh = shopify.get_order(o["id"])
    checks = [{"key": "cancel", "ok": not fresh["cancelled"], "text": "Order is not cancelled in Shopify"}]
    try:
        if getattr(deposco, "skipped", False):
            checks.append({"key": "deposco", "ok": True, "text": "Deposco NOT checked (DEPOSCO_MODE=off). "
                           "Confirm by hand that UFSI has not imported this order."})
        else:
            in_deposco = deposco.exists(o["name"])
            checks.append({"key": "deposco", "ok": not in_deposco, "text": "Not in Deposco (looked up by order number--*)"})
    except DeposcoUnavailable as e:
        checks.append({"key": "deposco", "ok": False, "text": f"Could not check Deposco: {e}"})
    assigned = settings.claim_mode != "live" or fresh["location_id"] == settings.babylon_location_id
    checks += [
        {"key": "assigned", "ok": assigned, "text": "Still assigned to Babylon"},
        {"key": "hold", "ok": not fresh["on_hold"], "text": "Not on hold"},
        {"key": "unchanged", "ok": lines_hash(fresh) == o["lines_hash"], "text": "Items and address unchanged since the pick"},
    ]
    if assigned and fresh.get("fo_id"):
        o["fo_id"] = fresh["fo_id"]  # the fulfillment that closes the order must use Shopify's current one
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def request_label(oid):
    with _lk(oid):
        o = _get(oid)
        if o["label"]:
            return view(o)
        if o["status"] != "packed" or not _pick_done(o):
            raise Refuse(409, "Scan every unit, scan the package and weigh the parcel first")
        if o["kind"] == "order":
            try:
                lc = live_check(o)
            except ShopifyError as e:
                raise Refuse(502, f"Could not re-read the order from Shopify: {e}")
        else:
            lc = {"ok": True, "checks": [{"key": "sample", "ok": True, "text": "Sample shipment: no Shopify or Deposco check needed"}]}
        o["live_check"] = lc
        if not lc["ok"]:
            db.log(oid, "label_refused", _failed(lc["checks"]))
            db.save_order(o)
            raise Refuse(409, "The live check refused the label. Nothing will ship.", lc["checks"])
        lots = [x["lot"] for p in o["pick"] for x in p["picked"]]
        dims, oz = o["package"]["dims"], o["weight_oz"]
        try:
            sh = o.get("shipment")
            if sh and (sh["dims"], sh["oz"]) != (dims, oz) and not shipping.has_postage(sh["id"]):
                sh = None  # never bought, so a fresh shipment with the new weight or package is safe
            if not sh:
                sh = {"id": shipping.create_shipment(o, dims, oz), "dims": dims, "oz": oz}
                o["shipment"] = sh
                db.save_order(o)  # saved before the buy, so a retry finds it instead of buying again
            ship = shipping.buy(sh["id"], o, oz, lots)
        except shipping.ShippingError as e:
            raise Refuse(502, str(e))
        o["label"] = {k: ship[k] for k in ("tracking", "carrier", "service", "rate", "url", "zpl", "zpl_url", "mock", "shipment_id")}
        o["status"] = "labeled"
        db.log(oid, "label", f"{ship['service']} {ship['tracking']} (${ship['rate']})")
        _print(o)
        return view(db.save_order(o))


def _print(o):
    name = re.sub(r"[^A-Za-z0-9_-]", "", o["name"]) or "label"
    try:
        if not o["label"].get("zpl"):
            o["label"]["zpl"] = shipping.fetch_zpl(o["label"].get("zpl_url"))
        o["label"]["print"] = zpl.send(o["label"]["zpl"], name)
        o["label"]["print_ok"] = True
        db.log(o["id"], "print", o["label"]["print"])
    except Exception as e:  # printer offline must not lose a bought label
        o["label"]["print"] = f"Print failed: {e}"
        o["label"]["print_ok"] = False
        db.log(o["id"], "print_failed", str(e))


def reprint(oid):
    with _lk(oid):
        o = _get(oid)
        if not o["label"]:
            raise Refuse(409, "No label to print yet")
        _print(o)
        return view(db.save_order(o))


def close_order(oid):
    with _lk(oid):
        o = _get(oid)
        if o["status"] not in ("labeled", "shipped") or not o["label"]:
            raise Refuse(409, "Buy the label first")
        if o.get("cancelled"):
            raise Refuse(409, o.get("attention") or "The order was cancelled in Shopify")
        if o["kind"] == "order" and not o.get("odoo_ref"):
            try:  # a cancel webhook can be missed: check before stock leaves Odoo
                fresh = shopify.get_order(oid)
            except ShopifyError as e:
                raise Refuse(502, f"Could not re-read the order from Shopify, nothing was changed: {e}")
            if fresh["cancelled"]:
                _mark_cancelled_after_label(o)
                db.save_order(o)
                raise Refuse(409, o["attention"])
        if not o.get("odoo_ref"):
            picks = [{"sku": p["sku"], "lot": x["lot"]} for p in o["pick"] for x in p["picked"]]
            try:
                o["odoo_ref"] = odoo.consume(oid, picks)
            except ValueError as e:
                raise Refuse(409, str(e))
            db.log(oid, "odoo", f"Delivery {o['odoo_ref']} validated, lots recorded")
            db.save_order(o)
        if o["kind"] == "order" and not o.get("fulfillment"):
            try:
                lb = o["label"]
                notify = settings.notify_customer()
                o["fulfillment"] = shopify.create_fulfillment(o["id"], o["fo_id"], lb["tracking"], lb["url"], lb["carrier"], notify)
                o["notified"] = notify
                o.pop("writeback_error", None)
                db.log(oid, "fulfilled", "Tracking posted to Shopify, " + ("customer notified" if notify
                       else "customer NOT notified (test or mock label, see NOTIFY_CUSTOMER)"))
            except ShopifyError as e:
                o["status"] = "shipped"
                o["writeback_error"] = str(e)
                db.log(oid, "writeback_failed", str(e))
                db.save_order(o)
                raise Refuse(502, f"Odoo is updated but Shopify rejected the tracking: {e}. Press close again to retry.")
        if o["kind"] == "sample":
            db.log(oid, "email", f"Tracking email to {o.get('email') or 'recipient'} (stub, not sent in the POC)")
        o["status"] = "shipped"
        return view(db.save_order(o))


# ------------------------------------------------------------ dashboard and stock sync
def summary():
    """Everything the dashboard shows, from one grouped count, one short indexed list and one stock pass."""
    counts = db.count_by_status()
    by_status = {}
    for (kind, status), n in counts.items():
        by_status[status] = by_status.get(status, 0) + n
    levels = odoo.available_all()
    orders = lambda statuses: sum(counts.get(("order", s), 0) for s in statuses)  # Shopify orders, not samples
    return {"counts": by_status,
            "routing": {"babylon": orders(ACTIVE + ("shipped", "cancelled")),
                        "ufsi": orders(("ufsi", "claim_failed", "released")), "shadow": orders(("shadow",))},
            "samples_shipped": counts.get(("sample", "shipped"), 0),
            "to_pack": sum(by_status.get(s, 0) for s in ACTIVE),
            "low_stock": sorted(s for s, a in levels.items() if a <= LOW_STOCK),
            "recent": [summary_row(o) for o in db.list_orders(limit=8)]}


def sync_inventory():
    if settings.shopify_mode == "live" and settings.odoo_mode == "mock" and not settings.allow_mock_sync:
        raise Refuse(409, "Stock sync is off: Odoo is the mock, and its dummy counts would overwrite the real store's stock. "
                          "On a test store, set ALLOW_MOCK_SYNC=1.")
    qty = odoo.available_all()  # one pass for the whole catalog
    res = shopify.set_inventory(qty)
    db.kv_set("last_sync", {"at": db.now_iso(), "count": len(qty), "errors": res["errors"]})
    return {"synced": res["updated"], "errors": res["errors"], "quantities": qty}
