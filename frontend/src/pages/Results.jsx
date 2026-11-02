import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, Empty } from "../components/ui.jsx";
import { BarList, SERIES } from "../components/charts.jsx";

const KEPT = { stock: "Not enough stock", clean: "Not paid / on hold", domestic: "Not a US order", margin: "Too close to UFSI's import",
  single: "Split across warehouses", unrequested: "Already sent to UFSI", allowlist: "Product not on the list",
  cap: "Daily cap reached", package: "No package fits", complete: "Too many lines" };

// Is the lane worth running? (build plan P1 #5)
export default function Results({ tick }) {
  const [days, setDays] = useState(30);
  const [r, setR] = useState(null);
  useEffect(() => { api(`/api/results?days=${days}`).then(setR).catch(() => {}); }, [days, tick]);
  if (!r) return <div className="page"><Empty>Loading…</Empty></div>;
  const faster = r.median_ship_hours != null ? r.ufsi_baseline_hours - r.median_ship_hours : null;
  return (
    <div className="page">
      <div className="pack-bar">
        <div className="seg-tabs" role="tablist">
          {[7, 30, 90].map(d => <button key={d} role="tab" aria-selected={days === d} onClick={() => setDays(d)}>Last {d} days</button>)}
        </div>
        <span className="muted">{r.orders_seen} Shopify orders seen</span>
      </div>
      <section className="kpis card">
        <Kpi label="Checkout to ship (median)" value={r.median_ship_hours == null ? "—" : r.median_ship_hours < 1 ? `${Math.round(r.median_ship_hours * 60)} min` : `${r.median_ship_hours} h`}
             hint={faster != null ? `${faster >= 0 ? faster.toFixed(1) + " h faster" : (-faster).toFixed(1) + " h slower"} than UFSI's ${r.ufsi_baseline_hours} h` : `UFSI: ${r.ufsi_baseline_hours} h`} />
        <Kpi label="Claimed of eligible" value={`${r.claimed} / ${r.eligible}`} hint={r.eligible ? `${Math.round(100 * r.claimed / r.eligible)}% of the orders Babylon could take` : "no eligible orders yet"} />
        <Kpi label="Shipped by Babylon" value={r.shipped} hint="in this period" />
        <Kpi label="Packaging per order" value={r.avg_packaging_cost != null ? `$${r.avg_packaging_cost.toFixed(2)}` : "—"} hint="average, shipped orders" />
        <Kpi label="Postage per order" value={r.avg_postage != null ? `$${r.avg_postage.toFixed(2)}` : "—"} hint="average label cost" />
      </section>
      <div className="grid-3 two">
        <Card title="Why claimed orders were handed back">
          {r.release_reasons.length ? <BarList rows={r.release_reasons.map(([k, n]) => ({ label: k, value: n }))} color={SERIES.ufsi} unit="orders" />
                                    : <p className="muted" style={{ margin: 0 }}>None handed back.</p>}
        </Card>
        <Card title="Why UFSI kept orders">
          {r.kept_by_ufsi_reasons.length ? <BarList rows={r.kept_by_ufsi_reasons.map(([k, n]) => ({ label: KEPT[k] || k, value: n }))} color={SERIES.ufsi} unit="orders" />
                                         : <p className="muted" style={{ margin: 0 }}>Nothing kept.</p>}
        </Card>
      </div>
    </div>
  );
}

function Kpi({ label, value, hint }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-hint">{hint}</span>
    </div>
  );
}
