"""Real Odoo Enterprise (on-prem) adapter: SKELETON, not wired up in the POC.

The pipeline only calls the methods below, so swapping MockOdoo for this class is the
whole Odoo integration. Mapping to Odoo (verify against the actual version):

  catalog()/product(sku)   product.product  default_code == Shopify SKU; dims/weight/glass flag
                           as product fields or x_ custom fields
  expand(sku, qty)         mrp.bom of type "phantom" (kit) -> components
  on_hand(sku)             stock.quant filtered to the Babylon warehouse location (child_of)
  reserved(sku)            stock.move.line reserved qty / quant reserved_quantity
  reserve(order, items)    create stock.picking (out type of Babylon warehouse) + action_confirm
                           + action_assign; return False if not fully reserved
  release(order)           picking.action_cancel
  lot(code)                stock.lot (name == lot barcode), expiration_date if product_expiry installed
  fefo_lots(sku)           stock.quant grouped by lot, ordered by lot expiration_date
  consume(order, picks)    set move line lot_id/qty_done, picking.button_validate; return picking.name
  available_all()          read_group on stock.quant for the Babylon location
  stock_levels()           the same read_group (quantity, reserved_quantity) grouped by product and lot:
                           one call for the whole catalog, never one call per SKU
"""
import os
import xmlrpc.client


class RealOdoo:
    def __init__(self):
        self.url = os.environ["ODOO_URL"]
        self.db = os.environ["ODOO_DB"]
        self.user = os.environ["ODOO_USER"]
        self.key = os.environ["ODOO_API_KEY"]
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.user, self.key, {})
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def _call(self, model, method, *args, **kw):
        return self.models.execute_kw(self.db, self.uid, self.key, model, method, list(args), kw)

    def product(self, sku):
        rows = self._call("product.product", "search_read", [["default_code", "=", sku]],
                          fields=["name", "weight"], limit=1)
        return rows[0] if rows else None

    def __getattr__(self, name):
        raise NotImplementedError(f"RealOdoo.{name} is not implemented yet. See the mapping in this file.")
