"""Load hackathons (found through the Luma MCP in Claude) into the app, through the API only.
    python -m tracker.hackathons import events.json
    python -m tracker.hackathons clear
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone

from .api import Store
from .errors import Terminal

try:
    from zoneinfo import ZoneInfo
    NYC = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NYC = timezone.utc


def _pick(d, *names):
    for n in names:
        v = d.get(n)
        if v not in (None, "", []):
            return v
    return None


def _iso(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=NYC)).isoformat()   # no timezone given -> assume New York


def normalize(e):
    """Accept the field names an assistant is likely to use. Returns None if unusable."""
    if not isinstance(e, dict):
        return None
    name, url = _pick(e, "name", "title", "event_name"), _pick(e, "url", "link", "event_url", "luma_url")
    if not name or not url or not re.match(r"^https?://", str(url).strip(), re.I):
        return None   # no name, or not a plain http(s) link: drop it
    loc = _pick(e, "location", "address", "venue", "city")
    if isinstance(loc, dict):
        loc = ", ".join(str(x) for x in (loc.get("name"), loc.get("address"), loc.get("city")) if x)
    host = _pick(e, "host", "hosts", "organizer", "calendar")
    if isinstance(host, list):
        host = ", ".join(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in host)
    url = str(url).strip()
    return {
        "key": hashlib.sha1(url.lower().split("?")[0].rstrip("/").encode()).hexdigest()[:16],
        "name": str(name).strip()[:300],
        "starts_at": _iso(_pick(e, "starts_at", "start_at", "start", "start_time", "date")),
        "ends_at": _iso(_pick(e, "ends_at", "end_at", "end", "end_time")),
        "location": str(loc or "")[:300], "host": str(host or "")[:200], "url": url[:2048],
        "description": re.sub(r"\s+", " ", str(_pick(e, "description", "summary") or ""))[:600],
    }


def main(argv):
    if not argv or argv[0] not in ("import", "clear"):
        raise SystemExit(__doc__)
    try:
        store = Store()
        store.login()
        if argv[0] == "clear":
            store._req("DELETE", "/api/tracker/hackathons")
            print("Hackathons cleared.")
            return
        if len(argv) < 2:
            raise SystemExit("usage: python -m tracker.hackathons import events.json")
        data = json.load(open(argv[1]))
        raw = data.get("events", data) if isinstance(data, dict) else data
        events = list({e["key"]: e for e in map(normalize, raw) if e}.values())
        print(f"{len(events)} usable events out of {len(raw)}")
        if not events:
            raise Terminal("nothing to import (each event needs a name and an http(s) url)")
        r = store._req("POST", "/api/tracker/hackathons/import", json={"events": events[:300]})
        print(f"Imported: {r['added']} new, {r['updated']} updated")
    except (Terminal, ValueError, OSError) as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
