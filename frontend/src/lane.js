// What the station knows about an order, in plain words: which step it is on,
// what the packer should do next, and why each step exists.

export const PRESETS = {
  glass: { items: [{ sku: "BAB-SER-30", qty: 1 }, { sku: "BAB-LIP-01", qty: 1 }] },
  kit: { items: [{ sku: "BAB-KIT-01", qty: 1 }] },
  mailer: { items: [{ sku: "BAB-MST-60", qty: 1 }] },
  short: { items: [{ sku: "BAB-MSK-01", qty: 3 }] },
  intl: { items: [{ sku: "BAB-LIP-01", qty: 1 }], country: "CA" },
};

export const STATUS = {
  queued: ["To pick", "info"], picking: ["Picking", "info"], packed: ["Packed", "warn"], labeled: ["Label printed", "warn"],
  shipped: ["Shipped", "ok"], released: ["Handed to UFSI", "bad"], cancelled: ["Cancelled", "bad"],
  ufsi: ["UFSI ships", "bad"], shadow: ["Would claim", "ok"], claim_failed: ["Claim failed", "bad"],
};

export const ACTIVE = ["queued", "picking", "packed", "labeled"];
export const NOT_OURS = ["ufsi", "shadow", "claim_failed"];
export const DONE = ["shipped", "released", "cancelled"];

// Who ends up shipping it. "released" was Babylon's until it handed the order back.
export function routeOf(o) {
  if (o.kind === "sample") return "Sample";
  if (o.status === "shadow") return "Babylon (shadow)";
  if (o.status === "released" || NOT_OURS.includes(o.status)) return "UFSI";
  return "Babylon";
}

export const SCENARIOS = [
  { key: "glass", title: "Normal order", tip: "Serum and lip gloss", who: "babylon" },
  { key: "kit", title: "Gift kit", tip: "A bundle of 3 items", who: "babylon" },
  { key: "mailer", title: "Small order", tip: "One face mist", who: "babylon" },
  { key: "short", title: "Not enough stock", tip: "3 clay masks, Babylon has 2", who: "ufsi" },
  { key: "intl", title: "Ships abroad", tip: "Going to Canada", who: "ufsi" },
  { key: "late", title: "Too late in the hour", tip: "Arrives at :58", who: "ufsi" },
];

export function ago(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

// list rows carry `units`; a full order has its lines
export const units = o => o.units ?? o.lines.reduce((a, l) => a + l.qty, 0);

// plain-language reason for each failed check; falls back to the server's text
const FAIL = {
  stock: "Babylon doesn't have every item in stock", clean: "It isn't paid, or it's on hold or flagged",
  domestic: "It isn't a US web order", margin: "It arrived too close to UFSI's hourly import",
  single: "Shopify split it across warehouses", unrequested: "It was already sent to UFSI",
  allowlist: "It has a product Babylon doesn't ship yet", cap: "Babylon hit today's order limit",
  cancel: "The customer cancelled it", deposco: "UFSI's system already has it",
  assigned: "Shopify moved it back to UFSI", hold: "It was put on hold",
  unchanged: "The items or address changed after picking",
};
export const reason = c => (c.key === "deposco" && /Could not/.test(c.text)) ? c.text : (FAIL[c.key] || c.text);
export const failedReasons = checks => (checks || []).filter(c => !c.ok).map(reason);

// 0 pick, 1 package, 2 weigh, 3 label, 4 ship, 5 shipped
export function stepOf(o) {
  const p = o.package || {};
  return !o.pick_done ? 0 : !p.scanned ? 1 : !o.weight_oz ? 2 : !o.label ? 3 : o.status !== "shipped" ? 4 : 5;
}

export const refused = o => !!(o.live_check && !o.live_check.ok && !o.label);

// The big banner: what to do now and why.
export function banner(o) {
  const why = list => failedReasons(list).join(". ");
  if (o.status === "shipped") return { tone: "ok", title: "Shipped", text: `Tracking ${o.label.tracking}` };
  if (o.status === "cancelled") return { tone: "bad", title: "Cancelled", text: "Put the items back on the shelf." };
  if (o.status === "released") return { tone: "bad", title: "Handed back to UFSI", text: "Put the items back on the shelf." };
  if (o.status === "ufsi") return { tone: "warn", title: "UFSI ships this one", text: why(o.checks) + "." };
  if (o.status === "shadow") return { tone: "ok", title: "Babylon could ship this", text: "Test mode. Nothing was changed." };
  if (o.status === "claim_failed") return { tone: "warn", title: "UFSI keeps this one", text: "Shopify would not hand it over." };
  if (o.cancelled && o.status === "labeled") return { tone: "bad", title: "Cancelled. Do not ship.", text: "Void the label to get the postage back." };
  if (refused(o)) return { tone: "bad", title: "Stop. Do not ship.", text: why(o.live_check.checks) + "." };
  return [
    { tone: "go", title: "Pick the items", text: "Scan each item." },
    { tone: "go", title: "Choose the package", text: "Use the suggested one." },
    { tone: "go", title: "Weigh the parcel", text: "Confirm the weight." },
    { tone: "go", title: "Buy the label", text: "The app checks the order one last time first." },
    { tone: "go", title: "Ship it", text: "Stick the label on, then mark it shipped." },
  ][stepOf(o)];
}

// One sentence on why this step exists, shown behind "Why?".
export function whyStep(o) {
  if (NOT_OURS.includes(o.status)) return "Babylon only takes orders it can ship completely. Everything else stays with UFSI, exactly as today.";
  if (o.status === "shipped") return null;
  if (o.status === "released") return "Babylon let go before buying a label, so UFSI ships it and the customer still gets one parcel.";
  if (o.status === "cancelled") return "The stock Babylon was holding for this order is free again.";
  if (o.cancelled) return "Voiding refunds the postage, so the cancelled order costs nothing.";
  if (refused(o)) return "This is the safety net: right before paying for postage, the app re-checks the order. That's how nothing ships twice.";
  return [
    "Each barcode is a batch with an expiry date. Scanning records which batch went to which customer.",
    "The app picks the cheapest package that keeps the items safe. Glass always goes in a box.",
    "Postage is priced by weight. The number is an estimate, so confirm it or use the scale.",
    "Before paying, the app checks the order is still Babylon's, not cancelled and not changed.",
    "This takes the stock out of Odoo and gives Shopify the tracking number.",
  ][stepOf(o)];
}

