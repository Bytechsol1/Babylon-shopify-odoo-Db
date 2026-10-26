import { useEffect, useRef, useState } from "react";
import { Gift } from "lucide-react";

const FIELDS = [["name", "Recipient"], ["address1", "Street address"]];

export default function SampleDialog({ stock, onClose, onSubmit }) {
  const ref = useRef(null);
  const products = stock?.products || [];
  const [addr, setAddr] = useState({ name: "", address1: "", city: "", state: "", zip: "" });
  const [email, setEmail] = useState("");
  const [items, setItems] = useState([{ sku: "BAB-SMP-01", qty: 1 }, { sku: "", qty: 0 }, { sku: "", qty: 0 }]);
  useEffect(() => { ref.current?.showModal(); }, []);

  const set = (k, v) => setAddr(a => ({ ...a, [k]: v }));
  const setItem = (i, k, v) => setItems(list => list.map((it, j) => (j === i ? { ...it, [k]: v } : it)));
  const submit = e => {
    e.preventDefault();
    onSubmit({ address: addr, email, items: items.filter(i => i.sku && i.qty > 0) });
  };

  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose}>
      <form onSubmit={submit}>
        <h2 className="h-icon"><Gift size={20} /> New free sample</h2>
        <p className="hint">Samples never become Shopify orders, so UFSI can't see them. They use the same packing steps.</p>
        {FIELDS.map(([k, label]) => <label key={k}>{label}<input required value={addr[k]} onChange={e => set(k, e.target.value)} /></label>)}
        <div className="three">
          <label>City<input required value={addr.city} onChange={e => set("city", e.target.value)} /></label>
          <label>State<input required maxLength={2} value={addr.state} onChange={e => set("state", e.target.value.toUpperCase())} /></label>
          <label>ZIP<input required value={addr.zip} onChange={e => set("zip", e.target.value)} /></label>
        </div>
        <label>Email for the tracking message<input type="email" value={email} onChange={e => setEmail(e.target.value)} /></label>
        <p className="field-label">Items</p>
        {items.map((it, i) => (
          <div className="item-row" key={i}>
            <select value={it.sku} onChange={e => setItem(i, "sku", e.target.value)} aria-label={`Item ${i + 1}`}>
              <option value="">None</option>
              {products.map(p => <option key={p.sku} value={p.sku}>{p.name} ({p.available} free)</option>)}
            </select>
            <input type="number" min="0" value={it.qty} onChange={e => setItem(i, "qty", parseInt(e.target.value) || 0)} aria-label={`Quantity ${i + 1}`} />
          </div>
        ))}
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary">Add to the pack list</button>
        </div>
      </form>
    </dialog>
  );
}
