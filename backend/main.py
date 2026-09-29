import os
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
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
TOKEN_TTL_MINUTES = 60

ph = PasswordHasher()  # argon2id by default
DUMMY_HASH = ph.hash("dummy-password-for-timing")
PUBLIC_COLS = "id, username, email, created_at"  # password_hash is never in this list

app = FastAPI(title="NYU A1 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


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
    username: str
    password: str


class UpdateIn(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


# ---------- auth ----------
def unauthorized():
    return HTTPException(401, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def current_user(authorization: Optional[str] = Header(default=None)):
    # Hand-written (not HTTPBearer) so EVERY failure is 401, never 403.
    if not authorization:
        raise unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise unauthorized()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        uid = int(payload["sub"])
    except (jwt.PyJWTError, ValueError, TypeError):
        raise unauthorized()
    with get_conn() as conn:
        user = conn.execute(f"SELECT {PUBLIC_COLS} FROM users WHERE id = %s", (uid,)).fetchone()
    if not user:  # e.g. account deleted but token still valid
        raise unauthorized()
    return user


def own_or_404(user_id: str, user: dict):
    # Rule 3: someone else's id -> 404, same as an id that doesn't exist.
    if str(user["id"]) != user_id:
        raise HTTPException(404, "Not found")


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
def login(body: LoginIn):
    with get_conn() as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE username = %s", (body.username,)).fetchone()
    # Same work + same message whether the user exists or not (no user enumeration).
    stored = row["password_hash"] if row else DUMMY_HASH
    try:
        ph.verify(stored, body.password)
        ok = row is not None
    except VerifyMismatchError:
        ok = False
    if not ok:
        raise HTTPException(401, "Invalid username or password")
    exp = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)
    token = jwt.encode({"sub": str(row["id"]), "exp": exp}, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "token": token, "token_type": "bearer"}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/users/{user_id}")
def read_user(user_id: str, user=Depends(current_user)):
    own_or_404(user_id, user)
    return user


@app.patch("/api/users/{user_id}")
def update_user(user_id: str, body: UpdateIn, user=Depends(current_user)):
    own_or_404(user_id, user)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "Provide email or password")
    sets, vals = [], []
    if "email" in fields:
        sets.append("email = %s")
        vals.append(fields["email"])
    if fields.get("password"):
        sets.append("password_hash = %s")
        vals.append(ph.hash(fields["password"]))
    if not sets:
        raise HTTPException(422, "Provide email or password")
    try:
        with get_conn() as conn:
            return conn.execute(
                f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING {PUBLIC_COLS}", (*vals, user["id"])
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "Email already in use")


@app.delete("/api/users/{user_id}", status_code=204)
def delete_user(user_id: str, user=Depends(current_user)):
    own_or_404(user_id, user)
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (user["id"],))
    return Response(status_code=204)
