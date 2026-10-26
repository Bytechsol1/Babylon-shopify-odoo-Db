import { AnimatePresence, motion } from "motion/react";
import { units } from "../lane.js";
import { Empty, StatusChip } from "../components/ui.jsx";
import OrderView from "../components/OrderView.jsx";

// `queue` comes from the server already filtered to orders being packed, oldest first.
export default function Pack({ queue, detail, selId, cfg, stock, act, busy }) {
  // the open order stays in the list even after it leaves the queue, so the packer sees where it went
  const list = detail && !queue.some(o => o.id === detail.id) ? [detail, ...queue] : queue;
  return (
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
              <p>Pick one from the queue{cfg?.dev_tools ? ", or create one with “New test order” at the top right" : ""}.</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
