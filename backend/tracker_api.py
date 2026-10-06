"""Tracker endpoints. Every route requires a valid token (401 otherwise) and only touches the caller's rows."""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator

from main import current_user, get_conn

router = APIRouter(prefix="/api/tracker", tags=["tracker"])


def init_tracker_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_runs (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        topic TEXT NOT NULL, k INTEGER NOT NULL, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ NOT NULL,
        partial BOOLEAN NOT NULL DEFAULT FALSE, partial_reason TEXT NOT NULL DEFAULT '',
        report_md TEXT NOT NULL, stats JSONB NOT NULL DEFAULT '{}')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_developments (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL, title TEXT NOT NULL, company TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '', fit TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
        sources JSONB NOT NULL DEFAULT '[]', first_run_id INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (user_id, key))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_run_items (
        run_id INTEGER NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
        development_id INTEGER NOT NULL REFERENCES tracker_developments(id) ON DELETE CASCADE,
        rank INTEGER, status TEXT NOT NULL CHECK (status IN ('new','still','dropped')),
        PRIMARY KEY (run_id, development_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_fetches (
        id SERIAL PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES tracker_runs(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
        fetched_at TIMESTAMPTZ NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS tracker_fetches_user_idx ON tracker_fetches (user_id, status)")
    _init_hackathons(conn)


# ---------- request models (length limits keep one bad run from bloating the DB)
class DevIn(BaseModel):
    id: Optional[int] = None
    key: str = Field(max_length=64)
    rank: int = Field(ge=1, le=10)
    title: str = Field(max_length=300)
    company: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=1000)
    fit: str = Field(default="", max_length=1000)
    evidence: str = Field(default="", max_length=1000)
    sources: List[str] = Field(min_length=1, max_length=20)
    status: str = Field(pattern="^(new|still)$")

    @field_validator("sources")
    @classmethod
    def http_only(cls, v):
        for u in v:  # sources become clickable links, so only http(s) is accepted
            if not u.lower().startswith(("http://", "https://")) or len(u) > 2048:
                raise ValueError("sources must be http(s) URLs")
        return v


class FetchIn(BaseModel):
    url: str = Field(max_length=2048)  # may be a REJECTED url like file:// or javascript:, stored as inert text
    title: str = Field(default="", max_length=300)
    status: str = Field(pattern="^(fetched|skipped_seen|rejected|error)$")
    detail: str = Field(default="", max_length=300)
    fetched_at: datetime


class RunIn(BaseModel):
    topic: str = Field(max_length=300)
    k: int = Field(ge=3, le=10)
    started_at: datetime
    finished_at: datetime
    partial: bool = False
    partial_reason: str = Field(default="", max_length=300)
    report_md: str = Field(max_length=60000)
    developments: List[DevIn] = Field(max_length=10)
    dropped_ids: List[int] = Field(default_factory=list, max_length=10)
    fetches: List[FetchIn] = Field(default_factory=list, max_length=300)
    stats: dict = Field(default_factory=dict)


def _dev_cols():
    return "d.id, d.key, d.title, d.company, d.location, d.summary, d.fit, d.evidence, d.sources"


# ---------- routes
@router.get("/state")
def state(user=Depends(current_user)):
    """What the agent needs at the start of a run: URLs fetched, developments reported, last top K."""
    with get_conn() as c:
        urls = [r["url"] for r in c.execute(
            "SELECT DISTINCT url FROM tracker_fetches WHERE user_id=%s AND status='fetched'", (user["id"],))]
        devs = c.execute(f"SELECT {_dev_cols()} FROM tracker_developments d WHERE d.user_id=%s ORDER BY d.id", (user["id"],)).fetchall()
        last = c.execute("SELECT id FROM tracker_runs WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        top = []
        if last:
            top = [r["development_id"] for r in c.execute(
                "SELECT development_id FROM tracker_run_items WHERE run_id=%s AND status IN ('new','still') ORDER BY rank",
                (last["id"],))]
    return {"fetched_urls": urls, "developments": devs, "last_top_k": top}


@router.post("/runs", status_code=201)
def save_run(body: RunIn, user=Depends(current_user)):
    uid = user["id"]
    with get_conn() as c:  # one transaction: a half-saved run can't corrupt the next recrawl
        run_id = c.execute(
            "INSERT INTO tracker_runs (user_id, topic, k, started_at, finished_at, partial, partial_reason, report_md, stats) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (uid, body.topic, body.k, body.started_at, body.finished_at, body.partial, body.partial_reason,
             body.report_md, json.dumps(body.stats))).fetchone()["id"]
        for d in body.developments:
            if d.id is not None:
                own = c.execute("SELECT 1 FROM tracker_developments WHERE id=%s AND user_id=%s", (d.id, uid)).fetchone()
                if not own:
                    raise HTTPException(422, "unknown development id")
                c.execute("UPDATE tracker_developments SET title=%s, company=%s, location=%s, summary=%s, fit=%s, "
                          "evidence=%s, sources=%s WHERE id=%s",
                          (d.title, d.company, d.location, d.summary, d.fit, d.evidence, json.dumps(d.sources), d.id))
                dev_id = d.id
            else:
                dev_id = c.execute(
                    "INSERT INTO tracker_developments (user_id, key, title, company, location, summary, fit, evidence, sources, first_run_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, key) DO UPDATE SET "
                    "summary=EXCLUDED.summary, sources=EXCLUDED.sources RETURNING id",
                    (uid, d.key, d.title, d.company, d.location, d.summary, d.fit, d.evidence, json.dumps(d.sources), run_id)).fetchone()["id"]
            c.execute("INSERT INTO tracker_run_items (run_id, development_id, rank, status) VALUES (%s,%s,%s,%s) "
                      "ON CONFLICT DO NOTHING", (run_id, dev_id, d.rank, d.status))
        for did in body.dropped_ids:
            own = c.execute("SELECT 1 FROM tracker_developments WHERE id=%s AND user_id=%s", (did, uid)).fetchone()
            if own:
                c.execute("INSERT INTO tracker_run_items (run_id, development_id, rank, status) VALUES (%s,%s,NULL,'dropped') "
                          "ON CONFLICT DO NOTHING", (run_id, did))
        for f in body.fetches:
            c.execute("INSERT INTO tracker_fetches (run_id, user_id, url, title, status, detail, fetched_at) "
                      "VALUES (%s,%s,%s,%s,%s,%s,%s)", (run_id, uid, f.url, f.title, f.status, f.detail, f.fetched_at))
    return {"run_id": run_id}


def _run_detail(c, run):
    items = c.execute(
        f"SELECT i.rank, i.status, {_dev_cols()} FROM tracker_run_items i JOIN tracker_developments d ON d.id=i.development_id "
        "WHERE i.run_id=%s ORDER BY i.rank NULLS LAST, d.id", (run["id"],)).fetchall()
    return {**run, "developments": [i for i in items if i["status"] != "dropped"],
            "dropped": [i for i in items if i["status"] == "dropped"]}


@router.get("/runs")
def list_runs(user=Depends(current_user)):
    """Run history: when each run happened and what changed."""
    with get_conn() as c:
        runs = c.execute("SELECT id, topic, k, started_at, finished_at, partial, partial_reason, stats FROM tracker_runs "
                         "WHERE user_id=%s ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
        for r in runs:
            rows = c.execute("SELECT i.status, d.title, d.company FROM tracker_run_items i JOIN tracker_developments d "
                             "ON d.id=i.development_id WHERE i.run_id=%s ORDER BY i.rank NULLS LAST", (r["id"],)).fetchall()
            r["counts"] = {s: sum(1 for x in rows if x["status"] == s) for s in ("new", "still", "dropped")}
            r["new_titles"] = [f"{x['title']} ({x['company']})" for x in rows if x["status"] == "new"][:10]
            r["dropped_titles"] = [f"{x['title']} ({x['company']})" for x in rows if x["status"] == "dropped"][:10]
            r["fetch_counts"] = {x["status"]: x["n"] for x in c.execute(
                "SELECT status, count(*) AS n FROM tracker_fetches WHERE run_id=%s GROUP BY status", (r["id"],))}
    return runs


@router.get("/runs/latest")
def latest_run(user=Depends(current_user)):
    with get_conn() as c:
        run = c.execute("SELECT * FROM tracker_runs WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
        if not run:
            raise HTTPException(404, "No runs yet")
        return _run_detail(c, run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, user=Depends(current_user)):
    with get_conn() as c:
        run = c.execute("SELECT * FROM tracker_runs WHERE id=%s AND user_id=%s", (run_id, user["id"])).fetchone()
        if not run:  # someone else's run looks exactly like a missing one
            raise HTTPException(404, "Not found")
        return _run_detail(c, run)


@router.get("/runs/{run_id}/fetches")
def run_fetches(run_id: int, user=Depends(current_user)):
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM tracker_runs WHERE id=%s AND user_id=%s", (run_id, user["id"])).fetchone():
            raise HTTPException(404, "Not found")
        return c.execute("SELECT url, title, status, detail, fetched_at FROM tracker_fetches WHERE run_id=%s ORDER BY id",
                         (run_id,)).fetchall()


@router.delete("/state", status_code=204)
def reset_state(user=Depends(current_user)):
    with get_conn() as c:  # runs cascade to items and fetches
        c.execute("DELETE FROM tracker_runs WHERE user_id=%s", (user["id"],))
        c.execute("DELETE FROM tracker_developments WHERE user_id=%s", (user["id"],))
    return Response(status_code=204)


# ================= Hackathons tab (events found through the Luma MCP in Claude, loaded via the import command)
def _init_hackathons(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_hackathons (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        key TEXT NOT NULL, name TEXT NOT NULL, starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
        location TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '', added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (user_id, key))""")


class HackIn(BaseModel):
    key: str = Field(max_length=64)
    name: str = Field(max_length=300)
    url: str = Field(max_length=2048)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location: str = Field(default="", max_length=300)
    host: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=600)

    @field_validator("url")
    @classmethod
    def http_only(cls, v):  # the url becomes a clickable link, so only http(s) is accepted
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("url must be http(s)")
        return v


class HackImport(BaseModel):
    events: List[HackIn] = Field(max_length=300)


@router.post("/hackathons/import", status_code=201)
def import_hackathons(body: HackImport, user=Depends(current_user)):
    added = updated = 0
    with get_conn() as c:
        for e in body.events:
            row = c.execute(
                "INSERT INTO tracker_hackathons (user_id, key, name, starts_at, ends_at, location, host, url, description) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (user_id, key) DO UPDATE SET name=EXCLUDED.name, "
                "starts_at=EXCLUDED.starts_at, ends_at=EXCLUDED.ends_at, location=EXCLUDED.location, host=EXCLUDED.host, "
                "url=EXCLUDED.url, description=EXCLUDED.description, updated_at=now() RETURNING (xmax = 0) AS inserted",
                (user["id"], e.key, e.name, e.starts_at, e.ends_at, e.location, e.host, e.url, e.description)).fetchone()
            added, updated = added + bool(row["inserted"]), updated + (not row["inserted"])
    return {"added": added, "updated": updated}


@router.get("/hackathons")
def list_hackathons(user=Depends(current_user)):
    """Upcoming only: undated events, or events that start no earlier than a day ago."""
    with get_conn() as c:
        return c.execute(
            "SELECT id, name, starts_at, ends_at, location, host, url, description, added_at, updated_at "
            "FROM tracker_hackathons WHERE user_id=%s AND (starts_at IS NULL OR starts_at >= now() - interval '1 day') "
            "ORDER BY starts_at NULLS LAST, id", (user["id"],)).fetchall()


@router.delete("/hackathons", status_code=204)
def clear_hackathons(user=Depends(current_user)):
    with get_conn() as c:
        c.execute("DELETE FROM tracker_hackathons WHERE user_id=%s", (user["id"],))
    return Response(status_code=204)
