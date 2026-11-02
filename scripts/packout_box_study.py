"""Which boxes to buy (build plan, pack-out): run the same package selection over real orders against a
candidate packaging list, and report how many orders fit, how many could go in a mailer, how many contain
glass, and how often each package would be used.

    python scripts/packout_box_study.py                       # the live catalog, last 30 days
    python scripts/packout_box_study.py candidates.json 90    # a candidate list, last 90 days

candidates.json is a list like the catalog: [{"code","name","type","inside":[L,W,H],"max_lb","weight_oz","cost"}].
Every candidate counts as in stock. It uses the database in .env (DATABASE_URL or the local SQLite file) and the
product sizes from Odoo, so the answer is only as good as those measurements."""
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db, packaging  # noqa: E402
from app.eligibility import leaf_needs  # noqa: E402
from app.services import odoo  # noqa: E402


def study(candidates, days):
    cat = {p["code"]: packaging._valid(dict(p, start=1)) for p in candidates}
    counts = {code: 1 for code in cat}
    s = packaging.settings()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    used, n, fitted, mailer, glass, unknown = Counter(), 0, 0, 0, 0, 0
    for o in db.list_orders(since=since, kind="order", limit=100_000):
        need = leaf_needs(odoo, o.get("lines") or [])
        if not need or any(not odoo.product(sku) for sku in need):
            unknown += 1  # an item without measurements can't be judged
            continue
        items = [(odoo.product(sku), q) for sku, q in need.items()]
        n += 1
        glass += packaging.order_class(items) == "box"
        pick = packaging.suggest(items, counts, s, cat)
        if pick:
            fitted += 1
            used[pick["bin"]] += 1
            mailer += pick["type"] != "box"
    return {"orders": n, "fit": fitted, "fit_pct": round(100 * fitted / n, 1) if n else None,
            "mailer_pct": round(100 * mailer / n, 1) if n else None, "glass_pct": round(100 * glass / n, 1) if n else None,
            "skipped_unmeasured": unknown, "by_package": used.most_common()}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    candidates = json.loads(Path(args[0]).read_text()) if args and not args[0].isdigit() else list(packaging.catalog().values())
    days = int(next((a for a in args if a.isdigit()), 30))
    r = study(candidates, days)
    print(f"{r['orders']} orders in the last {days} days ({r['skipped_unmeasured']} skipped: items without measurements)")
    print(f"fit one package: {r['fit']} ({r['fit_pct']}%)   in a mailer: {r['mailer_pct']}%   with glass: {r['glass_pct']}%")
    for code, k in r["by_package"]:
        print(f"  {code:<12} {k}")
