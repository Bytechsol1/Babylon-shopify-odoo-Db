// Sign-in through Supabase Auth, switched on by the backend (AUTH=supabase). The page asks the backend at
// load time, so one build works everywhere: locally with logins off, online with them on.
let client = null;

export async function initAuth() {
  const r = await fetch("/api/public-config");
  const cfg = r.ok ? await r.json() : { auth: "off" };
  if (cfg.auth === "supabase") {
    const { createClient } = await import("@supabase/supabase-js");  // only downloaded when logins are on
    client = createClient(cfg.supabase_url, cfg.supabase_anon_key);
  }
  return client;
}

export const authClient = () => client;

export async function accessToken() {
  if (!client) return null;
  const { data } = await client.auth.getSession();
  return data.session?.access_token || null;
}
