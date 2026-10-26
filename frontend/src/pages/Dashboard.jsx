import { ArrowRight } from "lucide-react";
import { ago, units } from "../lane.js";
import { Card, Empty, RouteChip, StatusChip } from "../components/ui.jsx";
import { BarList, SERIES, SplitBar } from "../components/charts.jsx";

const STAGES = [["queued", "To pick"], ["picking", "Picking"], ["packed", "Packed"], ["labeled", "Labeled"], ["shipped", "Shipped"]];

// Everything here comes from /api/summary: counts grouped in SQL, never the order list itself.
export default function Dashboard({ summary, stock, open, go }) {
  if (!summary) return <div className="page"><Empty>Loading…</Empty></div>;
  const n = s => summary.counts[s] || 0;
  const { babylon, ufsi, shadow } = summary.routing;
  const low = summary.low_stock;

  return (
    <div className="page">
      <section className="kpis card">
        <Kpi label="To pack" value={summary.to_pack} hint={summary.to_pack ? "waiting at the station" : "nothing waiting"} onClick={() => go("pack")} />
        <Kpi label="Shipped by Babylon" value={n("shipped")} hint={`${summary.samples_shipped} of them samples`} />
        <Kpi label="Kept by UFSI" value={ufsi} hint="not eligible, or handed back" />
        {shadow > 0 && <Kpi label="Would claim (shadow)" value={shadow} hint="read-only test" />}
        <Kpi label="Low stock" value={low.length} hint={low.length ? low.join(", ") : "all products fine"} tone={low.length ? "warn" : ""} onClick={() => go("stock")} />
      </section>

      <div className="grid-3">
        <Card title="Where orders went">
          <SplitBar parts={[
            { label: "Babylon", value: babylon + shadow, color: SERIES.babylon },
            { label: "UFSI", value: ufsi, color: SERIES.ufsi },
          ]} />
          <p className="note">Babylon only takes an order it can ship completely. Everything else stays with UFSI.</p>
        </Card>
        <Card title="Order pipeline">
          <BarList rows={STAGES.map(([s, label]) => ({ label, value: n(s) }))} unit="orders" />
        </Card>
        <Card title="Available stock" action={<button className="link" onClick={() => go("stock")}>View all <ArrowRight size={13} /></button>}>
          {stock ? <BarList rows={stock.products.slice(0, 6).map(p => ({ label: shortName(p.name), value: p.available, warn: p.available <= 5, note: `${p.reserved} held` }))} unit="available" />
                 : <Empty>Loading…</Empty>}
        </Card>
      </div>

      <Card title="Recent orders" pad={false} action={<button className="link" onClick={() => go("orders")}>View all <ArrowRight size={13} /></button>}>
        <OrderTable orders={summary.recent} open={open} />
      </Card>
    </div>
  );
}

function Kpi({ label, value, hint, tone = "", onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className={"kpi " + tone} onClick={onClick}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-hint">{hint}</span>
    </Tag>
  );
}

export const shortName = n => n.replace(/\s*\(.*\)\s*$/, "");

export function OrderTable({ orders, open, empty = "No orders yet. Use “New test order” at the top right." }) {
  if (!orders.length) return <Empty>{empty}</Empty>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>Order</th><th>Customer</th><th>Ships to</th><th className="num">Items</th><th>Shipped by</th><th>Status</th><th>Updated</th></tr></thead>
        <tbody>
          {orders.map(o => (
            <tr key={o.id} onClick={() => open(o.id)} tabIndex={0} onKeyDown={e => e.key === "Enter" && open(o.id)}>
              <td className="strong">{o.name}</td>
              <td>{o.address.name}</td>
              <td className="muted">{[o.address.city, o.address.state].filter(Boolean).join(", ") || "—"}{o.address.country && o.address.country !== "US" ? ` · ${o.address.country}` : ""}</td>
              <td className="num">{units(o)}</td>
              <td><RouteChip o={o} /></td>
              <td><StatusChip status={o.status} /></td>
              <td className="muted">{ago(o.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
