"""QA pass: one test per bug fixed and per scale guarantee, so none of them can come back."""
import sqlite3
import threading
import httpx
import pytest
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app import db, packaging, pipeline
from app.config import Settings, settings
from app.main import app
from app.pipeline import Refuse
from app.services import odoo, shopify, deposco
from app.shopify import LiveShopify, ShopifyError

BAB, UFSI = settings.babylon_location_id, settings.ufsi_location_id
LIP, MSK, SER = "BAB-LIP-01", "BAB-MSK-01", "BAB-SER-30"
ADDR = {"name": "Client A", "address1": "5 Main St", "city": "Austin", "state": "TX", "zip": "78701", "country": "US"}


def make(items, **kw):
    return pipeline.ingest_order(shopify.create_order([{"sku": s, "qty": q, "title": s} for s, q in items], **kw))


def pack_ready(o, lots, weight=6.0):
    for lot in lots:
        pipeline.scan_lot(o["id"], lot)
    pipeline.set_package(o["id"], db.get_order(o["id"])["package"]["bin"])
    return pipeline.set_weight(o["id"], weight)


def run_together(fns):
    """Start every function at the same moment and wait for all of them."""
    gate = threading.Barrier(len(fns))
    out = [None] * len(fns)

    def go(i, fn):
        gate.wait()
        out[i] = fn()
    ts = [threading.Thread(target=go, args=(i, f)) for i, f in enumerate(fns)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


# ------------------------------------------------------------ concurrency
def test_two_orders_racing_for_the_last_units_never_oversell():
    gids = [shopify.create_order([{"sku": MSK, "qty": 2, "title": MSK}]) for _ in range(8)]  # Babylon has 2 masks
    results = run_together([lambda g=g: pipeline.ingest_order(g) for g in gids])
    assert sum(r["status"] == "queued" for r in results) == 1
    assert odoo.reserved(MSK) == 2 and odoo.available(MSK) == 0


def test_packaging_count_is_exact_under_concurrent_packers():
    start = packaging.stock()["BX-S"]
    run_together([lambda: packaging.take("BX-S") for _ in range(40)])
    assert packaging.stock()["BX-S"] == start - 40


def test_a_webhook_racing_a_poll_claims_once():
    gid = shopify.create_order([{"sku": LIP, "qty": 1, "title": LIP}])
    run_together([lambda: pipeline.ingest_order(gid), lambda: pipeline.poll_recent(), lambda: pipeline.ingest_order(gid)])
    assert odoo.reserved(LIP) == 1 and [e["kind"] for e in db.events(gid)].count("claimed") == 1


# ------------------------------------------------------------ state machine
def test_cannot_weigh_an_order_that_is_already_done():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    pipeline.set_package(o["id"], "PM-0")
    shopify.cancel(o["id"])
    pipeline.handle_cancel(o["id"])
    with pytest.raises(Refuse, match="cancelled"):
        pipeline.set_weight(o["id"], 2)
    assert db.get_order(o["id"])["status"] == "cancelled"


def test_cancel_after_packing_puts_the_box_back():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    before = packaging.stock()["PM-0"]
    pipeline.set_package(o["id"], "PM-0")
    shopify.cancel(o["id"])
    pipeline.handle_cancel(o["id"])
    assert packaging.stock()["PM-0"] == before


def test_scanning_the_same_box_twice_takes_one_box():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    before = packaging.stock()["PM-0"]
    pipeline.set_package(o["id"], "PM-0")
    pipeline.set_package(o["id"], "PM-0")
    assert packaging.stock()["PM-0"] == before - 1


def test_failed_hand_back_changes_nothing(monkeypatch):
    o = make([(LIP, 1)])
    monkeypatch.setattr(shopify, "move_fulfillment_order", lambda *a: (_ for _ in ()).throw(ShopifyError("down")))
    with pytest.raises(Refuse, match="nothing was changed"):
        pipeline.short_at_pick(o["id"], LIP)
    assert db.get_order(o["id"])["status"] == "queued" and odoo.reserved(LIP) == 1


def test_edit_whose_hand_back_fails_keeps_the_hold_and_flags_it(monkeypatch):
    o = make([(LIP, 1)])
    shopify.edit(o["id"], [{"sku": LIP, "qty": 1}, {"sku": MSK, "qty": 5}])
    monkeypatch.setattr(shopify, "move_fulfillment_order", lambda *a: (_ for _ in ()).throw(ShopifyError("down")))
    out = pipeline.handle_edit(o["id"])
    assert out["status"] == "queued" and "by hand" in out["attention"] and odoo.reserved(LIP) == 1


def test_stand_down_after_a_cancel_cancels_instead_of_handing_over():
    o = pack_ready(make([(LIP, 1)]), ["L2609-L"])
    shopify.cancel(o["id"])
    with pytest.raises(Refuse):
        pipeline.request_label(o["id"])
    assert pipeline.stand_down(o["id"])["status"] == "cancelled"
    assert shopify.s["orders"][o["id"]]["location_id"] == BAB and odoo.reserved(LIP) == 0


def test_cancel_after_shipping_is_logged_once():
    o = pipeline.close_order(pipeline.request_label(pack_ready(make([(LIP, 1)]), ["L2609-L"])["id"])["id"])
    shopify.s["orders"][o["id"]]["cancelled"] = True
    pipeline.handle_cancel(o["id"])
    pipeline.handle_cancel(o["id"])
    assert [e["kind"] for e in db.events(o["id"])].count("cancelled") == 1


def test_an_order_too_long_to_read_whole_is_never_claimed():
    gid = shopify.create_order([{"sku": LIP, "qty": 1, "title": LIP}])
    shopify.s["orders"][gid]["lines_truncated"] = True
    o = pipeline.ingest_order(gid)
    assert o["status"] == "ufsi" and o["checks"][0]["key"] == "complete"


# ------------------------------------------------------------ polling
def test_poll_sees_a_cancel_on_a_claimed_order():
    o = make([(LIP, 1)])
    shopify.cancel(o["id"])  # no webhook
    pipeline.poll_recent()
    assert db.get_order(o["id"])["status"] == "cancelled" and odoo.reserved(LIP) == 0


def test_poll_sees_an_edit_on_a_claimed_order():
    o = make([(LIP, 1)])
    shopify.edit(o["id"], [{"sku": LIP, "qty": 2}])
    pipeline.poll_recent()
    o = db.get_order(o["id"])
    assert o["pick"][0]["need"] == 2 and odoo.reserved(LIP) == 2


def test_poll_does_not_refetch_unchanged_orders(monkeypatch):
    for _ in range(5):
        make([(LIP, 1)])
    pipeline.poll_recent()
    calls = []
    real = shopify.get_order
    monkeypatch.setattr(shopify, "get_order", lambda gid: calls.append(gid) or real(gid))
    pipeline.poll_recent()
    assert calls == []


def test_poll_does_not_mistake_its_own_move_for_an_edit():
    o = make([(LIP, 1)])  # the claim moved the order, which changed Shopify's updatedAt
    pipeline.scan_lot(o["id"], "L2609-L")
    pipeline.set_package(o["id"], "PM-0")
    pipeline.set_weight(o["id"], 1.5)
    pipeline.poll_recent()
    assert not db.get_order(o["id"]).get("attention")


def test_poll_mark_moves_only_after_a_clean_window(monkeypatch):
    make([(LIP, 1)])
    db.kv_set("poll_since", None)
    shopify.create_order([{"sku": LIP, "qty": 1, "title": LIP}])
    monkeypatch.setattr(shopify, "get_order", lambda gid: (_ for _ in ()).throw(ShopifyError("timeout")))
    pipeline.poll_recent()
    assert db.kv_get("poll_since") is None  # retried next time instead of lost
    monkeypatch.undo()
    pipeline.poll_recent()
    assert db.kv_get("poll_since")


def test_poll_after_a_long_outage_still_finds_the_orders():
    db.kv_set("poll_since", (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())
    gid = shopify.create_order([{"sku": LIP, "qty": 1, "title": LIP}])
    assert pipeline.poll_recent() == 1 and db.get_order(gid)


def test_live_poll_reads_every_page(monkeypatch):
    monkeypatch.setattr(settings, "shop", "x.myshopify.com")
    monkeypatch.setattr(settings, "shopify_token", "t")
    pages = [{"orders": {"pageInfo": {"hasNextPage": i < 2, "endCursor": f"c{i}"},
                         "nodes": [{"id": f"gid://shopify/Order/{i}{j}", "updatedAt": "2026-10-26T00:00:00Z"} for j in range(250)]}}
             for i in range(3)]
    seen = []
    live = LiveShopify()
    monkeypatch.setattr(live, "_gql", lambda q, v: seen.append(v["after"]) or pages[len(seen) - 1])
    assert len(live.list_updated_orders(datetime.now(timezone.utc))) == 750 and seen == [None, "c0", "c1"]


def test_live_stock_sync_looks_skus_up_in_batches(monkeypatch):
    monkeypatch.setattr(settings, "shop", "x.myshopify.com")
    monkeypatch.setattr(settings, "shopify_token", "t")
    lookups, writes = [], []

    def gql(q, v):
        if "productVariants" in q:
            lookups.append(v["q"])
            return {"productVariants": {"nodes": [{"sku": s.split('"')[1], "inventoryItem": {"id": "i" + s.split('"')[1]}}
                                                  for s in v["q"].split(" OR ")]}}
        writes.append(len(v["i"]["quantities"]))
        return {"inventorySetQuantities": {"userErrors": []}}
    live = LiveShopify()
    monkeypatch.setattr(live, "_gql", gql)
    res = live.set_inventory({f"SKU-{n}": n for n in range(120)})
    assert len(lookups) == 3 and writes == [100, 20] and res["updated"] == 120
    live.set_inventory({f"SKU-{n}": n for n in range(120)})
    assert len(lookups) == 3  # cached


# ------------------------------------------------------------ input, ids, config
def test_sample_ids_are_unique_even_when_created_together():
    body = {"address": ADDR, "items": [{"sku": "BAB-SMP-01", "qty": 1}]}
    ids = run_together([lambda: pipeline.create_sample(body)["id"] for _ in range(10)])
    assert len(set(ids)) == 10


def test_sample_rejects_a_bad_quantity():
    for bad in ("two", -1, 10**9):
        with pytest.raises(Refuse) as e:
            pipeline.create_sample({"address": ADDR, "items": [{"sku": LIP, "qty": bad}]})
        assert e.value.status == 400


def test_a_mistyped_mode_stops_the_app(monkeypatch):
    monkeypatch.setenv("CLAIM_MODE", "Liv")
    with pytest.raises(ValueError, match="CLAIM_MODE"):
        Settings()
    monkeypatch.setenv("CLAIM_MODE", "SHADOW")
    assert Settings().claim_mode == "shadow"


# ------------------------------------------------------------ storage and API
def test_old_databases_are_upgraded_in_place(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE orders(id TEXT PRIMARY KEY, name TEXT, kind TEXT, status TEXT, updated_at TEXT, data TEXT);"
        "CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, ts TEXT, kind TEXT, detail TEXT);"
        "CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);"
        """INSERT INTO orders VALUES('a','#1','order','queued','2026','{"id":"a","name":"#1","kind":"order","status":"queued","address":{"name":"Ann"},"claimed_day":"2026-10-26","created_at":"2026-10-26T00:00:00"}');""")
    c.commit()
    c.close()
    monkeypatch.setattr(settings, "db_path", str(path))
    monkeypatch.setattr(db, "_conn", None)
    try:
        assert db.count_claimed_on("2026-10-26") == 1
        assert [o["id"] for o in db.list_orders(q="ann")] == ["a"]
    finally:
        db._conn.close()


def test_lists_are_paged_searched_and_slim():
    for _ in range(12):
        make([(LIP, 1)], address=dict(ADDR, name="Zed Example"))
    make([(LIP, 1)], address=dict(ADDR, name="Ann Other"))
    c = TestClient(app)
    page = c.get("/api/orders?view=all&limit=5").json()
    assert len(page) == 5 and "label" not in page[0] and "lines" not in page[0] and page[0]["units"] == 1
    assert len(c.get("/api/orders?view=all&limit=5&offset=10").json()) == 3
    assert [o["address"]["name"] for o in c.get("/api/orders?view=all&q=ann").json()] == ["Ann Other"]
    assert c.get("/api/orders?view=all&q=50%25").json() == []  # % is matched literally, not as a wildcard
    assert c.get("/api/orders?view=nope").status_code == 400


def test_queue_is_first_in_first_out():
    a, b = make([(LIP, 1)]), make([(LIP, 1)])
    pipeline.scan_lot(a["id"], "L2609-L")  # touching the older one must not move it behind the newer one
    ids = [o["id"] for o in TestClient(app).get("/api/orders?view=queue").json()]
    assert ids == [a["id"], b["id"]]


def test_summary_counts_without_loading_orders():
    make([(LIP, 1)])
    make([(MSK, 3)])
    s = TestClient(app).get("/api/summary").json()
    assert s["to_pack"] == 1 and s["counts"]["ufsi"] == 1 and "BAB-MSK-01" in s["low_stock"] and len(s["recent"]) == 2


def test_stock_levels_match_the_per_sku_answers():
    make([(SER, 2)])
    levels = odoo.stock_levels()
    for sku in odoo.catalog():
        assert levels[sku]["available"] == odoo.available(sku) and levels[sku]["reserved"] == odoo.reserved(sku)
    assert levels["BAB-MST-60"]["lots"] == ["L2609-A"]  # the expired lot is left out


def test_order_history_comes_back_in_order():
    o = make([(LIP, 1)])
    pipeline.scan_lot(o["id"], "L2609-L")
    kinds = [e["kind"] for e in TestClient(app).get("/api/orders/" + o["id"].replace("/", "%2F")).json()["events"]]
    assert kinds[0] == "claimed" and kinds[-1] == "pick_done"
