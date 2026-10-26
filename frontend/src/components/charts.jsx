// Small hand-built charts. Marks follow one spec: thin bars with a 4px rounded data end
// (square at the baseline), a 2px surface gap between touching segments, values and labels
// in text colours (never the series colour), and a hover tooltip on every mark.
import { useRef, useState } from "react";
import { motion } from "motion/react";

// Validated pair (light surface): Babylon from the logo navy, UFSI from the logo gold.
// Meter tracks use --track in styles.css, a lighter step of the Babylon hue.
export const SERIES = { babylon: "#4545b5", ufsi: "#c27f00" };

function useTip() {
  const box = useRef(null);
  const [tip, setTip] = useState(null);
  const show = (e, text) => {
    const r = box.current.getBoundingClientRect();
    setTip({ x: e.clientX - r.left, y: e.clientY - r.top, text });
  };
  const node = tip && <div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.text}</div>;
  return { box, show, hide: () => setTip(null), node };
}

// Part-to-whole as one horizontal stacked bar, with a legend that also carries the values.
export function SplitBar({ parts, unit = "orders" }) {
  const { box, show, hide, node } = useTip();
  const total = parts.reduce((a, p) => a + p.value, 0);
  const shown = parts.filter(p => p.value > 0);
  return (
    <div className="split" ref={box}>
      <div className="split-bar" role="img"
           aria-label={parts.map(p => `${p.label}: ${p.value} ${unit}`).join(", ")}>
        {total === 0 ? <span className="split-empty" /> : shown.map((p, i) => (
          <motion.span key={p.label} className="seg" initial={{ flexGrow: 0 }} animate={{ flexGrow: p.value }}
                       transition={{ duration: 0.5, ease: "easeOut" }}
                       style={{ background: p.color, borderRadius: `${i === 0 ? 4 : 0}px ${i === shown.length - 1 ? 4 : 0}px ${i === shown.length - 1 ? 4 : 0}px ${i === 0 ? 4 : 0}px` }}
                       onMouseMove={e => show(e, `${p.label}: ${p.value} ${unit} (${Math.round(100 * p.value / total)}%)`)}
                       onMouseLeave={hide} />
        ))}
      </div>
      <ul className="legend">
        {parts.map(p => (
          <li key={p.label}>
            <i style={{ background: p.color }} />
            <span>{p.label}</span>
            <b>{p.value}</b>
            <small>{total ? Math.round(100 * p.value / total) : 0}%</small>
          </li>
        ))}
      </ul>
      {node}
    </div>
  );
}

// One series of horizontal bars, value at the tip. rows: [{label, value, note?, warn?}]
export function BarList({ rows, max, color = SERIES.babylon, unit = "" }) {
  const { box, show, hide, node } = useTip();
  const top = max ?? Math.max(1, ...rows.map(r => r.value));
  return (
    <div className="barlist" ref={box}>
      {rows.map(r => (
        <div key={r.label} className="bar-row"
             onMouseMove={e => show(e, `${r.label}: ${r.value}${unit ? " " + unit : ""}${r.note ? ` · ${r.note}` : ""}`)}
             onMouseLeave={hide}>
          <span className="bar-label">{r.label}</span>
          <span className={"bar-track" + (r.value ? "" : " zero")}>
            <motion.i initial={{ width: 0 }} animate={{ width: `${(100 * r.value) / top}%` }}
                      transition={{ duration: 0.5, ease: "easeOut" }}
                      style={{ background: r.warn ? "var(--warn)" : color }} />
          </span>
          <span className="bar-value">{r.value}{r.warn && <em className="flag">Low</em>}</span>
        </div>
      ))}
      {node}
    </div>
  );
}

// A ratio against a limit: fill on a lighter track of the same hue.
export function Meter({ value, max, warn }) {
  return (
    <span className="meter" title={`${value} of ${max}`}>
      <motion.i initial={{ width: 0 }} animate={{ width: `${max ? (100 * value) / max : 0}%` }}
                transition={{ duration: 0.5, ease: "easeOut" }}
                style={{ background: warn ? "var(--warn)" : SERIES.babylon }} />
    </span>
  );
}
