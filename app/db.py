"""Storage. Postgres (Supabase) when DATABASE_URL is set, else a local SQLite file.
Both modules offer the same functions; the rest of the app only ever imports this one."""
from .config import settings

if settings.database_url:
    from .store_pg import *  # noqa: F401,F403
else:
    from .store_sqlite import *  # noqa: F401,F403
