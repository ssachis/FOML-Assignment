# Agentic orchestration

The orchestrator sends a run ID for correlation, a request ID for deduplication,
a time budget, usage limits, and a task-specific payload. The verifier receives
claim IDs, claim text and cited URLs. It fetches its own copy of each source,
so the researcher's explanation and page excerpts cannot bias its verdict.
Only the orchestrator loads and saves run state through the A1 API.

## Retries and failures

A retry reuses the original JSON body. The worker compares canonical JSON and
rejects a changed body with 409. The lock in `orchestration/worker.py` makes
reservation and lookup atomic. If the request is in flight, `result is None`
returns 503 with Retry-After. After completion, `status, response = result`
replays the stored result. A Render restart clears this in-memory cache, so a
retry crossing a restart can repeat the work. The orchestrator charges an
unanswered logical request its entire allocation.

A verifier failure leaves claims unverified. A researcher failure reuses the
last top K and labels it stale. The caller deadline includes a 90-second
startup allowance for the verifier. Measure the actual cold-start penalty by
comparing the first request after an idle period with a warm request in the
caller-side JSON traces.

## Untrusted data and evidence

A page could include a misleading statement that exactly matches a claim. The
literal verifier never executes directions from a page, but it can accept a
misleading quote when the source itself is inaccurate or when context negates
the statement. Evidence is checked after HTML entity decoding, Unicode NFKC,
whitespace collapsing, and lowercasing. A literal match can miss a paraphrase.

## Live measurements

| Run | Researcher | Verifier | Supported | Unsupported | Unreachable |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 5,079 ms | 2,694 ms | 8 | 0 | 0 |
| 2 (retry) | 5,715 ms | 2,953 ms | 8 | 0 | 0 |

The two worker calls totaled 7,773 ms in run 1 and 8,668 ms in run 2.
These sums exclude calls to the A1 backend. The first attempt at run 2 timed
out during backend login after 30 seconds. After a request to the backend
root returned HTTP 404, the retry completed.

After at least 20 minutes idle, the first public verifier health check took
21.417345 s. The immediate repeat took 0.237872 s. Both returned HTTP 200.
The difference estimates 21.179473 s of cold-start overhead for the health
endpoint. The verifier request timeout includes a 90-second startup allowance.

## Claim audit

The two successful runs produced 16 supported verdicts and 10 distinct
claims. The audit covered all 10 distinct claims. An independent download
of the cited SimplifyJobs feed contained a matching active record for each
company, title, and location. Zero of the ten supported claims disagreed
with the feed. Neither run produced unsupported or unreachable claims.
The audit checks what the cited feed says about each listing.

## Original tracker baseline

The original tracker completed in 19.350 s wall-clock time and reported
6,431 model tokens across three model steps and one feed fetch. One malformed
tool call had no reported token usage, and one per-minute rate-limit response
triggered a retry. The orchestrated runs' worker calls took 7.773 s and
8.668 s, excluding calls to the A1 backend. The baseline wall-clock time
includes backend calls and a retry, which limits the latency comparison.
The matching Researcher logs reported 5,003 tokens in run 1 and 5,123
in run 2. Each run used two model steps and one Researcher feed fetch.
These are 22.2% and 20.3% fewer reported tokens than the original tracker.
The runs had different saved state and the baseline encountered a rate
limit, so this comparison describes the observed runs. The Verifier used
no model tokens.

## Worker demo checks

The [live worker checks](reports/worker-checks.log) captured Verifier and
Researcher in-flight duplicate responses of 503 with Retry-After 1. Both
workers returned 401 for missing or wrong credentials, 400 for an invalid
body with valid credentials, and 409 for a changed body using the same
request ID. Completed replays returned identical HTTP 200 responses.
Verifier request `f50869e8-3683-4ea1-86c9-676fe6f1ef87` logged one successful
fetch and two responses. The first took 1,864 ms and the replay took 1 ms.

The Verifier returned an unreachable verdict with zero fetches charged for
a blocked loopback URL. Its 1 ms deadline returned HTTP 200 with a partial
result. The original Researcher returned 500 for that deadline.
[Local endpoint checks after the fix](reports/researcher-budget-checks.log)
show HTTP 200 partial with zero usage for both a 1 ms deadline and a
one-token allocation. Both checks confirmed zero model calls. The
Researcher reserves prompt tokens conservatively using serialized byte
length plus framing allowance and caps requested completion tokens.
Provider-side framing is estimated.

See the [first live run](reports/orchestration-live-run1.log), the
[second successful run](reports/orchestration-live-run2-retry.log), and
the [baseline trace](traces/a2-baseline.jsonl).
