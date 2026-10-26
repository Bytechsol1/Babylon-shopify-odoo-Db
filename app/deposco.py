"""UFSI's Deposco lookup: 'is this Shopify order already in UFSI's system?'
Deposco order numbers look like '<shopify number>--<suffix>', so we match on the prefix.
The live adapter is a stub until UFSI gives us an API or read access."""
from .db import kv_get, kv_set


class DeposcoUnavailable(Exception):
    pass


class MockDeposco:
    def __init__(self):
        self.orders = set(kv_get("deposco") or [])

    def reset(self):
        self.orders = set()
        kv_set("deposco", [])

    def add(self, number):
        self.orders.add(number.lstrip("#"))
        kv_set("deposco", sorted(self.orders))

    def exists(self, order_name):
        n = order_name.lstrip("#")
        return any(o == n or o.startswith(n + "--") for o in self.orders)


class SkippedDeposco:
    """DEPOSCO_MODE=off: no lookup at all. The live check says so on every label."""
    skipped = True

    def exists(self, order_name):
        return False


class LiveDeposco:
    def exists(self, order_name):
        raise DeposcoUnavailable("Deposco access is not configured yet. Waiting on UFSI.")
