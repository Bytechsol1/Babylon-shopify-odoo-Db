import base64
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient
from app import clock, db, pipeline, zpl
from app.config import settings
from app.main import app
from app.pipeline import Refuse
from app.services import odoo, shopify, deposco

BAB, UFSI = settings.babylon_location_id, settings.ufsi_location_id
SER, CRM, MST, LIP, MSK, SMP, KIT = ("BAB-SER-30", "BAB-CRM-50", "BAB-MST-60", "BAB-LIP-01", "BAB-MSK-01", "BAB-SMP-01", "BAB-KIT-01")


def make(items, **kw):
    gid = shopify.create_order([{"sku": s, "qty": q, "title": s} for s, q in items], **kw)
    return pipeline.ingest_order(gid)


def pack_ready(o, lots, weight=6.0, bin_code=None):
    for lot in lots:
        pipeline.scan_lot(o["id"], lot)
    o = db.get_order(o["id"])
    pipeline.set_package(o["id"], bin_code or o["package"]["bin"])
    return pipeline.set_weight(o["id"], weight)


# ---------------------------------------------------------------- claim
def test_claims_when_every_unit_is_in_babylon_stock():
    o = make([(SER, 1), (LIP, 1)])
    assert o["status"] == "queued" and o["claim_route"] == "move"
    assert shopify.s["orders"][o["id"]]["location_id"] == BAB
    assert odoo.reserved(SER) == 1


def test_order_routed_to_babylon_but_declined_is_moved_to_ufsi():
    canada = {"name": "A", "address1": "1 St", "city": "Toronto", "state": "ON", "zip": "M5H", "country": "CA"}
    o = make([(LIP, 1)], routed_to=BAB, address=canada)
    assert o["status"] == "ufsi" and shopify.s["orders"][o["id"]]["location_id"] == UFSI
    assert any(e["kind"] == "routed_to_ufsi" for e in db.events(o["id"]))
    settings.claim_mode = "shadow"  # shadow mode only notes it
    o = make([(LIP, 1)], routed_to=BAB, address=canada)
    assert shopify.s["orders"][o["id"]]["location_id"] == BAB


def test_routing_already_assigned_means_no_move():
    o = make([(LIP, 1)], routed_to=BAB)
    assert o["status"] == "queued" and o["claim_route"] == "routing"


def test_stays_with_ufsi_when_a_unit_is_short():
    o = make([(MSK, 3)])
    assert o["status"] == "ufsi"
    assert not next(c for c in o["checks"] if c["key"] == "stock")["ok"]
    assert shopify.s["orders"][o["id"]]["location_id"] == UFSI


def test_expired_lot_does_not_count_as_stock():
    assert odoo.available(MST) == 40  # 5 units in the expired lot are excluded


def test_margin_rule_hands_late_orders_to_ufsi():
    clock.override_minute = 58
    assert make([(LIP, 1)])["status"] == "ufsi"
    clock.override_minute = 56  # 4 min left, more than the 3-min margin
    assert make([(LIP, 1)])["status"] == "queued"


def test_international_and_pos_orders_stay_with_ufsi():
    assert make([(LIP, 1)], address={"name": "A", "address1": "1 St", "city": "Toronto", "state": "ON", "zip": "M5H", "country": "CA"})["status"] == "ufsi"
    assert make([(LIP, 1)], source="pos")["status"] == "ufsi"


def test_bundles_split_into_components():
    o = make([(KIT, 1)])
    assert o["status"] == "queued" and {p["sku"] for p in o["pick"]} == {SER, CRM, LIP}


def test_shadow_mode_claims_nothing():
    settings.claim_mode = "shadow"
    o = make([(LIP, 1)])
    assert o["status"] == "shadow" and shopify.s["orders"][o["id"]]["location_id"] == UFSI and odoo.reserved(LIP) == 0


def test_allowlist_and_daily_cap():
    settings.sku_allowlist = {LIP}
    assert make([(SER, 1)])["status"] == "ufsi"
    settings.sku_allowlist = set()
    settings.daily_cap = 1
    assert make([(LIP, 1)])["status"] == "queued"
    assert make([(LIP, 1)])["status"] == "ufsi"


def test_split_or_already_requested_orders_stay_with_ufsi():
    assert make([(LIP, 1)], fo_count=2)["status"] == "ufsi"
    o = make([(LIP, 1)], fo_request="ACCEPTED")
    assert o["status"] == "ufsi" and not next(c for c in o["checks"] if c["key"] == "unrequested")["ok"]


def test_live_shopify_counts_current_quantity_after_an_edit(monkeypatch):
    from app.shopify import LiveShopify
    monkeypatch.setattr(settings, "shop", "x.myshopify.com")
    monkeypatch.setattr(settings, "shopify_token", "t")
    order = {"id": "gid://shopify/Order/9", "name": "#9", "cancelledAt": None, "displayFinancialStatus": "PAID",
             "tags": [], "sourceName": "web", "createdAt": "2026-10-23T00:00:00Z", "updatedAt": "2026-10-23T00:05:00Z", "shippingAddress": None,
             "shippingLines": {"nodes": []}, "risk": None,
             "lineItems": {"pageInfo": {"hasNextPage": False}, "nodes": [{"sku": LIP, "name": "Gloss", "currentQuantity": 1},
                                     {"sku": MSK, "name": "Mask", "currentQuantity": 0}]},
             "fulfillmentOrders": {"pageInfo": {"hasNextPage": False}, "nodes": [{"id": "fo1", "status": "OPEN", "requestStatus": "UNSUBMITTED",
                                              "assignedLocation": {"location": {"id": UFSI}}}]}}
    live = LiveShopify()
    monkeypatch.setattr(live, "_gql", lambda *a, **k: {"order": order})
    o = live.get_order(order["id"])
    assert o["lines"] == [{"sku": LIP, "title": "Gloss", "qty": 1}] and o["fo_count"] == 1


def test_live_shopify_gets_and_renews_a_client_credentials_token(monkeypatch):
    import httpx
    from app import shopify as sh
    monkeypatch.setattr(settings, "shop", "x.myshopify.com")
    monkeypatch.setattr(settings, "shopify_token", "")
    monkeypatch.setattr(settings, "shopify_client_id", "cid")
    monkeypatch.setattr(settings, "shopify_client_secret", "secret")
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        req = httpx.Request("POST", url)
        if url.endswith("/oauth/access_token"):
            assert kw["data"]["grant_type"] == "client_credentials"
            return httpx.Response(200, json={"access_token": f"tok{len(calls)}", "expires_in": 86399}, request=req)
        if kw["headers"]["X-Shopify-Access-Token"] == "tok1":
            return httpx.Response(401, json={}, request=req)  # the first token was revoked
        return httpx.Response(200, json={"data": {"ok": True}}, request=req)
    monkeypatch.setattr(sh.httpx, "post", fake_post)
    live = sh.LiveShopify()
    assert live._gql("{ shop { name } }") == {"ok": True}
    assert [u.rsplit("/", 1)[-1] for u in calls] == ["access_token", "graphql.json", "access_token", "graphql.json"]


def test_stock_sync_refuses_to_push_mock_odoo_into_a_real_store(monkeypatch):
    monkeypatch.setattr(settings, "shopify_mode", "live")
    with pytest.raises(Refuse, match="overwrite"):
        pipeline.sync_inventory()
    monkeypatch.setattr(settings, "allow_mock_sync", True)  # a test store stocked with the mock SKUs
    assert pipeline.sync_inventory()["errors"] == []


def test_order_fixed_before_ufsi_import_is_checked_again():
    o = make([(LIP, 1)], address={"name": "A", "address1": "", "city": "", "state": "", "zip": "", "country": None})
    assert o["status"] == "ufsi"
    assert pipeline.poll_recent() == 0 and db.get_order(o["id"])["status"] == "ufsi"  # nothing changed yet
    shopify.update(o["id"], address=dict(ADDR, country="US"))  # support adds the address
    pipeline.poll_recent()
    o = db.get_order(o["id"])
    assert o["status"] == "queued" and o["address"]["city"] == "Austin"
    assert any(e["kind"] == "changed" for e in db.events(o["id"]))


def test_shadow_order_is_claimed_when_claims_are_switched_on():
    settings.claim_mode = "shadow"
    o = make([(LIP, 1)])
    assert o["status"] == "shadow"
    settings.claim_mode = "live"
    pipeline.poll_recent()
    o = db.get_order(o["id"])
    assert o["status"] == "queued" and shopify.s["orders"][o["id"]]["location_id"] == BAB


def test_order_stays_with_ufsi_once_its_import_has_run():
    o = make([(LIP, 1)], address={"name": "A", "address1": "", "city": "", "state": "", "zip": "", "country": None})
    rec = db.get_order(o["id"])
    rec["created_at"] = "2020-01-01T00:00:00+00:00"  # an hourly import has run since: UFSI may have it
    db.save_order(rec)
    shopify.update(o["id"], address=dict(ADDR, country="US"))
    assert pipeline.recheck_order(o["id"])["status"] == "ufsi"


def test_ingest_is_idempotent():
    gid = shopify.create_order([{"sku": LIP, "qty": 1, "title": "x"}])
    pipeline.ingest_order(gid)
    pipeline.ingest_order(gid, "poll")
    assert odoo.reserved(LIP) == 1 and len(db.list_orders()) == 1


# ---------------------------------------------------------------- pick and pack
def test_scan_rules():
    o = make([(SER, 1)])
    with pytest.raises(Refuse, match="not on this order"):
        pipeline.scan_lot(o["id"], "L2609-L")
    with pytest.raises(Refuse, match="Unknown lot"):
        pipeline.scan_lot(o["id"], "NOPE")
    _, warn = pipeline.scan_lot(o["id"], "L2610-B")  # later expiry than L2607-A
    assert "L2607-A" in warn
    with pytest.raises(Refuse, match="already scanned"):
        pipeline.scan_lot(o["id"], "L2607-A")


def test_expired_lot_is_rejected():
    o = make([(MST, 1)])
    with pytest.raises(Refuse, match="expired"):
        pipeline.scan_lot(o["id"], "L2504-X")


def test_package_rules():
    assert make([(SER, 1), (LIP, 1)])["package"]["bin"] == "BX-S"       # glass -> box
    assert make([(LIP, 1)])["package"]["bin"] == "PM-0"                  # small plastic -> #0 mailer
    assert make([(MST, 1)])["package"]["bin"] == "PM-2"                  # a full-size pump -> #2 mailer
    assert make([(SER, 1), (CRM, 1)])["package"]["bin"] == "BX-S"
    assert make([(SER, 1), (CRM, 1), (LIP, 1)])["package"]["bubble"] == 2


def test_cannot_package_or_weigh_before_the_pick_is_done():
    o = make([(LIP, 1)])
    with pytest.raises(Refuse, match="Scan every unit"):
        pipeline.set_package(o["id"], "PM-0")


def test_override_is_logged_and_packaging_stock_counts_down():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    out = pipeline.set_package(o["id"], "BX-M")
    assert out["package"]["override"]
    assert any(e["kind"] == "package_override" for e in db.events(o["id"]))
    from app import packaging
    assert packaging.stock()["BX-M"] == 149


# ---------------------------------------------------------------- label and close
def test_full_flow_ships_and_writes_back():
    o = make([(SER, 1), (LIP, 1)])
    o = pack_ready(o, ["L2607-A", "L2609-L"])
    assert o["status"] == "packed"
    o = pipeline.request_label(o["id"])
    assert o["status"] == "labeled" and o["label"]["tracking"].startswith("9400") and o["label"]["print_ok"]
    assert all(c["ok"] for c in o["live_check"]["checks"])
    o = pipeline.close_order(o["id"])
    assert o["status"] == "shipped" and o["odoo_ref"].startswith("BAB/OUT/")
    f = shopify.s["fulfillments"][0]
    assert f["tracking"] == o["label"]["tracking"] and f["notify"] is False  # mock tracking: never email the customer
    assert odoo.lot("L2607-A")["qty"] == 17 and odoo.reserved(SER) == 0


def test_label_is_never_bought_twice():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    a = pipeline.request_label(o["id"])
    b = pipeline.request_label(o["id"])
    assert a["label"]["tracking"] == b["label"]["tracking"]
    assert [e["kind"] for e in db.events(o["id"])].count("label") == 1


def test_lost_buy_response_is_retried_without_a_second_purchase(monkeypatch):
    from app import shipping
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    real = shipping.buy

    def bought_then_timeout(*a, **k):
        real(*a, **k)  # EasyPost charged us...
        raise shipping.ShippingError("timeout")  # ...but the answer never arrived
    monkeypatch.setattr(shipping, "buy", bought_then_timeout)
    with pytest.raises(Refuse):
        pipeline.request_label(o["id"])
    monkeypatch.setattr(shipping, "buy", real)
    o = pipeline.request_label(o["id"])
    assert o["status"] == "labeled" and len(db.kv_get("easypost_mock")) == 1


def test_weight_change_before_purchase_makes_a_new_shipment():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    deposco.add(o["name"] + "--1")
    with pytest.raises(Refuse):
        pipeline.request_label(o["id"])  # refused before any shipment exists
    deposco.reset()
    pipeline.set_weight(o["id"], 2.0)
    assert pipeline.request_label(o["id"])["shipment"]["oz"] == 2.0


def test_label_zpl_download_failure_keeps_the_label(monkeypatch):
    from app import shipping
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    real = shipping.buy
    monkeypatch.setattr(shipping, "buy", lambda *a, **k: dict(real(*a, **k), zpl=None, zpl_url="https://x/l.zpl"))
    monkeypatch.setattr(shipping, "fetch_zpl", lambda url: (_ for _ in ()).throw(shipping.ShippingError("down")))
    o = pipeline.request_label(o["id"])
    assert o["status"] == "labeled" and not o["label"]["print_ok"]
    monkeypatch.setattr(shipping, "fetch_zpl", lambda url: "^XA^XZ")
    assert pipeline.reprint(o["id"])["label"]["print_ok"]


def test_notify_customer_only_for_real_postage(monkeypatch):
    assert settings.notify_customer() is False  # mock EasyPost
    monkeypatch.setattr(settings, "easypost_mode", "live")
    monkeypatch.setattr(settings, "easypost_key", "EZTK_test")
    assert settings.notify_customer() is False
    monkeypatch.setattr(settings, "easypost_key", "EZAK_prod")
    assert settings.notify_customer() is True
    monkeypatch.setattr(settings, "notify_mode", "off")
    assert settings.notify_customer() is False


def test_label_needs_weight_and_package():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    with pytest.raises(Refuse):
        pipeline.request_label(o["id"])


def test_live_check_refuses_when_already_in_deposco():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    deposco.add(o["name"] + "--1")
    with pytest.raises(Refuse) as e:
        pipeline.request_label(o["id"])
    assert any(c["key"] == "deposco" and not c["ok"] for c in e.value.checks)
    assert db.get_order(o["id"])["label"] is None


def test_live_check_backstops_a_missed_cancel_webhook():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    shopify.cancel(o["id"])  # webhook never arrived
    with pytest.raises(Refuse) as e:
        pipeline.request_label(o["id"])
    assert any(c["key"] == "cancel" and not c["ok"] for c in e.value.checks)


def test_live_check_refuses_if_the_order_moved_back_to_ufsi():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    shopify.move_fulfillment_order(o["fo_id"], UFSI)
    with pytest.raises(Refuse) as e:
        pipeline.request_label(o["id"])
    assert any(c["key"] == "assigned" and not c["ok"] for c in e.value.checks)


def test_live_check_refuses_if_the_address_changed_after_the_pick():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    shopify.s["orders"][o["id"]]["address"]["address1"] = "999 Different Rd"
    with pytest.raises(Refuse) as e:
        pipeline.request_label(o["id"])
    assert any(c["key"] == "unchanged" and not c["ok"] for c in e.value.checks)


def test_deposco_down_fails_closed(monkeypatch):
    from app import pipeline as p
    from app.deposco import LiveDeposco
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    monkeypatch.setattr(p, "deposco", LiveDeposco())
    with pytest.raises(Refuse, match="refused"):
        pipeline.request_label(o["id"])


def test_printer_failure_keeps_the_label(monkeypatch):
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    monkeypatch.setattr(zpl, "send", lambda *a, **k: (_ for _ in ()).throw(OSError("printer offline")))
    o = pipeline.request_label(o["id"])
    assert o["status"] == "labeled" and not o["label"]["print_ok"]
    monkeypatch.undo()
    assert pipeline.reprint(o["id"])["label"]["print_ok"]


def test_shopify_writeback_failure_is_retryable(monkeypatch):
    from app.shopify import ShopifyError
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    pipeline.request_label(o["id"])
    real = shopify.create_fulfillment
    monkeypatch.setattr(shopify, "create_fulfillment", lambda *a, **k: (_ for _ in ()).throw(ShopifyError("boom")))
    with pytest.raises(Refuse, match="retry"):
        pipeline.close_order(o["id"])
    assert odoo.lot("L2609-L")["qty"] == 59  # Odoo already moved, and moves only once
    monkeypatch.setattr(shopify, "create_fulfillment", real)
    assert pipeline.close_order(o["id"])["status"] == "shipped"
    assert odoo.lot("L2609-L")["qty"] == 59 and len(shopify.s["fulfillments"]) == 1


# ---------------------------------------------------------------- change events
def test_cancel_webhook_pulls_it_off_the_list_and_frees_stock():
    o = make([(LIP, 2)])
    shopify.cancel(o["id"])
    out = pipeline.handle_cancel(o["id"])
    assert out["status"] == "cancelled" and odoo.reserved(LIP) == 0


def test_cancel_after_label_asks_for_a_void():
    o = pipeline.request_label(pack_ready(make([(LIP, 1)]), ["L2609-L"])["id"])
    shopify.cancel(o["id"])
    assert "Void the label" in pipeline.handle_cancel(o["id"])["attention"]
    with pytest.raises(Refuse, match="Do not ship"):
        pipeline.close_order(o["id"])
    out = pipeline.void_label(o["id"])
    assert out["status"] == "cancelled" and odoo.reserved(LIP) == 0 and odoo.lot("L2609-L")["qty"] == 60


def test_close_catches_a_missed_cancel_before_odoo_moves():
    o = pipeline.request_label(pack_ready(make([(LIP, 1)]), ["L2609-L"])["id"])
    shopify.cancel(o["id"])  # webhook never arrived
    with pytest.raises(Refuse, match="Do not ship"):
        pipeline.close_order(o["id"])
    o = db.get_order(o["id"])
    assert not o.get("odoo_ref") and odoo.lot("L2609-L")["qty"] == 60 and shopify.s["fulfillments"] == []
    assert pipeline.void_label(o["id"])["status"] == "cancelled"


def test_void_needs_a_cancelled_order():
    o = pipeline.request_label(pack_ready(make([(LIP, 1)]), ["L2609-L"])["id"])
    with pytest.raises(Refuse, match="cancelled"):
        pipeline.void_label(o["id"])


def test_edit_to_an_item_babylon_lacks_releases_to_ufsi():
    o = make([(LIP, 1)])
    shopify.edit(o["id"], [{"sku": LIP, "qty": 1}, {"sku": MSK, "qty": 5}])
    out = pipeline.handle_edit(o["id"])
    assert out["status"] == "released" and shopify.s["orders"][o["id"]]["location_id"] == UFSI
    assert odoo.reserved(LIP) == 0


def test_edit_that_stays_eligible_updates_the_pick_list():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    shopify.edit(o["id"], [{"sku": LIP, "qty": 2}])
    out = pipeline.handle_edit(o["id"])
    assert out["status"] == "picking" and out["pick"][0]["need"] == 2 and len(out["pick"][0]["picked"]) == 1


def test_short_at_pick_goes_back_to_ufsi():
    o = make([(LIP, 1)])
    out = pipeline.short_at_pick(o["id"], LIP)
    assert out["status"] == "released" and shopify.s["orders"][o["id"]]["location_id"] == UFSI


# ---------------------------------------------------------------- samples and sync
ADDR = {"name": "Client A", "address1": "5 Main St", "city": "Austin", "state": "TX", "zip": "78701"}


def test_sample_ships_without_touching_shopify():
    s = pipeline.create_sample({"address": ADDR, "items": [{"sku": SMP, "qty": 3}]})
    assert s["id"] == "SMP-0001" and s["package"]["bin"] == "PM-0"
    s = pack_ready(s, ["L2610-S"] * 3, weight=1.0)
    s = pipeline.close_order(pipeline.request_label(s["id"])["id"])
    assert s["status"] == "shipped" and shopify.s["fulfillments"] == []
    assert odoo.lot("L2610-S")["qty"] == 297


def test_sample_validation():
    with pytest.raises(Refuse, match="Missing"):
        pipeline.create_sample({"address": {"name": "x"}, "items": [{"sku": SMP, "qty": 1}]})
    with pytest.raises(Refuse, match="Not enough stock"):
        pipeline.create_sample({"address": ADDR, "items": [{"sku": MSK, "qty": 9}]})


def test_inventory_sync_pushes_odoo_available_to_shopify():
    o = make([(LIP, 4)])
    res = pipeline.sync_inventory()
    assert shopify.s["inventory"][LIP] == 56 and shopify.s["inventory"][MST] == 40 and res["errors"] == []


# ---------------------------------------------------------------- HTTP
def test_webhook_signature(monkeypatch):
    monkeypatch.setattr(settings, "webhook_secret", "topsecret")
    c = TestClient(app)
    body = json.dumps({"id": 1, "admin_graphql_api_id": "gid://shopify/Order/1"}).encode()
    good = base64.b64encode(hmac.new(b"topsecret", body, hashlib.sha256).digest()).decode()
    assert c.post("/webhooks/shopify/orders-cancelled", content=body, headers={"X-Shopify-Hmac-Sha256": good}).status_code == 200
    assert c.post("/webhooks/shopify/orders-cancelled", content=body, headers={"X-Shopify-Hmac-Sha256": "bad"}).status_code == 401


def test_http_flow_with_gid_in_the_path():
    c = TestClient(app)
    o = c.post("/api/dev/order", json={"items": [{"sku": LIP, "qty": 1}]}).json()
    assert o["status"] == "queued"
    from urllib.parse import quote
    oid = quote(o["id"], safe="")
    assert c.post(f"/api/orders/{oid}/scan", json={"code": "L2609-L"}).json()["order"]["pick_done"]
    assert c.post(f"/api/orders/{oid}/package", json={"code": "PM-0"}).status_code == 200
    assert c.post(f"/api/orders/{oid}/weight", json={"oz": 1.5}).json()["ready_for_label"]
    assert c.post(f"/api/orders/{oid}/label").json()["status"] == "labeled"
    assert c.post(f"/api/orders/{oid}/close").json()["status"] == "shipped"
    r = c.post(f"/api/orders/{oid}/scan", json={"code": "L2609-L"})
    assert r.status_code == 409 and "message" in r.json()["detail"]
    assert c.get(f"/api/orders/{oid}").json()["events"]


def test_zpl_contains_tracking_and_order():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    o = pipeline.request_label(o["id"])
    assert o["label"]["tracking"] in o["label"]["zpl"] and o["name"] in o["label"]["zpl"]


def test_stand_down_releases_stock_and_hands_the_order_to_ufsi():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    deposco.add(o["name"] + "--1")
    with pytest.raises(Refuse):
        pipeline.request_label(o["id"])
    out = pipeline.stand_down(o["id"])
    assert out["status"] == "released" and odoo.reserved(LIP) == 0
    assert shopify.s["orders"][o["id"]]["location_id"] == UFSI  # it still ships, from UFSI
    assert "Not in Deposco" in db.events(o["id"])[-1]["detail"]
