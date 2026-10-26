"""Package suggestion and packaging stock. Rules follow the simulator:
  glass in the order        -> smallest box with volume >= item volume / 0.7 (+ a bubble pouch per glass unit)
  no glass, fits flat       -> smallest padded mailer that fits (PM-0, then PM-2)
  no glass, soft, too bulky -> poly mailer
  anything else             -> smallest box that fits
Mailer 'inside' = (length, width, max stack thickness) and max_item = longest single item it should hold. These limits are starting guesses to tune from override logs."""
from . import db

BINS = {
    "PM-0": {"name": "Padded mailer #0", "kind": "mailer", "inside": [10, 6, 2.5], "max_item": 5.0, "start": 250, "reorder": 75},
    "PM-2": {"name": "Padded mailer #2", "kind": "mailer", "inside": [12, 8.5, 3.5], "max_item": 8.0, "start": 200, "reorder": 60},
    "PL-1": {"name": "Poly mailer", "kind": "poly", "inside": [13, 10, 4.0], "start": 200, "reorder": 50},
    "BX-S": {"name": "Box, small", "kind": "box", "inside": [6, 4, 4], "start": 150, "reorder": 40},
    "BX-M": {"name": "Box, medium", "kind": "box", "inside": [8, 6, 4], "start": 150, "reorder": 40},
    "BX-L": {"name": "Box, large", "kind": "box", "inside": [10, 8, 6], "start": 100, "reorder": 25},
}
FILL = 0.7


def _vol(d):
    return d[0] * d[1] * d[2]


def _fits_mailer(units, inside, max_item=99):
    L, W, T = inside
    longest = max(max(d) for d, _ in units)
    widest = max(sorted(d)[1] for d, _ in units)
    thick = sum(min(d) * q for d, q in units)
    return longest <= min(L - 0.5, max_item) and widest <= W - 0.5 and thick <= T


def _fits_box(units, inside):
    vol = sum(_vol(d) * q for d, q in units)
    longest = max(max(d) for d, _ in units)
    return vol / FILL <= _vol(inside) and longest <= max(inside)


def suggest(items):
    """items: [(product_dict, qty)] -> {bin, reason, bubble}"""
    units = [(p["dims"], q) for p, q in items]
    glass = sum(q for p, q in items if p["glass"])
    soft = all(p["soft"] for p, _ in items)
    by_size = lambda kinds: sorted((b for b, v in BINS.items() if v["kind"] in kinds), key=lambda b: _vol(BINS[b]["inside"]))
    if glass:
        for b in by_size(["box"]):
            if _fits_box(units, BINS[b]["inside"]):
                return {"bin": b, "reason": "Glass in the order: smallest box with room for bubble and void fill", "bubble": glass}
    else:
        for b in ("PM-0", "PM-2"):
            if _fits_mailer(units, BINS[b]["inside"], BINS[b].get("max_item", 99)):
                return {"bin": b, "reason": "No glass and it fits flat: smallest padded mailer", "bubble": 0}
        if soft:
            return {"bin": "PL-1", "reason": "Soft, unbreakable and too bulky for a padded mailer", "bubble": 0}
        for b in by_size(["box"]):
            if _fits_box(units, BINS[b]["inside"]):
                return {"bin": b, "reason": "Smallest box that fits", "bubble": 0}
    return {"bin": "BX-L", "reason": "Nothing fits the rules: largest box, ask a lead to check", "bubble": glass}


# ---- stock ------------------------------------------------------
def _start():
    return {b: v["start"] for b, v in BINS.items()}


def stock():
    return db.kv_get("packaging") or _start()


def take(bin_code, n=1):
    """One atomic step, so two packers taking boxes at once can't lose a count."""
    def change(s):
        s[bin_code] = s.get(bin_code, 0) - n
        return s
    db.kv_update("packaging", change, _start())


def give_back(bin_code, n=1):
    take(bin_code, -n)


def reset():
    db.kv_set("packaging", _start())


def stock_view():
    s = stock()
    return [{"bin": b, "name": v["name"], "qty": s[b], "reorder": v["reorder"], "low": s[b] <= v["reorder"],
             "inside": v["inside"]} for b, v in BINS.items()]


def parcel_dims(bin_code, items):
    """Box dims for the label request. Mailers get a thickness estimated from the items."""
    L, W, T = BINS[bin_code]["inside"]
    if BINS[bin_code]["kind"] in ("mailer", "poly"):
        thick = sum(min(p["dims"]) * q for p, q in items)
        return [L, W, round(max(0.5, min(T, thick)), 1)]
    return [L, W, T]
