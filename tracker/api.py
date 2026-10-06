"""The tracker is a CLIENT of the A1 backend. It never touches the database directly."""
import requests

from .config import env
from .errors import Terminal


class Store:
    def __init__(self):
        self.base = env("TRACKER_API_URL").rstrip("/")
        self.token = None

    def _req(self, method, path, **kw):
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = requests.request(method, self.base + path, headers=h, timeout=30, **kw)
        except requests.RequestException as e:
            raise Terminal(f"Cannot reach the A1 backend at {self.base} ({e}). Is it running?")
        if r.status_code == 401:
            raise Terminal("A1 backend rejected the tracker login (401). Check TRACKER_USER / TRACKER_PASSWORD.")
        if r.status_code >= 400:
            raise Terminal(f"A1 backend error {r.status_code} on {path}: {r.text[:300]}")
        return r.json() if r.content else None

    def login(self):
        r = self._req("POST", "/api/auth/login", json={"username": env("TRACKER_USER"), "password": env("TRACKER_PASSWORD")})
        self.token = r["access_token"]

    def load_state(self) -> dict:
        return self._req("GET", "/api/tracker/state")

    def save_run(self, payload: dict) -> dict:
        return self._req("POST", "/api/tracker/runs", json=payload)

    def reset(self):
        return self._req("DELETE", "/api/tracker/state")
