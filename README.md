# Homebase

A small accounts app: a FastAPI + Neon Postgres backend (register, login, view/update/delete your own account) and a React frontend with Register, Log in, Home and Account screens.

## Prerequisites
- Python 3.11+
- Node 20+ and npm

## Environment
- `backend/.env.example` lists every variable. `backend/.env` contains only `DATABASE_URL`, the connection string of a **throwaway** Neon database made for this assignment (nothing else uses it).
- No signing secret is committed. On first start the backend generates a random `JWT_SECRET` into `backend/.jwt_secret` (git-ignored). You can override it with a `JWT_SECRET` env var.
- `frontend/.env` is intentionally empty. In development the app talks to `http://<current hostname>:4000`; a production build reads `VITE_API_URL` from the host's environment settings.

## Run the backend (terminal 1)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed.py                     # creates NYUgrader / Courant2026! (safe to re-run)
python -m uvicorn main:app --port 4000
```
API: http://localhost:4000 (interactive docs at `/docs`). Tables are created automatically on startup.

## Run the frontend (terminal 2)
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173 (http://127.0.0.1:5173 works too). Grader login: `NYUgrader` / `Courant2026!`.

## Test the API
With the backend running: `cd backend && python test_api.py`

## Troubleshooting
- `ModuleNotFoundError`: the venv isn't active. Run `source .venv/bin/activate` and start the server with `python -m uvicorn`, not bare `uvicorn`.
- Frontend can't reach the API: confirm the backend is on port 4000 and the frontend on 5173.

## Design decisions
- **Rule 3, 404 not 403:** another user's `:id` returns 404, identical to a nonexistent id, so the API never confirms that an account exists. The check runs before body validation, so even an invalid PATCH body returns the same code.
- **Hashing:** argon2id (64 MiB, t=3, p=4) via `argon2-cffi`, with automatic rehash if parameters are ever raised. Login does equal work and returns one message for unknown users and wrong passwords.
- **Sessions:** JWT (HS256, 60 min, issuer checked) tied to a `sessions` row. Logout deletes that row; changing a password signs out every other session. Every auth failure is 401.
- **Password change:** requires `current_password`; a token less than 5 minutes old may omit it (step-up rule), which keeps the API easy to script while a stale stolen token can't take over the account.
- **Brute force:** 5 failures per IP+username (and 50 per IP) in 15 minutes gives 429.
- **Browser session:** httpOnly cookie (SameSite=Lax locally, SameSite=None; Secure in the cross-site deployment); JS can't read it. Cookie-authenticated writes also need `X-Requested-With` and an allowed `Origin` (CSRF). API clients use `Authorization: Bearer`.
- **CORS:** only known frontend origins are allowed, with credentials: localhost / 127.0.0.1 on 5173, plus anything in `FRONTEND_ORIGINS`.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `no-store`.
- **Known limits:** the rate limiter is in memory (resets on restart); no email verification or password reset; on the deployed version (Vercel and Render are different sites) Safari and Brave block the cross-site cookie, so use Chrome/Firefox or run locally.



---

# Assignment 1B: Job tracker agent

An agent that checks the job sources in `config.yaml`, keeps only postings that match your preferences, ranks the top K, and on every later run reports **New since last run / Still in top K / Dropped**. Results show up on the **Jobs** tab of the Homebase app (behind the A1 login).

## Prerequisites
Everything from A1, plus free keys: a Groq key (LLM, console.groq.com/keys) and a Tavily key (search, tavily.com). Spend caps: both have free tiers with no card; if you switch to a paid provider, set a hard spend cap first.

## Setup (once)
```bash
cp .env.example .env               # then fill in GROQ_API_KEY, TAVILY_API_KEY, TRACKER_PASSWORD
python3 -m venv .venv-tracker && source .venv-tracker/bin/activate
pip install -r tracker/requirements.txt
```
Edit `config.yaml`: put your job sources under `sources:` and your filters under `preferences:`.

## Commands, in order
```bash
# 1. bring up the A1 backend and frontend (two terminals, as in A1 above)
cd backend && source .venv/bin/activate && pip install -r requirements.txt && python seed.py && python -m uvicorn main:app --port 4000
cd frontend && npm install && npm run dev            # http://localhost:5173, login NYUgrader / Courant2026!

# 2. run the tracker (from the repo root)
python -m tracker run --report reports/run1.md --trace traces/run1.jsonl

# 3. run it again (at least a day later for the submission)
python -m tracker run --report reports/run2.md --trace traces/run2.jsonl

# 4. reset its saved state
python -m tracker reset
```
Other: `python -m tracker.tools fetch_article <url>` (also `search_web "<q>"`, `finish '<json>'`) calls a tool without the model. `python -m tracker.test_tracker` runs the offline tests (guardrails, failure classes, budgets, recrawl, injection, provenance).

## Where state lives
The tracker is a client of the A1 backend: it logs in as `TRACKER_USER` and reads/writes state only through the endpoints below, never the database.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/tracker/state` | token, else 401 | URLs fetched, developments reported, last top K (loaded at run start) |
| POST | `/api/tracker/runs` | token, else 401 | Save a finished run in one transaction (report, ranked jobs, dropped ids, fetch log) |
| GET | `/api/tracker/runs` | token, else 401 | Run history with new/still/dropped counts and titles |
| GET | `/api/tracker/runs/latest` | token, else 401 | Latest report: ranked jobs, sources, dropped (404 if none) |
| GET | `/api/tracker/runs/{id}` | token, else 401 | One run (404 if it isn't yours) |
| GET | `/api/tracker/runs/{id}/fetches` | token, else 401 | Articles fetched in that run: title, URL, time, status |
| DELETE | `/api/tracker/state` | token, else 401 | Reset: delete all of the caller's tracker data |

Schema (created on backend startup): `tracker_runs(id, user_id, topic, k, started_at, finished_at, partial, partial_reason, report_md, stats)`, `tracker_developments(id, user_id, key, title, company, location, summary, fit, evidence, sources jsonb, first_run_id)` unique on `(user_id, key)`, `tracker_run_items(run_id, development_id, rank, status new|still|dropped)`, `tracker_fetches(id, run_id, user_id, url, title, status fetched|skipped_seen|rejected|error, detail, fetched_at)`. Every row carries a `user_id`; every query filters on it. Recrawl needs only: `fetched_urls` = fetches with status `fetched`; developments + their `sources`; the previous run's items = last top K.

## Notes
- Source URLs in `config.yaml` are *feeds* and are re-fetched every run (otherwise nothing could ever be new). Individual posting pages are skipped once fetched.
- Fetched web text is rendered as text in the app, and only `http(s)` links are clickable.


## Hackathons tab (extra)
Upcoming NYC hackathons, found through [Luma's MCP server](https://help.luma.com/p/mcp) and stored in the same database.
Luma's MCP signs in as *your* Luma account in an AI assistant, so it is not called from the server: connect Luma in Claude,
ask it for upcoming NYC hackathons as a JSON array (`name, starts_at, ends_at, location, host, url, description`), save it as
`events.json`, then load it through the API (set `TRACKER_API_URL` to your deployed backend to fill the deployed app):
```bash
python -m tracker.hackathons import events.json     # new events are tagged "new" for 7 days
python -m tracker.hackathons clear                  # remove them
```
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/tracker/hackathons/import` | token, else 401 | Upsert a list of events (per user) |
| GET | `/api/tracker/hackathons` | token, else 401 | Upcoming events for the caller |
| DELETE | `/api/tracker/hackathons` | token, else 401 | Delete the caller's events |

This tab is a snapshot you refresh, not an autonomous agent run. It does not touch the job-tracker tables.

## Note on run timing
`reports/run1.md` (2026-10-06 22:01 UTC) and `reports/run2.md` (2026-10-07 02:32 UTC) were produced only a few hours apart because of time constraints, not the required one day. The feed still changed between them (1 new, 7 still, 1 dropped). I will add a run at least one day later as `reports/run3.md` and `traces/run3.jsonl` and push it by October 9, 2026.
