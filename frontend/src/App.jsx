import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, enc } from "./api.js";
import { PRESETS } from "./lane.js";
import { PAGES, Rail, Topbar } from "./components/Shell.jsx";
import SampleDialog from "./components/SampleDialog.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Pack from "./pages/Pack.jsx";
import Orders from "./pages/Orders.jsx";
import Stock from "./pages/Stock.jsx";

const SUBTITLES = {
  dashboard: "Babylon ships what it can. UFSI ships the rest.",
  pack: "Pick, pack, weigh, label, ship",
  orders: "Every order the lane has seen",
  stock: "Babylon's San Jose warehouse",
};

export default function App() {
  const [page, setPage] = useState("dashboard");
  const [cfg, setCfg] = useState(null);
  const [summary, setSummary] = useState(null);  // dashboard counts, low stock, recent orders
  const [queue, setQueue] = useState([]);        // the pack queue, oldest first (slim rows)
  const [tick, setTick] = useState(0);           // bumps on every refresh so the Orders page reloads its page
  const [stock, setStock] = useState(null);
  const [selId, setSelId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [sampleOpen, setSampleOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(() => {
    try { return localStorage.getItem("babylon-menu") === "open"; } catch { return false; }
  });
  const toggleMenu = v => {
    setMenuOpen(v);
    try { localStorage.setItem("babylon-menu", v ? "open" : "closed"); } catch { /* private window */ }
  };
  const busyRef = useRef(false);
  const cur = useRef({ selId, sampleOpen, page });
  cur.current = { selId, sampleOpen, page };

  const show = useCallback((text, kind = "ok") => setToast({ text, kind, at: Date.now() }), []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === "ok" ? 2500 : 6000);
    return () => clearTimeout(t);
  }, [toast]);

  // Each page asks only for what it shows; nothing ever downloads the whole order table.
  // A response that arrives after the packer opened another order is dropped.
  const refresh = useCallback(async () => {
    const { selId: id, page: pg } = cur.current;
    const [c, s, st, qu] = await Promise.all([
      api("/api/config"), api("/api/summary"),
      pg !== "orders" ? api("/api/stock") : null,
      pg === "pack" ? api("/api/orders?view=queue&limit=200") : null,
    ]);
    setCfg(c); setSummary(s);
    if (st) setStock(st);
    if (qu && cur.current.page === "pack") setQueue(qu);
    setTick(t => t + 1);
    if (!id) { setDetail(null); return; }
    try {
      const d = await api("/api/orders/" + enc(id));
      if (cur.current.selId === id) setDetail(d);
    } catch {
      if (cur.current.selId === id) { setSelId(null); setDetail(null); }
    }
  }, []);

  useEffect(() => { refresh().catch(e => show(e.message, "err")); }, [selId, page, refresh, show]);
  useEffect(() => {
    const t = setInterval(() => {
      const a = document.activeElement;
      const typing = a && ((a.tagName === "INPUT" && a.value) || a.tagName === "SELECT");
      if (!busyRef.current && !typing && !cur.current.sampleOpen) refresh().catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, [refresh]);

  // The pack station opens the next order in the queue by itself.
  useEffect(() => {
    if (page === "pack" && !selId && queue.length) setSelId(queue[0].id);
  }, [page, selId, queue]);

  // Every button goes through here: one action at a time, errors shown, then a refresh.
  const run = useCallback(async fn => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      const msg = await fn();
      if (msg) show(...(Array.isArray(msg) ? msg : [msg]));
    } catch (e) {
      if (e.checks) setDetail(d => d && { ...d, live_check: { ok: false, checks: e.checks } });
      show(e.checks ? "Stopped before buying the label" : e.message, "err");
    } finally {
      busyRef.current = false; setBusy(false);
    }
    await refresh().catch(() => {});
  }, [refresh, show]);

  const oid = () => enc(cur.current.selId);
  const post = (path, body) => api(`/api/orders/${oid()}/${path}`, "POST", body);
  const open = id => { setSelId(id); setPage("pack"); };

  const newOrder = async preset => {
    const o = await api("/api/dev/order", "POST", preset);
    open(o.id);
    return o.status === "queued" ? `${o.name}: Babylon takes it` : [`${o.name}: UFSI keeps it. See why.`, "warn"];
  };

  const act = {
    scenario: key => run(async () => {
      if (key !== "late") return newOrder(PRESETS[key]);
      await api("/api/dev/clock", "POST", { minute: 58 });
      try { return await newOrder(PRESETS.glass); } finally { await api("/api/dev/clock", "POST", { minute: null }); }
    }),
    open,
    scan: code => run(async () => {
      if (/^(PM|PL|BX)-/i.test(code)) { await post("package", { code }); return null; }
      const r = await post("scan", { code });
      return r.warning ? [r.warning, "warn"] : null;
    }),
    pack: code => run(async () => { await post("package", { code }); }),
    weigh: oz => run(async () => { await post("weight", { oz }); }),
    label: () => run(async () => { await post("label"); return "Label bought and sent to the printer"; }),
    reprint: () => run(async () => { await post("reprint"); return "Sent to the printer again"; }),
    ship: () => run(async () => { await post("close"); return "Shipped"; }),
    void: () => run(async () => {
      if (!confirm("Void this label? The postage is refunded and the order will not ship.")) return null;
      await post("void"); return "Label voided";
    }),
    standDown: () => run(async () => {
      if (!confirm("Give this order back to UFSI? Babylon will not ship it.")) return null;
      await post("standdown"); return "Handed back to UFSI";
    }),
    short: (sku, name) => run(async () => {
      if (!confirm(`Is ${name} missing or damaged? The order goes back to UFSI so the customer still gets everything.`)) return null;
      await post("short", { sku }); return ["Handed back to UFSI", "warn"];
    }),
    cancel: () => run(async () => { await api("/api/dev/cancel", "POST", { id: cur.current.selId }); return ["The customer cancelled in Shopify", "warn"]; }),
    edit: () => run(async () => {
      const items = [...detail.lines.map(l => ({ sku: l.sku, qty: l.qty })), { sku: "BAB-MSK-01", qty: 5 }];
      await api("/api/dev/edit", "POST", { id: cur.current.selId, items });
      return ["Support added 5 clay masks to the order", "warn"];
    }),
    ufsiTakes: () => run(async () => {
      await api("/api/dev/deposco", "POST", { order_name: detail.name });
      return ["UFSI's system now has this order. Try to buy the label.", "warn"];
    }),
    sync: () => run(async () => { const r = await api("/api/sync/inventory", "POST"); return `Sent stock counts for ${r.synced} products to Shopify`; }),
    reset: () => run(async () => {
      if (!confirm("Wipe all demo orders and reset stock?")) return null;
      await api("/api/dev/reset", "POST"); setSelId(null); setDetail(null); return "Demo data reset";
    }),
    sample: () => setSampleOpen(true),
    createSample: body => run(async () => {
      const o = await api("/api/samples", "POST", body);
      setSampleOpen(false); open(o.id); return `${o.name} added to the queue`;
    }),
  };

  const q = search.trim().toLowerCase();
  const shownQueue = useMemo(() => q ? queue.filter(o => o.name.toLowerCase().includes(q) || (o.address.name || "").toLowerCase().includes(q)) : queue, [q, queue]);

  return (
    <div className="shell">
      <Rail page={page} setPage={setPage} toPack={summary?.to_pack || 0} open={menuOpen} setOpen={toggleMenu} />
      <div className="main">
        <Topbar title={PAGES.find(p => p.key === page).label} sub={SUBTITLES[page]} cfg={cfg}
                search={search} setSearch={v => { setSearch(v); if (v && page === "dashboard") setPage("orders"); }}
                act={act} busy={busy} />
        <AnimatePresence mode="wait">
          <motion.div key={page} className="content" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}>
            {page === "dashboard" && <Dashboard summary={summary} stock={stock} open={open} go={setPage} />}
            {page === "pack" && <Pack queue={shownQueue} detail={detail} selId={selId} cfg={cfg} stock={stock} act={act} busy={busy} />}
            {page === "orders" && <Orders q={search.trim()} tick={tick} counts={summary?.counts || {}} open={open} />}
            {page === "stock" && <Stock stock={stock} />}
          </motion.div>
        </AnimatePresence>
      </div>

      {sampleOpen && <SampleDialog stock={stock} onClose={() => setSampleOpen(false)} onSubmit={act.createSample} />}
      <AnimatePresence>
        {toast && (
          <motion.div key={toast.at} className={"toast " + toast.kind} role="status"
                      initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 12 }}>
            {toast.text}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
