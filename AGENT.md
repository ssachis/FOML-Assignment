# AGENT.md

> Sections marked **FILL IN** need numbers from your own trace and runs. Everything else describes the code as written.

## 1. Workflow vs. agent
**The model decides:** which source/search to look at next, which postings to include, how to rank them, how to write the 1–2 sentence summary and the "fit" note, and whether a new posting is the same job as an `ALREADY_REPORTED` one (it only *proposes* that, see §3).

**My code decides:** which tools exist (`config.yaml`), every budget (steps, fetches, searches, tokens), whether a URL may be fetched (scheme, host allowlist, DNS to public IPs, redirects, size, timeout), whether a posting was already fetched (skipped), whether a posting passes my hard preferences, whether a claim is really in the cited page, dedupe, top-K bookkeeping, and the New/Still/Dropped split.

**A decision I moved out of the model:** the hard preference filter (`titles_exclude`, locations, `keywords_avoid`). Early on that was just an instruction in the prompt. But a Greenhouse board has hundreds of postings and the model would see all of them, spend tokens on them, and sometimes recommend a "Staff" role I'd excluded. Now `fetch_article` filters structured listings in code before the model sees them (`kept 12/340 by preferences` appears in the trace), and `finish` re-checks every reported job against the same filter. The model can no longer talk itself past a rule, and the prompt shrinks.

## 2. The network
Run 1 (`traces/run1.jsonl`) made 2 model calls to Groq (`api.groq.com`, 704 ms and 5,661 ms), 1 `fetch_article` call to `raw.githubusercontent.com` for the SimplifyJobs feed (765 ms, 12.8 MB, kept 50 of 4,537 listings by preferences), 0 Tavily searches, and `finish` (0 ms). It also made about 3 calls to my own backend (login, `GET /api/tracker/state`, `POST /api/tracker/runs`), which are not in the trace. Of about 7.1 s traced, roughly 6.4 s (89%) was the two model calls, and 0.8 s was the feed fetch. The second model call, which writes the ranked list, took most of the model time.

## 3. "New"
Three checks, in order, all in `agent.py`:
1. **URL match:** normalized URL (lowercased host, no fragment, tracking params like `utm_*` removed, trailing slash stripped) equals a source already stored for a development.
2. **Fingerprint match:** `sha1(company | title | location)` of lowercased alphanumerics equals a stored development key.
3. **Model proposal, code verification:** the model may return `matches_existing: <id>`; I accept it only if `difflib` similarity of "company title" is ≥ 0.6.

**A case this gets wrong:** the same role reposted with a different title and a different req URL, e.g. "ML Engineer, Platform" → "Machine Learning Engineer II (Platform)" with a different location string. Fingerprint differs, the URL differs, and similarity can fall under 0.6 (so it is reported as new). The opposite error is also possible: two genuinely different openings with the same title at the same company and location (common for large companies hiring in bulk) collapse into one development.

## 4. Failure
`errors.py` classifies every HTTP response. The 429 branch:
```python
if s == 429:
    ra = _retry_after(resp)
    if _DAILY.search(body):
        # A daily/monthly cap does not reset in a few seconds. Retrying it is a bug.
        raise Terminal(f"{service}: daily or quota limit exhausted (HTTP 429): {body}")
    if ra is not None and ra > 10 * cap:
        raise Terminal(f"{service}: rate limit asks for a {int(ra)}s wait, treating as exhausted.")
    raise Transient(f"{service}: per-minute rate limit (HTTP 429)", retry_after=ra)
```
- **Per-minute limit** → `Transient`: `with_retry` sleeps `min(cap, base·2^attempt)` (or the server's `Retry-After` if longer, still capped), at most `max_retries` times, then gives up with a clear message.
- **Daily cap** (body mentions per day / TPD / RPD / quota, or `Retry-After` is absurdly long) → `Terminal`: no retry. The loop stops; if the run already has evidence it writes a report marked **partial**, otherwise it exits with code 2 and the message.
- 401/403/402 are terminal; timeouts, connection errors, 408 and 5xx are transient. (Tested in `python -m tracker.test_tracker`; also try a bogus `GROQ_API_KEY` and airplane mode.)

## 5. Budget
**FILL IN** from your traces: tokens per run = last `total_tokens=` note in the trace; searches = Tavily credits column. Limits in `config.yaml` cap a run at 60,000 tokens, 10 steps, 8 fetches, 1 search. Groq's free-tier daily token limit for your model is on https://console.groq.com/docs/rate-limits; days until it runs out = daily limit ÷ tokens per run (per day, so with one run a day it only runs out if a single run exceeds it). Tavily's monthly credits ÷ (searches per run × runs per month) gives the second number. Check both against the live pages: I did not verify current limits.
