"""Build plan P1 and P2 foundations: exceptions work list, pick waves, void and relabel, end-of-day scan forms
and pickups, results, cycle counts, tracking, the carrier interface, client on every record, and the
international data gates."""
import pytest
from fastapi.testclient import TestClient
from app import db, ops, packaging, pipeline, shipping
from app.config import settings
from app.main import app
from app.pipeline import Refuse
from app.services import odoo, shopify

LIP, SER, MSK, SMP = "BAB-LIP-01", "BAB-SER-30", "BAB-MSK-01", "BAB-SMP-01"
ADDR = {"name": "Client A", "address1": "5 Main St", "city": "Austin", "state": "TX", "zip": "78701"}


def make(items, **kw):
    return pipeline.ingest_order(shopify.create_order([{"sku": s, "qty": q, "title": s} for s, q in items], **kw))


def labeled(items, lots):
    o = make(items)
    for lot in lots:
        pipeline.scan_lot(o["id"], lot)
    o = pipeline.set_package(o["id"], db.get_order(o["id"])["package"]["bin"])
    pipeline.set_weight(o["id"], o["package"]["expected_oz"])
    return pipeline.request_label(o["id"])


# ------------------------------------------------------------ exceptions work list
def test_released_orders_land_on_the_exceptions_list_with_reason_owner_and_age():
    o = make([(LIP, 1)])
    pipeline.short_at_pick(o["id"], LIP)
    rows = ops.exceptions()
    assert [r["id"] for r in rows] == [o["id"]]
    assert rows[0]["reason"].startswith("Stock short at pick") and rows[0]["owner"] == "" and rows[0]["age_hours"] >= 0
    ops.assign_exception(o["id"], "maria@babylonllc.com")
    assert ops.exceptions()[0]["owner"] == "maria@babylonllc.com"
    with pytest.raises(Refuse, match="Say what was done"):
        ops.resolve_exception(o["id"], " ")
    ops.resolve_exception(o["id"], "UFSI confirmed they shipped it")
    assert ops.exceptions() == []
    assert db.events(o["id"])[-1]["kind"] == "exception_resolved"


def test_a_failed_tracking_writeback_is_an_exception(monkeypatch):
    from app.shopify import ShopifyError
    o = labeled([(LIP, 1)], ["L2609-L"])
    monkeypatch.setattr(shopify, "create_fulfillment", lambda *a, **k: (_ for _ in ()).throw(ShopifyError("boom")))
    with pytest.raises(Refuse):
        pipeline.close_order(o["id"])
    assert "Shopify rejected the tracking" in ops.exceptions()[0]["reason"]


def test_orders_ufsi_simply_kept_are_not_exceptions():
    make([(MSK, 3)])
    assert ops.exceptions() == []


# ------------------------------------------------------------ pick waves
def test_wave_cutoffs_are_10_and_14_pacific():
    def wave(utc):
        return ops.wave_for(utc).strftime("%d %H:%M")
    assert wave("2026-11-02T16:30:00+00:00") == "02 10:00"   # 08:30 PT -> 10:00 wave
    assert wave("2026-11-02T18:00:00+00:00") == "02 10:00"   # 10:00 PT exactly -> still the 10:00 wave
    assert wave("2026-11-02T19:00:00+00:00") == "02 14:00"   # 11:00 PT
    assert wave("2026-11-02T23:30:00+00:00") == "03 10:00"   # 15:30 PT -> tomorrow 10:00


def test_a_wave_has_one_combined_pick_list_by_shelf():
    a, b = make([(LIP, 2)]), make([(LIP, 1), (SER, 1)])
    pipeline.scan_lot(a["id"], "L2609-L")                      # one of a's two already picked
    w = ops.waves()
    assert len(w) == 1 and {o["id"] for o in w[0]["orders"]} == {a["id"], b["id"]}
    lines = {l["sku"]: l for l in w[0]["pick_list"]}
    assert lines[LIP]["qty"] == 2 and lines[SER]["qty"] == 1     # 1 left for a + 1 for b
    assert [l["bin"] for l in w[0]["pick_list"]] == sorted(l["bin"] for l in w[0]["pick_list"])


# ------------------------------------------------------------ end of day, pickups, tracking
def test_one_scan_form_per_carrier_for_the_days_labels():
    a, b = labeled([(LIP, 1)], ["L2609-L"]), labeled([(LIP, 1)], ["L2609-L"])
    assert ops.end_of_day_status()["pending"] == {"USPS": 2}
    form = ops.create_scan_form("USPS")
    assert {p["order"] for p in form["parcels"]} == {a["name"], b["name"]}
    assert ops.end_of_day_status()["pending"] == {}
    with pytest.raises(Refuse, match="No USPS labels"):
        ops.create_scan_form("USPS")
    pick = ops.book_pickup("USPS", "2026-11-03T15:00:00-08:00", "2026-11-03T17:00:00-08:00")
    assert pick["parcels"] == 2 and pick["confirmation"]


def test_a_voided_label_leaves_the_scan_form_list():
    o = labeled([(LIP, 1)], ["L2609-L"])
    pipeline.void_label(o["id"], "misprint")
    assert ops.end_of_day_status()["pending"] == {}


def test_tracking_status_comes_from_the_provider():
    o = labeled([(LIP, 1)], ["L2609-L"])
    assert ops.track(o["id"])["status"] == "pre_transit"
    assert db.get_order(o["id"])["tracking_status"]["status"] == "pre_transit"


def test_only_parcel_shipping_is_set_up():
    assert shipping.rate({"id": "x"}, [6, 4, 2], 8)[0]["carrier"] == "USPS"
    with pytest.raises(shipping.ShippingError, match="LTL shipping isn't set up"):
        shipping.create_shipment({"id": "x"}, [48, 40, 40], 900, mode="ltl")


# ------------------------------------------------------------ results
def test_results_compare_babylon_with_ufsi():
    o = labeled([(LIP, 1)], ["L2609-L"])
    pipeline.close_order(o["id"])
    r = make([(LIP, 1)])
    pipeline.short_at_pick(r["id"], LIP)
    make([(MSK, 3)])                                            # kept by UFSI: not enough stock
    res = ops.results(30)
    assert res["claimed"] == 2 and res["shipped"] == 1 and res["eligible"] == 2 and res["orders_seen"] == 3
    assert res["median_ship_hours"] is not None and res["ufsi_baseline_hours"] == 16.1
    assert res["avg_postage"] > 0 and res["avg_packaging_cost"] == packaging.catalog()["PL-1"]["cost"]
    assert dict(res["release_reasons"]) == {"Stock short at pick": 1}
    assert dict(res["kept_by_ufsi_reasons"])["stock"] == 1


# ------------------------------------------------------------ cycle counts
def test_a_cycle_count_logs_the_variance_against_odoo():
    rec = ops.cycle_count(LIP, 58, "shelf B-02")
    assert rec["system"] == 60 and rec["variance"] == -2
    assert ops.cycle_counts()[0]["variance"] == -2 and odoo.on_hand(LIP) == 60   # Odoo stays the record
    for bad in (-1, "lots"):
        with pytest.raises(Refuse):
            ops.cycle_count(LIP, bad)


# ------------------------------------------------------------ client on every record, international gates
def test_every_record_carries_its_client():
    o = labeled([(LIP, 1)], ["L2609-L"])
    assert o["client_id"] == "purity" and all(l["client_id"] == "purity" for l in o["shipment_lines"])
    assert odoo.product(LIP)["client_id"] == "purity"
    assert [x["id"] for x in db.list_orders(client_id="purity")] == [o["id"]]
    assert db.list_orders(client_id="another-brand") == []


def test_no_customs_data_no_international_label():
    s = pipeline.create_sample({"address": ADDR, "items": [{"sku": SMP, "qty": 1}]})
    pipeline.scan_lot(s["id"], "L2610-S")
    s = pipeline.set_package(s["id"], s["package"]["bin"])
    pipeline.set_weight(s["id"], s["package"]["expected_oz"])
    rec = db.get_order(s["id"])
    rec["address"]["country"] = "CA"                            # a sample going to Canada
    db.save_order(rec)
    with pytest.raises(Refuse, match="isn't registered for CA"):
        pipeline.request_label(s["id"])
    p = odoo.product(SMP)
    p["registrations"] = {"CA": "Cosmetic Notification 123"}
    p["hs_code"] = ""                                           # and now the HS code is missing
    with pytest.raises(Refuse, match="has no hs_code"):
        pipeline.request_label(s["id"])
    p["hs_code"] = "3304.99"
    assert pipeline.request_label(s["id"])["status"] == "labeled"


# ------------------------------------------------------------ HTTP and the box study
def test_the_new_pages_answer_over_http():
    c = TestClient(app)
    o = make([(LIP, 1)])
    pipeline.short_at_pick(o["id"], LIP)
    enc = o["id"].replace("/", "%2F")
    assert c.get("/api/exceptions").json()[0]["id"] == o["id"]
    assert c.post(f"/api/exceptions/{enc}/assign", json={"owner": "lead"}).status_code == 200
    assert c.post(f"/api/exceptions/{enc}/resolve", json={"note": "done"}).status_code == 200
    assert c.get("/api/waves").status_code == 200 and c.get("/api/end-of-day").json()["pending"] == {}
    assert c.get("/api/results?days=7").json()["days"] == 7
    assert c.post(f"/api/stock/{LIP}/count", json={"counted": 60}).json()["variance"] == 0
    cfg = c.get("/api/config").json()
    assert cfg["client_id"] == "purity" and cfg["wave_times"] == ["10:00", "14:00"] and cfg["parcel_provider"] == "mock"


def test_box_study_counts_fit_mailer_and_glass_share():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("study", Path(__file__).resolve().parents[1] / "scripts" / "packout_box_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    make([(LIP, 1)])
    make([(SER, 1)])
    only_mailers = [p for p in packaging.DEFAULT_CATALOG if p["type"] != "box"]
    r = mod.study(only_mailers, 30)
    assert r["orders"] == 2 and r["fit"] == 1 and r["glass_pct"] == 50.0      # glass needs a box
    assert mod.study(packaging.DEFAULT_CATALOG, 30)["fit_pct"] == 100.0
