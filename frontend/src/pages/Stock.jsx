import { useEffect, useRef, useState } from "react";
import { Pencil, Plus } from "lucide-react";
import { api } from "../api.js";
import { ago } from "../lane.js";
import { Card, Empty } from "../components/ui.jsx";
import { Meter } from "../components/charts.jsx";
import { shortName } from "./Dashboard.jsx";

const TYPE_LABEL = { box: "Box", mailer: "Padded mailer", poly: "Poly mailer" };

export default function Stock({ stock, act, tick }) {
  const [editing, setEditing] = useState(null);  // a package being edited, or {} for a new one
  const [overrides, setOverrides] = useState([]);
  useEffect(() => { api("/api/packaging/overrides?limit=50").then(setOverrides).catch(() => {}); }, [tick]);
  if (!stock) return <div className="page"><Empty>Loading…</Empty></div>;
  const top = Math.max(1, ...stock.products.map(p => p.on_hand));
  const packTop = Math.max(1, ...stock.packaging.map(b => b.qty));
  return (
    <div className="page">
      <Card title="Products in Babylon's warehouse" pad={false}>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>SKU</th><th>Product</th><th>Shelf</th><th className="num">On hand</th><th className="num">Held for orders</th><th className="num">Available</th><th className="wide" /></tr></thead>
            <tbody>
              {stock.products.map(p => (
                <tr key={p.sku} className="static">
                  <td className="mono">{p.sku}</td>
                  <td className="strong">{shortName(p.name)}</td>
                  <td className="muted">{p.bin}</td>
                  <td className="num">{p.on_hand}</td>
                  <td className="num">{p.reserved}</td>
                  <td className="num strong">{p.available}{p.available <= 5 && <em className="flag">Low</em>}</td>
                  <td><Meter value={p.available} max={top} warn={p.available <= 5} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Packaging" pad={false} action={<button className="btn sm" onClick={() => setEditing({})}><Plus size={14} /> Add package</button>}>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Code</th><th>Package</th><th>Type</th><th>Inside (in)</th><th className="num">Max lb</th><th className="num">Cost</th><th className="num">In stock</th><th className="num">Reorder at</th><th className="wide" /><th /></tr></thead>
            <tbody>
              {stock.packaging.map(b => (
                <tr key={b.bin} className="static">
                  <td className="mono">{b.bin}</td>
                  <td className="strong">{b.name}</td>
                  <td className="muted">{TYPE_LABEL[b.type] || b.type}</td>
                  <td className="muted">{b.inside.join(" × ")}</td>
                  <td className="num muted">{b.max_lb}</td>
                  <td className="num muted">${Number(b.cost).toFixed(2)}</td>
                  <td className="num">{b.qty}{b.low && <em className="flag">Reorder</em>}</td>
                  <td className="num muted">{b.reorder}</td>
                  <td><Meter value={Math.max(0, b.qty)} max={packTop} warn={b.low} /></td>
                  <td><button className="btn ghost sm" onClick={() => setEditing(b)} aria-label={`Edit ${b.name}`}><Pencil size={13} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Package and weight overrides" pad={false}>
        {overrides.length === 0 ? <Empty>No overrides yet. They show up here for the weekly review.</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>When</th><th>What</th><th>Detail</th><th>Who</th></tr></thead>
              <tbody>
                {overrides.map((e, i) => (
                  <tr key={i} className="static">
                    <td className="muted">{ago(e.ts)}</td>
                    <td>{e.kind === "package_override" ? "Package" : "Weight"}</td>
                    <td className="wrap">{e.detail}</td>
                    <td className="muted">{e.actor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing && <PackageDialog pkg={editing} act={act} onClose={() => setEditing(null)} />}
    </div>
  );
}

function PackageDialog({ pkg, act, onClose }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  const isNew = !pkg.bin;
  const [f, setF] = useState({
    code: pkg.bin || "", name: pkg.name || "", type: pkg.type || "box",
    l: pkg.inside?.[0] ?? "", w: pkg.inside?.[1] ?? "", h: pkg.inside?.[2] ?? "",
    max_lb: pkg.max_lb ?? "", weight_oz: pkg.weight_oz ?? "", cost: pkg.cost ?? "",
    reorder: pkg.reorder ?? 0, qty: pkg.qty ?? 0,
  });
  const set = (k, v) => setF(s => ({ ...s, [k]: v }));
  const submit = e => {
    e.preventDefault();
    act.savePackage(f.code, {
      name: f.name, type: f.type, inside: [f.l, f.w, f.h].map(Number), max_lb: Number(f.max_lb),
      weight_oz: Number(f.weight_oz), cost: Number(f.cost), reorder: Number(f.reorder), start: Number(f.qty),
    }, Number(f.qty), onClose);
  };
  const num = (k, label, step = "0.1") => (
    <label>{label}<input type="number" step={step} min="0" required value={f[k]} onChange={e => set(k, e.target.value)} /></label>
  );
  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose}>
      <form onSubmit={submit}>
        <h2>{isNew ? "Add package" : `Edit ${pkg.name}`}</h2>
        <div className="two-col">
          <label>Code<input required value={f.code} disabled={!isNew} onChange={e => set("code", e.target.value.toUpperCase())} placeholder="BX-12X9X4" /></label>
          <label>Name<input required value={f.name} onChange={e => set("name", e.target.value)} placeholder="Box 12×9×4" /></label>
        </div>
        <label>Type<select value={f.type} onChange={e => set("type", e.target.value)}>
          <option value="box">Box</option><option value="mailer">Padded mailer</option><option value="poly">Poly mailer</option>
        </select></label>
        <p className="field-label">Inside size, inches{f.type !== "box" ? " (length × width × fill limit)" : ""}</p>
        <div className="three-eq">{num("l", "Length")}{num("w", "Width")}{num("h", f.type === "box" ? "Height" : "Fill limit")}</div>
        <div className="three-eq">{num("max_lb", "Max weight (lb)")}{num("weight_oz", "Own weight (oz)")}{num("cost", "Cost each ($)", "0.01")}</div>
        <div className="two-col">{num("qty", "In stock now", "1")}{num("reorder", "Reorder at", "1")}</div>
        <div className="dialog-actions">
          {!isNew && <button type="button" className="btn danger" onClick={() => act.deletePackage(pkg.bin, onClose)}>Remove</button>}
          <span className="spacer" />
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary">Save</button>
        </div>
      </form>
    </dialog>
  );
}
