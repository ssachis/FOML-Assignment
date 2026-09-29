import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
from pydantic import AliasChoices, BaseModel, EmailStr, Field

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

def load_secret() -> str:
"""JWT secret: env var if set, else a random one generated once into a git-ignored file."""
s = os.getenv("JWT_SECRET", "")

```
if s:
    if len(s) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")
    return s

f = Path(__file__).with_name(".jwt_secret")

if f.exists():
    return f.read_text().strip()

s = secrets.token_hex(32)
f.write_text(s)
f.chmod(0o600)

return s
```

JWT_SECRET = load_secret()
ISSUER = "nyu-a1"

# Comma-separated list of allowed frontend origins.

#

# Render example:

# FRONTEND_ORIGINS=https://foml-assignment.vercel.app

#

# Multiple origins can be supplied:

# FRONTEND_ORIGINS=https://foml-assignment.vercel.app,https://another-site.vercel.app

ORIGINS = {
o.strip()
for o in os.getenv("FRONTEND_ORIGINS", "").split(",")
if o.strip()
} | {
"http://localhost:5173",
"http://127.0.0.1:5173",
}

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax").lower()

if COOKIE_SAMESITE == "none" and not COOKIE_SECURE:
raise RuntimeError(
"COOKIE_SAMESITE=none requires COOKIE_SECURE=1 "
"(browsers reject it otherwise)"
)

COOKIE_NAME = "session"
TOKEN_TTL_MINUTES = 60
FRESH_SECONDS = 300

# argon2id, RFC 9106 "low memory" profile

ph = PasswordHasher(
time_cost=3,
memory_cost=65536,
parallelism=4,
)

DUMMY_HASH = ph.hash("dummy-password-for-timing")
PUBLIC_COLS = "id, username, email, created_at"

app = FastAPI(title="NYU A1 API")

app.add_middleware(
CORSMiddleware,
allow_origins=sorted(ORIGINS),
allow_credentials=True,
allow_methods=[
"GET",
"POST",
"PATCH",
"DELETE",
"OPTIONS",
],
allow_headers=[
"Authorization",
"Content-Type",
"X-Requested-With",
],
)

@app.middleware("http")
async def security_headers(request: Request, call_next):
resp = await call_next(request)

```
resp.headers["X-Content-Type-Options"] = "nosniff"
resp.headers["X-Frame-Options"] = "DENY"
resp.headers["Referrer-Policy"] = "no-referrer"
resp.headers["Permissions-Policy"] = (
    "camera=(), microphone=(), geolocation=()"
)
resp.headers["Cache-Control"] = "no-store"

return resp
```

def get_conn():
return psycopg.connect(
DATABASE_URL,
row_factory=dict_row,
)

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

```
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
             sid TEXT PRIMARY KEY,
             user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
             created_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
    )
```

# ---------- brute-force protection ----------

MAX_FAILS, WINDOW = 5, 15 * 60
_fails: dict = {}

def _recent(key):
now = time.time()

```
_fails[key] = [
    t
    for t in _fails.get(key, [])
    if now - t < WINDOW
]

return _fails[key]
```

def check_limit(key, limit=MAX_FAILS):
ts = _recent(key)

```
if len(ts) >= limit:
    retry = int(
        WINDOW - (time.time() - ts[0])
    ) + 1

    raise HTTPException(
        429,
        "Too many failed attempts. Try again later.",
        headers={
            "Retry-After": str(retry)
        },
    )
```

def record_fail(key):
_recent(key).append(time.time())

def client_ip(request: Request):
return (
request.client.host
if request.client
else "unknown"
)

# ---------- errors ----------

@app.exception_handler(RequestValidationError)
async def validation_handler(
request: Request,
exc: RequestValidationError,
):
errs = [
{
"field": ".".join(
str(p)
for p in e["loc"][1:]
),
"message": e["msg"],
}
for e in exc.errors()
]

```
return JSONResponse(
    status_code=422,
    content={"detail": errs},
)
```

@app.exception_handler(Exception)
async def unhandled(
request: Request,
exc: Exception,
):
return JSONResponse(
status_code=500,
content={
"detail": "Internal server error"
},
)

# ---------- schemas ----------

class RegisterIn(BaseModel):
username: str = Field(
min_length=3,
max_length=64,
pattern=r"^[A-Za-z0-9_.@+-]+$",
)

```
password: str = Field(
    min_length=8,
    max_length=128,
)

email: Optional[EmailStr] = None
```

class LoginIn(BaseModel):
username: Optional[str] = Field(
default=None,
max_length=254,
)

```
email: Optional[str] = Field(
    default=None,
    max_length=254,
)

password: str = Field(
    max_length=128,
)
```

class UpdateIn(BaseModel):
email: Optional[EmailStr] = None

```
password: Optional[str] = Field(
    default=None,
    min_length=8,
    max_length=128,
    validation_alias=AliasChoices(
        "password",
        "new_password",
    ),
)

current_password: Optional[str] = Field(
    default=None,
    max_length=128,
    validation_alias=AliasChoices(
        "current_password",
        "old_password",
    ),
)
```

# ---------- auth ----------

def unauthorized():
return HTTPException(
401,
"Not authenticated",
headers={
"WWW-Authenticate": "Bearer"
},
)

def make_token(
uid: int,
sid: str,
) -> str:
now = datetime.now(timezone.utc)

```
claims = {
    "sub": str(uid),
    "sid": sid,
    "iss": ISSUER,
    "iat": now,
    "exp": now + timedelta(
        minutes=TOKEN_TTL_MINUTES
    ),
}

return jwt.encode(
    claims,
    JWT_SECRET,
    algorithm="HS256",
)
```

def authenticate(
request: Request,
authorization: Optional[str],
):
token = None
from_cookie = False

```
if authorization:
    scheme, _, t = authorization.partition(" ")

    if (
        scheme.lower() != "bearer"
        or not t
    ):
        raise unauthorized()

    token = t

elif request.cookies.get(COOKIE_NAME):
    token = request.cookies[COOKIE_NAME]
    from_cookie = True

if not token:
    raise unauthorized()

if (
    from_cookie
    and request.method
    not in ("GET", "HEAD", "OPTIONS")
):
    origin = request.headers.get("origin")

    if (
        request.headers.get(
            "x-requested-with"
        ) != "fetch"
        or (
            origin
            and origin not in ORIGINS
        )
    ):
        raise HTTPException(
            403,
            "CSRF check failed",
        )

try:
    p = jwt.decode(
        token,
        JWT_SECRET,
        algorithms=["HS256"],
        issuer=ISSUER,
        options={
            "require": [
                "exp",
                "iat",
                "iss",
                "sub",
                "sid",
            ]
        },
    )

    uid = int(p["sub"])
    sid = str(p["sid"])
    iat = int(p["iat"])

except (
    jwt.PyJWTError,
    ValueError,
    KeyError,
    TypeError,
):
    raise unauthorized()

with get_conn() as conn:
    row = conn.execute(
        "SELECT u.id, u.username, u.email, u.created_at "
        "FROM users u "
        "JOIN sessions s ON s.user_id = u.id "
        "WHERE u.id = %s AND s.sid = %s",
        (uid, sid),
    ).fetchone()

if not row:
    raise unauthorized()

request.state.sid = sid
request.state.iat = iat

return row
```

def current_user(
request: Request,
authorization: Optional[str] = Header(
default=None
),
):
return authenticate(
request,
authorization,
)

def owned_user(
user_id: str,
user=Depends(current_user),
):
if str(user["id"]) != user_id:
raise HTTPException(
404,
"Not found",
)

```
return user
```

def clear_cookie(resp: Response):
resp.delete_cookie(
COOKIE_NAME,
path="/",
)

# ---------- routes ----------

@app.get("/healthz")
def healthz():
return {"status": "ok"}

@app.post(
"/api/auth/register",
status_code=201,
)
def register(body: RegisterIn):
try:
with get_conn() as conn:
return conn.execute(
f"""
INSERT INTO users
(username, email, password_hash)
VALUES
(%s, %s, %s)
RETURNING {PUBLIC_COLS}
""",
(
body.username,
body.email,
ph.hash(body.password),
),
).fetchone()

```
except psycopg.errors.UniqueViolation:
    raise HTTPException(
        409,
        "Username or email already in use",
    )
```

@app.post("/api/auth/login")
def login(
body: LoginIn,
request: Request,
response: Response,
):
ident = body.username or body.email

```
if not ident:
    raise HTTPException(
        422,
        "username is required",
    )

ip = client_ip(request)

key = f"{ip}|{ident.lower()}"
ipkey = f"ip|{ip}"

check_limit(key)
check_limit(ipkey, 50)

with get_conn() as conn:
    row = conn.execute(
        """
        SELECT id, password_hash
        FROM users
        WHERE username = %s
           OR lower(email) = lower(%s)
        ORDER BY (username = %s) DESC
        LIMIT 1
        """,
        (
            ident,
            ident,
            ident,
        ),
    ).fetchone()

stored = (
    row["password_hash"]
    if row
    else DUMMY_HASH
)

try:
    ph.verify(
        stored,
        body.password,
    )
    ok = row is not None

except VerifyMismatchError:
    ok = False

if not ok:
    record_fail(key)
    record_fail(ipkey)

    raise HTTPException(
        401,
        "Invalid username or password",
    )

_fails.pop(key, None)

sid = secrets.token_hex(16)

with get_conn() as conn:
    if ph.check_needs_rehash(
        row["password_hash"]
    ):
        conn.execute(
            """
            UPDATE users
            SET password_hash = %s
            WHERE id = %s
            """,
            (
                ph.hash(body.password),
                row["id"],
            ),
        )

    conn.execute(
        """
        DELETE FROM sessions
        WHERE created_at < now()
          - interval '2 hours'
        """
    )

    conn.execute(
        """
        INSERT INTO sessions
            (sid, user_id)
        VALUES
            (%s, %s)
        """,
        (
            sid,
            row["id"],
        ),
    )

token = make_token(
    row["id"],
    sid,
)

response.set_cookie(
    COOKIE_NAME,
    token,
    max_age=TOKEN_TTL_MINUTES * 60,
    httponly=True,
    samesite=COOKIE_SAMESITE,
    secure=COOKIE_SECURE,
    path="/",
)

return {
    "access_token": token,
    "token": token,
    "token_type": "bearer",
}
```

@app.post(
"/api/auth/logout",
status_code=204,
)
def logout(
request: Request,
authorization: Optional[str] = Header(
default=None
),
):
try:
authenticate(
request,
authorization,
)

```
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE sid = %s",
            (request.state.sid,),
        )

except HTTPException:
    pass

resp = Response(status_code=204)
clear_cookie(resp)

return resp
```

@app.get("/api/auth/me")
def me(
user=Depends(current_user),
):
return user

@app.get("/api/users/{user_id}")
def read_user(
user=Depends(owned_user),
):
return user

@app.patch("/api/users/{user_id}")
def update_user(
body: UpdateIn,
request: Request,
user=Depends(owned_user),
):
fields = body.model_dump(
exclude_unset=True
)

```
sets = []
vals = []
pw_changed = False

if "email" in fields:
    sets.append("email = %s")
    vals.append(fields["email"])

if fields.get("password"):
    if fields.get("current_password"):
        key = (
            f"{client_ip(request)}"
            f"|pw{user['id']}"
        )

        check_limit(key)

        with get_conn() as conn:
            row = conn.execute(
                """
                SELECT password_hash
                FROM users
                WHERE id = %s
                """,
                (user["id"],),
            ).fetchone()

        try:
            ph.verify(
                row["password_hash"],
                fields["current_password"],
            )

        except VerifyMismatchError:
            record_fail(key)

            raise HTTPException(
                400,
                "Current password is incorrect",
            )

        _fails.pop(key, None)

    elif (
        time.time()
        - request.state.iat
        > FRESH_SECONDS
    ):
        raise HTTPException(
            403,
            "Send current_password, or log in again, "
            "to change your password",
        )

    sets.append("password_hash = %s")
    vals.append(
        ph.hash(fields["password"])
    )
    pw_changed = True

if not sets:
    raise HTTPException(
        422,
        "Provide email or password",
    )

try:
    with get_conn() as conn:
        updated = conn.execute(
            f"""
            UPDATE users
            SET {', '.join(sets)}
            WHERE id = %s
            RETURNING {PUBLIC_COLS}
            """,
            (
                *vals,
                user["id"],
            ),
        ).fetchone()

        if pw_changed:
            conn.execute(
                """
                DELETE FROM sessions
                WHERE user_id = %s
                  AND sid <> %s
                """,
                (
                    user["id"],
                    request.state.sid,
                ),
            )

except psycopg.errors.UniqueViolation:
    raise HTTPException(
        409,
        "Email already in use",
    )

return updated
```

@app.delete(
"/api/users/{user_id}",
status_code=204,
)
def delete_user(
user=Depends(owned_user),
):
with get_conn() as conn:
conn.execute(
"DELETE FROM users WHERE id = %s",
(user["id"],)
)

```
resp = Response(status_code=204)
clear_cookie(resp)

return resp
```
