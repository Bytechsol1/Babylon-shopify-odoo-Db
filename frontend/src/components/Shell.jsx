import { AnimatePresence, motion } from "motion/react";
import {
  Boxes, ChartColumn, ChevronDown, Gift, LayoutGrid, ListOrdered, LogOut, PanelLeftClose, Plus, RefreshCw, RotateCcw, ScanLine, Search, TriangleAlert, Truck,
} from "lucide-react";
import { SCENARIOS } from "../lane.js";
import { Menu } from "./ui.jsx";
import logo from "../assets/babylon-logo.png";

export const PAGES = [
  { key: "dashboard", label: "Dashboard", icon: LayoutGrid },
  { key: "pack", label: "Pack station", icon: ScanLine },
  { key: "orders", label: "Orders", icon: ListOrdered },
  { key: "exceptions", label: "Exceptions", icon: TriangleAlert },
  { key: "endofday", label: "End of day", icon: Truck },
  { key: "results", label: "Results", icon: ChartColumn },
  { key: "stock", label: "Stock", icon: Boxes },
];

// Collapsible like Gemini's menu: closed shows only the logo and icons (the logo opens it),
// open adds the labels and a close button.
export function Rail({ page, setPage, counts = {}, open, setOpen }) {
  return (
    <motion.nav className={"rail" + (open ? " open" : "")} aria-label="Main"
                initial={false} animate={{ width: open ? 220 : 64 }} transition={{ duration: 0.2, ease: "easeOut" }}>
      <div className="rail-top">
        {open ? <img className="rail-logo" src={logo} alt="Babylon" width="40" height="40" /> : (
          <button className="rail-logo-btn" onClick={() => setOpen(true)} aria-expanded={false} aria-label="Expand menu">
            <img className="rail-logo" src={logo} alt="" width="40" height="40" />
          </button>
        )}
        {open && (
          <button className="rail-toggle" onClick={() => setOpen(false)} aria-expanded={true} aria-label="Collapse menu">
            <PanelLeftClose size={18} strokeWidth={1.8} />
          </button>
        )}
      </div>
      {PAGES.map(p => (
        <button key={p.key} className={"rail-btn" + (page === p.key ? " on" : "")} onClick={() => setPage(p.key)}
                aria-current={page === p.key ? "page" : undefined} aria-label={open ? undefined : p.label}>
          {page === p.key && <motion.span layoutId="rail-on" className="rail-on" transition={{ type: "spring", stiffness: 500, damping: 38 }} />}
          <p.icon size={19} strokeWidth={1.8} />
          <AnimatePresence initial={false}>
            {open && (
              <motion.span className="rail-label" initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }}
                           exit={{ opacity: 0, transition: { duration: 0.05 } }} transition={{ duration: 0.15, delay: 0.06 }}>
                {p.label}
              </motion.span>
            )}
          </AnimatePresence>
          {counts[p.key] > 0 && <span className={"rail-count" + (p.key === "exceptions" ? " alert" : "")}>{counts[p.key]}</span>}
        </button>
      ))}
    </motion.nav>
  );
}

export function Topbar({ title, sub, cfg, search, setSearch, act, busy, user, onSignOut }) {
  const store = cfg?.shopify === "live" ? (cfg.shop || "Shopify").replace(".myshopify.com", "") : null;
  const shadow = store && cfg.claims === "shadow";
  return (
    <header className="topbar">
      <div className="crumbs">
        <h1>{title}</h1>
        {sub && <span>{sub}</span>}
      </div>
      <label className="search">
        <Search size={15} />
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search orders or customers" aria-label="Search orders" />
      </label>
      {cfg && (
        <span className={"mode " + (!store ? "demo" : shadow ? "shadow" : "live")}
              title={["shopify", "odoo", "easypost", "printer", "deposco"].map(k => `${k}: ${cfg[k]}`).join(" · ")}>
          <i />{!store ? "Demo data" : `${store} · ${shadow ? "watching only" : "taking orders"}`}
        </span>
      )}
      {cfg && (
        <span className={"import" + (cfg.daily_cap > 0 && cfg.claimed_today >= cfg.daily_cap ? " cap-hit" : "")}
              title={cfg.daily_cap > 0 ? "Orders claimed today against the daily cap" : "No daily claim cap is set"}>
          Claimed today {cfg.claimed_today ?? 0}{cfg.daily_cap > 0 ? ` of ${cfg.daily_cap}` : ""}
        </span>
      )}
      {cfg && <span className="import" title="UFSI imports new Shopify orders every hour, on the hour">UFSI import in {Math.round(cfg.minutes_to_import)}m</span>}
      {cfg?.dev_tools && (
        <Menu button={({ toggle }) => (
          <button className="btn primary" onClick={toggle} disabled={busy}><Plus size={15} /> New test order <ChevronDown size={14} /></button>
        )}>
          <p className="menu-label">Babylon ships these</p>
          {SCENARIOS.filter(s => s.who === "babylon").map(s => (
            <button key={s.key} className="menu-item" onClick={() => act.scenario(s.key)}><i className="dot babylon" />{s.title}<small>{s.tip}</small></button>
          ))}
          <p className="menu-label">UFSI keeps these</p>
          {SCENARIOS.filter(s => s.who === "ufsi").map(s => (
            <button key={s.key} className="menu-item" onClick={() => act.scenario(s.key)}><i className="dot ufsi" />{s.title}<small>{s.tip}</small></button>
          ))}
          <hr />
          <button className="menu-item" onClick={act.sample}><Gift size={14} />Free sample</button>
          <button className="menu-item" onClick={act.sync}><RefreshCw size={14} />Sync stock to Shopify</button>
          <button className="menu-item danger" onClick={act.reset}><RotateCcw size={14} />Reset demo data</button>
        </Menu>
      )}
      {user && (
        <Menu button={({ toggle }) => (
          <button className="avatar" onClick={toggle} aria-label={`Signed in as ${user}`} title={user}>{user[0].toUpperCase()}</button>
        )}>
          <p className="menu-label">Signed in as</p>
          <p className="menu-user">{user}</p>
          <hr />
          <button className="menu-item" onClick={onSignOut}><LogOut size={14} />Sign out</button>
        </Menu>
      )}
    </header>
  );
}
