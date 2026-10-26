import os
import tempfile

_d = tempfile.mkdtemp()
os.environ.update(BABYLON_NO_DOTENV="1", DB_PATH=f"{_d}/t.db", OUT_DIR=f"{_d}/labels", SHOPIFY_MODE="mock", EASYPOST_MODE="mock",
                  PRINTER_MODE="file", DEPOSCO_MODE="mock", CLAIM_MODE="live", SKU_ALLOWLIST="", DAILY_CAP="0", AUTH="off")
# TEST_DATABASE_URL=postgresql://... runs the whole suite on Postgres (e.g. Supabase), in its own schema,
# which every test wipes. Without it the tests use a throwaway SQLite file.
os.environ.pop("DATABASE_URL", None)
if os.environ.get("TEST_DATABASE_URL"):
    os.environ.update(DATABASE_URL=os.environ["TEST_DATABASE_URL"], DB_SCHEMA="babylon_test")

import pytest  # noqa: E402
from app import clock, db, packaging  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import odoo, shopify, deposco  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    db.wipe()
    odoo.reset()
    shopify.reset()
    deposco.reset()
    packaging.reset()
    clock.override_minute = 10  # 50 min to the import: margin is fine
    settings.claim_mode = "live"
    settings.daily_cap = 0
    settings.sku_allowlist = set()
    yield
    clock.override_minute = None
