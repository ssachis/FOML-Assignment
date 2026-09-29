# Homebase

A small accounts app: a FastAPI + Neon Postgres backend (register, login, view/update/delete your account) and a React frontend with Register, Log in, Home and Account screens.

## Prerequisites
- Python 3.11+
- Node 20+ and npm

## Environment
`backend/.env.example` lists every variable. `backend/.env` holds the working values for a **throwaway** Neon database created only for this assignment (no other data or accounts use it). `frontend/.env` sets `VITE_API_URL`.

## Run the backend (terminal 1)
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python seed.py                     # creates NYUgrader / Courant2026! (safe to re-run)
uvicorn main:app --port 4000
```
Backend: http://localhost:4000 (interactive docs at /docs). The `users` table is created automatically on startup.

## Run the frontend (terminal 2)
```bash
cd frontend
npm install
npm run dev
```
Frontend: http://localhost:5173

## Test the API
With the backend running: `cd backend && python test_api.py`

## Design decisions
- **Rule 3:** another user's `:id` returns **404**, identical to a nonexistent id, so the API never confirms an account exists.
- **Hashing:** argon2id via `argon2-cffi`. Login does the same work and returns the same message for unknown users and wrong passwords.
- **Tokens:** JWT (HS256), 60 min expiry. Every auth failure is 401.
- **CORS:** only `FRONTEND_ORIGIN` is allowed.

## Security measures
- Passwords: argon2id. Changing a password requires the current one.
- Brute force: 5 failed logins (or current-password guesses) per IP+username in 15 minutes returns 429.
- Revocation: each user has a `token_version`; logout and password change invalidate all existing tokens.
- Browser session: httpOnly, SameSite=Lax cookie (JS cannot read it). Cookie-authenticated writes also need an `X-Requested-With` header (CSRF defense). API clients can use `Authorization: Bearer`.
- `JWT_SECRET` must be 32+ chars. Responses set `nosniff`, `X-Frame-Options: DENY`, `no-store`.
- Known limits: the rate limiter is in memory (resets on restart); no email verification or password reset flow.
