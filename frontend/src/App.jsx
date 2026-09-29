import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL;

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(API + path, {
      method,
      credentials: "include", // session lives in an httpOnly cookie; JS never sees the token
      headers: { "Content-Type": "application/json", "X-Requested-With": "fetch" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Can't reach the server. Check that the backend is running on port 4000.");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => `${e.field}: ${e.message}`).join("; ") : d || "Something went wrong. Try again.");
  }
  return data;
}

const LABELS = ["Too short", "Weak", "Okay", "Good", "Strong"];
function strength(p) {
  let s = 0;
  if (p.length >= 8) s++;
  if (p.length >= 12) s++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++;
  if (/\d/.test(p) && /[^A-Za-z0-9]/.test(p)) s++;
  return s;
}

function FloorPlan() {
  return (
    <svg className="plan" viewBox="0 0 240 170" fill="none" aria-hidden="true">
      <path className="wall" pathLength="1" d="M10 10H230V160H10Z" />
      <path className="wall" pathLength="1" d="M10 90H110M110 10V90M150 90V160M150 90H230" />
      <path className="wall" pathLength="1" d="M110 90A24 24 0 0 1 134 114" />
      <rect className="room" x="158" y="98" width="64" height="54" rx="2" />
    </svg>
  );
}

function Field({ label, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input {...props} />
    </label>
  );
}

function PasswordField({ label, value, onChange, autoComplete, minLength, meter }) {
  const [show, setShow] = useState(false);
  const id = "pw-" + label.replace(/\s/g, "-");
  const s = strength(value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="pw">
        <input id={id} type={show ? "text" : "password"} value={value} onChange={onChange} autoComplete={autoComplete} minLength={minLength} required />
        <button type="button" className="ghost" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {meter && value && (
        <div className="meter" data-level={s}>
          <i /><i /><i /><i />
          <span>{LABELS[s]}</span>
        </div>
      )}
    </div>
  );
}

const Msg = ({ m }) => (m ? <p className={m.kind === "ok" ? "success" : "error"} role="status">{m.text}</p> : null);

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
      if (isReg) await api("/api/auth/register", { method: "POST", body: { username: f.username, password: f.password, ...(f.email ? { email: f.email } : {}) } });
      await api("/api/auth/login", { method: "POST", body: { username: f.username, password: f.password } });
      onLogin();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="panel">
      <h2>{isReg ? "Create your account" : "Welcome back"}</h2>
      {notice && <p className="success" role="status">{notice}</p>}
      <Field label="Username" value={f.username} onChange={set("username")} autoComplete="username" required minLength={3} />
      {isReg && <Field label="Email (optional)" type="email" value={f.email} onChange={set("email")} autoComplete="email" />}
      <PasswordField label="Password" value={f.password} onChange={set("password")} autoComplete={isReg ? "new-password" : "current-password"} minLength={isReg ? 8 : undefined} meter={isReg} />
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
    <section className="stack">
      <div>
        <h2 className="greet">Welcome, {user.username}</h2>
        <p className="sub">You're signed in. This is your home base.</p>
      </div>
      <dl className="facts">
        <dt>Username</dt><dd>{user.username}</dd>
        <dt>Email</dt><dd>{user.email || "Not set"}</dd>
        <dt>Member since</dt><dd>{new Date(user.created_at).toLocaleDateString(undefined, { dateStyle: "long" })}</dd>
      </dl>
      <div className="empty">
        <b>This room is empty</b>
        <span>Whatever you build next gets furnished here.</span>
      </div>
    </section>
  );
}

function Account({ user, onUser, onDeleted }) {
  const [email, setEmail] = useState(user.email || "");
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [m, setM] = useState({});
  const say = (k, kind, text) => setM((x) => ({ ...x, [k]: { kind, text } }));
  const url = `/api/users/${user.id}`;

  async function saveEmail(e) {
    e.preventDefault();
    try {
      onUser(await api(url, { method: "PATCH", body: { email } }));
      say("email", "ok", "Email saved.");
    } catch (err) { say("email", "err", err.message); }
  }
  async function savePw(e) {
    e.preventDefault();
    try {
      await api(url, { method: "PATCH", body: { password: pw, current_password: cur } });
      setCur(""); setPw("");
      say("pw", "ok", "Password changed. Your other sessions were signed out.");
    } catch (err) { say("pw", "err", err.message); }
  }
  async function del() {
    try { await api(url, { method: "DELETE" }); onDeleted(); }
    catch (err) { say("del", "err", err.message); }
  }

  return (
    <section className="stack">
      <h2 className="greet">Account</h2>
      <form onSubmit={saveEmail} className="block">
        <h3>Email</h3>
        <Field label="Email address" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <Msg m={m.email} />
        <button className="primary">Save email</button>
      </form>
      <form onSubmit={savePw} className="block">
        <h3>Password</h3>
        <PasswordField label="Current password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" />
        <PasswordField label="New password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" minLength={8} meter />
        <Msg m={m.pw} />
        <button className="primary">Change password</button>
      </form>
      <div className="block danger">
        <h3>Delete account</h3>
        <p>This permanently deletes your account and signs you out everywhere. Type <b>{user.username}</b> to confirm.</p>
        <Field label="Username" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        <Msg m={m.del} />
        <button className="destructive" disabled={confirm !== user.username} onClick={del}>Delete my account</button>
      </div>
    </section>
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
        <h1>Homebase</h1>
        <FloorPlan />
        <p>One account. Everything you build next plugs into it.</p>
      </aside>
      <main>
        {authed && (
          <header className="top">
            <div className="who"><span className="avatar">{user.username[0].toUpperCase()}</span><b>{user.username}</b></div>
            <nav>
              <button className={view === "home" ? "on" : ""} onClick={() => setView("home")}>Home</button>
              <button className={view === "account" ? "on" : ""} onClick={() => setView("account")}>Account</button>
              <button onClick={logout}>Log out</button>
            </nav>
          </header>
        )}
        {!authed && <AuthForm key={view} mode={view === "register" ? "register" : "login"} onLogin={login} notice={notice} switchTo={() => setView(view === "register" ? "login" : "register")} />}
        {authed && view === "home" && <Home user={user} />}
        {authed && view === "account" && <Account user={user} onUser={setUser} onDeleted={() => clear("Your account was deleted.")} />}
      </main>
    </div>
  );
}
