import { useState } from "react";
import { Mail } from "lucide-react";
import logo from "../assets/babylon-logo.png";

// Accounts are created by invitation in Supabase; this page never signs anyone up.
export default function Login({ client, notice }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState(notice ? { kind: "err", text: notice } : null);
  const [busy, setBusy] = useState(false);

  const signIn = async e => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    const { error } = await client.auth.signInWithPassword({ email: email.trim(), password });
    setBusy(false);
    if (error) setMsg({ kind: "err", text: error.message });
  };

  const sendLink = async () => {
    if (!email.trim()) { setMsg({ kind: "err", text: "Enter your email first." }); return; }
    setBusy(true); setMsg(null);
    const { error } = await client.auth.signInWithOtp({
      email: email.trim(), options: { shouldCreateUser: false, emailRedirectTo: window.location.origin },
    });
    setBusy(false);
    setMsg(error ? { kind: "err", text: error.message } : { kind: "ok", text: "Check your email for a sign-in link." });
  };

  return (
    <div className="login">
      <form className="login-card card" onSubmit={signIn}>
        <img src={logo} alt="Babylon" width="72" height="72" />
        <h1>Pack station</h1>
        <p className="muted">Sign in with your Babylon account.</p>
        <label>Email<input type="email" autoComplete="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        {msg && <p className={"login-msg " + msg.kind}>{msg.text}</p>}
        <button className="btn primary lg" disabled={busy || !password}>Sign in</button>
        <button type="button" className="link" onClick={sendLink} disabled={busy}><Mail size={13} /> Email me a sign-in link instead</button>
      </form>
    </div>
  );
}
