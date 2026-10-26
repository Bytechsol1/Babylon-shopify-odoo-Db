"""Singletons wired by mode."""
import re
from .config import settings
from .shopify import MockShopify, LiveShopify
from .deposco import MockDeposco, LiveDeposco, SkippedDeposco

if settings.shopify_mode == "live" and settings.deposco_mode == "mock":
    raise RuntimeError("DEPOSCO_MODE=mock would pass every real order. Set DEPOSCO_MODE=off "
                       "(check skipped and flagged on every label) or live (no labels until the adapter exists).")

_LOC = re.compile(r"^gid://shopify/Location/\d+$")
if settings.shopify_mode == "live" and settings.claim_mode == "live" and not all(
        _LOC.match(x) for x in (settings.babylon_location_id, settings.ufsi_location_id)):
    raise RuntimeError("CLAIM_MODE=live moves real orders, so BABYLON_LOCATION_ID and UFSI_LOCATION_ID "
                       "must both be real gid://shopify/Location/<number> IDs.")

if settings.odoo_mode == "real":
    from .odoo.real import RealOdoo
    odoo = RealOdoo()
else:
    from .odoo.mock import MockOdoo
    odoo = MockOdoo()

shopify = MockShopify() if settings.shopify_mode == "mock" else LiveShopify()
deposco = {"mock": MockDeposco, "off": SkippedDeposco}.get(settings.deposco_mode, LiveDeposco)()
