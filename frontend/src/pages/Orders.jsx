import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { ACTIVE, DONE, NOT_OURS } from "../lane.js";
import { Card } from "../components/ui.jsx";
import { OrderTable } from "./Dashboard.jsx";

const PAGE = 50;
const FILTERS = [  // tab, label, server view, statuses counted for the tab
  ["all", "All", "all", null],
  ["pack", "To pack", "queue", ACTIVE],
  ["done", "Done", "done", DONE],
  ["ufsi", "Kept by UFSI", "decisions", NOT_OURS],
];

// One page at a time from the server, filtered and searched there. Counts come from the summary.
export default function Orders({ q, tick, counts, open }) {
  const [f, setF] = useState("all");
  const [limit, setLimit] = useState(PAGE);
  const [rows, setRows] = useState(null);
  const latest = useRef(0);
  useEffect(() => { setLimit(PAGE); }, [f, q]);
  useEffect(() => {
    const view = FILTERS.find(x => x[0] === f)[2];
    const ask = ++latest.current;
    api(`/api/orders?view=${view}&limit=${limit}&q=${encodeURIComponent(q)}`)
      .then(r => { if (ask === latest.current) setRows(r); })  // a slower, older answer never overwrites a newer one
      .catch(() => {});
  }, [f, q, limit, tick]);
  const total = statuses => statuses ? statuses.reduce((a, s) => a + (counts[s] || 0), 0) : Object.values(counts).reduce((a, n) => a + n, 0);
  return (
    <div className="page">
      <Card pad={false} title={
        <div className="seg-tabs" role="tablist">
          {FILTERS.map(([k, label, , statuses]) => (
            <button key={k} role="tab" aria-selected={f === k} onClick={() => setF(k)}>
              {label}<span>{total(statuses)}</span>
            </button>
          ))}
        </div>
      }>
        {rows === null ? <p className="empty">Loading…</p> : (
          <>
            <OrderTable orders={rows} open={open} empty={q ? `No orders match “${q}”.` : "No orders here yet."} />
            {rows.length >= limit && (
              <div className="more-row"><button className="btn" onClick={() => setLimit(l => l + PAGE)}>Show {PAGE} more</button></div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
