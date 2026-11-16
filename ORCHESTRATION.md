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

Run the A1 tracker and then the orchestrated tracker against the same topic
and source configuration. Keep all three services' JSON logs and compare
model tokens and caller-side latencies. Record complete, unsupported and
unreachable verdict counts, then inspect all unsupported claims and a random
sample of ten supported claims across runs. Live measurements and the manual
audit are pending a deployed verifier and external network access.
