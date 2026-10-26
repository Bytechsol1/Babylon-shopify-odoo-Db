import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { STATUS, routeOf } from "../lane.js";

export function Card({ title, action, children, className = "", pad = true }) {
  return (
    <section className={"card " + className}>
      {(title || action) && (
        <header className="card-head">
          <h3>{title}</h3>
          {action}
        </header>
      )}
      <div className={pad ? "card-body" : ""}>{children}</div>
    </section>
  );
}

export function StatusChip({ status }) {
  const [label, tone] = STATUS[status] || [status, "info"];
  return <span className={"chip " + tone}><i />{label}</span>;
}

export function RouteChip({ o }) {
  const r = routeOf(o);
  const tone = r.startsWith("Babylon") ? "babylon" : r === "UFSI" ? "ufsi" : "info";
  return <span className={"route " + tone}>{r}</span>;
}

export function Empty({ children }) {
  return <p className="empty">{children}</p>;
}

// A small dropdown that closes on outside click or Escape.
export function Menu({ button, children, align = "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const off = e => { if (!ref.current?.contains(e.target)) setOpen(false); };
    const esc = e => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="menu-wrap" ref={ref}>
      {button({ open, toggle: () => setOpen(o => !o) })}
      <AnimatePresence>
        {open && (
          <motion.div className={"menu " + align} role="menu"
                      initial={{ opacity: 0, y: -4, scale: 0.98 }} animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: -4, scale: 0.98 }} transition={{ duration: 0.14 }}
                      onClick={() => setOpen(false)}>
            {children}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
