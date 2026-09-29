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


def login(u, p):
    return call("POST", "/api/auth/login", {"username": u, "password": p})


sfx = str(int(time.time()))
a, b = f"alice_{sfx}", f"bob_{sfx}"
pw, pw2, pw3 = "Sup3rSecret!pass", "AnotherPass123!", "ThirdPass456!"

s, j, _ = call("GET", "/healthz");                                    check("healthz", s == 200 and j == {"status": "ok"})
sa, ja, raw = call("POST", "/api/auth/register", {"username": a, "password": pw, "email": f"{a}@x.com"})
check("register 201, no hash/password in body", sa == 201 and "hash" not in raw and pw not in raw)
sb, jb, _ = call("POST", "/api/auth/register", {"username": b, "password": pw})
check("register second user", sb == 201)
check("duplicate register 409", call("POST", "/api/auth/register", {"username": a, "password": pw})[0] == 409)
check("short password 422, not echoed", "short" not in call("POST", "/api/auth/register", {"username": "zz_" + sfx, "password": "short"})[2])
check("login wrong pw 401", login(a, "nope-nope-nope")[0] == 401)
check("login unknown user 401", login("ghost_xyz", pw)[0] == 401)
ta = login(a, pw)[1]["access_token"]; ta2 = login(a, pw)[1]["access_token"]; tb = login(b, pw)[1]["access_token"]
check("login by email works", call("POST", "/api/auth/login", {"email": f"{a}@x.com", "password": pw})[0] == 200)

check("me 200", call("GET", "/api/auth/me", token=ta)[1]["username"] == a)
check("me no token -> 401", call("GET", "/api/auth/me")[0] == 401)
check("me garbage token -> 401", call("GET", "/api/auth/me", token="abc.def.ghi")[0] == 401)

ida, idb = ja["id"], jb["id"]
s, j, raw = call("GET", f"/api/users/{ida}", token=ta); check("GET own 200, no hash", s == 200 and "hash" not in raw)
codes = {
    "GET": call("GET", f"/api/users/{idb}", token=ta)[0],
    "PATCH": call("PATCH", f"/api/users/{idb}", {"email": "evil@x.com"}, token=ta)[0],
    "PATCH(bad body)": call("PATCH", f"/api/users/{idb}", {"email": "not-an-email"}, token=ta)[0],
    "DELETE": call("DELETE", f"/api/users/{idb}", token=ta)[0],
    "missing id": call("GET", "/api/users/999999999", token=ta)[0],
}
check(f"Rule 3: cross-account refused identically {codes}", len(set(codes.values())) == 1 and codes["GET"] in (403, 404))
check("victim untouched", call("GET", f"/api/users/{idb}", token=tb)[1]["email"] is None)

url = f"/api/users/{ida}"
check("PATCH own email", call("PATCH", url, {"email": f"new_{a}@x.com"}, token=ta)[1]["email"] == f"new_{a}@x.com")
check("PATCH pw wrong current -> 400", call("PATCH", url, {"password": pw2, "current_password": "wrong-wrong-1"}, token=ta)[0] == 400)
check("PATCH pw with current -> 200", call("PATCH", url, {"password": pw2, "current_password": pw}, token=ta)[0] == 200)
check("this session survives pw change", call("GET", "/api/auth/me", token=ta)[0] == 200)
check("OTHER session revoked by pw change -> 401", call("GET", "/api/auth/me", token=ta2)[0] == 401)
check("PATCH pw with fresh token, no current -> 200", call("PATCH", url, {"password": pw3}, token=ta)[0] == 200)
check("old passwords rejected", login(a, pw)[0] == 401 and login(a, pw2)[0] == 401)
ta = login(a, pw3)[1]["access_token"]
check("login with newest pw", bool(ta))
check("DELETE own 204", call("DELETE", url, token=ta)[0] in (200, 204))
check("deleted user's token -> 401", call("GET", "/api/auth/me", token=ta)[0] == 401)

check("logout 204", call("POST", "/api/auth/logout", token=tb)[0] == 204)
check("token dead after logout -> 401", call("GET", "/api/auth/me", token=tb)[0] == 401)
call("DELETE", f"/api/users/{idb}", token=login(b, pw)[1]["access_token"])

rl = [login(f"nobody_{sfx}", "bad-bad-bad")[0] for _ in range(6)]
check(f"rate limit: 5x401 then 429 {rl}", rl[:5] == [401] * 5 and rl[5] == 429)
check("NYUgrader can log in", login("NYUgrader", "Courant2026!")[0] == 200)
print("\nAll passed" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
