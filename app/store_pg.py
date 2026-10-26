"""Postgres storage (Supabase). The same functions as store_sqlite, so the rest of the app can't tell them apart.

The tables live in their own schema (DB_SCHEMA, default "babylon"), not "public": Supabase publishes the public
schema through its web API to anyone holding the anon key, which is public by design. Row level security is
switched on as a second barrier; the app connects as the table owner, which bypasses it.

Search uses pg_trgm GIN indexes, so "contains" matches on order number and customer don't scan the table."""
import re
import threading
from datetime import datetime, timezone
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from . import audit
from .config import settings

__all__ = ["now_iso", "get_order", "save_order", "list_orders", "count_by_status", "count_claimed_on",
           "log", "events", "kv_get", "kv_set", "kv_update", "next_seq", "wipe"]

if not re.fullmatch(r"[a-z_][a-z0-9_]*", settings.db_schema):
    raise ValueError(f"DB_SCHEMA={settings.db_schema!r} must be a plain lower-case identifier")
S = settings.db_schema
ORDERS, EVENTS, KV = f"{S}.orders", f"{S}.events", f"{S}.kv"

_pool = None
_init_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _setup(c):
    c.execute(f"CREATE SCHEMA IF NOT EXISTS {S}")
    trgm = c.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                     "WHERE e.extname = 'pg_trgm'").fetchone()
    if not trgm:
        has_ext_schema = c.execute("SELECT 1 FROM pg_namespace WHERE nspname = 'extensions'").fetchone()
        c.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm" + (" WITH SCHEMA extensions" if has_ext_schema else ""))
        trgm = c.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                         "WHERE e.extname = 'pg_trgm'").fetchone()
    ops = f"{trgm[0]}.gin_trgm_ops"
    c.execute(f"""
      CREATE TABLE IF NOT EXISTS {ORDERS}(id text PRIMARY KEY, name text, kind text, status text, updated_at text,
        customer text, created_at text, claimed_day text, data jsonb NOT NULL);
      CREATE TABLE IF NOT EXISTS {EVENTS}(id bigserial PRIMARY KEY, order_id text, ts text, kind text, detail text, actor text);
      CREATE TABLE IF NOT EXISTS {KV}(key text PRIMARY KEY, value jsonb);
      ALTER TABLE {ORDERS} ENABLE ROW LEVEL SECURITY;
      ALTER TABLE {EVENTS} ENABLE ROW LEVEL SECURITY;
      ALTER TABLE {KV} ENABLE ROW LEVEL SECURITY;
      CREATE INDEX IF NOT EXISTS orders_status_created ON {ORDERS}(status, created_at);
      CREATE INDEX IF NOT EXISTS orders_updated ON {ORDERS}(updated_at);
      CREATE INDEX IF NOT EXISTS orders_kind_claimed ON {ORDERS}(kind, claimed_day);
      CREATE INDEX IF NOT EXISTS orders_kind_status ON {ORDERS}(kind, status);
      CREATE INDEX IF NOT EXISTS orders_name_trgm ON {ORDERS} USING gin (name {ops});
      CREATE INDEX IF NOT EXISTS orders_customer_trgm ON {ORDERS} USING gin (customer {ops});
      CREATE INDEX IF NOT EXISTS events_order ON {EVENTS}(order_id, id);
    """)


def _p():
    global _pool
    if _pool is None:
        with _init_lock:
            if _pool is None:
                # prepare_threshold=None: Supabase's pooler can't keep prepared statements across clients
                pool = ConnectionPool(settings.database_url, min_size=1, max_size=8, open=True, timeout=30,
                                      kwargs={"autocommit": True, "prepare_threshold": None})
                with pool.connection() as c:
                    _setup(c)
                _pool = pool
    return _pool


def _one(sql, args=()):
    with _p().connection() as c:
        return c.execute(sql, args).fetchone()


def _all(sql, args=()):
    with _p().connection() as c:
        return c.execute(sql, args).fetchall()


def _run(sql, args=()):
    with _p().connection() as c:
        c.execute(sql, args)


def get_order(oid):
    r = _one(f"SELECT data FROM {ORDERS} WHERE id = %s", (oid,))
    return r[0] if r else None


def save_order(o):
    o["updated_at"] = now_iso()
    a = o.get("address") or {}
    _run(f"""INSERT INTO {ORDERS}(id, name, kind, status, updated_at, customer, created_at, claimed_day, data)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
             ON CONFLICT (id) DO UPDATE SET name = excluded.name, kind = excluded.kind, status = excluded.status,
               updated_at = excluded.updated_at, customer = excluded.customer, created_at = excluded.created_at,
               claimed_day = excluded.claimed_day, data = excluded.data""",
         (o["id"], o["name"], o["kind"], o["status"], o["updated_at"], a.get("name") or "",
          o.get("created_at") or "", o.get("claimed_day") or "", Jsonb(o)))
    return o


def list_orders(statuses=None, kind=None, q=None, limit=None, offset=0, oldest_first=False):
    """Filtered, sorted and paged in SQL. The queue is oldest first (FIFO); everything else newest first."""
    where, args = [], []
    if statuses:
        where.append("status = ANY(%s)")
        args.append(list(statuses))
    if kind:
        where.append("kind = %s")
        args.append(kind)
    if q:
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append("(name ILIKE %s OR customer ILIKE %s)")  # served by the trigram indexes
        args += [like, like]
    sql = f"SELECT data FROM {ORDERS}" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created_at ASC, id ASC" if oldest_first else " ORDER BY updated_at DESC, id DESC"
    if limit:
        sql += " LIMIT %s OFFSET %s"
        args += [int(limit), int(offset)]
    return [r[0] for r in _all(sql, args)]


def count_by_status():
    """{(kind, status): n} in one grouped query."""
    return {(r[0], r[1]): r[2] for r in _all(f"SELECT kind, status, count(*) FROM {ORDERS} GROUP BY kind, status")}


def count_claimed_on(day):
    return _one(f"SELECT count(*) FROM {ORDERS} WHERE kind = 'order' AND claimed_day = %s", (day,))[0]


def log(order_id, kind, detail=""):
    _run(f"INSERT INTO {EVENTS}(order_id, ts, kind, detail, actor) VALUES (%s, %s, %s, %s, %s)",
         (order_id, now_iso(), kind, detail, audit.who()))


def events(order_id, limit=200):
    rows = _all(f"SELECT ts, kind, detail, actor FROM {EVENTS} WHERE order_id = %s ORDER BY id DESC LIMIT %s",
                (order_id, limit))
    return [{"ts": r[0], "kind": r[1], "detail": r[2], "actor": r[3]} for r in reversed(rows)]


def kv_get(key):
    r = _one(f"SELECT value FROM {KV} WHERE key = %s", (key,))
    return r[0] if r else None


def kv_set(key, value):
    _run(f"INSERT INTO {KV}(key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
         (key, Jsonb(value)))


def kv_update(key, fn, default=None):
    """Read, change and write one value in one transaction, with the row locked: no lost updates,
    even across several app servers."""
    with _p().connection() as c, c.transaction():
        c.execute(f"INSERT INTO {KV}(key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, Jsonb(default)))
        current = c.execute(f"SELECT value FROM {KV} WHERE key = %s FOR UPDATE", (key,)).fetchone()[0]
        value = fn(default if current is None else current)
        c.execute(f"UPDATE {KV} SET value = %s WHERE key = %s", (Jsonb(value), key))
        return value


def next_seq(name):
    return kv_update(f"seq:{name}", lambda n: n + 1, 0)


def wipe():
    _run(f"TRUNCATE {ORDERS}, {EVENTS}, {KV}")
