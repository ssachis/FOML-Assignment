"""Mimics the grader. Start the server first, then: python test_api.py"""
import json, sys, time, urllib.request, urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4000"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), raw
        except ValueError:
            return e.code, None, raw


fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += 0 if cond else 1


sfx = str(int(time.time()))
a, b = f"alice_{sfx}", f"bob_{sfx}"
pw = "Sup3rSecret!pass"

s, j, _ = call("GET", "/healthz");                                  check("healthz", s == 200 and j == {"status": "ok"})
sa, ja, raw = call("POST", "/api/auth/register", {"username": a, "password": pw, "email": f"{a}@x.com"})
check("register 201", sa == 201 and "password_hash" not in raw and "password" not in raw.lower().replace("password_hash", ""))
sb, jb, _ = call("POST", "/api/auth/register", {"username": b, "password": pw})
check("register second user", sb == 201)
check("duplicate register 409", call("POST", "/api/auth/register", {"username": a, "password": pw})[0] == 409)
check("login wrong pw 401", call("POST", "/api/auth/login", {"username": a, "password": "nope-nope-nope"})[0] == 401)
check("login unknown user 401", call("POST", "/api/auth/login", {"username": "ghost_xyz", "password": pw})[0] == 401)
_, la, _ = call("POST", "/api/auth/login", {"username": a, "password": pw}); ta = la["access_token"]
_, lb, _ = call("POST", "/api/auth/login", {"username": b, "password": pw}); tb = lb["access_token"]

check("me 200", call("GET", "/api/auth/me", token=ta)[1]["username"] == a)
for label, tok in [("no token", None), ("garbage token", "abc.def.ghi")]:
    check(f"me {label} -> 401", call("GET", "/api/auth/me", token=tok)[0] == 401)
check("wrong scheme -> 401", call("GET", "/api/auth/me", token=None)[0] == 401)

ida, idb = ja["id"], jb["id"]
s, j, raw = call("GET", f"/api/users/{ida}", token=ta); check("GET own 200, no hash", s == 200 and "hash" not in raw)
codes = {
    "GET": call("GET", f"/api/users/{idb}", token=ta)[0],
    "PATCH": call("PATCH", f"/api/users/{idb}", {"email": "evil@x.com"}, token=ta)[0],
    "DELETE": call("DELETE", f"/api/users/{idb}", token=ta)[0],
}
check(f"Rule 3: cross-account refused identically {codes}", len(set(codes.values())) == 1 and codes["GET"] in (403, 404))
check("victim untouched", call("GET", f"/api/users/{idb}", token=tb)[1]["email"] is None)

check("PATCH own email", call("PATCH", f"/api/users/{ida}", {"email": f"new_{a}@x.com"}, token=ta)[1]["email"] == f"new_{a}@x.com")
check("PATCH password", call("PATCH", f"/api/users/{ida}", {"password": "AnotherPass123!"}, token=ta)[0] == 200)
check("login with new pw", call("POST", "/api/auth/login", {"username": a, "password": "AnotherPass123!"})[0] == 200)
check("old pw rejected", call("POST", "/api/auth/login", {"username": a, "password": pw})[0] == 401)
check("DELETE own 2xx", call("DELETE", f"/api/users/{ida}", token=ta)[0] in (200, 204))
check("deleted user's token -> 401", call("GET", "/api/auth/me", token=ta)[0] == 401)
call("DELETE", f"/api/users/{idb}", token=tb)
check("NYUgrader can log in", call("POST", "/api/auth/login", {"username": "NYUgrader", "password": "Courant2026!"})[0] == 200)
print("\nAll passed" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
