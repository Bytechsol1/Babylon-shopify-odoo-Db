"""SQLite storage. Each order is one row: a JSON document plus the columns the app filters, sorts and
counts by, all indexed, so lists, counts and searches run in SQL instead of loading every order."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from .config import settings

LOCK = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(id TEXT PRIMARY KEY, name TEXT, kind TEXT, status TEXT, updated_at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, ts TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""
# columns added after the first release; older databases get them (and a one-off backfill) on open
EXTRA_COLUMNS = {"customer": "TEXT", "created_at": "TEXT", "claimed_day": "TEXT"}
INDEXES = """
CREATE INDEX IF NOT EXISTS orders_status_created ON orders(status, created_at);
CREATE INDEX IF NOT EXISTS orders_updated ON orders(updated_at);
CREATE INDEX IF NOT EXISTS orders_kind_claimed ON orders(kind, claimed_day);
CREATE INDEX IF NOT EXISTS orders_kind_status ON orders(kind, status);
CREATE INDEX IF NOT EXISTS events_order ON events(order_id, id);
"""
# Substring search on order number and customer without scanning: an FTS5 trigram index kept in step
# with the orders table by triggers. Searches shorter than 3 characters fall back to LIKE.
SEARCH = """
CREATE VIRTUAL TABLE IF NOT EXISTS orders_search USING fts5(name, customer, content='orders', content_rowid='rowid', tokenize='trigram');
CREATE TRIGGER IF NOT EXISTS orders_search_ai AFTER INSERT ON orders BEGIN
  INSERT INTO orders_search(rowid, name, customer) VALUES (new.rowid, new.name, new.customer); END;
CREATE TRIGGER IF NOT EXISTS orders_search_ad AFTER DELETE ON orders BEGIN
  INSERT INTO orders_search(orders_search, rowid, name, customer) VALUES ('delete', old.rowid, old.name, old.customer); END;
CREATE TRIGGER IF NOT EXISTS orders_search_au AFTER UPDATE ON orders BEGIN
  INSERT INTO orders_search(orders_search, rowid, name, customer) VALUES ('delete', old.rowid, old.name, old.customer);
  INSERT INTO orders_search(rowid, name, customer) VALUES (new.rowid, new.name, new.customer); END;
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _columns(o):
    return {"name": o["name"], "kind": o["kind"], "status": o["status"], "updated_at": o.get("updated_at") or "",
            "customer": (o.get("address") or {}).get("name") or "", "created_at": o.get("created_at") or "",
            "claimed_day": o.get("claimed_day") or ""}


def _migrate(c):
    have = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
    missing = [k for k in EXTRA_COLUMNS if k not in have]
    for k in missing:
        c.execute(f"ALTER TABLE orders ADD COLUMN {k} {EXTRA_COLUMNS[k]}")
    if missing:
        for r in c.execute("SELECT data FROM orders").fetchall():
            o = json.loads(r[0])
            cols = _columns(o)
            c.execute("UPDATE orders SET customer=?, created_at=?, claimed_day=? WHERE id=?",
                      (cols["customer"], cols["created_at"], cols["claimed_day"], o["id"]))
    c.executescript(INDEXES)
    new_search = not c.execute("SELECT 1 FROM sqlite_master WHERE name='orders_search'").fetchone()
    c.executescript(SEARCH)
    if new_search:
        c.execute("INSERT INTO orders_search(orders_search) VALUES ('rebuild')")  # index the orders already there


def conn():
    global _conn
    if _conn is None:
        with LOCK:
            if _conn is None:
                Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
                c = sqlite3.connect(settings.db_path, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
                c.execute("PRAGMA synchronous=NORMAL")
                c.executescript(SCHEMA)
                _migrate(c)
                c.commit()
                _conn = c
    return _conn


def get_order(oid):
    with LOCK:
        r = conn().execute("SELECT data FROM orders WHERE id=?", (oid,)).fetchone()
    return json.loads(r["data"]) if r else None


def save_order(o):
    o["updated_at"] = now_iso()
    cols = _columns(o)
    with LOCK:
        conn().execute(
            "INSERT INTO orders(id,name,kind,status,updated_at,customer,created_at,claimed_day,data) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,status=excluded.status,"
            "updated_at=excluded.updated_at,customer=excluded.customer,created_at=excluded.created_at,"
            "claimed_day=excluded.claimed_day,data=excluded.data",
            (o["id"], cols["name"], cols["kind"], cols["status"], cols["updated_at"], cols["customer"],
             cols["created_at"], cols["claimed_day"], json.dumps(o)))
        conn().commit()
    return o


def list_orders(statuses=None, kind=None, q=None, limit=None, offset=0, oldest_first=False):
    """Filtered, sorted and paged in SQL. The queue is oldest first (FIFO); everything else newest first."""
    where, args = [], []
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        args += list(statuses)
    if kind:
        where.append("kind=?")
        args.append(kind)
    if q and len(q) >= 3:  # trigram index: any substring of 3+ characters, found without a scan
        where.append("rowid IN (SELECT rowid FROM orders_search WHERE orders_search MATCH ?)")
        args.append('"' + q.replace('"', '""') + '"')
    elif q:
        where.append("(name LIKE ? ESCAPE '\\' OR customer LIKE ? ESCAPE '\\')")
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args += [like, like]
    sql = "SELECT data FROM orders" + (" WHERE " + " AND ".join(where) if where else "")
    sql += " ORDER BY created_at ASC, rowid ASC" if oldest_first else " ORDER BY updated_at DESC, rowid DESC"
    if limit:
        sql += " LIMIT ? OFFSET ?"
        args += [int(limit), int(offset)]
    with LOCK:
        rows = conn().execute(sql, args).fetchall()
    return [json.loads(r["data"]) for r in rows]


def count_by_status():
    """{(kind, status): n} in one grouped query."""
    with LOCK:
        rows = conn().execute("SELECT kind, status, COUNT(*) n FROM orders GROUP BY kind, status").fetchall()
    return {(r["kind"], r["status"]): r["n"] for r in rows}


def count_claimed_on(day):
    with LOCK:
        return conn().execute("SELECT COUNT(*) FROM orders WHERE kind='order' AND claimed_day=?", (day,)).fetchone()[0]


def log(order_id, kind, detail=""):
    with LOCK:
        conn().execute("INSERT INTO events(order_id,ts,kind,detail) VALUES(?,?,?,?)",
                       (order_id, now_iso(), kind, detail))
        conn().commit()


def events(order_id, limit=200):
    with LOCK:
        rows = conn().execute("SELECT ts,kind,detail FROM events WHERE order_id=? ORDER BY id DESC LIMIT ?",
                              (order_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def kv_get(key):
    with LOCK:
        r = conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else None


def kv_set(key, value):
    with LOCK:
        conn().execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))
        conn().commit()


def kv_update(key, fn, default=None):
    """Read, change and write one value as a single step, so two threads can't lose each other's update."""
    with LOCK:
        current = kv_get(key)
        value = fn(default if current is None else current)
        kv_set(key, value)
        return value


def next_seq(name):
    return kv_update(f"seq:{name}", lambda n: n + 1, 0)


def wipe():
    with LOCK:
        for t in ("orders", "events", "kv"):
            conn().execute(f"DELETE FROM {t}")
        conn().commit()
