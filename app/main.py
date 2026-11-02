import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from . import audit, auth, clock, db, ops, packaging, pipeline
from .config import settings
from .pipeline import Refuse
from .services import odoo, shopify, deposco
from .shopify import MockShopify, verify_webhook, order_gid

STATIC = Path(__file__).parent / "static"
VIEWS = {"all": None, "queue": pipeline.ACTIVE, "done": pipeline.DONE, "decisions": pipeline.NOT_OURS}
_hooks = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook")  # bounded, however many arrive


STARTED = time.time()
_last_ok = {}  # background job -> when it last finished without an error


def _loop(fn, every, name):
    while True:
        time.sleep(every)
        try:
            fn()
            _last_ok[name] = time.time()
        except Exception as e:  # keep the loop alive; the log shows what failed
            db.log("system", f"{name}_error", str(e))


def _ping_self():
    """A request to our own public address counts as traffic, so a free host doesn't put the app to sleep."""
    httpx.get(f"{settings.keepalive_url}/healthz", timeout=20).raise_for_status()


@asynccontextmanager
async def lifespan(app):
    jobs = [(pipeline.poll_recent, settings.poll_seconds, "poll"), (pipeline.sync_inventory, settings.sync_seconds, "sync")]
    if settings.keepalive_url:
        jobs.append((_ping_self, settings.keepalive_seconds, "keepalive"))
    for fn, every, name in jobs:
        if every:
            threading.Thread(target=_loop, args=(fn, every, name), daemon=True).start()
    yield


app = FastAPI(title="Babylon lane POC", lifespan=lifespan)
if (STATIC / "assets").is_dir():  # the React build (frontend/, `npm run build`)
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.exception_handler(Refuse)
def _refuse(_, e: Refuse):
    return JSONResponse(status_code=e.status, content={"detail": {"message": e.message, "checks": e.checks}})


PUBLIC_API = {"/api/public-config"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    """With AUTH=supabase every /api call needs a signed-in, allowed user; who it was goes into the audit log.
    Webhooks are checked by their Shopify signature instead, and the page itself is public (it shows the login)."""
    path = request.url.path
    if settings.auth != "supabase" or not path.startswith("/api/") or path in PUBLIC_API:
        return await call_next(request)
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    try:
        email = await run_in_threadpool(auth.verify, token)
    except auth.AuthError as e:
        return JSONResponse(status_code=401, content={"detail": str(e)})
    mark = audit.actor.set(email)
    try:
        return await call_next(request)
    finally:
        audit.actor.reset(mark)


@app.api_route("/healthz", methods=["GET", "HEAD"])  # uptime monitors often send HEAD
def healthz():
    """For Render's health check and an uptime pinger: public, no login, no database call.
    `poll_age_s` says how long ago Shopify was last read successfully (null until the first poll)."""
    now = time.time()
    poll = _last_ok.get("poll")
    return {"ok": True, "uptime_s": int(now - STARTED), "poll_age_s": int(now - poll) if poll else None}


@app.get("/api/public-config")
def public_config():
    """What the browser needs before anyone signs in. The anon key is public by design."""
    on = settings.auth == "supabase"
    return {"auth": settings.auth, "supabase_url": settings.supabase_url if on else "",
            "supabase_anon_key": settings.supabase_anon_key if on else ""}


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    # always fetch the page fresh, so a new build shows on the next refresh (its assets have hashed names)
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def config():
    return dict(settings.public(), margin_min=settings.import_margin_min, minutes_to_import=round(clock.minutes_to_import(), 1),
                clock_override=clock.override_minute, last_sync=db.kv_get("last_sync"),
                daily_cap=settings.daily_cap, claimed_today=pipeline.claimed_today())


# ------------------------------------------------------------ Shopify webhooks
def _webhook(topic):
    async def handler(request: Request):
        raw = await request.body()
        if not verify_webhook(raw, request.headers.get("X-Shopify-Hmac-Sha256", "")):
            raise HTTPException(401, "Bad webhook signature")
        gid = order_gid(json.loads(raw))
        _hooks.submit(_run_hook, topic, gid)
        return {"ok": True}  # Shopify wants a fast 200. Work happens in the background.
    return handler


HOOKS = {"create": pipeline.ingest_order, "updated": pipeline.on_order_changed, "edited": pipeline.on_order_changed,
         "cancelled": pipeline.handle_cancel}


def _run_hook(topic, gid):
    try:
        HOOKS[topic](gid)
    except Exception as e:  # a failed webhook is picked up again by the next poll
        db.log(gid, "webhook_failed", f"{topic}: {e}")


for _t in HOOKS:
    app.post(f"/webhooks/shopify/orders-{_t}")(_webhook(_t))


# ------------------------------------------------------------ pack station API
@app.get("/api/orders")
def orders(view: str = "queue", q: str = "", limit: int = 100, offset: int = 0):
    """Slim rows for lists, filtered, searched and paged in SQL. The queue is oldest first."""
    if view not in VIEWS:
        raise HTTPException(400, f"view must be one of {', '.join(VIEWS)}")
    limit = max(1, min(limit, 500))
    rows = db.list_orders(statuses=VIEWS[view], q=q.strip() or None, limit=limit, offset=max(0, offset),
                          oldest_first=view == "queue")
    return [pipeline.summary_row(o) for o in rows]


@app.get("/api/summary")
def summary():
    return pipeline.summary()


@app.get("/api/orders/{oid:path}/events")
def events(oid: str):
    return db.events(oid)


class Code(BaseModel):
    code: str
    reason: str | None = None  # why the packer used a package other than the suggested one


class Weight(BaseModel):
    oz: float
    override_reason: str | None = None  # accepting a weight outside the expected range


class Short(BaseModel):
    sku: str


@app.post("/api/orders/{oid:path}/scan")
def scan(oid: str, b: Code):
    o, warn = pipeline.scan_lot(oid, b.code)
    return {"order": o, "warning": warn}


@app.post("/api/orders/{oid:path}/package")
def package(oid: str, b: Code):
    return pipeline.set_package(oid, b.code, b.reason)


@app.post("/api/orders/{oid:path}/weight")
def weight(oid: str, b: Weight):
    return pipeline.set_weight(oid, b.oz, b.override_reason)


@app.post("/api/orders/{oid:path}/short")
def short(oid: str, b: Short):
    return pipeline.short_at_pick(oid, b.sku)


@app.post("/api/orders/{oid:path}/standdown")
def standdown(oid: str):
    return pipeline.stand_down(oid)


@app.post("/api/orders/{oid:path}/label")
def label(oid: str):
    return pipeline.request_label(oid)


@app.post("/api/orders/{oid:path}/reprint")
def reprint(oid: str):
    return pipeline.reprint(oid)


class VoidIn(BaseModel):
    reason: str | None = None  # misprint, wrong weight, ... (not needed when the order was cancelled)


@app.post("/api/orders/{oid:path}/void")
def void(oid: str, b: VoidIn | None = None):
    return pipeline.void_label(oid, b.reason if b else None)


@app.get("/api/orders/{oid:path}/track")
def track(oid: str):
    return ops.track(oid)


@app.post("/api/orders/{oid:path}/close")
def close(oid: str):
    return pipeline.close_order(oid)


def _labeled(oid):
    o = db.get_order(oid)
    if not o or not (o.get("label") or {}).get("zpl"):
        raise HTTPException(404, "No label yet")
    return o, re.sub(r"[^A-Za-z0-9_-]", "", o["name"]) or "label"


def _render_label(zpl, accept):
    """ZPL to an image or PDF through labelary.com. Note: this sends the label (name, address) to that service."""
    try:
        r = httpx.post("https://api.labelary.com/v1/printers/8dpmm/labels/4x6/0/", content=zpl.encode(),
                       headers={"Accept": accept}, timeout=15)
        r.raise_for_status()
        return r.content
    except Exception as e:
        raise HTTPException(502, f"Showing the label needs internet access to labelary.com: {e}")


@app.get("/api/orders/{oid:path}/label.png")
def label_png(oid: str):
    o, _ = _labeled(oid)
    return Response(_render_label(o["label"]["zpl"], "image/png"), media_type="image/png")


@app.get("/api/orders/{oid:path}/label.pdf")
def label_pdf(oid: str):
    """For printing on an ordinary printer until the Zebra is set up."""
    o, name = _labeled(oid)
    return Response(_render_label(o["label"]["zpl"], "application/pdf"), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="label-{name}.pdf"'})


@app.get("/api/orders/{oid:path}/label.zpl")
def label_zpl(oid: str):
    o, name = _labeled(oid)
    return Response(o["label"]["zpl"], media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="label-{name}.zpl"'})


@app.get("/api/orders/{oid:path}")
def order(oid: str):
    o = db.get_order(oid)
    if not o:
        raise HTTPException(404, "Order not found")
    return dict(pipeline.view(o), events=db.events(oid))


class SampleIn(BaseModel):
    address: dict
    items: list
    note: str = ""
    email: str = ""


@app.post("/api/samples")
def sample(b: SampleIn):
    return pipeline.create_sample(b.model_dump())


# ------------------------------------------------------------ packaging (pack-out)
@app.get("/api/packaging")
def packaging_list():
    return {"packages": packaging.stock_view(), "settings": packaging.settings(),
            "override_reasons": packaging.OVERRIDE_REASONS}


@app.put("/api/packaging/{code}")
def packaging_save(code: str, body: dict):
    try:
        return packaging.save_package({**body, "code": code})
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.delete("/api/packaging/{code}")
def packaging_remove(code: str):
    packaging.remove_package(code.upper())
    return {"ok": True}


class Count(BaseModel):
    qty: int


@app.put("/api/packaging/{code}/count")
def packaging_count(code: str, b: Count):
    if code.upper() not in packaging.catalog():
        raise HTTPException(404, f"{code} is not in the catalog")
    packaging.set_count(code.upper(), b.qty)
    db.log("packaging", "count", f"{code.upper()} counted: {b.qty}")
    return {"ok": True}


@app.put("/api/packout/settings")
def packout_settings(changes: dict):
    try:
        return packaging.save_settings(changes)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))


@app.get("/api/packaging/overrides")
def packaging_overrides(limit: int = 100):
    """Package and weight overrides with their reasons, newest first: the weekly review list."""
    return db.events_of_kind(("package_override", "weight_override"), max(1, min(limit, 500)))


@app.get("/api/stock")
def stock():
    levels = odoo.stock_levels()  # one pass over the warehouse, not one per product
    cat = odoo.catalog()
    return {"products": [dict(levels[s], sku=s, name=p["name"], bin=p["bin"], oz=p.get("oz")) for s, p in cat.items()],
            "packaging": packaging.stock_view()}


# ------------------------------------------------------------ exceptions, waves, end of day, results, counts
@app.get("/api/exceptions")
def exceptions():
    return ops.exceptions()


class OwnerIn(BaseModel):
    owner: str


class NoteIn(BaseModel):
    note: str


@app.post("/api/exceptions/{oid:path}/assign")
def exception_assign(oid: str, b: OwnerIn):
    return ops.assign_exception(oid, b.owner)


@app.post("/api/exceptions/{oid:path}/resolve")
def exception_resolve(oid: str, b: NoteIn):
    return ops.resolve_exception(oid, b.note)


@app.get("/api/waves")
def waves():
    return ops.waves()


@app.get("/api/end-of-day")
def end_of_day():
    return ops.end_of_day_status()


class CarrierIn(BaseModel):
    carrier: str


@app.post("/api/end-of-day/scan-form")
def scan_form(b: CarrierIn):
    return ops.create_scan_form(b.carrier)


class PickupIn(BaseModel):
    carrier: str
    start: str  # ISO date-time
    end: str


@app.post("/api/end-of-day/pickup")
def pickup(b: PickupIn):
    return ops.book_pickup(b.carrier, b.start, b.end)


@app.get("/api/results")
def results(days: int = 30):
    return ops.results(max(1, min(days, 365)))


class CountIn(BaseModel):
    counted: int
    note: str = ""


@app.post("/api/stock/{sku}/count")
def stock_count(sku: str, b: CountIn):
    return ops.cycle_count(sku, b.counted, b.note)


@app.get("/api/stock/counts")
def stock_counts(limit: int = 100):
    return ops.cycle_counts(max(1, min(limit, 1000)))


@app.post("/api/sync/inventory")
def sync():
    return pipeline.sync_inventory()


@app.post("/api/poll")
def poll():
    return {"new": pipeline.poll_recent()}


# ------------------------------------------------------------ demo panel (mock Shopify only)
def _dev():
    if not settings.dev_tools or not isinstance(shopify, MockShopify):
        raise HTTPException(404, "Test controls are only available with SHOPIFY_MODE=mock")


class SimOrder(BaseModel):
    items: list
    country: str = "US"
    shipping_method: str = "Standard Ground"
    source: str = "web"
    route: str = "ufsi"  # which location the mock 'routing' assigns first: ufsi | babylon


@app.post("/api/dev/order")
def dev_order(b: SimOrder):
    _dev()
    addr = None
    if b.country != "US":
        addr = {"name": "Liam Chen", "address1": "1 Queen St", "address2": "", "city": "Toronto", "state": "ON",
                "zip": "M5H 2N2", "country": b.country, "phone": ""}
    lines = [{"sku": i["sku"], "qty": i["qty"], "title": (odoo.product(i["sku"]) or {}).get("name")} for i in b.items]
    gid = shopify.create_order(lines, address=addr, shipping_method=b.shipping_method, source=b.source,
                               routed_to=settings.babylon_location_id if b.route == "babylon" else None)
    return pipeline.view(pipeline.ingest_order(gid, "webhook"))


class OidIn(BaseModel):
    id: str


@app.post("/api/dev/cancel")
def dev_cancel(b: OidIn):
    _dev()
    shopify.cancel(b.id)
    return pipeline.handle_cancel(b.id)


class EditIn(BaseModel):
    id: str
    items: list


@app.post("/api/dev/edit")
def dev_edit(b: EditIn):
    _dev()
    lines = [{"sku": i["sku"], "qty": i["qty"], "title": (odoo.product(i["sku"]) or {}).get("name")} for i in b.items]
    shopify.edit(b.id, lines)
    return pipeline.handle_edit(b.id)


class DepIn(BaseModel):
    order_name: str


@app.post("/api/dev/deposco")
def dev_deposco(b: DepIn):
    _dev()
    deposco.add(b.order_name + "--1")
    return {"ok": True}


class ClockIn(BaseModel):
    minute: int | None = None


@app.post("/api/dev/clock")
def dev_clock(b: ClockIn):
    _dev()
    clock.override_minute = b.minute
    return {"override": clock.override_minute}


@app.get("/api/dev/shopify")
def dev_shopify():
    _dev()
    return {"inventory": shopify.s["inventory"], "fulfillments": shopify.s["fulfillments"],
            "orders": [{"id": o["id"], "name": o["name"], "location": o["location_id"], "cancelled": o["cancelled"]}
                       for o in shopify.s["orders"].values()][-10:]}


@app.post("/api/dev/reset")
def dev_reset():
    _dev()
    db.wipe()
    odoo.reset()
    shopify.reset()
    deposco.reset()
    packaging.reset()
    clock.override_minute = None
    return {"ok": True}
