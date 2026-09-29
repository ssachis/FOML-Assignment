# Homebase

A small accounts app: a FastAPI + Neon Postgres backend (register, login, view/update/delete your own account) and a React frontend with Register, Log in, Home and Account screens.

## Prerequisites
- Python 3.11+
- Node 20+ and npm

## Environment
- `backend/.env.example` lists every variable. `backend/.env` contains only `DATABASE_URL`, the connection string of a **throwaway** Neon database made for this assignment (nothing else uses it).
- No signing secret is committed. On first start the backend generates a random `JWT_SECRET` into `backend/.jwt_secret` (git-ignored). You can override it with a `JWT_SECRET` env var.
- `frontend/.env` is optional; the API URL defaults to `http://<current hostname>:4000`.

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
- **Browser session:** httpOnly, SameSite=Lax cookie; JS can't read it. Cookie-authenticated writes also need `X-Requested-With` and an allowed `Origin` (CSRF). API clients use `Authorization: Bearer`.
- **CORS:** only the frontend origins (localhost / 127.0.0.1 on 5173) are allowed, with credentials.
- **Headers:** `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `no-store`.
- **Known limits:** the rate limiter is in memory (resets on restart); no email verification or password reset; cross-domain deployment would need `SameSite=None; Secure` cookies.
