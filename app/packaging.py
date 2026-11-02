"""Pack-out: which package an order ships in (Cody's build plan, Oct 30 2026).

The package is chosen when the order is claimed, from packaging that is in stock. If nothing in stock fits,
Babylon doesn't claim the order (claim check 7) and it stays with UFSI.

  Protection class  An order takes the strictest class of its units.
                    safe (tubes, sticks, sachets)      -> any mailer or box
                    care (pumps, jars, plastic bottles) -> padded mailer (bagged, pump clipped) or a box
                    box  (glass, compacts, powders)    -> box only, glass wrapped
  Box fit           3D packing (extreme points, largest item first, 6 orientations). Each item grows by the
                    wrap allowance when it is wrapped; the box shrinks by the clearance on every side.
  Mailer fit        Items lie flat. The stacked thickness stays under the mailer's fill limit, and each layer's
                    footprint fits in (length - thickness) x (width - thickness), since a mailer shrinks as it
                    fills. A rule of thumb to calibrate on real mailers.
  Cost              packaging + fill + estimated postage. Cheapest wins; on a tie, the smaller package.
  Expected weight   items + packaging + fill + bubble wrap. The weigh step must land within the tolerance.

The catalog, the counts on hand and the settings are data (kv store), so ops can replace the demo list."""
from itertools import permutations
from . import db

TYPES = ("box", "mailer", "poly")
ALLOWED = {"safe": {"box", "mailer", "poly"}, "care": {"box", "mailer"}, "box": {"box"}}
RANK = {"safe": 0, "care": 1, "box": 2}
OVERRIDE_REASONS = ("didn't fit", "too big", "out of stock", "damaged")

# Demo catalog: Cody's six box sizes (October analysis: 99.9% of fillable orders fit with 0.5 in of fill)
# plus the mailers. Costs and weights are placeholders until Babylon ops supply the real list.
DEFAULT_CATALOG = [
    {"code": "PM-0", "name": "Padded mailer #0", "type": "mailer", "inside": [10, 6, 2.0], "max_lb": 4, "weight_oz": 0.5, "cost": 0.30, "start": 250, "reorder": 75},
    {"code": "PM-2", "name": "Padded mailer #2", "type": "mailer", "inside": [12, 8.5, 2.5], "max_lb": 6, "weight_oz": 0.8, "cost": 0.42, "start": 200, "reorder": 60},
    {"code": "PL-1", "name": "Poly mailer", "type": "poly", "inside": [13, 10, 4.0], "max_lb": 10, "weight_oz": 0.4, "cost": 0.12, "start": 200, "reorder": 50},
    {"code": "BX-7X3X3", "name": "Box 7×3×3", "type": "box", "inside": [7, 3, 3], "max_lb": 20, "weight_oz": 2.5, "cost": 0.38, "start": 150, "reorder": 40},
    {"code": "BX-9X6X3", "name": "Box 9×6×3", "type": "box", "inside": [9, 6, 3], "max_lb": 25, "weight_oz": 4.0, "cost": 0.52, "start": 150, "reorder": 40},
    {"code": "BX-10X8X4", "name": "Box 10×8×4", "type": "box", "inside": [10, 8, 4], "max_lb": 30, "weight_oz": 6.0, "cost": 0.70, "start": 150, "reorder": 40},
    {"code": "BX-14X10X4", "name": "Box 14×10×4", "type": "box", "inside": [14, 10, 4], "max_lb": 40, "weight_oz": 9.0, "cost": 0.95, "start": 100, "reorder": 25},
    {"code": "BX-14X10X6", "name": "Box 14×10×6", "type": "box", "inside": [14, 10, 6], "max_lb": 40, "weight_oz": 11.0, "cost": 1.10, "start": 100, "reorder": 25},
    {"code": "BX-14X14X8", "name": "Box 14×14×8", "type": "box", "inside": [14, 14, 8], "max_lb": 50, "weight_oz": 16.0, "cost": 1.45, "start": 60, "reorder": 15},
]
DEFAULT_SETTINGS = {
    "clearance_in": 0.5,        # fill space per side inside a box
    "wrap_in": 0.25,            # bubble wrap per side around each glass unit
    "bubble_oz": 0.2,           # weight of one unit's bubble wrap
    "fill_oz_per_in3": 0.002,   # paper fill, per cubic inch of empty box space
    "fill_cost_per_in3": 0.0003,
    "mailer_area_use": 0.85,    # share of a mailer layer's footprint items can really cover
    "weigh_tol_pct": 8,         # the weigh step accepts expected ± max(pct, min_oz)
    "weigh_tol_min_oz": 1.0,
}
# USPS Ground Advantage, estimated, by billable ounces. Replace with real rates once the carrier quote lands.
POSTAGE = [(4, 4.75), (8, 5.45), (12, 6.20), (16, 7.60), (32, 9.15), (48, 10.40), (64, 11.90), (80, 13.55),
           (96, 15.05), (112, 16.45), (128, 17.85), (160, 20.90), (240, 26.00), (400, 40.00), (1120, 70.00)]
USPS_DIM_DIVISOR = 166   # USPS counts size only above 1 cubic foot
CUBIC_FOOT = 1728


# ------------------------------------------------------------ data: catalog, counts, settings
def catalog():
    """code -> package definition."""
    return {p["code"]: p for p in (db.kv_get("packaging_catalog") or DEFAULT_CATALOG)}


def save_package(p):
    """Add or change one package definition (Babylon ops replacing the demo list)."""
    p = _valid(p)
    def change(items):
        items = [x for x in items if x["code"] != p["code"]] + [p]
        return sorted(items, key=lambda x: (TYPES.index(x["type"]), _vol(x["inside"])))
    db.kv_update("packaging_catalog", change, [dict(x) for x in DEFAULT_CATALOG])
    return p


def remove_package(code):
    db.kv_update("packaging_catalog", lambda items: [x for x in items if x["code"] != code], [dict(x) for x in DEFAULT_CATALOG])


def _valid(p):
    code = str(p.get("code", "")).strip().upper()
    if not code:
        raise ValueError("A package needs a code")
    if p.get("type") not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    inside = [float(x) for x in p.get("inside", [])]
    if len(inside) != 3 or min(inside) <= 0:
        raise ValueError("inside must be three positive sizes in inches")
    out = {"code": code, "name": str(p.get("name") or code), "type": p["type"], "inside": inside,
           "max_lb": float(p.get("max_lb") or 0), "weight_oz": float(p.get("weight_oz") or 0),
           "cost": float(p.get("cost") or 0), "start": int(p.get("start") or 0), "reorder": int(p.get("reorder") or 0)}
    if out["max_lb"] <= 0:
        raise ValueError("max_lb must be above 0")
    return out


def settings():
    return {**DEFAULT_SETTINGS, **(db.kv_get("packout_settings") or {})}


def save_settings(changes):
    unknown = set(changes) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
    return db.kv_update("packout_settings", lambda s: {**s, **{k: float(v) for k, v in changes.items()}}, {})


def stock(cat=None):
    """code -> count on hand. A package never counted yet starts at its catalog quantity."""
    counts = db.kv_get("packaging") or {}
    return {code: counts.get(code, p["start"]) for code, p in (cat or catalog()).items()}


def take(code, n=1):
    """One atomic step, so two packers taking boxes at once can't lose a count."""
    start = catalog().get(code, {}).get("start", 0)
    def change(s):
        s[code] = s.get(code, start) - n
        return s
    db.kv_update("packaging", change, {})


def give_back(code, n=1):
    take(code, -n)


def set_count(code, qty):
    """A physical count of one package, e.g. after receiving or a cycle count."""
    def change(s):
        s[code] = int(qty)
        return s
    db.kv_update("packaging", change, {})


def reset():
    db.kv_set("packaging", {p["code"]: p["start"] for p in DEFAULT_CATALOG})
    db.kv_set("packaging_catalog", [dict(p) for p in DEFAULT_CATALOG])
    db.kv_set("packout_settings", {})


def stock_view():
    cat = catalog()
    s = stock(cat)
    return [dict(p, bin=code, qty=s[code], low=s[code] <= p["reorder"]) for code, p in cat.items()]


# ------------------------------------------------------------ products
def protection(product):
    """The product's protection class; glass always means box only."""
    if product.get("glass"):
        return "box"
    return product.get("protection") or "safe"


def order_class(items):
    return max((protection(p) for p, _ in items), key=RANK.get, default="safe")


# ------------------------------------------------------------ fit
def _vol(d):
    return d[0] * d[1] * d[2]


def _overlap(a, b):
    return all(a[i] < b[i] + b[i + 3] - 1e-9 and b[i] < a[i] + a[i + 3] - 1e-9 for i in range(3))


def box_fits(units, inner):
    """3D extreme-point packing: largest item first, each tried in its 6 orientations at the lowest free
    corner. units are (l, w, h) already including any wrap; inner is the usable inside size.
    O(n² · points) for n units, which is instant for a parcel's worth of items."""
    if not units:
        return True
    if any(x <= 0 for x in inner):
        return False
    lim = sorted(inner)
    if any(any(a > b + 1e-9 for a, b in zip(sorted(u), lim)) for u in units):
        return False  # some item is bigger than the box in some dimension, whatever the orientation
    if sum(_vol(u) for u in units) > _vol(inner) + 1e-9:
        return False
    placed, points = [], [(0.0, 0.0, 0.0)]
    for u in sorted(units, key=_vol, reverse=True):
        spot = None
        for p in sorted(points, key=lambda q: (q[2], q[1], q[0])):
            for o in sorted(set(permutations(u))):
                box = (*p, *o)
                if all(p[i] + o[i] <= inner[i] + 1e-9 for i in range(3)) and not any(_overlap(box, q) for q in placed):
                    spot = box
                    break
            if spot:
                break
        if not spot:
            return False
        placed.append(spot)
        x, y, z, dx, dy, dz = spot
        points.remove((x, y, z))
        points += [(x + dx, y, z), (x, y + dy, z), (x, y, z + dz)]
    return True


def mailer_fits(units, inside, area_use):
    """Items lie flat (thinnest side down) in k layers. Thickness t is the sum of each layer's thickest item;
    each layer must fit in (L - t) x (W - t) at area_use coverage. Tries 1..n layers."""
    if not units:
        return True
    L, W, fill = inside
    flat = sorted((sorted(u) for u in units), key=lambda d: d[1] * d[2], reverse=True)
    for k in range(1, len(flat) + 1):
        layers = [[] for _ in range(k)]
        for d in flat:  # largest footprint first, into the emptiest layer
            min(layers, key=lambda l: sum(x[1] * x[2] for x in l)).append(d)
        t = sum(max(x[0] for x in l) for l in layers if l)
        if t > fill + 1e-9:
            return False  # more layers only adds thickness
        ul, uw = L - t, W - t
        if ul <= 0 or uw <= 0:
            return False
        if all(max(x[2] for x in l) <= ul and max(x[1] for x in l) <= uw for l in layers if l) and \
           all(sum(x[1] * x[2] for x in l) <= area_use * ul * uw for l in layers):
            return True
    return False


# ------------------------------------------------------------ cost and weight
def postage_estimate(weight_oz, outer):
    billable = weight_oz
    if _vol(outer) > CUBIC_FOOT:
        billable = max(weight_oz, _vol(outer) / USPS_DIM_DIVISOR * 16)
    for limit, price in POSTAGE:
        if billable <= limit:
            return price
    return POSTAGE[-1][1]


def _items_oz(items):
    return sum(p.get("oz", 0) * q for p, q in items)


def _glass(items):
    return sum(q for p, q in items if p.get("glass"))


def expected_oz(code, items, s=None):
    """What the packed parcel should weigh in this package: items + packaging + fill + bubble wrap."""
    return _expected(catalog()[code], items, s or settings())


def _expected(pkg, items, s):
    fill = 0.0
    if pkg["type"] == "box":
        void = max(0.0, _vol(pkg["inside"]) - sum(_vol(p["dims"]) * q for p, q in items))
        fill = void * s["fill_oz_per_in3"]
    return round(_items_oz(items) + pkg["weight_oz"] + fill + _glass(items) * s["bubble_oz"], 1)


def tolerance_oz(expected, s=None):
    s = s or settings()
    return round(max(s["weigh_tol_min_oz"], expected * s["weigh_tol_pct"] / 100), 1)


def _units(items, wrap):
    """One (l, w, h) per unit; glass units grow by the wrap allowance on every side."""
    out = []
    for p, q in items:
        d = [x + (2 * wrap if p.get("glass") else 0) for x in p["dims"]]
        out += [d] * q
    return out


def fits(pkg, items, s=None):
    s = s or settings()
    if order_class(items) not in ALLOWED or pkg["type"] not in ALLOWED[order_class(items)]:
        return False
    if _items_oz(items) + pkg["weight_oz"] > pkg["max_lb"] * 16:
        return False
    if pkg["type"] == "box":
        inner = [x - 2 * s["clearance_in"] for x in pkg["inside"]]
        return box_fits(_units(items, s["wrap_in"]), inner)
    return mailer_fits(_units(items, 0), pkg["inside"], s["mailer_area_use"])


def parcel_dims(code, items):
    """Parcel size for the label request. Mailers get a thickness estimated from the items."""
    pkg = catalog()[code]
    L, W, T = pkg["inside"]
    if pkg["type"] in ("mailer", "poly"):
        thick = sum(min(p["dims"]) * q for p, q in items)
        return [L, W, round(max(0.5, min(T, thick)), 1)]
    return [L, W, T]


def _instructions(items, pkg):
    out = []
    glass = _glass(items)
    if glass:
        out.append(f"Wrap {glass} glass item{'s' if glass > 1 else ''} in bubble")
    if pkg["type"] != "box" and any(protection(p) == "care" for p, _ in items):
        out.append("Bag liquids and clip pumps shut")
    elif any(p.get("liquid") for p, _ in items):
        out.append("Bag liquids")
    if pkg["type"] == "box":
        out.append("Fill the empty space so nothing moves")
    return out


def suggest(items, counts=None, s=None, cat=None):
    """The cheapest package in stock that fits, or None if nothing does. items: [(product, qty)].
    cat: a candidate catalog instead of the live one (the box-buying study)."""
    s = s or settings()
    cat = cat or catalog()  # read once: on Postgres every read is a round trip
    counts = counts if counts is not None else stock(cat)
    cls = order_class(items)
    best = None
    for code, pkg in cat.items():
        if counts.get(code, 0) <= 0 or not fits(pkg, items, s):
            continue
        weight = _expected(pkg, items, s)
        outer = [x + 0.25 for x in pkg["inside"]]
        fill_cost = (max(0.0, _vol(pkg["inside"]) - sum(_vol(p["dims"]) * q for p, q in items)) * s["fill_cost_per_in3"]
                     if pkg["type"] == "box" else 0.0)
        cost = round(pkg["cost"] + fill_cost + postage_estimate(weight, outer), 2)
        key = (cost, _vol(pkg["inside"]))
        if best is None or key < best[0]:
            best = (key, code, pkg, weight, cost)
    if not best:
        return None
    _, code, pkg, weight, cost = best
    why = {"box": "Glass, compacts or powders: box only", "care": "Pumps, jars or bottles: padded mailer or box",
           "safe": "Nothing fragile: any mailer or box"}[cls]
    return {"bin": code, "name": pkg["name"], "type": pkg["type"], "protection": cls,
            "reason": f"{why}. Cheapest that fits, about ${cost:.2f} with postage",
            "bubble": _glass(items), "instructions": _instructions(items, pkg),
            "expected_oz": weight, "tolerance_oz": tolerance_oz(weight, s), "est_cost": cost}
