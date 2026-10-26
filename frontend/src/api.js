// Thin wrapper over the Python API. Errors carry the server's message and, for a refused
// label, the list of checks that failed.
export const enc = encodeURIComponent;

export async function api(path, method = "GET", body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j = null;
  try { j = await r.json(); } catch { /* empty body */ }
  if (!r.ok) {
    const d = j && j.detail;
    const err = new Error(typeof d === "string" ? d : (d && d.message) || r.statusText);
    err.checks = d && d.checks;
    throw err;
  }
  return j;
}
