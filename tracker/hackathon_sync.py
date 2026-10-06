"""Pull NYC hackathons from a public GitHub list and load them through the API.
    python -m tracker.hackathon_sync            # fetch, filter, import
    python -m tracker.hackathon_sync --dry-run  # fetch and print only, no login needed
"""
import hashlib
import re
import sys

import requests

from .api import Store
from .errors import Terminal
from .hackathons import normalize

SOURCE = "https://raw.githubusercontent.com/amahjoor/Hackathons/main/README.md"
# Matched against the CITY part of "City, State", so Ithaca/Troy/Rochester, New York are excluded.
NYC_CITIES = {"new york", "new york city", "nyc", "brooklyn", "manhattan", "queens", "bronx", "the bronx", "staten island"}
ROW = re.compile(r"^\|\s*\*\*\[(?P<name>.+?)\]\((?P<url>https?://[^)\s]+)\)\*\*\s*\|\s*(?P<loc>[^|]*?)\s*\|")
MAX_BYTES = 2_000_000


def fetch(url=SOURCE):
    r = requests.get(url, timeout=20, headers={"User-Agent": "a1-hackathon-tracker"}, stream=True)
    if r.status_code != 200:
        raise Terminal(f"could not fetch the hackathon list (HTTP {r.status_code})")
    body = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(body) > MAX_BYTES:
        raise Terminal("hackathon list is unexpectedly large; refusing to parse")
    return body.decode("utf-8", "replace")


def is_nyc(location):
    city = location.split(",")[0].strip().lower()
    return city in NYC_CITIES


def parse(markdown):
    """Yield dicts for every table row, remembering the '## ... Semester' heading above it."""
    section, out = "", []
    for line in markdown.splitlines():
        if line.startswith("#"):
            section = line.lstrip("# ").strip()
            continue
        m = ROW.match(line.strip())
        if m:
            out.append({"name": m["name"].strip(), "url": m["url"].strip(),
                        "location": m["loc"].strip(), "section": section})
    return out


def to_events(rows):
    events = {}
    for row in rows:
        if not is_nyc(row["location"]):
            continue
        e = normalize({
            "name": row["name"], "url": row["url"], "location": row["location"], "host": "",
            "description": f"{row['section']} (recurring annual hackathon; check the site for this year's dates). "
                           "Source: github.com/amahjoor/Hackathons",
        })
        if e:
            # two hackathons can share one page (e.g. several Cornell events), so key on name + url
            e["key"] = hashlib.sha1((row["name"] + "|" + row["url"]).lower().encode()).hexdigest()[:16]
            events[e["key"]] = e
    return list(events.values())


def main(argv):
    try:
        rows = parse(fetch())
        events = to_events(rows)
        print(f"{len(rows)} hackathons in the list, {len(events)} in NYC")
        for e in events:
            print(f"  - {e['name']} | {e['location']} | {e['url']}")
        if "--dry-run" in argv:
            return
        if not events:
            raise Terminal("no NYC hackathons found (did the list's table format change?)")
        store = Store()
        store.login()
        r = store._req("POST", "/api/tracker/hackathons/import", json={"events": events})
        print(f"Imported: {r['added']} new, {r['updated']} updated")
    except (Terminal, requests.RequestException, OSError) as e:
        print(f"STOPPED: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
