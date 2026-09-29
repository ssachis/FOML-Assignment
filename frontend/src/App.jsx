import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL || "http://localhost:4000";

async function api(path, { method = "GET", body, token } = {}) {
  const res = await fetch(API + path, {
    method,
    credentials: "include", // session lives in an httpOnly cookie; JS never sees the token
    headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d) ? d.map((e) => `${e.field}: ${e.message}`).join("; ") : d || "Something went wrong";
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

function Field({ label, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input {...props} />
    </label>
  );
}

function AuthForm({ mode, onLogin, notice, switchTo }) {
  const [f, setF] = useState({ username: "", email: "", password: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const isReg = mode === "register";
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      if (isReg) {
        await api("/api/auth/register", { method: "POST", body: { username: f.username, password: f.password, ...(f.email ? { email: f.email } : {}) } });
      }
      await api("/api/auth/login", { method: "POST", body: { username: f.username, password: f.password } });
      onLogin();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card">
      <h2>{isReg ? "Create your account" : "Welcome back"}</h2>
      {notice && <p className="success" role="status">{notice}</p>}
      <Field label="Username" value={f.username} onChange={set("username")} autoComplete="username" required minLength={3} />
      {isReg && <Field label="Email (optional)" type="email" value={f.email} onChange={set("email")} autoComplete="email" />}
      <Field label="Password" type="password" value={f.password} onChange={set("password")} autoComplete={isReg ? "new-password" : "current-password"} required minLength={isReg ? 8 : 1} />
      {err && <p className="error" role="alert">{err}</p>}
      <button className="primary" disabled={busy}>{busy ? "Working…" : isReg ? "Create account" : "Log in"}</button>
      <p className="alt">
        {isReg ? "Already have an account?" : "New here?"}{" "}
        <button type="button" className="link" onClick={switchTo}>{isReg ? "Log in" : "Create an account"}</button>
      </p>
    </form>
  );
}

function Home({ user }) {
  return (
    <div className="card">
      <h2>You're signed in</h2>
      <dl>
        <dt>Username</dt><dd>{user.username}</dd>
        <dt>Email</dt><dd>{user.email || "Not set"}</dd>
        <dt>Member since</dt><dd>{new Date(user.created_at).toLocaleDateString()}</dd>
      </dl>
    </div>
  );
}

function Account({ user, onUser, onDeleted, onPwChanged }) {
  const [email, setEmail] = useState(user.email || "");
  const [pw, setPw] = useState("");
  const [cur, setCur] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState({ kind: "", text: "" });

  async function patch(body, okText) {
    setMsg({ kind: "", text: "" });
    try {
      const u = await api(`/api/users/${user.id}`, { method: "PATCH", body, token });
      if (body.password) { onPwChanged(); return; }
      onUser(u);
      setMsg({ kind: "ok", text: okText });
    } catch (e) {
      setMsg({ kind: "err", text: e.message });
    }
  }
  async function del() {
    try {
      await api(`/api/users/${user.id}`, { method: "DELETE", token });
      onDeleted();
    } catch (e) {
      setMsg({ kind: "err", text: e.message });
    }
  }

  return (
    <div className="stack">
      <div className="card">
        <h2>Account settings</h2>
        <form onSubmit={(e) => { e.preventDefault(); patch({ email }, "Email updated."); }}>
          <Field label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <button className="primary">Save email</button>
        </form>
        <form onSubmit={(e) => { e.preventDefault(); patch({ password: pw, current_password: cur }, "Password updated."); }}>
          <Field label="Current password" type="password" value={cur} onChange={(e) => setCur(e.target.value)} required autoComplete="current-password" />
          <Field label="New password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={8} required autoComplete="new-password" />
          <button className="primary">Change password</button>
        </form>
        {msg.text && <p className={msg.kind === "ok" ? "success" : "error"} role="status">{msg.text}</p>}
      </div>
      <div className="card danger">
        <h2>Delete account</h2>
        <p>This permanently removes your account. Type <b>{user.username}</b> to confirm.</p>
        <Field label="Username" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <button className="destructive" disabled={confirm !== user.username} onClick={del}>Delete my account</button>
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [view, setView] = useState("login");
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState("");

  const load = () =>
    api("/api/auth/me")
      .then((u) => { setUser(u); setView((v) => (v === "login" || v === "register" ? "home" : v)); })
      .catch(() => setUser(null));
  useEffect(() => { load().finally(() => setReady(true)); }, []);

  const clear = (msg = "") => { setUser(null); setView("login"); setNotice(msg); };
  const logout = () => api("/api/auth/logout", { method: "POST" }).catch(() => {}).finally(() => clear());
  const login = () => { setNotice(""); load(); };

  if (!ready) return null;
  const authed = !!user;

  return (
    <div className="shell">
      <aside className="brand">
        <h1>Home<br />base</h1>
        <p>One account. Everything you build next plugs into it.</p>
      </aside>
      <main>
        {authed && (
          <nav>
            <button className={view === "home" ? "on" : ""} onClick={() => setView("home")}>Home</button>
            <button className={view === "account" ? "on" : ""} onClick={() => setView("account")}>Account</button>
            <span className="grow" />
            <button onClick={logout}>Log out</button>
          </nav>
        )}
        {!authed && <AuthForm key={view} mode={view === "register" ? "register" : "login"} onLogin={login} notice={notice} switchTo={() => setView(view === "register" ? "login" : "register")} />}
        {authed && view === "home" && <Home user={user} />}
        {authed && view === "account" && <Account user={user} onUser={setUser} onDeleted={logout} onPwChanged={() => clear("Password changed. Log in again.")} />}
      </main>
    </div>
  );
}
