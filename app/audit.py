"""Who is acting right now. The login middleware sets it per request; background work (polling,
webhooks) leaves it empty and is recorded as "system"."""
from contextvars import ContextVar

actor: ContextVar = ContextVar("actor", default=None)


def who():
    return actor.get() or "system"
