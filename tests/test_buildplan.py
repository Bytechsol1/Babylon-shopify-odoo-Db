"""Cody's build plan (Oct 30, 2026): pack-out, claim check 7, weigh tolerance, no lot no label, Deposco fail
closed, cancels by webhook, and the daily cap on show."""
import base64
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient
from app import db, packaging, pipeline
from app.config import settings
from app.main import app
from app.odoo.mock import SEED
from app.pipeline import Refuse
from app.services import odoo, shopify, deposco

P = SEED["products"]
SER, CRM, MST, LIP, MSK, SMP = "BAB-SER-30", "BAB-CRM-50", "BAB-MST-60", "BAB-LIP-01", "BAB-MSK-01", "BAB-SMP-01"
UFSI = settings.ufsi_location_id


def make(items, **kw):
    return pipeline.ingest_order(shopify.create_order([{"sku": s, "qty": q, "title": s} for s, q in items], **kw))


def picked(o, lots):
    for lot in lots:
        pipeline.scan_lot(o["id"], lot)
    return db.get_order(o["id"])


def items(*pairs):
    return [(P[s], q) for s, q in pairs]


# ------------------------------------------------------------ fit geometry
def test_box_fit_rotates_items_but_never_bends_them():
    assert packaging.box_fits([[5, 1, 1]], [1, 1, 5])                   # turned on its end
    assert not packaging.box_fits([[5.1, 1, 1]], [5, 5, 5])             # too long however it's turned
    assert packaging.box_fits([[2, 2, 2]] * 8, [4, 4, 4])               # exactly fills the box
    assert not packaging.box_fits([[3, 3, 3]] * 2, [6, 3, 2.9])         # volume would fit, shape doesn't
    assert not packaging.box_fits([[2, 2, 2]] * 9, [4, 4, 4])           # one too many


def test_mailer_shrinks_as_it_fills():
    tube = [0.8, 0.8, 4.4]
    assert packaging.mailer_fits([tube], [10, 6, 2.0], 0.85)
    assert not packaging.mailer_fits([[3, 3, 3]], [10, 6, 2.0], 0.85)   # thicker than the fill limit
    assert not packaging.mailer_fits([[0.5, 5.8, 9.8]], [10, 6, 2.0], 0.85)  # fits flat only in an empty mailer


# ------------------------------------------------------------ protection classes and choice
def test_the_strictest_unit_decides_the_class():
    assert packaging.order_class(items((LIP, 1))) == "safe"
    assert packaging.order_class(items((LIP, 1), (MST, 1))) == "care"
    assert packaging.order_class(items((LIP, 1), (MST, 1), (SER, 1))) == "box"


def test_glass_never_goes_in_a_mailer_and_pumps_never_in_poly():
    cat = packaging.catalog()
    assert not packaging.fits(cat["PM-2"], items((SER, 1)))
    assert not packaging.fits(cat["PL-1"], items((MST, 1)))
    assert packaging.fits(cat["PM-0"], items((MST, 1)))


def test_the_cheapest_package_in_stock_wins():
    assert packaging.suggest(items((LIP, 1)))["bin"] == "PL-1"
    counts = dict(packaging.stock(), **{"PL-1": 0})                     # poly mailers ran out
    assert packaging.suggest(items((LIP, 1)), counts)["bin"] == "PM-0"


def test_postage_counts_size_only_above_a_cubic_foot():
    assert packaging.postage_estimate(10, [10, 8, 4]) == packaging.postage_estimate(10, [6, 4, 4])
    assert packaging.postage_estimate(10, [14, 14, 14]) > packaging.postage_estimate(10, [10, 8, 4])


# ------------------------------------------------------------ claim check 7
def test_no_package_in_stock_fits_means_ufsi_keeps_it():
    for code, p in packaging.catalog().items():
        if p["type"] == "box":
            packaging.set_count(code, 0)                                # no boxes: glass can't ship from Babylon
    o = make([(SER, 1)])
    assert o["status"] == "ufsi" and odoo.reserved(SER) == 0
    check = next(c for c in o["checks"] if c["key"] == "package")
    assert not check["ok"] and "package in stock fits" in check["text"]
    assert make([(LIP, 1)])["status"] == "queued"                       # a mailer still works


def test_shadow_mode_also_needs_a_package():
    for code in packaging.catalog():
        packaging.set_count(code, 0)
    settings.claim_mode = "shadow"
    assert make([(LIP, 1)])["status"] == "ufsi"


# ------------------------------------------------------------ weigh tolerance and overrides
def test_a_weight_off_by_more_than_the_tolerance_stops_the_order():
    o = picked(make([(LIP, 1)]), ["L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    exp, tol = o["package"]["expected_oz"], o["package"]["tolerance_oz"]
    with pytest.raises(Refuse, match="missing or extra item"):
        pipeline.set_weight(o["id"], exp + tol + 0.5)
    assert pipeline.set_weight(o["id"], exp + tol - 0.1)["status"] == "packed"


def test_a_lead_can_accept_an_odd_weight_with_a_reason():
    o = picked(make([(LIP, 1)]), ["L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    out = pipeline.set_weight(o["id"], 9.0, "scale off by a gram, checked by hand")
    assert out["weight_check"]["ok"] is False and out["status"] == "packed"
    rows = TestClient(app).get("/api/packaging/overrides").json()
    assert rows[0]["kind"] == "weight_override" and "checked by hand" in rows[0]["detail"]


def test_switching_package_needs_a_reason_and_resets_the_weight():
    o = picked(make([(LIP, 1)]), ["L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    pipeline.set_weight(o["id"], o["package"]["expected_oz"])
    with pytest.raises(Refuse, match="Pick a reason"):
        pipeline.set_package(o["id"], "PM-2")
    out = pipeline.set_package(o["id"], "PM-2", "damaged")
    assert out["weight_oz"] is None and out["status"] == "picking"      # weigh again in the new package
    assert out["package"]["expected_oz"] == packaging.expected_oz("PM-2", items((LIP, 1)))


def test_an_order_suggested_from_an_old_catalog_gets_a_fresh_suggestion():
    o = picked(make([(LIP, 1)]), ["L2609-L"])
    rec = db.get_order(o["id"])
    rec["package"].update(bin="BX-S", expected_oz=None)                 # a code from the demo list that's gone
    db.save_order(rec)
    out = pipeline.set_package(o["id"], "PL-1")
    assert out["package"]["bin"] == "PL-1" and not out["package"]["override"]


# ------------------------------------------------------------ no lot, no label
def test_no_lot_no_label():
    o = picked(make([(LIP, 2)]), ["L2609-L", "L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    pipeline.set_weight(o["id"], o["package"]["expected_oz"])
    rec = db.get_order(o["id"])
    rec["pick"][0]["picked"][1]["lot"] = ""                             # a unit that lost its lot
    db.save_order(rec)
    with pytest.raises(Refuse, match="No lot, no label"):
        pipeline.request_label(o["id"])
    assert db.get_order(o["id"])["label"] is None


def test_the_label_records_lots_per_shipment_line():
    o = picked(make([(SER, 2), (LIP, 1)]), ["L2607-A", "L2610-B", "L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    pipeline.set_weight(o["id"], o["package"]["expected_oz"])
    lines = pipeline.request_label(o["id"])["shipment_lines"]
    assert sorted((l["sku"], l["lot"], l["qty"]) for l in lines) == [(LIP, "L2609-L", 1), (SER, "L2607-A", 1), (SER, "L2610-B", 1)]


# ------------------------------------------------------------ Deposco fails closed
def test_any_deposco_error_refuses_the_label(monkeypatch):
    o = picked(make([(LIP, 1)]), ["L2609-L"])
    o = pipeline.set_package(o["id"], o["package"]["bin"])
    pipeline.set_weight(o["id"], o["package"]["expected_oz"])
    monkeypatch.setattr(deposco, "exists", lambda name: (_ for _ in ()).throw(TimeoutError("read timed out")))
    with pytest.raises(Refuse) as e:
        pipeline.request_label(o["id"])
    check = next(c for c in e.value.checks if c["key"] == "deposco")
    assert not check["ok"] and "TimeoutError" in check["text"]


# ------------------------------------------------------------ cancels by webhook
def _signed(body, secret):
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_webhooks_are_checked_with_the_apps_client_secret(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "")
    monkeypatch.setattr(settings, "shopify_client_secret", "shpss_test")
    body = json.dumps({"id": 1, "admin_graphql_api_id": "gid://shopify/Order/1"}).encode()
    c = TestClient(app)
    assert c.post("/webhooks/shopify/orders-cancelled", content=body, headers={"X-Shopify-Hmac-Sha256": _signed(body, "shpss_test")}).status_code == 200
    assert c.post("/webhooks/shopify/orders-cancelled", content=body, headers={"X-Shopify-Hmac-Sha256": _signed(body, "wrong")}).status_code == 401


def test_a_cancel_webhook_pulls_the_order_off_the_pick_list():
    from app.main import _run_hook
    o = make([(LIP, 1)])
    shopify.cancel(o["id"])
    _run_hook("cancelled", o["id"])
    assert db.get_order(o["id"])["status"] == "cancelled" and odoo.reserved(LIP) == 0


def test_an_update_webhook_takes_in_new_orders_and_syncs_known_ones():
    from app.main import _run_hook
    gid = shopify.create_order([{"sku": LIP, "qty": 1, "title": LIP}])
    _run_hook("updated", gid)
    assert db.get_order(gid)["status"] == "queued"
    shopify.edit(gid, [{"sku": LIP, "qty": 2}])
    _run_hook("edited", gid)
    assert db.get_order(gid)["pick"][0]["need"] == 2


# ------------------------------------------------------------ daily cap on show, packaging data
def test_the_daily_cap_and_todays_count_are_in_the_config():
    settings.daily_cap = 5
    make([(LIP, 1)])
    cfg = TestClient(app).get("/api/config").json()
    assert cfg["daily_cap"] == 5 and cfg["claimed_today"] == 1


def test_ops_can_replace_the_packaging_list():
    c = TestClient(app)
    new = {"name": "Box 12×9×4", "type": "box", "inside": [12, 9, 4], "max_lb": 30, "weight_oz": 7, "cost": 0.8,
           "start": 40, "reorder": 10}
    assert c.put("/api/packaging/bx-1294", json=new).json()["code"] == "BX-1294"
    assert c.put("/api/packaging/BAD", json={"type": "crate", "inside": [1, 1, 1], "max_lb": 1}).status_code == 400
    assert c.put("/api/packaging/BX-1294/count", json={"qty": 7}).status_code == 200
    row = next(p for p in c.get("/api/packaging").json()["packages"] if p["code"] == "BX-1294")
    assert row["qty"] == 7 and row["low"]                                # 7 is under the reorder point of 10
    assert c.delete("/api/packaging/BX-1294").status_code == 200
    assert "BX-1294" not in packaging.catalog()


def test_settings_can_be_tuned_but_not_invented():
    c = TestClient(app)
    assert c.put("/api/packout/settings", json={"clearance_in": 0.25}).json()["clearance_in"] == 0.25
    assert packaging.settings()["clearance_in"] == 0.25
    assert c.put("/api/packout/settings", json={"made_up": 1}).status_code == 400
