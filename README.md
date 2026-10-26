# Babylon lane POC

Proof of concept for the Babylon fulfillment lane described in the Order-to-Ship Flow Simulator:
Shopify orders that Babylon can fill completely are claimed, picked, packed, labeled and fulfilled from
San Jose, with a live check at the label so an order never ships twice. Samples go through the same
station without ever becoming Shopify orders.

**What is real and what is mocked in this POC**

| Piece | POC default | Real mode |
|---|---|---|
| Odoo stock, lots, deliveries | Mock with dummy data (`app/odoo/mock.py`) | `app/odoo/real.py` is a skeleton with the Odoo model mapping. Needs Odoo access. |
| Shopify | Mock store, runs offline | `SHOPIFY_MODE=live` uses the Admin GraphQL API (see below) |
| EasyPost | Fake tracking and our own ZPL | `EASYPOST_MODE=live` with an EasyPost **test** key |
| Zebra printer | Writes `var/labels/<order>.zpl` | `PRINTER_MODE=tcp` sends raw ZPL to `PRINTER_HOST:9100` |
| Deposco (UFSI) | Mock lookup | Stub. Waiting on UFSI for API or read access. |
| Scale | Type the weight, or "Read scale" (WebHID, Chrome or Edge) | Same |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000                       # one worker: the per-order locks live in this process
# open http://localhost:8000
pytest                                                  # 79 tests
```

The screen is a React app in `frontend/`. Its build is already in `app/static`, so running the station needs only
Python. To change the screen, install Node and run:

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, live reload, talks to the Python server on :8000
npm run build    # writes the finished screen into app/static
```

Everything starts in mock mode. The left column has **Test controls** that create orders in the mock
Shopify store and fire the same code paths the real webhooks use.

## Demo walk-through (maps to the simulator scenarios)

1. **Babylon ships it.** "New order: serum and lip gloss". It is claimed, then scan `L2607-A` and `L2609-L`,
   scan `BX-S`, set a weight, buy the label, close. Tracking lands in the mock Shopify (open "What Shopify holds").
2. **Margin rule.** "Pretend it is :58", then create an order. It stays with UFSI because it is inside the 3-minute
   margin before the hourly import. "Use the real clock" to undo.
3. **Not in Babylon's stock.** "3 clay masks" (Babylon has 2) and "ships to Canada" both stay with UFSI. See *Not ours*.
4. **Bundles.** "Glow Kit" splits into serum, cream and gloss for the pick list.
5. **Stock short at pick.** Press *Short* on a line. The order goes back to UFSI.
6. **Late cancel.** Claim an order, press "Customer cancels". It leaves the pick list and the stock hold is released.
7. **Live check backstop.** Pack an order, press "Bright Socket imports the selected order", then buy the label.
   The label is refused and the screen says why. *Stand down* hands it to UFSI.
8. **Edit.** "Support adds 5 clay masks" re-runs eligibility and releases the order to UFSI.
9. **Sample.** "New sample shipment". It never touches Shopify.
10. **Stock sync.** "Sync Odoo stock to Shopify" pushes available quantities (expired lots excluded).

Bad lots to try at the scanner: `L2504-X` is expired. Scanning a later-expiry lot first gives a first-expiry-first-out warning.

## Going live on a Shopify store

1. Create a custom app. Scopes to start with (confirm against what the store allows):
   `read_orders`, `read_products`, `read_locations`, `read_inventory`, `write_inventory`,
   `read_merchant_managed_fulfillment_orders`, `write_merchant_managed_fulfillment_orders`,
   plus the third-party fulfillment order scopes if UFSI is a fulfillment-service location.
2. Make sure the **Babylon location** exists, and that every Babylon SKU is stocked at it (inventory activated).
   Odoo `default_code` must equal the Shopify SKU.
3. Copy `.env.example` to `.env`: set `SHOPIFY_MODE=live`, shop, token, API version, both location IDs
   (`gid://shopify/Location/...`), and the webhook secret.
   Set `DEPOSCO_MODE=off` until a Deposco adapter exists. The app refuses to start with live Shopify and mock Deposco,
   because the mock would pass every real order. With `off`, every label says Deposco was not checked.
4. Start with `CLAIM_MODE=shadow`. The app logs what it would claim and changes nothing (see the *Not ours* tab).
   Shadow mode checks the eligibility decisions. It can't answer the Bright Socket question, because nothing is ever
   assigned to Babylon. For that, switch to `CLAIM_MODE=live` with `DAILY_CAP=1` (or `SKU_ALLOWLIST`) and check
   whether the claimed orders show up in Deposco.
5. Webhooks (`orders/create`, `orders/edited`, `orders/cancelled`) need a public HTTPS URL
   (`/webhooks/shopify/orders-create` and so on). Without one, set `POLL_SECONDS=60` and new orders are picked up
   by polling. The live check at the label still catches cancels and edits.
6. Put `SYNC_SECONDS=300` to mirror Odoo stock into the Babylon location every 5 minutes.

## Built for volume

Measured on a laptop with 100,000 orders and 300,000 history entries:

| Operation | Time |
|---|---|
| Claim a new order (eligibility, reserve, save) | 0.2 ms |
| Pack queue, an orders page, one order, its history | under 4 ms |
| Dashboard summary | 10 ms |
| Search by order number or customer (trigram index) | 1–50 ms |

How it stays fast:
- Every order operation touches one order, its lines and its SKUs. Lists, counts and searches are indexed SQL
  (`app/db.py`); the screen asks for one page or one summary, never the whole table.
- Polling asks Shopify only for orders updated since the last poll (all pages), and fetches an order only when
  its `updatedAt` moved. If any order in the window fails, the window is retried next poll, so nothing is lost.
- Stock questions use indexes in the Odoo adapter (lots by SKU, running reservation totals); the whole catalog
  is one pass. Stock sync looks SKUs up 50 per Shopify call.
- Order locks come from a fixed pool (bounded memory); webhooks run on a fixed pool of 8 workers.
- **Run one uvicorn worker.** The locks live in the process. Several workers would need a shared lock (for
  example Postgres advisory locks) before they could share the work.

## Not verified yet (read before showing a client)

- **Live Shopify calls were written to the documented GraphQL API but not run against a real store.** This build
  environment has no access to one. Field names such as `risk` differ between API versions (the code retries without it).
  Run shadow mode first.
- **Mock EasyPost only.** The live path is written but untested. Use a test key. Buying is two steps (create the
  shipment and save its id, then buy only if EasyPost says it has no postage), so a lost response can't buy twice.
- **Customer emails.** `NOTIFY_CUSTOMER=auto` emails tracking only with a production EasyPost key (`EZAK`). Mock and
  test labels post tracking to Shopify silently, because those tracking numbers don't exist.
- **Orders already sent to a fulfillment service, or split across locations, stay with UFSI.** If UFSI is a fulfillment
  service and the store requests fulfillment the moment an order arrives, shadow mode will show every order
  failing the "not yet sent to a fulfillment service" check. That tells us claiming has to happen earlier, or differently.
- **Cancel after the label.** Close re-reads Shopify before moving stock, and a cancelled order can only be voided
  (EasyPost refund plus stock released).
- **Scale report format** in the WebHID code is based on the common DYMO layout. Confirm on the bench.
- **Package rules** are the simulator's rules turned into code. Mailer thickness and item-length limits are guesses
  in `app/packaging.py`. Every override is logged so they can be tuned from real packs.
- **Deposco** has no live adapter. Until one exists the live check **fails closed** (no label) when Deposco is
  unreachable, which is the safe default but needs a decision before Phase 3.
- **Not built:** pick waves (the queue is the wave), bin-location scanning, multi-parcel orders, returns, user logins.
- The mock Odoo has no real warehouse model. Reserve, lot and delivery behavior is what the real adapter must match.

## Layout

```
app/pipeline.py      claim, pick, pack, live check, label, close, cancel, edit
app/eligibility.py   the simulator's eligibility check
app/packaging.py     package suggestion and packaging stock
app/shopify.py       MockShopify and LiveShopify (same interface)
app/odoo/            mock.py (dummy data) and real.py (skeleton)
app/shipping.py      mock labels and EasyPost
app/zpl.py           ZPL builder and raw port-9100 print
app/main.py          API, webhooks, test controls
frontend/            pack station screen (React). `npm run build` writes it into app/static
tests/               79 tests: every scenario above, plus concurrency, polling and scale (tests/test_qa.py)
```
