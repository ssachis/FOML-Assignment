"""Tools. Each is callable without the model:
    python -m tracker.tools fetch_article <url> [--raw]
    python -m tracker.tools search_web "<query>"
    python -m tracker.tools finish '<json report>'
"""
import json
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from .config import env, load_config
from .errors import Terminal, Transient, classify_http, with_retry
from .guardrails import Rejected, _bad_ip, validate_url

UA = "FOML-job-tracker/1.0 (student project)"
TEXT_TYPES = ("text/", "application/json", "application/xhtml", "application/xml", "application/ld+json")


# ---------------------------------------------------------------- preferences (hard filters, in code)
def job_passes(title: str, location: str, prefs: dict) -> bool:
    t, loc = (title or "").lower(), (location or "").lower()
    inc = [x.lower() for x in prefs.get("titles_include", [])]
    exc = [x.lower() for x in prefs.get("titles_exclude", [])]
    if inc and not any(x in t for x in inc):
        return False
    if any(x in t for x in exc):
        return False
    linc = [x.lower() for x in prefs.get("locations_include", [])]
    lexc = [x.lower() for x in prefs.get("locations_exclude", [])]
    if linc and loc and not any(x in loc for x in linc):
        return False
    if any(x in loc for x in lexc):
        return False
    text = f"{t} {loc}"
    if any(x.lower() in text for x in prefs.get("keywords_avoid", [])):
        return False
    return True


def score_job(title: str, location: str, prefs: dict) -> int:
    text = f"{title} {location}".lower()
    return sum(1 for x in prefs.get("keywords_boost", []) if x.lower() in text)


# ---------------------------------------------------------------- text extraction
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "head"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.title, self._skip, self._in_title, self._href = base, [], "", 0, False, None

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP and tag != "head":
            self._skip += 1
        if tag == "a":
            self._href = dict(attrs).get("href")
        if tag in ("p", "div", "li", "br", "tr", "h1", "h2", "h3", "section"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and tag != "head" and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            h = urljoin(self.base, self._href)
            if urlparse(h).scheme in ("http", "https"):
                self.out.append(f" [{h}] ")
            self._href = None

    def handle_data(self, d):
        if self._in_title:
            self.title += d
        elif not self._skip:
            self.out.append(d)


def html_to_text(html: str, base: str):
    p = _Text(base)
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return p.title.strip(), text


def _jobs_from_json(data, source_name: str, host: str):
    """Recognise Greenhouse / Lever / Ashby-style listings and Simplify's listings.json. Newest first."""
    items = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict) or it.get("active") is False or it.get("is_visible") is False:
            continue
        title = it.get("title") or it.get("text") or it.get("name")
        if not title:
            continue
        loc = it.get("location")
        if isinstance(loc, dict):
            loc = loc.get("name")
        if not loc and isinstance(it.get("locations"), list):
            loc = "; ".join(str(x) for x in it["locations"])
        loc = loc or (it.get("categories") or {}).get("location") or ""
        url = it.get("absolute_url") or it.get("hostedUrl") or it.get("jobUrl") or it.get("url") or ""
        posted = it.get("date_posted") or 0
        out.append({"title": str(title), "location": str(loc), "url": str(url),
                    "company": str(it.get("company_name") or source_name or host),
                    "posted": posted if isinstance(posted, (int, float)) else 0})
    out.sort(key=lambda j: -j["posted"])
    return out or None


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- fetch_article
def fetch_article(url: str, cfg: dict, prefilter: bool = True, source_name: str = "") -> dict:
    """Guardrails run before any request. Raises Rejected (guardrail) or Terminal/Transient (network)."""
    lim = cfg["limits"]
    timeout, max_bytes = lim["fetch_timeout_seconds"], lim["fetch_max_bytes"]
    deadline = time.time() + timeout * 2

    def once(u):
        validate_url(u, cfg)  # scheme, allowlist, DNS -> public IPs only. Nothing is requested yet.
        r = requests.get(u, headers={"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.1"},
                         timeout=(timeout, timeout), stream=True, allow_redirects=False)
        try:  # defense against DNS rebinding: check the address we actually connected to
            peer = r.raw._connection.sock.getpeername()[0]
            if _bad_ip(peer):
                r.close()
                raise Rejected(f"connected to non-public address ({peer})")
        except (AttributeError, OSError, ValueError):
            pass
        return r

    u, r = url, None
    for hop in range(4):  # follow at most 3 redirects, re-validating every hop
        r = once(u)
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
            u = urljoin(u, r.headers["location"])
            r.close()
            continue
        break
    else:
        raise Rejected("too many redirects")
    classify_http(r, "fetch", cfg)

    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith(TEXT_TYPES):
        r.close()
        raise Rejected(f"unsupported content type: {ctype}")
    cl = r.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        r.close()
        raise Rejected(f"response too large ({cl} bytes > {max_bytes})")
    buf, truncated = bytearray(), False
    for chunk in r.iter_content(8192):
        buf += chunk
        if len(buf) > max_bytes:
            truncated, buf = True, buf[:max_bytes]
            break
        if time.time() > deadline:
            truncated = True
            break
    r.close()
    raw = buf.decode(r.encoding or "utf-8", errors="replace")
    host = urlparse(u).hostname or ""

    candidates, title, filtered = [], "", None
    if "json" in ctype or raw.lstrip()[:1] in "[{":
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
            if truncated and raw.lstrip().startswith("["):   # size cap cut the file: keep the complete objects
                cut = raw.rfind("},")
                try:
                    data = json.loads(raw[:cut + 1] + "]") if cut > 0 else None
                except ValueError:
                    data = None
        jobs = _jobs_from_json(data, source_name, host) if data is not None else None
        if jobs is not None:
            title = f"{source_name or host} listings"
            total = len(jobs)
            if prefilter:
                jobs = [j for j in jobs if job_passes(j["title"], j["location"], cfg["preferences"])]
            candidates, filtered = jobs, {"total": total, "kept": len(jobs)}
            text = "\n".join(f"{j['company']} | {j['title']} | {j['location']} | {j['url']}" for j in jobs)
        else:
            text = json.dumps(data, separators=(",", ":"))[:200000] if data is not None else raw
    else:
        title, text = html_to_text(raw, u)
    return {"ok": True, "url": url, "final_url": u, "title": _norm(title)[:200], "text": text,
            "truncated": truncated, "candidates": candidates, "filtered": filtered, "bytes": len(buf)}


# ---------------------------------------------------------------- search_web
def search_web(query: str, cfg: dict) -> dict:
    key = env("TAVILY_API_KEY")
    n = cfg["search"]["max_results"]

    def call():
        r = requests.post("https://api.tavily.com/search", timeout=20,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"query": str(query)[:300], "max_results": n, "search_depth": "basic"})
        classify_http(r, "search", cfg)
        return r.json()

    data = with_retry(call, cfg, "search_web")
    res = [{"title": _norm(x.get("title"))[:200], "url": x.get("url"), "snippet": _norm(x.get("content"))[:300]}
           for x in data.get("results", [])][:n]
    credits = (data.get("usage") or {}).get("credits", 1)
    return {"ok": True, "results": res, "credits": credits}


# ---------------------------------------------------------------- finish
JOB_FIELDS = ("title", "company", "location", "url", "summary", "evidence", "fit")


def finish(report: dict) -> dict:
    """Validate the shape of a report. Provenance is verified by the runtime against fetched pages."""
    if isinstance(report, str):
        report = json.loads(report)
    if not isinstance(report, dict) or not isinstance(report.get("jobs"), list):
        raise ValueError("report must be an object with a 'jobs' list")
    jobs = []
    for j in report["jobs"]:
        if not isinstance(j, dict):
            continue
        row = {f: _norm(str(j.get(f, "")))[:600] for f in JOB_FIELDS}
        if not row["title"] or not row["url"]:
            continue
        me = j.get("matches_existing")
        row["matches_existing"] = me if isinstance(me, int) else None
        jobs.append(row)
    return {"jobs": jobs}


TOOL_SCHEMAS = {
    "search_web": {"type": "function", "function": {
        "name": "search_web", "description": "Search the web. Returns titles, URLs, snippets.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    "fetch_article": {"type": "function", "function": {
        "name": "fetch_article", "description": "Fetch a public http(s) page or JSON feed and return its text.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    "finish": {"type": "function", "function": {
        "name": "finish", "description": "Submit the final ranked report. Call once.",
        "parameters": {"type": "object", "properties": {"jobs": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "company": {"type": "string"}, "location": {"type": "string"},
                           "url": {"type": "string"}, "summary": {"type": "string"}, "evidence": {"type": "string"},
                           "fit": {"type": "string"}, "matches_existing": {"type": "integer"}},
            "required": ["title", "company", "url", "summary", "evidence"]}}}, "required": ["jobs"]}}},
}


def _main(argv):
    cfg = load_config()
    if len(argv) < 2 or argv[0] not in TOOL_SCHEMAS:
        raise SystemExit(__doc__)
    tool, arg = argv[0], argv[1]
    try:
        if tool == "fetch_article":
            out = fetch_article(arg, cfg, prefilter="--raw" not in argv)
            out["text"] = out["text"][:3000]
            out.pop("candidates", None)
        elif tool == "search_web":
            out = search_web(arg, cfg)
        else:
            out = finish(json.loads(arg))
    except Rejected as e:
        out = {"ok": False, "status": "rejected", "error": str(e)}
    except (Terminal, Transient, ValueError) as e:
        out = {"ok": False, "status": "error", "error": str(e)}
    print(json.dumps(out, indent=2))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    _main(sys.argv[1:])
