"""Label purchase. mock = fake tracking + our own ZPL. live = EasyPost (use the TEST key first).
Buying is two steps so a retry can never buy a second label: create_shipment() makes an unbought
shipment (free) and the pipeline saves its id, then buy() asks EasyPost whether that shipment already
has postage and only buys if it doesn't. A lost response or a crash after the buy is safe to retry."""
import hashlib
import httpx
from .config import settings
from .db import kv_get, kv_set
from . import zpl

EP = "https://api.easypost.com/v2"


class ShippingError(Exception):
    pass


def _parcel(dims, weight_oz):
    l, w, h = sorted(dims, reverse=True)
    return {"length": l, "width": w, "height": h, "weight": round(weight_oz, 1)}


def _live():
    return settings.easypost_mode == "live"


def _req(method, path, **kw):
    if not settings.easypost_key:
        raise ShippingError("EASYPOST_API_KEY is not set")
    try:
        r = httpx.request(method, f"{EP}{path}", auth=(settings.easypost_key, ""), timeout=30, **kw)
    except httpx.HTTPError as e:
        raise ShippingError(f"EasyPost did not answer: {e}")
    if r.status_code >= 400:
        raise ShippingError(f"EasyPost: {r.text[:300]}")
    return r.json()


# ---- mock EasyPost state: which shipments have been bought
def _mock_bought():
    return kv_get("easypost_mock") or {}


def create_shipment(order, dims, weight_oz):
    if not _live():
        return "shp_mock_" + hashlib.sha1(f"{order['id']}|{dims}|{weight_oz}".encode()).hexdigest()[:12]
    a = order["address"]
    sh = _req("POST", "/shipments", json={"shipment": {
        "to_address": {"name": a["name"], "street1": a["address1"], "street2": a.get("address2") or "", "city": a["city"],
                       "state": a["state"], "zip": a["zip"], "country": a["country"], "phone": a.get("phone") or ""},
        "from_address": settings.ship_from, "parcel": _parcel(dims, weight_oz),
        "options": {"label_format": "ZPL", "label_size": "4x6"},
        "reference": order["name"]}})
    return sh["id"]


def has_postage(shipment_id):
    if not _live():
        return shipment_id in _mock_bought()
    sh = _req("GET", f"/shipments/{shipment_id}")
    return bool(sh.get("tracking_code") and sh.get("postage_label"))


def buy(shipment_id, order, weight_oz, lots):
    """Returns the label for this shipment, buying it only if EasyPost says it has no postage yet."""
    if not _live():
        bought = _mock_bought()
        if shipment_id not in bought:
            digest = int(hashlib.sha1(order["id"].encode()).hexdigest(), 16)
            tracking = "9400" + str(digest)[:18].ljust(18, "0")
            service = "USPS Ground Advantage"
            bought[shipment_id] = {"tracking": tracking, "carrier": "USPS", "service": service,
                                   "rate": round(4.2 + 0.18 * weight_oz, 2),
                                   "url": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking}",
                                   "zpl": zpl.build_label(order, tracking, service, weight_oz, lots), "zpl_url": None,
                                   "mock": True, "shipment_id": shipment_id}
            kv_set("easypost_mock", bought)
        return dict(bought[shipment_id])
    sh = _req("GET", f"/shipments/{shipment_id}")
    if not (sh.get("tracking_code") and sh.get("postage_label")):
        rates = [x for x in sh.get("rates", []) if x["carrier"] == "USPS"]
        pick = next((x for x in rates if x["service"] == "GroundAdvantage"), None) or (
            min(rates, key=lambda x: float(x["rate"])) if rates else None)
        if not pick:
            raise ShippingError("EasyPost returned no USPS rates for this address and parcel")
        sh = _req("POST", f"/shipments/{shipment_id}/buy", json={"rate": {"id": pick["id"]}})
    sel = sh.get("selected_rate") or {}
    label = {"tracking": sh["tracking_code"], "carrier": sel.get("carrier") or "USPS", "service": sel.get("service") or "",
             "rate": float(sel.get("rate") or 0), "url": (sh.get("tracker") or {}).get("public_url") or "",
             "zpl": None, "zpl_url": (sh.get("postage_label") or {}).get("label_zpl_url"), "mock": False,
             "shipment_id": shipment_id}
    try:
        label["zpl"] = fetch_zpl(label["zpl_url"])
    except ShippingError:
        pass  # the label is bought; printing fetches the ZPL again
    return label


def fetch_zpl(url):
    if not url:
        raise ShippingError("EasyPost has no ZPL for this label")
    try:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ShippingError(f"Could not download the label ZPL: {e}")
    return r.text


def void(label):
    """Ask EasyPost to refund an unused label."""
    if label.get("mock"):
        return "Mock label, nothing to refund"
    sh = _req("POST", f"/shipments/{label['shipment_id']}/refund")
    return f"EasyPost refund {sh.get('refund_status') or 'requested'}"
