import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, EmailStr, Field

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]
JWT_SECRET = os.environ["JWT_SECRET"]
if len(JWT_SECRET) < 32:
    raise RuntimeError("JWT_SECRET must be at least 32 characters")
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # set to 1 when served over HTTPS
COOKIE_NAME = "session"
TOKEN_TTL_MINUTES = 60

ph = PasswordHasher()  # argon2id by default
DUMMY_HASH = ph.hash("dummy-password-for-timing")
PUBLIC_COLS = "id, username, email, created_at"  # password_hash / token_version never returned

app = FastAPI(title="NYU A1 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


@app.on_event("startup")
def init_db():
    with get_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                 id SERIAL PRIMARY KEY,
                 username TEXT UNIQUE NOT NULL,
                 email TEXT UNIQUE,
                 password_hash TEXT NOT NULL,
                 created_at TIMESTAMPTZ DEFAULT now())"""
        )
        # token_version lets us revoke all of a user's tokens (logout / password change)
        conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0")


# ---------- brute-force protection (in-memory; resets on restart) ----------
MAX_FAILS, WINDOW = 5, 15 * 60
_fails: dict = {}


def _recent(key):
    now = time.time()
    _fails[key] = [t for t in _fails.get(key, []) if now - t < WINDOW]
    return _fails[key]


def check_limit(key):
    ts = _recent(key)
    if len(ts) >= MAX_FAILS:
        retry = int(WINDOW - (time.time() - ts[0])) + 1
        raise HTTPException(429, "Too many failed attempts. Try again later.", headers={"Retry-After": str(retry)})


def record_fail(key):
    _recent(key).append(time.time())


def client_ip(request: Request):
    return request.client.host if request.client else "unknown"


# ---------- error handling: never echo input (could contain passwords) ----------
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errs})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------- schemas ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    email: Optional[EmailStr] = None


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=128)


class UpdateIn(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    current_password: Optional[str] = Field(default=None, max_length=128)


# ---------- auth ----------
def unauthorized():
    return HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def make_token(uid: int, tv: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)
    return jwt.encode({"sub": str(uid), "tv": tv, "exp": exp}, JWT_SECRET, algorithm="HS256")


def authenticate(request: Request, authorization: Optional[str]):
    # Hand-written (not HTTPBearer) so EVERY failure is 401, never 403.
    token, from_cookie = None, False
    if authorization:
        scheme, _, t = authorization.partition(" ")
        if scheme.lower() != "bearer" or not t:
            raise unauthorized()
        token = t
    elif request.cookies.get(COOKIE_NAME):
        token, from_cookie = request.cookies[COOKIE_NAME], True
    if not token:
        raise unauthorized()
    # CSRF defense for cookie auth: writes need a custom header, which forces a CORS preflight.
    if from_cookie and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(403, "Missing CSRF header")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub", "tv"]})
        uid, tv = int(payload["sub"]), int(payload["tv"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise unauthorized()
    with get_conn() as conn:
        row = conn.execute(f"SELECT {PUBLIC_COLS}, token_version FROM users WHERE id = %s", (uid,)).fetchone()
    if not row or row["token_version"] != tv:  # deleted user or revoked token
        raise unauthorized()
    row.pop("token_version")
    return row


def current_user(request: Request, authorization: Optional[str] = Header(default=None)):
    return authenticate(request, authorization)


def own_or_404(user_id: str, user: dict):
    # Rule 3: someone else's id -> 404, same as an id that doesn't exist.
    if str(user["id"]) != user_id:
        raise HTTPException(404, "Not found")


def clear_cookie(resp: Response):
    resp.delete_cookie(COOKIE_NAME, path="/")


# ---------- routes ----------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn):
    try:
        with get_conn() as conn:
            user = conn.execute(
                f"INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING {PUBLIC_COLS}",
                (body.username, body.email, ph.hash(body.password)),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Username or email already in use")
    return user


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    key = f"{client_ip(request)}|{body.username.lower()}"
    check_limit(key)  # applies to unknown usernames too, so it leaks nothing
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash, token_version FROM users WHERE username = %s", (body.username,)
        ).fetchone()
    stored = row["password_hash"] if row else DUMMY_HASH
    try:
        ph.verify(stored, body.password)
        ok = row is not None
    except VerifyMismatchError:
        ok = False
    if not ok:
        record_fail(key)
        raise HTTPException(401, "Invalid username or password")
    _fails.pop(key, None)
    token = make_token(row["id"], row["token_version"])
    response.set_cookie(
        COOKIE_NAME, token, max_age=TOKEN_TTL_MINUTES * 60, httponly=True,
        samesite="lax", secure=COOKIE_SECURE, path="/",
    )
    return {"access_token": token, "token": token, "token_type": "bearer"}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, authorization: Optional[str] = Header(default=None)):
    try:  # revoke every token this user has; always clear the cookie
        user = authenticate(request, authorization)
        with get_conn() as conn:
            conn.execute("UPDATE users SET token_version = token_version + 1 WHERE id = %s", (user["id"],))
    except HTTPException:
        pass
    resp = Response(status_code=204)
    clear_cookie(resp)
    return resp


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/users/{user_id}")
def read_user(user_id: str, user=Depends(current_user)):
    own_or_404(user_id, user)
    return user


@app.patch("/api/users/{user_id}")
def update_user(user_id: str, body: UpdateIn, request: Request, response: Response, user=Depends(current_user)):
    own_or_404(user_id, user)
    fields = body.model_dump(exclude_unset=True)
    sets, vals, pw_changed = [], [], False
    if "email" in fields:
        sets.append("email = %s")
        vals.append(fields["email"])
    if fields.get("password"):
        if not fields.get("current_password"):
            raise HTTPException(422, "current_password is required to change password")
        key = f"{client_ip(request)}|pw{user['id']}"
        check_limit(key)
        with get_conn() as conn:
            row = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()
        try:
            ph.verify(row["password_hash"], fields["current_password"])
        except VerifyMismatchError:
            record_fail(key)
            raise HTTPException(400, "Current password is incorrect")
        _fails.pop(key, None)
        sets.append("password_hash = %s")
        vals.append(ph.hash(fields["password"]))
        sets.append("token_version = token_version + 1")  # revokes all existing tokens
        pw_changed = True
    if not sets:
        raise HTTPException(422, "Provide email or password")
    try:
        with get_conn() as conn:
            updated = conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {PUBLIC_COLS}", (*vals, user["id"])
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Email already in use")
    if pw_changed:
        clear_cookie(response)
    return updated


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: str, user=Depends(current_user)):
    own_or_404(user_id, user)
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user["id"],))
    resp = Response(status_code=204)
    clear_cookie(resp)
    return resp
