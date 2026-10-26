import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { fileUrl } from "../api.js";
import {
  Box, Check, CircleCheck, CircleX, Download, Eye, FileText, FlaskConical, Printer, Scale, ScanLine, Truck, Undo2, X,
} from "lucide-react";
import { ACTIVE, NOT_OURS, banner, reason, refused, stepOf, whyStep } from "../lane.js";
import { Menu, RouteChip, StatusChip } from "./ui.jsx";

const STEPS = ["Pick", "Package", "Weigh", "Label", "Ship"];

export default function OrderView({ o, cfg, stock, act, busy }) {
  const b = banner(o);
  const ours = !NOT_OURS.includes(o.status);
  const active = ACTIVE.includes(o.status);
  return (
    <div className="order">
      <div className="work">
        <header className="order-head">
          <div>
            <h2>{o.name}</h2>
            <span className="muted">{o.kind === "sample" ? "Free sample" : "Shopify order"} · {o.address.name}</span>
          </div>
          <div className="order-tags">
            <StatusChip status={o.status} />
            <RouteChip o={o} />
            {cfg?.dev_tools && o.kind === "order" && active && !o.cancelled && (
              <Menu button={({ toggle }) => <button className="btn ghost sm" onClick={toggle} disabled={busy}><FlaskConical size={14} /> Simulate</button>}>
                <p className="menu-label">Make this happen to the order</p>
                <button className="menu-item" onClick={act.cancel}>Customer cancels</button>
                {!o.label && <button className="menu-item" onClick={act.edit}>Support adds 5 clay masks</button>}
                {!o.label && <button className="menu-item" onClick={act.ufsiTakes}>UFSI imports it first</button>}
              </Menu>
            )}
          </div>
        </header>

        {ours && <Stepper o={o} />}

        <section className={"card step-card " + b.tone}>
          <AnimatePresence mode="wait">
            <motion.div key={b.title} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} transition={{ duration: 0.16 }}>
              <h3 className="step-title">{b.title}</h3>
              <p className="step-sub">{b.text}</p>
              {o.attention && !o.cancelled && <p className="alert">{o.attention}</p>}
              {o.writeback_error && <p className="alert">Shopify didn't accept the tracking number. Press “Mark as shipped” again.</p>}
              <div className="step-body">
                {o.status === "shipped" ? <Shipped o={o} /> : <Now o={o} stock={stock} act={act} busy={busy} />}
                {o.writeback_error && o.status === "shipped" && <button className="btn primary lg" onClick={act.ship} disabled={busy}><Truck size={16} /> Mark as shipped</button>}
              </div>
            </motion.div>
          </AnimatePresence>
          <Why text={whyStep(o)} />
        </section>
      </div>

      <aside className="info">
        <Info title="Ship to">
          <p className="addr">
            <b>{o.address.name}</b><br />
            {[o.address.address1, o.address.address2].filter(Boolean).join(", ")}<br />
            {o.address.city}, {o.address.state} {o.address.zip}{o.address.country && o.address.country !== "US" ? `, ${o.address.country}` : ""}
          </p>
        </Info>
        <Info title="Items">
          <ul className="items">
            {(o.pick?.length ? o.pick.map(x => ({ key: x.sku, title: x.title, qty: x.need, lots: x.picked.map(p => p.lot) }))
                             : o.lines.map(l => ({ key: l.sku, title: l.title, qty: l.qty, lots: [] }))).map(i => (
              <li key={i.key}><span>{i.qty} ×</span><span>{i.title}{i.lots.length > 0 && <small>Lots {i.lots.join(", ")}</small>}</span></li>
            ))}
          </ul>
        </Info>
        <Checks o={o} />
        <Info title="History">
          <ol className="timeline">
            {(o.events || []).slice().reverse().map((e, i) => (
              <li key={i}><time>{e.ts.slice(11, 16)}</time><span>{e.detail || e.kind.replace(/_/g, " ")}
                {e.actor && e.actor !== "system" && <small className="by">by {e.actor}</small>}</span></li>
            ))}
          </ol>
        </Info>
      </aside>
    </div>
  );
}

function Info({ title, children }) {
  return <section className="info-block"><h4>{title}</h4>{children}</section>;
}

function Checks({ o }) {
  const checks = (o.live_check ? o.live_check.checks : o.checks) || [];
  if (!checks.length) return null;
  return (
    <Info title={o.live_check ? "Safety check" : "Eligibility"}>
      <ul className="checks">
        {checks.map(c => (
          <li key={c.key} className={c.ok ? "" : "no"}>
            {c.ok ? <CircleCheck size={14} /> : <CircleX size={14} />}
            <span>{c.ok ? c.text : reason(c)}</span>
          </li>
        ))}
      </ul>
    </Info>
  );
}

function Why({ text }) {
  const [show, setShow] = useState(false);
  if (!text) return null;
  return (
    <div className="why">
      <button className="link" onClick={() => setShow(!show)}>{show ? "Hide" : "Why?"}</button>
      <AnimatePresence>{show && <motion.p initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>{text}</motion.p>}</AnimatePresence>
    </div>
  );
}

function Stepper({ o }) {
  const at = stepOf(o);
  const stop = refused(o) || o.cancelled || ["released", "cancelled"].includes(o.status);
  return (
    <ol className="stepper">
      {STEPS.map((name, i) => {
        const state = i < at ? "done" : i === at ? (stop ? "stop" : "now") : "";
        return (
          <li key={name} className={state}>
            <span className="dot">{state === "done" ? <Check size={12} strokeWidth={3} /> : state === "stop" ? <X size={12} strokeWidth={3} /> : i + 1}</span>
            {name}
          </li>
        );
      })}
    </ol>
  );
}

const productOf = (stock, sku) => (stock?.products || []).find(p => p.sku === sku) || {};
const binName = (stock, code) => ((stock?.packaging || []).find(b => b.bin === code) || {}).name || code;

function Now({ o, stock, act, busy }) {
  const step = stepOf(o), p = o.package || {};
  if (!ACTIVE.includes(o.status)) return null;
  if (o.cancelled && o.status === "labeled") return <button className="btn danger lg" onClick={act.void} disabled={busy}><Undo2 size={16} /> Void the label</button>;
  if (refused(o)) return <button className="btn danger lg" onClick={act.standDown} disabled={busy}><Undo2 size={16} /> Give it back to UFSI</button>;
  if (step === 0) return <Pick o={o} stock={stock} act={act} busy={busy} />;
  if (step === 1) {
    return (
      <>
        <button className="btn primary lg" onClick={() => act.pack(p.bin)} disabled={busy}><Box size={16} /> Use {binName(stock, p.bin)}</button>
        {p.bubble > 0 && <p className="hint">Wrap the glass in bubble pouches first.</p>}
        <OtherBin stock={stock} except={p.bin} onPick={act.pack} busy={busy} />
      </>
    );
  }
  if (step === 2) return <Weigh o={o} stock={stock} act={act} busy={busy} />;
  if (step === 3) return <button className="btn primary lg" onClick={act.label} disabled={busy}><Printer size={16} /> Buy label and print</button>;
  return <ShipStep o={o} act={act} busy={busy} />;
}

function Pick({ o, stock, act, busy }) {
  const [code, setCode] = useState("");
  const submit = e => { e.preventDefault(); if (code.trim()) { act.scan(code.trim()); setCode(""); } };
  return (
    <>
      <ul className="picks">
        {o.pick.map(x => {
          const done = x.picked.length >= x.need;
          const lot = (productOf(stock, x.sku).lots || [])[0];
          return (
            <li key={x.sku} className={done ? "done" : ""}>
              <span className="pick-qty">{done ? <CircleCheck size={16} /> : `${x.picked.length}/${x.need}`}</span>
              <span className="pick-name">{x.title}<small>Shelf {x.bin}{x.glass ? " · glass" : ""}</small></span>
              {!done && o.kind === "order" && <button className="link" onClick={() => act.short(x.sku, x.title)}>Missing?</button>}
              {!done && lot && <button className="btn primary" onClick={() => act.scan(lot)} disabled={busy}><ScanLine size={14} /> Scan</button>}
            </li>
          );
        })}
      </ul>
      <form className="scan-input" onSubmit={submit}>
        <ScanLine size={15} />
        <input value={code} onChange={e => setCode(e.target.value)} placeholder="Or scan / type a barcode" aria-label="Barcode" autoComplete="off" />
      </form>
    </>
  );
}

function OtherBin({ stock, except, onPick, busy }) {
  const bins = (stock?.packaging || []).filter(b => b.bin !== except);
  const [v, setV] = useState(bins[0]?.bin || "");
  return (
    <div className="inline">
      <span className="muted">Other package</span>
      <select value={v} onChange={e => setV(e.target.value)} aria-label="Package">{bins.map(b => <option key={b.bin} value={b.bin}>{b.name}</option>)}</select>
      <button className="btn" onClick={() => onPick(v)} disabled={busy}>Use</button>
    </div>
  );
}

function Weigh({ o, stock, act, busy }) {
  const p = o.package || {};
  const est = o.pick.reduce((a, x) => a + (productOf(stock, x.sku).oz || 0) * x.need, 0) + (p.scanned?.startsWith("BX") ? 4 : 1);
  const [oz, setOz] = useState((o.weight_oz || est).toFixed(1));
  const [err, setErr] = useState(null);
  const scale = async () => {
    setErr(null);
    try { setOz((await readScale()).toFixed(1)); } catch (e) { setErr(e.message); }
  };
  return (
    <>
      <form className="inline" onSubmit={e => { e.preventDefault(); act.weigh(parseFloat(oz)); }}>
        <span className="weight"><input type="number" step="0.1" min="0.1" max="400" value={oz} onChange={e => setOz(e.target.value)} aria-label="Weight in ounces" /><em>oz</em></span>
        <button className="btn primary lg" disabled={busy}><Scale size={16} /> Confirm weight</button>
        <button type="button" className="btn" onClick={scale}>Read scale</button>
      </form>
      <p className="hint">Packed in {binName(stock, p.scanned)}. The weight is an estimate until the scale reads it.</p>
      {err && <p className="hint bad">{err}</p>}
    </>
  );
}

function ShipStep({ o, act, busy }) {
  const lb = o.label;
  return (
    <>
      <div className="inline">
        <button className="btn primary lg" onClick={act.ship} disabled={busy}><Truck size={16} /> Mark as shipped</button>
        <button className="btn" onClick={act.reprint} disabled={busy}><Printer size={14} /> Print again</button>
      </div>
      <dl className="facts">
        <div><dt>Service</dt><dd>{lb.service}</dd></div>
        <div><dt>Postage</dt><dd>${Number(lb.rate).toFixed(2)}{lb.mock ? " (test)" : ""}</dd></div>
        <div><dt>Tracking</dt><dd className="mono">{lb.tracking}</dd></div>
      </dl>
      {lb.print_ok === false && <p className="alert">{lb.print}</p>}
      <LabelActions o={o} />
    </>
  );
}

function Shipped({ o }) {
  return (
    <>
      <dl className="facts">
        <div><dt>Odoo</dt><dd>Stock taken out, delivery {o.odoo_ref}</dd></div>
        <div><dt>Shopify</dt><dd>{o.kind === "order" ? "Tracking added to the order" : "Not touched (sample)"}</dd></div>
        <div><dt>UFSI</dt><dd>Never saw it</dd></div>
      </dl>
      {o.label && <LabelActions o={o} />}
    </>
  );
}

// The Zebra prints straight from the station; until it's set up, view the label or print it as a PDF.
// Label files are fetched with the sign-in token (a plain link can't carry it), then shown or opened.
function LabelActions({ o }) {
  const [show, setShow] = useState(false);
  const [err, setErr] = useState(null);
  const base = `/api/orders/${encodeURIComponent(o.id)}/label`;
  const openPdf = async () => {
    setErr(null);
    const tab = window.open("", "_blank");  // opened now, inside the click, so the browser doesn't block it
    try { tab.location = await fileUrl(`${base}.pdf`); } catch (e) { tab?.close(); setErr(e.message); }
  };
  const downloadZpl = async () => {
    setErr(null);
    try {
      const a = Object.assign(document.createElement("a"), { href: await fileUrl(`${base}.zpl`), download: `label-${o.name.replace(/\W/g, "")}.zpl` });
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    } catch (e) { setErr(e.message); }
  };
  return (
    <div className="label-box">
      <div className="inline">
        <span className="muted">Label</span>
        <button className="btn sm" onClick={() => setShow(!show)}><Eye size={14} /> {show ? "Hide label" : "View label"}</button>
        <button className="btn sm" onClick={openPdf}><FileText size={14} /> Print (PDF)</button>
        <button className="btn sm ghost" onClick={downloadZpl}><Download size={14} /> Download ZPL</button>
      </div>
      <p className="hint">{o.label.print?.startsWith("sent to") ? `Sent to the Zebra (${o.label.print.slice(8)}).`
        : "No Zebra printer is connected, so the label was saved as a file. Use Print (PDF) to print it on any printer."}</p>
      {err && <p className="hint bad">{err}</p>}
      <AnimatePresence>
        {show && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <LabelImage path={`${base}.png`} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function LabelImage({ path }) {
  const [src, setSrc] = useState(null);
  const [failed, setFailed] = useState(null);
  useEffect(() => {
    let url = null, gone = false;
    fileUrl(path).then(u => { url = u; if (!gone) setSrc(u); else URL.revokeObjectURL(u); }).catch(e => !gone && setFailed(e.message));
    return () => { gone = true; if (url) URL.revokeObjectURL(url); };
  }, [path]);
  return (
    <div className="label-preview">
      {failed ? <p className="hint">{failed}</p>
        : src ? <img src={src} alt="Shipping label" /> : <p className="hint">Drawing the label…</p>}
    </div>
  );
}

// DYMO M25 style USB report (verify on the bench): [status, unit, exponent, weight lo, weight hi].
// status 4 = stable, unit 11 = oz, 2 = g
async function readScale() {
  if (!navigator.hid) throw new Error("Reading the scale needs Chrome or Edge. Type the weight instead.");
  const [dev] = await navigator.hid.requestDevice({ filters: [{ vendorId: 0x0922 }] });
  if (!dev) throw new Error("No scale connected. Type the weight instead, then press Confirm.");
  if (!dev.opened) await dev.open();
  return new Promise((res, rej) => {
    const t = setTimeout(() => { dev.removeEventListener("inputreport", h); rej(new Error("The scale did not settle. Try again.")); }, 6000);
    function h(e) {
      const d = new Uint8Array(e.data.buffer);
      if (d[0] !== 4) return;
      const raw = d[3] | (d[4] << 8), exp = d[2] > 127 ? d[2] - 256 : d[2], v = raw * Math.pow(10, exp);
      clearTimeout(t); dev.removeEventListener("inputreport", h);
      res(d[1] === 2 ? v / 28.3495 : v);
    }
    dev.addEventListener("inputreport", h);
  });
}
