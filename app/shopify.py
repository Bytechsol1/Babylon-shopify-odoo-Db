"""Shopify access. MockShopify runs the whole flow offline. LiveShopify talks to the Admin GraphQL API.
Both return the same normalized order dict:
  id, name, number, cancelled, financial_status, risk, on_hold, tags[], source, country,
  shipping_method, address{}, lines[{sku,title,qty}], location_id, fo_id, created_at, updated_at,
  fo_count (open fulfillment orders), fo_request (requestStatus of the first one),
  lines_truncated (True if the order has more line items than one read returns)
"""
import base64
import copy
import hashlib
import hmac
import random
import re
import threading
import time
from datetime import datetime, timezone
import httpx
from .config import settings
from .db import kv_get, kv_set


class ShopifyError(Exception):
    pass


def verify_webhook(raw_body: bytes, header: str) -> bool:
    if not settings.webhook_secret:
        return settings.shopify_mode == "mock"
    digest = base64.b64encode(hmac.new(settings.webhook_secret.encode(), raw_body, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(digest, header or "")


def order_gid(payload):
    return payload.get("admin_graphql_api_id") or f"gid://shopify/Order/{payload['id']}"


# =============================================================== MOCK
DEMO_ADDRESSES = [
    {"name": "Ava Martinez", "address1": "410 Mission St", "address2": "", "city": "San Francisco", "state": "CA", "zip": "94105", "country": "US", "phone": "4155550111"},
    {"name": "Noah Williams", "address1": "88 Elm Ave", "address2": "Apt 4", "city": "Austin", "state": "TX", "zip": "78701", "country": "US", "phone": "5125550142"},
    {"name": "Mia Johnson", "address1": "1200 Lake Shore Dr", "address2": "", "city": "Chicago", "state": "IL", "zip": "60610", "country": "US", "phone": "3125550173"},
]


def _now_us():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class MockShopify:
    """Offline store for the demo and tests. Every change stamps updated_at, like Shopify does."""

    def __init__(self):
        self._mx = threading.RLock()
        self.s = kv_get("shopify") or self._fresh()

    @staticmethod
    def _fresh():
        return {"seq": 48212, "orders": {}, "inventory": {}, "fulfillments": []}

    def _save(self):
        kv_set("shopify", self.s)

    def reset(self):
        self.s = self._fresh()
        self._save()

    # ---- helpers for the demo panel
    def create_order(self, lines, address=None, shipping_method="Standard Ground", source="web", tags=None, routed_to=None, catalog=None,
                     fo_count=1, fo_request="UNSUBMITTED"):
        self.s["seq"] += 1
        n = self.s["seq"]
        gid = f"gid://shopify/Order/{n}"
        titles = {l["sku"]: l.get("title") for l in lines}
        self.s["orders"][gid] = {
            "id": gid, "name": f"#{n}", "number": n, "cancelled": False, "financial_status": "PAID",
            "risk": "LOW", "on_hold": False, "tags": tags or [], "source": source,
            "shipping_method": shipping_method,
            "address": address or random.choice(DEMO_ADDRESSES),
            "lines": [{"sku": l["sku"], "title": titles[l["sku"]] or l["sku"], "qty": int(l["qty"])} for l in lines],
            "location_id": routed_to or settings.ufsi_location_id,
            "fo_id": f"gid://shopify/FulfillmentOrder/{n}",
            "created_at": _now_us(), "updated_at": _now_us(),
            "fo_count": fo_count, "fo_request": fo_request,
        }
        self._save()
        return gid

    def update(self, gid, **fields):
        """Change an order the way support would in the Shopify admin."""
        with self._mx:
            o = self.s["orders"][gid]
            o.update(fields)
            o["updated_at"] = _now_us()
            self._save()

    def cancel(self, gid):
        self.update(gid, cancelled=True)

    def edit(self, gid, lines):
        self.update(gid, lines=[{"sku": l["sku"], "title": l.get("title") or l["sku"], "qty": int(l["qty"])} for l in lines])

    # ---- client interface
    def get_order(self, gid):
        o = self.s["orders"].get(gid)
        if not o:
            raise ShopifyError(f"Order {gid} not found")
        o = copy.deepcopy(o)
        o["country"] = o["address"].get("country")
        return o

    def list_updated_orders(self, since):
        """[(gid, updated_at)] for orders created or changed at or after `since` (an aware datetime)."""
        out = []
        for g, o in self.s["orders"].items():
            stamp = o.get("updated_at") or o["created_at"]  # demo orders saved before updated_at existed
            if datetime.fromisoformat(stamp) >= since:
                out.append((g, stamp))
        return out

    def move_fulfillment_order(self, fo_id, location_id):
        with self._mx:
            gid = "gid://shopify/Order/" + fo_id.rsplit("/", 1)[-1]  # the mock numbers both alike
            o = self.s["orders"].get(gid)
            if not o or o["fo_id"] != fo_id:
                raise ShopifyError("Fulfillment order not found")
            o["location_id"] = location_id
            o["updated_at"] = _now_us()
            self._save()
            return fo_id

    def create_fulfillment(self, gid, fo_id, tracking, url, company, notify=True):
        o = self.s["orders"][gid]
        if o["cancelled"]:
            raise ShopifyError("Order is cancelled")
        f = {"order": gid, "tracking": tracking, "url": url, "company": company, "notify": notify,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with self._mx:
            self.s["fulfillments"].append(f)
            o["fulfilled"] = f
            o["updated_at"] = _now_us()
            self._save()
        return f

    def set_inventory(self, qty_by_sku):
        for sku, q in qty_by_sku.items():
            self.s["inventory"][sku] = q
        self._save()
        return {"updated": len(qty_by_sku), "errors": []}


# =============================================================== LIVE
Q_ORDER = """
query($id: ID!){ order(id:$id){
  id name cancelledAt displayFinancialStatus tags sourceName createdAt updatedAt
  shippingAddress{ name address1 address2 city provinceCode zip countryCodeV2 phone }
  shippingLines(first:5){ nodes{ title } }
  lineItems(first:100){ pageInfo{ hasNextPage } nodes{ sku name currentQuantity } }
  %s
  fulfillmentOrders(first:10){ pageInfo{ hasNextPage } nodes{ id status requestStatus assignedLocation{ location{ id } } } }
}}"""
RISK = "risk{ recommendation }"

M_MOVE = """mutation($id:ID!,$loc:ID!){ fulfillmentOrderMove(id:$id,newLocationId:$loc){
  movedFulfillmentOrder{ id } userErrors{ field message } } }"""
M_FULFILL = """mutation($f: FulfillmentInput!){ fulfillmentCreate(fulfillment:$f){
  fulfillment{ id status } userErrors{ field message } } }"""
M_INV = """mutation($i: InventorySetQuantitiesInput!){ inventorySetQuantities(input:$i){
  inventoryAdjustmentGroup{ id } userErrors{ field message } } }"""
Q_VARIANTS = """query($q:String!){ productVariants(first:250, query:$q){ nodes{ sku inventoryItem{ id } } } }"""
Q_UPDATED = """query($q:String!, $after:String){ orders(first:250, after:$after, query:$q, sortKey:UPDATED_AT){
  pageInfo{ hasNextPage endCursor } nodes{ id updatedAt } } }"""
SKU_BATCH = 50  # SKUs per variant lookup; keeps the search query short


class LiveShopify:
    def __init__(self):
        if not settings.shop or not (settings.shopify_token or (settings.shopify_client_id and settings.shopify_client_secret)):
            raise RuntimeError("SHOPIFY_MODE=live needs SHOPIFY_SHOP and either SHOPIFY_ADMIN_TOKEN "
                               "or SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET")
        self.url = f"https://{settings.shop}/admin/api/{settings.api_version}/graphql.json"
        self._items = {}
        self._tok, self._tok_exp = None, 0

    def _token(self):
        """A fixed admin token if one is set, else a client-credentials token, renewed before it expires."""
        if settings.shopify_token:
            return settings.shopify_token
        if self._tok and time.time() < self._tok_exp - 300:
            return self._tok
        try:
            r = httpx.post(f"https://{settings.shop}/admin/oauth/access_token", timeout=25, data={
                "grant_type": "client_credentials", "client_id": settings.shopify_client_id,
                "client_secret": settings.shopify_client_secret})
            if r.status_code >= 400:  # Shopify answers with an HTML page whose title names the reason
                why = re.search(r"<title>(.*?)</title>", r.text, re.S)
                raise ShopifyError(f"Shopify refused the access token ({r.status_code}): "
                                   + (why.group(1).strip() if why else r.text[:300]))
            j = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ShopifyError(f"Could not get a Shopify access token. Is the app installed on {settings.shop}? {e}")
        self._tok, self._tok_exp = j["access_token"], time.time() + int(j.get("expires_in") or 86399)
        return self._tok

    def _gql(self, query, variables=None):
        body = {"query": query, "variables": variables or {}}
        try:
            for attempt in (1, 2):
                r = httpx.post(self.url, json=body, timeout=25,
                               headers={"X-Shopify-Access-Token": self._token(), "Content-Type": "application/json"})
                if r.status_code == 401 and attempt == 1 and not settings.shopify_token:
                    self._tok = None  # expired or revoked: fetch a fresh one and try once more
                    continue
                break
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ShopifyError(f"Shopify did not answer: {e}")
        j = r.json()
        if j.get("errors"):
            raise ShopifyError(str(j["errors"]))
        return j["data"]

    @staticmethod
    def _user_errors(node):
        errs = (node or {}).get("userErrors") or []
        if errs:
            raise ShopifyError("; ".join(e["message"] for e in errs))

    def get_order(self, gid):
        try:
            d = self._gql(Q_ORDER % RISK, {"id": gid})
        except ShopifyError as e:
            if "risk" not in str(e).lower():
                raise
            d = self._gql(Q_ORDER % "", {"id": gid})  # risk field differs by API version
        o = d["order"]
        if not o:
            raise ShopifyError(f"Order {gid} not found")
        addr = o.get("shippingAddress") or {}
        fos = o["fulfillmentOrders"]["nodes"]
        open_fos = [f for f in fos if f["status"] not in ("CLOSED", "CANCELLED")]
        fo = (open_fos or fos or [{}])[0]
        loc = ((fo.get("assignedLocation") or {}).get("location") or {}).get("id")
        rec = ((o.get("risk") or {}).get("recommendation") or "NONE").upper()
        ships = o["shippingLines"]["nodes"]
        return {
            "id": o["id"], "name": o["name"], "number": o["name"].lstrip("#"),
            "cancelled": bool(o.get("cancelledAt")),
            "financial_status": o.get("displayFinancialStatus") or "",
            "risk": "HIGH" if rec in ("CANCEL", "INVESTIGATE") else "LOW",
            "on_hold": any(f["status"] == "ON_HOLD" for f in open_fos),
            "tags": o.get("tags") or [], "source": (o.get("sourceName") or "web").lower(),
            "country": addr.get("countryCodeV2"),
            "shipping_method": ships[0]["title"] if ships else "",
            "address": {"name": addr.get("name"), "address1": addr.get("address1"), "address2": addr.get("address2") or "",
                        "city": addr.get("city"), "state": addr.get("provinceCode"), "zip": addr.get("zip"),
                        "country": addr.get("countryCodeV2"), "phone": addr.get("phone") or ""},
            # currentQuantity leaves out units removed by an edit; 'quantity' would still count them
            "lines": [{"sku": l["sku"] or "", "title": l["name"], "qty": l["currentQuantity"]}
                      for l in o["lineItems"]["nodes"] if l["currentQuantity"] > 0],
            "location_id": loc, "fo_id": fo.get("id"), "created_at": o.get("createdAt"), "updated_at": o.get("updatedAt"),
            "fo_count": len(open_fos) + (1 if o["fulfillmentOrders"]["pageInfo"]["hasNextPage"] else 0),
            "fo_request": fo.get("requestStatus") or "UNSUBMITTED",
            "lines_truncated": o["lineItems"]["pageInfo"]["hasNextPage"],
        }

    def list_updated_orders(self, since):
        """[(gid, updatedAt)] for every order created or changed since `since`, page by page (250 a page)."""
        q = "updated_at:>=" + since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out, after = [], None
        while True:
            page = self._gql(Q_UPDATED, {"q": q, "after": after})["orders"]
            out += [(n["id"], n["updatedAt"]) for n in page["nodes"]]
            if not page["pageInfo"]["hasNextPage"]:
                return out
            after = page["pageInfo"]["endCursor"]

    def move_fulfillment_order(self, fo_id, location_id):
        """Returns the moved fulfillment order's id, which Shopify may change on a move."""
        d = self._gql(M_MOVE, {"id": fo_id, "loc": location_id})
        self._user_errors(d["fulfillmentOrderMove"])
        return ((d["fulfillmentOrderMove"] or {}).get("movedFulfillmentOrder") or {}).get("id") or fo_id

    def create_fulfillment(self, gid, fo_id, tracking, url, company, notify=True):
        f = {"notifyCustomer": notify,
             "trackingInfo": {"number": tracking, "url": url, "company": company},
             "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fo_id}]}
        d = self._gql(M_FULFILL, {"f": f})
        self._user_errors(d["fulfillmentCreate"])
        return d["fulfillmentCreate"]["fulfillment"]

    def _inventory_items(self, skus):
        """SKU -> inventory item id, looked up SKU_BATCH at a time and cached for the process."""
        todo = [s for s in skus if s not in self._items]
        for i in range(0, len(todo), SKU_BATCH):
            batch = todo[i:i + SKU_BATCH]
            q = " OR ".join('sku:"%s"' % s.replace('"', '\\"') for s in batch)
            found = {n["sku"]: n["inventoryItem"]["id"] for n in self._gql(Q_VARIANTS, {"q": q})["productVariants"]["nodes"]}
            for s in batch:
                self._items[s] = found.get(s)
        return {s: self._items.get(s) for s in skus}

    def set_inventory(self, qty_by_sku):
        quantities, errors = [], []
        items = self._inventory_items(list(qty_by_sku))
        for sku, q in qty_by_sku.items():
            item = items[sku]
            if not item:
                errors.append(f"{sku}: no Shopify variant with this SKU")
                continue
            quantities.append({"inventoryItemId": item, "locationId": settings.babylon_location_id, "quantity": int(q)})
        for i in range(0, len(quantities), 100):
            try:
                d = self._gql(M_INV, {"i": {"name": "available", "reason": "correction", "ignoreCompareQuantity": True,
                                            "quantities": quantities[i:i + 100]}})
                self._user_errors(d["inventorySetQuantities"])
            except ShopifyError as e:
                errors.append(str(e))
        return {"updated": len(quantities), "errors": errors}
