import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if os.getenv("BABYLON_NO_DOTENV") != "1":  # tests run on their own settings, not the local store's
    load_dotenv(ROOT / ".env")


def _e(k, d=""):
    return (os.getenv(k) or "").strip() or d  # a blank line in .env counts as unset


def _bool(k, d="0"):
    return _e(k, d).lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self):
        self.shopify_mode = _e("SHOPIFY_MODE", "mock")
        self.easypost_mode = _e("EASYPOST_MODE", "mock")
        self.printer_mode = _e("PRINTER_MODE", "file")
        self.deposco_mode = _e("DEPOSCO_MODE", "mock")
        self.odoo_mode = _e("ODOO_MODE", "mock")
        self.claim_mode = _e("CLAIM_MODE", "live")

        self.shop = _e("SHOPIFY_SHOP")
        self.shopify_token = _e("SHOPIFY_ADMIN_TOKEN")
        # Without a token, the app swaps these for a 24-hour token (client credentials grant)
        self.shopify_client_id = _e("SHOPIFY_CLIENT_ID")
        self.shopify_client_secret = _e("SHOPIFY_CLIENT_SECRET")
        self.webhook_secret = _e("SHOPIFY_WEBHOOK_SECRET")
        self.api_version = _e("SHOPIFY_API_VERSION", "2026-07")
        self.babylon_location_id = _e("BABYLON_LOCATION_ID", "gid://shopify/Location/BABYLON")
        self.ufsi_location_id = _e("UFSI_LOCATION_ID", "gid://shopify/Location/UFSI")

        self.import_tz = _e("IMPORT_TZ", "America/Chicago")
        self.import_margin_min = float(_e("IMPORT_MARGIN_MIN", "3"))
        self.domestic_country = _e("DOMESTIC_COUNTRY", "US")
        self.sku_allowlist = {s.strip() for s in _e("SKU_ALLOWLIST").split(",") if s.strip()}
        self.daily_cap = int(_e("DAILY_CAP", "0") or 0)

        self.easypost_key = _e("EASYPOST_API_KEY")
        self.notify_mode = _e("NOTIFY_CUSTOMER", "auto").lower()
        self.ship_from = {
            "name": _e("SHIP_FROM_NAME", "Babylon LLC"),
            "company": _e("SHIP_FROM_NAME", "Babylon LLC"),
            "street1": _e("SHIP_FROM_STREET1", "123 Example St"),
            "city": _e("SHIP_FROM_CITY", "San Jose"),
            "state": _e("SHIP_FROM_STATE", "CA"),
            "zip": _e("SHIP_FROM_ZIP", "95112"),
            "country": "US",
            "phone": _e("SHIP_FROM_PHONE", "4085550100"),
        }

        self.printer_host = _e("PRINTER_HOST")
        self.printer_port = int(_e("PRINTER_PORT", "9100") or 9100)

        self.db_path = _e("DB_PATH", str(ROOT / "var" / "babylon.db"))
        self.database_url = _e("DATABASE_URL")       # set = Postgres (Supabase); empty = the SQLite file above
        self.db_schema = _e("DB_SCHEMA", "babylon")  # kept out of Supabase's public API schema

        # Logins (AUTH=supabase): the browser signs in with Supabase Auth, the API checks the token.
        self.auth = _e("AUTH", "off")
        self.supabase_url = _e("SUPABASE_URL").rstrip("/")
        self.supabase_anon_key = _e("SUPABASE_ANON_KEY")
        self.supabase_jwt_secret = _e("SUPABASE_JWT_SECRET")  # only for projects still signing with the legacy secret
        self.allowed_emails = {s.strip().lower() for s in _e("ALLOWED_EMAILS").split(",") if s.strip()}
        self.out_dir = Path(_e("OUT_DIR", str(ROOT / "var" / "labels")))
        self.dev_tools = self.shopify_mode == "mock" or _bool("DEV_TOOLS")
        self.poll_seconds = int(_e("POLL_SECONDS", "0") or 0)
        # Free hosts (Render) sleep after 15 idle minutes, which would stop polling. While running, the app
        # calls its own /healthz this often. Render sets RENDER_EXTERNAL_URL by itself.
        self.keepalive_url = (_e("KEEPALIVE_URL") or _e("RENDER_EXTERNAL_URL")).rstrip("/")
        self.keepalive_seconds = int(_e("KEEPALIVE_SECONDS", "600") or 0)
        self.sync_seconds = int(_e("SYNC_SECONDS", "0") or 0)
        # Only for a test store stocked with the mock SKUs: lets mock Odoo counts be pushed to live Shopify
        self.allow_mock_sync = _bool("ALLOW_MOCK_SYNC")
        self._validate()

    CHOICES = {"shopify_mode": ("mock", "live"), "easypost_mode": ("mock", "live"), "printer_mode": ("file", "tcp"),
               "deposco_mode": ("mock", "off", "live"), "odoo_mode": ("mock", "real"), "claim_mode": ("live", "shadow"),
               "notify_mode": ("auto", "on", "off"), "auth": ("off", "supabase")}

    def _validate(self):
        """A typo such as CLAIM_MODE=Live must stop the app, not quietly act like another mode."""
        for attr, allowed in self.CHOICES.items():
            v = getattr(self, attr).lower()
            if v not in allowed:
                raise ValueError(f"{attr.upper()}={getattr(self, attr)!r} is not one of {', '.join(allowed)}")
            setattr(self, attr, v)
        if self.auth == "supabase" and not (self.supabase_url and self.supabase_anon_key and self.allowed_emails):
            raise ValueError("AUTH=supabase needs SUPABASE_URL, SUPABASE_ANON_KEY and ALLOWED_EMAILS "
                             "(addresses, or @domain for a whole domain)")

    def notify_customer(self):
        """auto: email the customer only for real postage (live EasyPost with a production EZAK key).
        Mock and test labels carry tracking numbers that don't exist."""
        if self.notify_mode in ("on", "off"):
            return self.notify_mode == "on"
        return self.easypost_mode == "live" and self.easypost_key.startswith("EZAK")

    def public(self):
        return {
            "shopify": self.shopify_mode, "easypost": self.easypost_mode,
            "printer": self.printer_mode, "deposco": self.deposco_mode,
            "odoo": self.odoo_mode, "claims": self.claim_mode,
            "dev_tools": self.dev_tools, "notify": self.notify_customer(),
            "shop": self.shop if self.shopify_mode == "live" else "",
        }


settings = Settings()
