"""The eligibility check from the simulator. Runs on every new order, in code."""
from . import clock


def _c(key, ok, text):
    return {"key": key, "ok": bool(ok), "text": text}


def leaf_needs(odoo, lines):
    need = {}
    for l in lines:
        for sku, q in odoo.expand(l["sku"], l["qty"]).items():
            need[sku] = need.get(sku, 0) + q
    return need


def evaluate(order, odoo, settings, claimed_today=0, check_margin=True):
    if order.get("lines_truncated"):  # judging on part of the order could split it: never claim
        return {"eligible": False, "checks": [_c("complete", False, "The whole order could be read (it has too many lines)")]}
    need = leaf_needs(odoo, order["lines"])
    short = [s for s, q in need.items() if not odoo.product(s) or odoo.available(s) < q]
    checks = [_c("stock", not short and need,
                 "Every unit is in Babylon's own stock (bundles split into their components)"
                 + (f". Short or unknown: {', '.join(short)}" if short else ""))]

    tags = {t.lower() for t in order.get("tags", [])}
    clean = (order["financial_status"].upper() == "PAID" and order["risk"] != "HIGH"
             and not order["on_hold"] and not order["cancelled"] and "hold" not in tags)
    checks.append(_c("clean", clean, "Paid, not high risk, not on hold, not cancelled"))

    source = order["source"].lower()
    dom = (order["country"] == settings.domestic_country and source not in ("pos",)
           and "global-e" not in source and "global-e" not in tags)
    checks.append(_c("domestic", dom, "Domestic, not POS, not Global-e"))

    checks.append(_c("single", order.get("fo_count", 1) == 1,
                     "Ships as one fulfillment order (Shopify has not split it across locations)"))
    req = order.get("fo_request", "UNSUBMITTED")
    checks.append(_c("unrequested", req == "UNSUBMITTED",
                     "Not yet sent to a fulfillment service" + ("" if req == "UNSUBMITTED" else f" (request status {req})")))

    if check_margin:
        left = clock.minutes_to_import()
        ok = left > settings.import_margin_min
        checks.append(_c("margin", ok, f"{left:.0f} min until the :00 import"
                         + (f" (more than the {settings.import_margin_min:g}-min margin)" if ok
                            else f", inside the {settings.import_margin_min:g}-min margin")))

    if settings.sku_allowlist:
        skus = {s for s in need}
        checks.append(_c("allowlist", skus <= settings.sku_allowlist, "Every SKU is on the Babylon allowlist"))
    if settings.daily_cap:
        checks.append(_c("cap", claimed_today < settings.daily_cap,
                         f"Under today's cap ({claimed_today} of {settings.daily_cap} claimed)"))
    return {"eligible": all(c["ok"] for c in checks), "checks": checks}
