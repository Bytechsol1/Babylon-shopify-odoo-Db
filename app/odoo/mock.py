"""Mock of the Babylon Odoo warehouse. Same method names the real adapter must provide
(see real.py for how each maps to Odoo Enterprise models). Dummy data only.

Lookups are indexed (lots by SKU and by barcode, running reservation totals per SKU), so a stock
question costs the size of that SKU's lots, not the whole warehouse. Reserve, release and consume
run under one lock: two orders can never both take the last units."""
import copy
import threading
from collections import defaultdict
from datetime import date
from ..db import kv_get, kv_set

SEED = {
    "products": {
        "BAB-SER-30": {"name": "Vitamin C Serum 30 ml (glass dropper)", "dims": [1.6, 1.6, 4.6], "oz": 3.4, "glass": True, "soft": False, "bin": "A-01"},
        "BAB-CRM-50": {"name": "Night Cream 50 g (glass jar)", "dims": [2.6, 2.6, 2.3], "oz": 6.2, "glass": True, "soft": False, "bin": "A-02"},
        "BAB-MST-60": {"name": "Rose Face Mist 60 ml (pump)", "dims": [1.7, 1.7, 5.6], "oz": 3.6, "glass": False, "soft": False, "bin": "B-01"},
        "BAB-LIP-01": {"name": "Lip Gloss (tube)", "dims": [0.8, 0.8, 4.4], "oz": 0.7, "glass": False, "soft": False, "bin": "B-02"},
        "BAB-MSK-01": {"name": "Clay Mask (tube)", "dims": [1.5, 1.1, 6.0], "oz": 4.2, "glass": False, "soft": False, "bin": "B-03"},
        "BAB-SMP-01": {"name": "Sample Sachet", "dims": [3.0, 4.0, 0.12], "oz": 0.12, "glass": False, "soft": True, "bin": "C-01"},
    },
    "bundles": {
        "BAB-KIT-01": {"name": "Glow Kit (serum + cream + gloss)", "components": {"BAB-SER-30": 1, "BAB-CRM-50": 1, "BAB-LIP-01": 1}},
    },
    "lots": [
        {"lot": "L2607-A", "sku": "BAB-SER-30", "qty": 18, "expiry": "2027-07-31"},
        {"lot": "L2610-B", "sku": "BAB-SER-30", "qty": 30, "expiry": "2027-10-31"},
        {"lot": "L2608-A", "sku": "BAB-CRM-50", "qty": 22, "expiry": "2027-08-31"},
        {"lot": "L2504-X", "sku": "BAB-MST-60", "qty": 5, "expiry": "2026-05-31"},
        {"lot": "L2609-A", "sku": "BAB-MST-60", "qty": 40, "expiry": "2027-09-30"},
        {"lot": "L2609-L", "sku": "BAB-LIP-01", "qty": 60, "expiry": "2028-03-31"},
        {"lot": "L2606-M", "sku": "BAB-MSK-01", "qty": 2, "expiry": "2027-06-30"},
        {"lot": "L2610-S", "sku": "BAB-SMP-01", "qty": 300, "expiry": "2027-12-31"},
    ],
    "reserved": {},
    "picking_seq": 0,
}


class MockOdoo:
    def __init__(self):
        self._mx = threading.RLock()
        self._load(kv_get("odoo") or copy.deepcopy(SEED))

    def _load(self, state):
        self.s = state
        self._by_sku = defaultdict(list)   # sku -> its lots, oldest expiry first
        self._by_code = {}                 # LOT CODE -> lot
        for l in self.s["lots"]:
            self._by_sku[l["sku"]].append(l)
            self._by_code[l["lot"].upper()] = l
        for ls in self._by_sku.values():
            ls.sort(key=lambda l: l["expiry"])
        self._reserved = defaultdict(int)  # sku -> units held across all orders
        for items in self.s["reserved"].values():
            for sku, q in items.items():
                self._reserved[sku] += q

    def _save(self):
        kv_set("odoo", self.s)

    def reset(self):
        with self._mx:
            self._load(copy.deepcopy(SEED))
            self._save()

    # ---- catalog -------------------------------------------------
    def catalog(self):
        return self.s["products"]

    def product(self, sku):
        return self.s["products"].get(sku)

    def expand(self, sku, qty=1):
        """Bundles split into components. Returns {leaf_sku: qty}; unknown SKU -> {sku: qty}."""
        b = self.s["bundles"].get(sku)
        if not b:
            return {sku: qty}
        return {c: n * qty for c, n in b["components"].items()}

    # ---- stock ---------------------------------------------------
    @staticmethod
    def _expired(lot, today=None):
        return lot["expiry"] < (today or date.today().isoformat())

    def on_hand(self, sku, today=None):
        today = today or date.today().isoformat()
        return sum(l["qty"] for l in self._by_sku.get(sku, ()) if not self._expired(l, today))

    def reserved(self, sku):
        return self._reserved.get(sku, 0)

    def available(self, sku, today=None):
        return max(0, self.on_hand(sku, today) - self.reserved(sku))

    def stock_levels(self):
        """Every product's on hand, held, available and FEFO lots in one pass over the lots."""
        today = date.today().isoformat()
        with self._mx:
            out = {}
            for sku in self.s["products"]:
                lots = [l for l in self._by_sku.get(sku, ()) if l["qty"] > 0 and not self._expired(l, today)]
                on_hand = sum(l["qty"] for l in lots)
                held = self._reserved.get(sku, 0)
                out[sku] = {"on_hand": on_hand, "reserved": held, "available": max(0, on_hand - held),
                            "lots": [l["lot"] for l in lots]}
            return out

    def available_all(self):
        return {sku: v["available"] for sku, v in self.stock_levels().items()}

    def reserve(self, order_id, items):
        with self._mx:
            today = date.today().isoformat()
            own = self.s["reserved"].get(order_id, {})  # the order's earlier hold doesn't count against it
            if any(self.available(sku, today) + own.get(sku, 0) < q for sku, q in items.items()):
                return False
            self._drop(order_id)  # re-reserving replaces the order's earlier hold
            self.s["reserved"][order_id] = dict(items)
            for sku, q in items.items():
                self._reserved[sku] += q
            self._save()
            return True

    def _drop(self, order_id):
        held = self.s["reserved"].pop(order_id, None)
        for sku, q in (held or {}).items():
            self._reserved[sku] -= q
        return held is not None

    def release(self, order_id):
        with self._mx:
            if self._drop(order_id):
                self._save()

    # ---- lots ----------------------------------------------------
    def lot(self, code):
        l = self._by_code.get(code.strip().upper())
        return dict(l, expired=self._expired(l)) if l else None

    def fefo_lots(self, sku):
        today = date.today().isoformat()
        return [l for l in self._by_sku.get(sku, ()) if l["qty"] > 0 and not self._expired(l, today)]

    def consume(self, order_id, picks):
        """Validate the delivery: take one unit from each picked lot. Returns the picking reference."""
        need = defaultdict(int)
        for p in picks:
            need[p["lot"].upper()] += 1
        with self._mx:
            for code, n in need.items():
                if (self._by_code.get(code) or {"qty": 0})["qty"] < n:
                    raise ValueError(f"Lot {code} has fewer than {n} units in Odoo")
            for code, n in need.items():
                self._by_code[code]["qty"] -= n
            self._drop(order_id)
            self.s["picking_seq"] += 1
            self._save()
            return f"BAB/OUT/{self.s['picking_seq']:05d}"
