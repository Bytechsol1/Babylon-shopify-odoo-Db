"""One-off: copy a local SQLite station database (orders, history, mock Odoo stock, counters) into Postgres.

    python scripts/copy_sqlite_to_postgres.py var/test-store.db "postgresql://..." [schema]

Run it once when moving to Supabase, with the local server stopped so nothing changes mid-copy.
Refuses to write into a schema that already has orders."""
import json
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

src, url = sys.argv[1], sys.argv[2]
schema = sys.argv[3] if len(sys.argv) > 3 else "babylon"
if not Path(src).exists():
    sys.exit(f"{src} not found")

# create the schema, tables and indexes exactly as the app does
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os  # noqa: E402
os.environ.update(BABYLON_NO_DOTENV="1", DATABASE_URL=url, DB_SCHEMA=schema)
from app import store_pg  # noqa: E402
store_pg._p()

s = sqlite3.connect(src)
orders = s.execute("SELECT data FROM orders ORDER BY rowid").fetchall()
events = s.execute("SELECT order_id, ts, kind, detail FROM events ORDER BY id").fetchall()
kv = s.execute("SELECT key, value FROM kv").fetchall()

with psycopg.connect(url, prepare_threshold=None) as c:
    if c.execute(f"SELECT count(*) FROM {schema}.orders").fetchone()[0]:
        sys.exit(f"{schema}.orders already has rows; not copying over them")
    with c.cursor() as cur:
        for (data,) in orders:
            o = json.loads(data)
            a = o.get("address") or {}
            cur.execute(f"""INSERT INTO {schema}.orders(id, name, kind, status, updated_at, customer, created_at, claimed_day, data)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (o["id"], o["name"], o["kind"], o["status"], o.get("updated_at") or "", a.get("name") or "",
                         o.get("created_at") or "", o.get("claimed_day") or "", Jsonb(o)))
        cur.executemany(f"INSERT INTO {schema}.events(order_id, ts, kind, detail, actor) VALUES (%s, %s, %s, %s, 'system')", events)
        cur.executemany(f"INSERT INTO {schema}.kv(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                        [(k, Jsonb(json.loads(v))) for k, v in kv])
    c.commit()
print(f"copied {len(orders)} orders, {len(events)} history entries and {len(kv)} settings into {schema}")
