import { Card, Empty } from "../components/ui.jsx";
import { Meter } from "../components/charts.jsx";
import { shortName } from "./Dashboard.jsx";

export default function Stock({ stock }) {
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
      <Card title="Packaging" pad={false}>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Code</th><th>Package</th><th>Inside (in)</th><th className="num">In stock</th><th className="num">Reorder at</th><th className="wide" /></tr></thead>
            <tbody>
              {stock.packaging.map(b => (
                <tr key={b.bin} className="static">
                  <td className="mono">{b.bin}</td>
                  <td className="strong">{b.name}</td>
                  <td className="muted">{b.inside.join(" × ")}</td>
                  <td className="num">{b.qty}{b.low && <em className="flag">Reorder</em>}</td>
                  <td className="num muted">{b.reorder}</td>
                  <td><Meter value={b.qty} max={packTop} warn={b.low} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
