import { useEffect, useState } from "react";
import { FileText, Printer } from "lucide-react";
import { api } from "../api.js";
import { ago } from "../lane.js";
import { Card, Empty } from "../components/ui.jsx";

// One scan form per carrier per day for the pickup driver, then book the pickup (build plan P1 #3).
export default function EndOfDay({ tick, act, cfg }) {
  const [s, setS] = useState(null);
  const [show, setShow] = useState(null);  // a scan form whose manifest is open
  useEffect(() => { api("/api/end-of-day").then(setS).catch(() => {}); }, [tick]);
  if (!s) return <div className="page"><Empty>Loading…</Empty></div>;
  const carriers = Object.entries(s.pending);
  return (
    <div className="page">
      <Card title="Labels waiting for a scan form">
        {carriers.length === 0 ? <p className="muted" style={{ margin: 0 }}>Every label bought so far is on a scan form.</p> : (
          <div className="eod-carriers">
            {carriers.map(([c, n]) => (
              <div key={c} className="eod-carrier">
                <b>{c}</b><span>{n} label{n === 1 ? "" : "s"}</span>
                <button className="btn primary sm" onClick={() => act.scanForm(c)}><FileText size={14} /> Make scan form</button>
              </div>
            ))}
          </div>
        )}
        <p className="hint">Provider: {cfg?.parcel_provider === "mock" ? "test labels (no real carrier yet)" : cfg?.parcel_provider}.</p>
      </Card>

      <Card title="Scan forms" pad={false}>
        {s.forms.length === 0 ? <Empty>No scan forms yet.</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Made</th><th>Carrier</th><th>Form</th><th className="num">Parcels</th><th /></tr></thead>
              <tbody>
                {s.forms.map(f => (
                  <tr key={f.id} className="static">
                    <td className="muted">{ago(f.at)}</td>
                    <td className="strong">{f.carrier}</td>
                    <td className="mono">{f.id}</td>
                    <td className="num">{f.parcels.length}</td>
                    <td className="row-actions">
                      {f.url ? <a className="btn sm" href={f.url} target="_blank" rel="noreferrer"><Printer size={13} /> Carrier form</a>
                             : <button className="btn sm" onClick={() => setShow(f)}><Printer size={13} /> Manifest</button>}
                      <Pickup carrier={f.carrier} act={act} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {s.pickups.length > 0 && (
        <Card title="Pickups" pad={false}>
          <table className="table">
            <thead><tr><th>Booked</th><th>Carrier</th><th>Window</th><th className="num">Parcels</th><th>Confirmation</th></tr></thead>
            <tbody>{s.pickups.map(p => (
              <tr key={p.id} className="static"><td className="muted">{ago(p.at)}</td><td>{p.carrier}</td>
                <td className="muted">{fmt(p.start)} – {fmt(p.end)}</td><td className="num">{p.parcels}</td><td className="mono">{p.confirmation}</td></tr>
            ))}</tbody>
          </table>
        </Card>
      )}

      {show && (
        <div className="sheet-overlay">
          <div className="sheet print-area">
            <header className="sheet-head no-print">
              <h2>{show.carrier} manifest</h2>
              <button className="btn primary" onClick={() => window.print()}><Printer size={15} /> Print</button>
              <button className="btn ghost" onClick={() => setShow(null)}>Close</button>
            </header>
            <p>Babylon LLC · scan form <b>{show.id}</b> · {new Date(show.at).toLocaleString()} · {show.parcels.length} parcels</p>
            <table className="table">
              <thead><tr><th>Order</th><th>Tracking</th><th>Service</th><th className="num">Weight (oz)</th></tr></thead>
              <tbody>{show.parcels.map(p => (
                <tr key={p.tracking} className="static"><td>{p.order}</td><td className="mono">{p.tracking}</td><td>{p.service}</td><td className="num">{p.weight_oz}</td></tr>
              ))}</tbody>
            </table>
            <p className="hint">Driver signature: ____________________   Time: ________</p>
          </div>
        </div>
      )}
    </div>
  );
}

const fmt = iso => new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

function Pickup({ carrier, act }) {
  const [openForm, setOpen] = useState(false);
  const day = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  const [d, setD] = useState({ date: day, start: "15:00", end: "17:00" });
  if (!openForm) return <button className="btn sm ghost" onClick={() => setOpen(true)}>Book pickup</button>;
  const go = () => act.pickup(carrier, new Date(`${d.date}T${d.start}`).toISOString(), new Date(`${d.date}T${d.end}`).toISOString(), () => setOpen(false));
  return (
    <span className="inline-tight">
      <input type="date" value={d.date} onChange={e => setD({ ...d, date: e.target.value })} aria-label="Pickup date" />
      <input type="time" value={d.start} onChange={e => setD({ ...d, start: e.target.value })} aria-label="From" />
      <input type="time" value={d.end} onChange={e => setD({ ...d, end: e.target.value })} aria-label="To" />
      <button className="btn sm primary" onClick={go}>Book</button>
    </span>
  );
}
