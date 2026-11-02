import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Barcode, Printer } from "lucide-react";
import { api } from "../api.js";
import { units } from "../lane.js";
import { Empty, StatusChip } from "../components/ui.jsx";
import OrderView from "../components/OrderView.jsx";
import ScanBar from "../components/ScanBar.jsx";
import BarcodeSheet from "../components/BarcodeSheet.jsx";

// `queue` comes from the server already filtered to orders being packed, oldest first.
export default function Pack({ queue, detail, selId, cfg, stock, act, busy, scanHint, tick }) {
  const [view, setView] = useState("station");
  const [sheet, setSheet] = useState(false);
  // the open order stays in the list even after it leaves the queue, so the packer sees where it went
  const list = detail && !queue.some(o => o.id === detail.id) ? [detail, ...queue] : queue;
  return (
    <div className="page">
      <div className="pack-bar">
        <div className="seg-tabs" role="tablist">
          <button role="tab" aria-selected={view === "station"} onClick={() => setView("station")}>Station</button>
          <button role="tab" aria-selected={view === "waves"} onClick={() => setView("waves")}>Wave pick list</button>
        </div>
        <button className="btn sm ghost" onClick={() => setSheet(true)}><Barcode size={14} /> Barcode sheet</button>
      </div>
      {view === "waves" ? <Waves tick={tick} cfg={cfg} /> : (
        <>
          <ScanBar onScan={act.scanAny} hint={scanHint} busy={busy} />
          <div className="pack">
            <aside className="queue card">
              <header className="card-head"><h3>Queue</h3><span className="count-pill">{queue.length}</span></header>
              {list.length === 0 ? <Empty>Nothing to pack.</Empty> : (
                <ul>
                  <AnimatePresence initial={false}>
                    {list.map(o => (
                      <motion.li key={o.id} layout initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, height: 0 }}>
                        <button className={"q-row" + (o.id === selId ? " on" : "")} onClick={() => act.open(o.id)}>
                          <span className="q-top"><b>{o.name}</b><StatusChip status={o.status} /></span>
                          <span className="q-sub">{o.address.name} · {units(o)} item{units(o) === 1 ? "" : "s"}{o.kind === "sample" ? " · sample" : ""}</span>
                        </button>
                      </motion.li>
                    ))}
                  </AnimatePresence>
                </ul>
              )}
            </aside>
            <AnimatePresence mode="wait">
              {detail ? (
                <motion.div key={detail.id} className="pack-main" initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -8 }} transition={{ duration: 0.18 }}>
                  <OrderView o={detail} cfg={cfg} stock={stock} act={act} busy={busy} />
                </motion.div>
              ) : (
                <motion.div key="none" className="pack-main" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
                  <div className="card blank">
                    <h3>No order open</h3>
                    <p>Pick one from the queue, scan an order number, or scan <b>!NEXT</b>.</p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </>
      )}
      {sheet && <BarcodeSheet stock={stock} onClose={() => setSheet(false)} />}
    </div>
  );
}

// Orders claimed before a cutoff (10:00 and 14:00 Pacific by default) are picked together from one list.
function Waves({ tick, cfg }) {
  const [waves, setWaves] = useState(null);
  useEffect(() => { api("/api/waves").then(setWaves).catch(() => setWaves([])); }, [tick]);
  if (waves === null) return <Empty>Loading…</Empty>;
  if (!waves.length) return <div className="card blank"><h3>Nothing to pick</h3><p>Orders claimed for the next wave show up here.</p></div>;
  return (
    <div className="stack print-area">
      <p className="hint no-print">Waves at {(cfg?.wave_times || []).join(" and ")} ({cfg?.wave_tz}). <button className="link" onClick={() => window.print()}><Printer size={13} /> Print</button></p>
      {waves.map(w => (
        <section key={w.wave} className="card">
          <header className="card-head"><h3>Wave {w.label}</h3><span className="muted">{w.orders.length} order{w.orders.length === 1 ? "" : "s"}</span></header>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Shelf</th><th>SKU</th><th>Product</th><th className="num">Pick</th><th>For orders</th></tr></thead>
              <tbody>
                {w.pick_list.map(l => (
                  <tr key={l.sku} className="static">
                    <td className="mono strong">{l.bin}</td>
                    <td className="mono">{l.sku}</td>
                    <td>{l.title}{l.glass && <span className="chip warn" style={{ marginLeft: 6 }}><i />glass</span>}</td>
                    <td className="num strong">{l.qty}</td>
                    <td className="muted wrap">{l.orders.map(x => `${x.name}${x.qty > 1 ? ` ×${x.qty}` : ""}`).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
