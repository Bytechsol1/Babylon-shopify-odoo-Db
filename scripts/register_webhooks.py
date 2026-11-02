"""Register the order webhooks so Shopify tells the station about new orders, edits and cancels within
seconds (build plan P0 #3). Polling stays on as the backup. Safe to re-run: existing ones are kept.

    python scripts/register_webhooks.py https://babylon-shopify-odoo-db.onrender.com        # the shop in .env
    python scripts/register_webhooks.py https://... --list                                  # just show them

Shopify signs each delivery with the app's client secret; the station checks it (app/shopify.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402
from app.shopify import LiveShopify, ShopifyError  # noqa: E402

TOPICS = {"ORDERS_CREATE": "create", "ORDERS_UPDATED": "updated", "ORDERS_EDITED": "edited", "ORDERS_CANCELLED": "cancelled"}
Q_LIST = "{ webhookSubscriptions(first: 100) { nodes { id topic uri } } }"
M_CREATE = """mutation($topic: WebhookSubscriptionTopic!, $sub: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $sub) { webhookSubscription { id topic uri } userErrors { field message } } }"""


def main():
    if len(sys.argv) < 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: python scripts/register_webhooks.py https://<station address> [--list]")
    base = sys.argv[1].rstrip("/")
    if settings.shopify_mode != "live":
        sys.exit("SHOPIFY_MODE must be live (the .env decides which store)")
    s = LiveShopify()
    have = {(n["topic"], n["uri"]) for n in s._gql(Q_LIST)["webhookSubscriptions"]["nodes"]}
    print(f"store {settings.shop}: {len(have)} webhook(s) registered")
    for topic, uri in sorted(have):
        print(f"  {topic:<18} {uri}")
    if "--list" in sys.argv:
        return
    for topic, slug in TOPICS.items():
        uri = f"{base}/webhooks/shopify/orders-{slug}"
        if (topic, uri) in have:
            print(f"keep   {topic}")
            continue
        try:
            r = s._gql(M_CREATE, {"topic": topic, "sub": {"uri": uri, "format": "JSON"}})["webhookSubscriptionCreate"]
            s._user_errors(r)
            print(f"added  {topic} -> {uri}")
        except ShopifyError as e:
            print(f"FAILED {topic}: {e}")


if __name__ == "__main__":
    main()
