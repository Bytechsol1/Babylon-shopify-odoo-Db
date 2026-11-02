"""Facts derived from an order that storage keeps as indexed columns (both SQLite and Postgres)."""
from .config import settings

EXCEPTION_STATUSES = ("released", "claim_failed")


def exception_of(o):
    """The open exception's reason, or "" when there is none. An exception is anything a person must act on:
    an order handed back or failed to claim, a flag on an active order, or tracking Shopify rejected.
    Resolving it (resolve_exception) closes it."""
    if o.get("exception_resolved"):
        return ""
    if o["status"] in EXCEPTION_STATUSES:
        return o.get("release_reason") or ("Claim failed" if o["status"] == "claim_failed" else "Handed back to UFSI")
    if o.get("writeback_error"):
        return f"Shopify rejected the tracking: {o['writeback_error']}"
    if o.get("attention"):
        return o["attention"]
    return ""


def client_of(o):
    return o.get("client_id") or settings.client_id
