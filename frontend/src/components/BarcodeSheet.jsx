import { useEffect, useRef } from "react";
import JsBarcode from "jsbarcode";
import { Printer, X } from "lucide-react";
import { COMMANDS } from "./ScanBar.jsx";

function Code({ value, label }) {
  const ref = useRef(null);
  useEffect(() => {
    JsBarcode(ref.current, value, { format: "CODE128", height: 48, width: 1.6, fontSize: 12, margin: 4, displayValue: true });
  }, [value]);
  return <figure className="bc"><svg ref={ref} /><figcaption>{label}</figcaption></figure>;
}

// A printable sheet for the bench: the command barcodes, and one barcode per package to stick on its shelf.
export default function BarcodeSheet({ stock, onClose }) {
  return (
    <div className="sheet-overlay">
      <div className="sheet print-area">
        <header className="sheet-head no-print">
          <h2>Barcode sheet</h2>
          <button className="btn primary" onClick={() => window.print()}><Printer size={15} /> Print</button>
          <button className="btn ghost" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </header>
        <h3>Commands</h3>
        <div className="bc-grid">{COMMANDS.map(([v, l]) => <Code key={v} value={v} label={l} />)}</div>
        <h3>Packages</h3>
        <div className="bc-grid">{(stock?.packaging || []).map(p => <Code key={p.bin} value={p.bin} label={p.name} />)}</div>
      </div>
    </div>
  );
}
