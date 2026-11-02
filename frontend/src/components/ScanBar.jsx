import { useEffect, useRef, useState } from "react";
import { ScanLine } from "lucide-react";

// One box for every step (build plan P1 #1). The packer scans and the station works out what it was:
//   a lot barcode -> pick   a package code -> pack   a number at the weigh step -> weight
//   an order number (#1005) -> open it   a command (!LABEL, !SHIP, !REPRINT, !NEXT, !R1..!R4) -> do it
// It takes the focus back after every action, so no mouse is needed. Typing in another field is left alone.
export const COMMANDS = [
  ["!LABEL", "Buy the label and print"], ["!SHIP", "Mark as shipped"], ["!REPRINT", "Print the label again"],
  ["!NEXT", "Open the next order in the queue"],
  ["!R1", "Reason: didn't fit"], ["!R2", "Reason: too big"], ["!R3", "Reason: out of stock"], ["!R4", "Reason: damaged"],
];

export default function ScanBar({ onScan, hint, busy }) {
  const ref = useRef(null);
  const [v, setV] = useState("");
  useEffect(() => {
    const grab = () => {
      const a = document.activeElement;
      const elsewhere = a && a !== ref.current && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName);
      if (!elsewhere && !document.querySelector("dialog[open]")) ref.current?.focus();
    };
    grab();
    const t = setInterval(grab, 800);
    return () => clearInterval(t);
  }, []);
  const submit = e => {
    e.preventDefault();
    const code = v.trim();
    setV("");
    if (code) onScan(code);
  };
  return (
    <form className="scanbar" onSubmit={submit}>
      <ScanLine size={18} />
      <input ref={ref} value={v} onChange={e => setV(e.target.value)} disabled={busy} autoComplete="off"
             placeholder="Scan anything: a lot, a package, a weight, an order number or a command" aria-label="Scan" />
      {hint && <span className="scanbar-hint">{hint}</span>}
    </form>
  );
}
