// Thin wrapper over the Python API. Sends the sign-in token when logins are on. Errors carry the server's
// message and, for a refused label, the list of checks that failed. A 401 means the session ended.
import { accessToken } from "./auth.js";

export const enc = encodeURIComponent;

async function headers(extra) {
  const t = await accessToken();
  return { ...(t ? { Authorization: `Bearer ${t}` } : {}), ...extra };
}

export async function api(path, method = "GET", body) {
  const r = await fetch(path, {
    method,
    headers: await headers({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let j = null;
  try { j = await r.json(); } catch { /* empty body */ }
  if (r.status === 401) window.dispatchEvent(new CustomEvent("auth:expired", { detail: j?.detail }));
  if (!r.ok) {
    const d = j && j.detail;
    const err = new Error(typeof d === "string" ? d : (d && d.message) || r.statusText);
    err.checks = d && d.checks;
    throw err;
  }
  return j;
}

// Files (label image, PDF, ZPL) fetched with the sign-in token, as an object URL the page can show or open.
export async function fileUrl(path) {
  const r = await fetch(path, { headers: await headers() });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  return URL.createObjectURL(await r.blob());
}
