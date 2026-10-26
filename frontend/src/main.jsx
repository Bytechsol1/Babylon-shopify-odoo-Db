import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import Login from "./components/Login.jsx";
import { initAuth } from "./auth.js";
import "./styles.css";

// Shows the sign-in page when the backend requires it, the station otherwise.
function Root() {
  const [s, setS] = useState({ ready: false, client: null, session: null, notice: null });

  useEffect(() => {
    let unsub = null;
    initAuth().then(async client => {
      if (!client) { setS({ ready: true }); return; }
      const { data } = await client.auth.getSession();
      setS({ ready: true, client, session: data.session });
      unsub = client.auth.onAuthStateChange((_event, session) => setS(prev => ({ ...prev, session }))).data.subscription;
    }).catch(() => setS({ ready: true, notice: "Could not reach the server." }));
    return () => unsub?.unsubscribe();
  }, []);

  useEffect(() => {
    if (!s.client) return;
    const expired = e => {  // the API refused the session: expired, revoked, or not on the allowed list
      s.client.auth.signOut();
      setS(prev => ({ ...prev, session: null, notice: e.detail || "Your session ended. Sign in again." }));
    };
    window.addEventListener("auth:expired", expired);
    return () => window.removeEventListener("auth:expired", expired);
  }, [s.client]);

  if (!s.ready) return null;
  if (s.client && !s.session) return <Login client={s.client} notice={s.notice} />;
  return <App user={s.session?.user?.email} onSignOut={s.client ? () => s.client.auth.signOut() : null} />;
}

createRoot(document.getElementById("root")).render(<StrictMode><Root /></StrictMode>);
