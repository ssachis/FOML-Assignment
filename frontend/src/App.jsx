import { useEffect, useState } from "react";

// Dev: talk to the backend on :4000 (cross-origin, uses CORS). Production build: same-origin "/api/..."
// (the host proxies it to the backend), so the session cookie is first-party.
const API = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? `http://${window.location.hostname}:4000` : "");

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

// Anything that came from the web is rendered as TEXT (React escapes it). Links only for http(s),
// so a stored "javascript:" URL can never become a clickable script.
function SafeLink({ url }) {
  const ok = /^https?:\/\//i.test(url || "");
  return ok ? <a href={url} target="_blank" rel="noopener noreferrer nofollow">{url}</a> : <span className="inert">{url}</span>;
}
const when = (t) => new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

function JobCard({ d }) {
  return (
    <li className="job">
      <div className="jobhead"><span className="rank">{d.rank ?? "–"}</span><b>{d.title}</b><span className="co">{d.company}{d.location ? ` · ${d.location}` : ""}</span></div>
      {d.summary && <p>{d.summary}</p>}
      {d.fit && <p className="fit">Fit: {d.fit}</p>}
      <div className="srcs">{d.sources.map((u) => <SafeLink key={u} url={u} />)}</div>
    </li>
  );
}

function Jobs() {
  const [latest, setLatest] = useState(null);
  const [runs, setRuns] = useState([]);
  const [sel, setSel] = useState(null);
  const [fetches, setFetches] = useState([]);
  const [err, setErr] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const r = await api("/api/tracker/runs");
        setRuns(r);
        if (r.length) {
          setLatest(await api("/api/tracker/runs/latest"));
          setSel(r[0].id);
        }
      } catch (e) { setErr(e.message); } finally { setLoaded(true); }
    })();
  }, []);
  useEffect(() => {
    if (sel != null) api(`/api/tracker/runs/${sel}/fetches`).then(setFetches).catch((e) => setErr(e.message));
  }, [sel]);

  if (!loaded) return <p className="sub">Loading…</p>;
  if (err) return <p className="error" role="alert">{err}</p>;
  if (!latest) return <div className="empty"><b>No runs yet</b><span>Run <code>python -m tracker run</code> from the repo root, then refresh.</span></div>;

  const by = (s) => latest.developments.filter((d) => d.status === s);
  return (
    <section className="stack">
      <div>
        <h2 className="greet">Job tracker</h2>
        <p className="sub">{latest.topic}</p>
      </div>

      <div className="block">
        <h3>Latest report · {when(latest.finished_at)}</h3>
        {latest.partial && <p className="warn" role="status">Partial report: {latest.partial_reason}</p>}
        {[["New since last run", by("new")], ["Still in top K", by("still")]].map(([label, rows]) => (
          <div key={label}>
            <h4>{label} ({rows.length})</h4>
            {rows.length ? <ol className="jobs">{rows.map((d) => <JobCard key={d.id} d={d} />)}</ol> : <p className="sub">None.</p>}
          </div>
        ))}
        <h4>Dropped ({latest.dropped.length})</h4>
        {latest.dropped.length ? <ul className="plain">{latest.dropped.map((d) => <li key={d.id}>{d.title}, {d.company}</li>)}</ul> : <p className="sub">None.</p>}
      </div>

      <div className="block">
        <h3>Run history</h3>
        <ul className="plain runs">
          {runs.map((r) => (
            <li key={r.id} className={r.id === sel ? "on" : ""}>
              <button className="link" onClick={() => setSel(r.id)}>{when(r.started_at)}</button>
              {r.partial && <span className="tag">partial</span>}
              <span className="sub"> {r.counts.new} new · {r.counts.still} still · {r.counts.dropped} dropped · {r.stats?.fetches ?? 0} fetches · {r.stats?.tokens ?? 0} tokens</span>
              {r.new_titles.length > 0 && <div className="sub">New: {r.new_titles.join("; ")}</div>}
              {r.dropped_titles.length > 0 && <div className="sub">Dropped: {r.dropped_titles.join("; ")}</div>}
            </li>
          ))}
        </ul>
      </div>

      <div className="block">
        <h3>Articles fetched{sel ? ` · run ${when(runs.find((r) => r.id === sel)?.started_at)}` : ""}</h3>
        <div className="scroll">
          <table>
            <thead><tr><th>Title</th><th>URL</th><th>Fetched</th><th>Status</th></tr></thead>
            <tbody>
              {fetches.map((f, i) => (
                <tr key={i}>
                  <td>{f.title || "–"}</td>
                  <td className="url"><SafeLink url={f.url} />{f.detail && <div className="sub">{f.detail}</div>}</td>
                  <td>{when(f.fetched_at)}</td>
                  <td><span className={`tag ${f.status}`}>{f.status.replace("_", " ")}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function Hackathons() {
  const [rows, setRows] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api("/api/tracker/hackathons").then(setRows).catch((e) => setErr(e.message)); }, []);
  if (err) return <p className="error" role="alert">{err}</p>;
  if (!rows) return <p className="sub">Loading…</p>;
  const fmt = (t) => (t ? new Date(t).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "Date to be announced");
  const isNew = (r) => Date.now() - new Date(r.added_at).getTime() < 7 * 24 * 3600 * 1000;
  return (
    <section className="stack">
      <div>
        <h2 className="greet">NYC hackathons</h2>
        <p className="sub">NYC hackathons from a community GitHub list · {rows.length} listed</p>
      </div>
      {rows.length === 0 ? (
        <div className="empty"><b>No hackathons loaded</b><span>Load them with <code>python -m tracker.hackathon_sync</code></span></div>
      ) : (
        <ul className="jobs">
          {rows.map((r) => (
            <li key={r.id} className="job">
              <div className="jobhead"><b>{r.name}</b>{isNew(r) && <span className="tag fetched">new</span>}<span className="co">{fmt(r.starts_at)}</span></div>
              {(r.location || r.host) && <p className="fit">{[r.location, r.host].filter(Boolean).join(" · ")}</p>}
              {r.description && <p>{r.description}</p>}
              <div className="srcs"><SafeLink url={r.url} /></div>
            </li>
          ))}
        </ul>
      )}
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
    <div className={"shell" + (authed && (view === "jobs" || view === "hackathons") ? " full" : "")}>
      <aside className="brand">
        <h1>Homebase</h1>
        <FloorPlan />
        <p>One account. Everything you build next plugs into it.</p>
      </aside>
      <main className={authed && (view === "jobs" || view === "hackathons") ? "wide" : ""}>
        {authed && (
          <header className="top">
            <div className="who"><span className="avatar">{user.username[0].toUpperCase()}</span><b>{user.username}</b></div>
            <nav>
              <button className={view === "home" ? "on" : ""} onClick={() => setView("home")}>Home</button>
              <button className={view === "jobs" ? "on" : ""} onClick={() => setView("jobs")}>Jobs</button>
              <button className={view === "hackathons" ? "on" : ""} onClick={() => setView("hackathons")}>Hackathons</button>
              <button className={view === "account" ? "on" : ""} onClick={() => setView("account")}>Account</button>
              <button onClick={logout}>Log out</button>
            </nav>
          </header>
        )}
        {!authed && <AuthForm key={view} mode={view === "register" ? "register" : "login"} onLogin={login} notice={notice} switchTo={() => setView(view === "register" ? "login" : "register")} />}
        {authed && view === "home" && <Home user={user} />}
        {authed && view === "jobs" && <Jobs />}
        {authed && view === "hackathons" && <Hackathons />}
        {authed && view === "account" && <Account user={user} onUser={setUser} onDeleted={() => clear("Your account was deleted.")} />}
      </main>
    </div>
  );
}
