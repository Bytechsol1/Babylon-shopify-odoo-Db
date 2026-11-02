import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, Empty, StatusChip } from "../components/ui.jsx";

// Released and failed-claim orders, flagged orders and rejected tracking: each with reason, owner and age,
// oldest first, so somebody acts on every one (build plan P1 #4).
export default function Exceptions({ tick, act, open }) {
  const [rows, setRows] = useState(null);
  useEffect(() => { api("/api/exceptions").then(setRows).catch(() => setRows([])); }, [tick]);
  return (
    <div className="page">
      <Card pad={false} title={`Open exceptions${rows ? ` · ${rows.length}` : ""}`}>
        {rows === null ? <Empty>Loading…</Empty> : rows.length === 0 ? <Empty>Nothing needs attention.</Empty> : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Order</th><th>Status</th><th>What happened</th><th>Owner</th><th className="num">Age</th><th /></tr></thead>
              <tbody>
                {rows.map(r => <Row key={r.id} r={r} act={act} open={open} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function age(h) {
  if (h == null) return "";
  return h < 1 ? `${Math.round(h * 60)} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
}

function Row({ r, act, open }) {
  const [owner, setOwner] = useState(r.owner);
  return (
    <tr className="static">
      <td><button className="link strong" onClick={() => open(r.id)}>{r.name}</button><small>{r.address?.name}</small></td>
      <td><StatusChip status={r.status} /></td>
      <td className="wrap">{r.reason}</td>
      <td>
        <input className="cell-input" value={owner} placeholder="Assign…" onChange={e => setOwner(e.target.value)}
               onBlur={() => owner !== r.owner && act.assignException(r.id, owner)}
               onKeyDown={e => e.key === "Enter" && e.currentTarget.blur()} aria-label={`Owner of ${r.name}`} />
      </td>
      <td className={"num" + (r.age_hours > 24 ? " late" : "")}>{age(r.age_hours)}</td>
      <td><button className="btn sm" onClick={() => act.resolveException(r.id)}>Resolve</button></td>
    </tr>
  );
}
