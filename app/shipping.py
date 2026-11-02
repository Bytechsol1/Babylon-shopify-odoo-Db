"""Shipping: one interface, providers behind it (build plan, Oct 30 2026).

    rate(order, dims, oz)        what each service would cost
    create_shipment / buy        a label and tracking; buying is two steps so a retry never buys twice:
                                 the pipeline saves the shipment id, then buy() asks the provider whether
                                 that shipment already has postage and only buys if it doesn't
    void(label)                  refund an unused label
    end_of_day(carrier, labels)  the day's scan form (manifest) for the carrier's pickup driver
    track(label)                 current tracking status
    pickup(carrier, window, labels)  book a carrier pickup

Providers: "mock" (test labels and our own ZPL) and "easypost". ShipStation (with Unishippers) slots in as a
third once the quote is in. Every shipment has a mode; only parcel is set up. LTL and FTL need a freight
provider, ocean/air a forwarder: asking for them raises ShippingError instead of guessing."""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
import httpx
from .config import settings
from .db import kv_get, kv_update
from . import zpl

MODES = ("parcel", "ltl", "ftl", "ocean_air")
EP = "https://api.easypost.com/v2"


class ShippingError(Exception):
    pass


def _parcel(dims, weight_oz):
    l, w, h = sorted(dims, reverse=True)
    return {"length": l, "width": w, "height": h, "weight": round(weight_oz, 1)}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =============================================================== mock
class MockProvider:
    """Test labels: fake tracking, our own ZPL, nothing charged. State lives in the kv store like a real
    provider's account would, so retries and voids behave as they would for real."""
    name = "mock"

    def rate(self, order, dims, oz):
        return [{"carrier": "USPS", "service": "USPS Ground Advantage", "rate": round(4.2 + 0.18 * oz, 2)},
                {"carrier": "USPS", "service": "USPS Priority Mail", "rate": round(8.9 + 0.22 * oz, 2)}]

    def create_shipment(self, order, dims, oz):
        return "shp_mock_" + uuid.uuid4().hex[:12]

    def has_postage(self, shipment_id):
        return shipment_id in (kv_get("easypost_mock") or {})

    def buy(self, shipment_id, order, oz, lots):
        def add(bought):
            if shipment_id not in bought:
                digest = int(hashlib.sha1(f"{order['id']}|{shipment_id}".encode()).hexdigest(), 16)
                tracking = "9400" + str(digest)[:18].ljust(18, "0")
                service = "USPS Ground Advantage"
                bought[shipment_id] = {"tracking": tracking, "carrier": "USPS", "service": service,
                                       "rate": round(4.2 + 0.18 * oz, 2),
                                       "url": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking}",
                                       "zpl": zpl.build_label(order, tracking, service, oz, lots), "zpl_url": None,
                                       "mock": True, "shipment_id": shipment_id, "bought_at": _now()}
            return bought
        return dict(kv_update("easypost_mock", add, {})[shipment_id])

    def void(self, label):
        return "Test label voided, nothing to refund"

    def end_of_day(self, carrier, labels):
        return {"id": "sf_mock_" + uuid.uuid4().hex[:10], "url": None}

    def track(self, label):
        bought = (label.get("bought_at") or _now())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(bought)
        status = "pre_transit" if age < timedelta(hours=4) else "in_transit" if age < timedelta(days=2) else "delivered"
        return {"status": status, "detail": "Test label: status simulated from the label's age", "checked_at": _now()}

    def pickup(self, carrier, window, labels):
        return {"id": "pk_mock_" + uuid.uuid4().hex[:10], "confirmation": "TEST-" + uuid.uuid4().hex[:6].upper()}


# =============================================================== EasyPost
class EasyPostProvider:
    """EasyPost (start with a TEST key). USPS Ground Advantage first, else the cheapest USPS rate."""
    name = "easypost"

    def _req(self, method, path, **kw):
        if not settings.easypost_key:
            raise ShippingError("EASYPOST_API_KEY is not set")
        try:
            r = httpx.request(method, f"{EP}{path}", auth=(settings.easypost_key, ""), timeout=30, **kw)
        except httpx.HTTPError as e:
            raise ShippingError(f"EasyPost did not answer: {e}")
        if r.status_code >= 400:
            raise ShippingError(f"EasyPost: {r.text[:300]}")
        return r.json()

    def create_shipment(self, order, dims, oz):
        a = order["address"]
        sh = self._req("POST", "/shipments", json={"shipment": {
            "to_address": {"name": a["name"], "street1": a["address1"], "street2": a.get("address2") or "", "city": a["city"],
                           "state": a["state"], "zip": a["zip"], "country": a["country"], "phone": a.get("phone") or ""},
            "from_address": settings.ship_from, "parcel": _parcel(dims, oz),
            "options": {"label_format": "ZPL", "label_size": "4x6"},
            "reference": order["name"]}})
        return sh["id"]

    def rate(self, order, dims, oz):
        sh = self._req("GET", f"/shipments/{self.create_shipment(order, dims, oz)}")
        return [{"carrier": x["carrier"], "service": x["service"], "rate": float(x["rate"])} for x in sh.get("rates", [])]

    def has_postage(self, shipment_id):
        sh = self._req("GET", f"/shipments/{shipment_id}")
        return bool(sh.get("tracking_code") and sh.get("postage_label"))

    def buy(self, shipment_id, order, oz, lots):
        sh = self._req("GET", f"/shipments/{shipment_id}")
        if not (sh.get("tracking_code") and sh.get("postage_label")):
            rates = [x for x in sh.get("rates", []) if x["carrier"] == "USPS"]
            pick = next((x for x in rates if x["service"] == "GroundAdvantage"), None) or (
                min(rates, key=lambda x: float(x["rate"])) if rates else None)
            if not pick:
                raise ShippingError("EasyPost returned no USPS rates for this address and parcel")
            sh = self._req("POST", f"/shipments/{shipment_id}/buy", json={"rate": {"id": pick["id"]}})
        sel = sh.get("selected_rate") or {}
        label = {"tracking": sh["tracking_code"], "carrier": sel.get("carrier") or "USPS", "service": sel.get("service") or "",
                 "rate": float(sel.get("rate") or 0), "url": (sh.get("tracker") or {}).get("public_url") or "",
                 "zpl": None, "zpl_url": (sh.get("postage_label") or {}).get("label_zpl_url"), "mock": False,
                 "shipment_id": shipment_id, "bought_at": _now()}
        try:
            label["zpl"] = fetch_zpl(label["zpl_url"])
        except ShippingError:
            pass  # the label is bought; printing fetches the ZPL again
        return label

    def void(self, label):
        sh = self._req("POST", f"/shipments/{label['shipment_id']}/refund")
        return f"EasyPost refund {sh.get('refund_status') or 'requested'}"

    def end_of_day(self, carrier, labels):
        sf = self._req("POST", "/scan_forms", json={"shipments": [{"id": l["shipment_id"]} for l in labels]})
        return {"id": sf["id"], "url": sf.get("form_url")}

    def track(self, label):
        t = self._req("POST", "/trackers", json={"tracker": {"tracking_code": label["tracking"], "carrier": label["carrier"]}})
        last = (t.get("tracking_details") or [{}])[-1]
        return {"status": t.get("status") or "unknown", "detail": last.get("message") or "", "checked_at": _now()}

    def pickup(self, carrier, window, labels):
        p = self._req("POST", "/pickups", json={"pickup": {
            "address": settings.ship_from, "shipment": {"id": labels[0]["shipment_id"]},
            "min_datetime": window[0], "max_datetime": window[1], "is_account_address": False,
            "instructions": f"{len(labels)} parcel(s) at the Babylon pack station"}})
        rates = p.get("pickup_rates") or []
        if not rates:
            raise ShippingError("EasyPost offered no pickup for that window")
        bought = self._req("POST", f"/pickups/{p['id']}/buy", json={"carrier": carrier, "service": rates[0]["service"]})
        return {"id": p["id"], "confirmation": bought.get("confirmation") or ""}


PROVIDERS = {"mock": MockProvider, "easypost": EasyPostProvider}
_provider = None


def provider():
    """The parcel provider (PARCEL_PROVIDER, or the older EASYPOST_MODE)."""
    global _provider
    want = PROVIDERS["easypost" if settings.easypost_mode == "live" else settings.parcel_provider]
    if not isinstance(_provider, want):
        _provider = want()
    return _provider


def _parcel_only(mode):
    if mode != "parcel":
        raise ShippingError(f"{mode.upper()} shipping isn't set up yet: it needs a freight provider (build plan: "
                            "ShipStation or Unishippers for LTL, a broker for FTL, a forwarder for ocean/air)")


# ---- the interface the pipeline calls
def rate(order, dims, oz, mode="parcel"):
    _parcel_only(mode)
    return provider().rate(order, dims, oz)


def create_shipment(order, dims, oz, mode="parcel"):
    _parcel_only(mode)
    return provider().create_shipment(order, dims, oz)


def has_postage(shipment_id):
    return provider().has_postage(shipment_id)


def buy(shipment_id, order, oz, lots):
    return provider().buy(shipment_id, order, oz, lots)


def void(label):
    if label.get("mock"):
        return MockProvider().void(label)
    return provider().void(label)


def end_of_day(carrier, labels):
    return provider().end_of_day(carrier, labels)


def track(label):
    return (MockProvider() if label.get("mock") else provider()).track(label)


def pickup(carrier, window, labels):
    return provider().pickup(carrier, window, labels)


def fetch_zpl(url):
    if not url:
        raise ShippingError("The provider has no ZPL for this label")
    try:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ShippingError(f"Could not download the label ZPL: {e}")
    return r.text
