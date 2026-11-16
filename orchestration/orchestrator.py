"""Run the HTTP workers and persist a report through the A1 API."""

import json
from time import monotonic, sleep
from uuid import uuid4

import requests

from tracker.agent import now
from tracker.api import Store
from tracker.config import env, load_config

from .contract import ResearchResult, VerifyResult, WorkerResponse


def call_worker(name, run_id, payload, budget, settings, schema, validate):
    worker = settings[name.lower()]
    request_id = str(uuid4())
    body = {"request_id": request_id, "run_id": run_id, "payload": payload,
            "budget": {f"max_{key}": value for key, value in budget.items()},
            "time_budget_ms": worker["time_budget_ms"]}
    url = env(f"{name}_URL").rstrip("/") + ("/research" if name == "RESEARCHER" else "/verify")
    headers = {"Authorization": f"Bearer {env(f'{name}_TOKEN')}"}
    timeout = body["time_budget_ms"] / 1000 + worker["startup_allowance_seconds"]
    for attempt in range(1, 4):
        started = monotonic()
        delay = 2 ** (attempt - 1)
        status = "connection_error"
        try:
            response = requests.post(url, json=body, headers=headers, timeout=timeout)
            status = response.status_code
            if status == 200:
                result = schema.model_validate(response.json())
                if any(value > budget[key] for key, value in result.usage.model_dump().items()):
                    raise ValueError("Worker exceeded budget")
                if (result.status == "partial") != bool(result.error):
                    raise ValueError("Worker status contradicts its error")
                validate(result)
                return result.result, result.usage.model_dump(), result.error is not None
            if status != 503:
                break
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, int(retry_after))
            if delay > timeout:
                break
        except (requests.ConnectionError, requests.Timeout):
            pass
        except (ValueError, requests.RequestException):
            status = "invalid_response"
            break
        finally:
            print(json.dumps({"ts": now(), "service": "orchestrator", "run_id": run_id,
                              "request_id": request_id, "step": name.lower(), "direction": "out",
                              "endpoint_or_tool": name.lower(), "status": status,
                              "latency_ms": round((monotonic() - started) * 1000),
                              "attempt": attempt, "tokens": 0}))
        if attempt < 3:
            sleep(delay)
    return None, budget, True


def run(cfg, store):
    started = now()
    run_id = str(uuid4())
    settings = cfg["orchestration"]
    topic, k = cfg["topic"], cfg["k"]
    state = store.load_state()
    old = {job["id"]: job for job in state["developments"]}
    old_keys = {job["key"]: job["id"] for job in old.values()}
    last_top = state["last_top_k"]
    total = settings["budget"]
    share = settings["researcher"]["budget"]
    failures = []

    def validate_research(response):
        jobs = response.result.developments
        job_ids = {job.id for job in jobs}
        claims = [claim for job in jobs for claim in job.claims]
        if (len(jobs) > k or len(job_ids) != len(jobs) or len(claims) > 100
                or len({claim.claim_id for claim in claims}) != len(claims)):
            raise ValueError("Invalid developments")
        for job in jobs:
            if not 1 <= len(job.sources) <= 20 or any(
                len(url) > 2048 or not url.startswith(("https://", "http://"))
                for url in job.sources
            ):
                raise ValueError("Invalid sources")
            if any(claim.source_url not in job.sources for claim in job.claims):
                raise ValueError("Unlisted claim source")

    result, research_usage, partial = call_worker(
        "RESEARCHER", run_id, {"topic": topic, "k": k}, share, settings,
        WorkerResponse[ResearchResult], validate_research)
    if partial:
        failures.append("researcher")
    ranked = []
    claim_map = {}
    if result is not None:
        for rank, job in enumerate(result.developments, 1):
            fields = job.model_dump(exclude={"id", "claims"})
            job_id = old_keys.get(job.id)
            ranked.append(dict(fields, key=job.id, id=job_id, rank=rank,
                               status="still" if job_id in last_top else "new"))
            claim_map[job.id] = [dict(claim.model_dump(), verdict="unverified", evidence=None)
                                 for claim in job.claims]
    else:
        for rank, job_id in enumerate(last_top[:k], 1):
            ranked.append(dict(old[job_id], rank=rank, status="still"))

    claims = [claim for entries in claim_map.values() for claim in entries]
    by_id = {claim["claim_id"]: claim for claim in claims}
    verify_usage = dict.fromkeys(total, 0)

    def validate_verify(response):
        verdicts = response.result.verdicts
        ids = {row.claim_id for row in verdicts}
        if (not ids <= by_id.keys() or len(ids) != len(verdicts)
                or (response.status == "complete" and ids != by_id.keys())):
            raise ValueError("Invalid verdict IDs")
        if any((row.verdict == "supported") != bool(row.evidence) for row in verdicts):
            raise ValueError("Invalid verdict evidence")

    if claims:
        remaining = {key: total[key] - research_usage[key] for key in total}
        verified, verify_usage, partial = call_worker(
            "VERIFIER", run_id,
            {"claims": [{key: claim[key] for key in ("claim_id", "text", "source_url")}
                        for claim in claims]},
            remaining, settings, WorkerResponse[VerifyResult], validate_verify)
        if partial:
            failures.append("verifier")
        if verified is not None:
            for verdict in verified.verdicts:
                by_id[verdict.claim_id].update(verdict=verdict.verdict, evidence=verdict.evidence)

    stale = result is None
    dropped = [] if stale else [job_id for job_id in last_top if job_id not in {j["id"] for j in ranked}]
    reason = ", ".join(failures)
    report = [f"# {topic}", "", f"Partial run ({reason})" if failures else "Complete run"]
    if stale:
        report.append("No new results. Previous results are stale.")
    for job in ranked:
        report.append(f"\n## {job['rank']}. {job['title']}")
        for claim in claim_map.get(job["key"], []):
            report.append(f"- {claim['verdict']} — {claim['text']} ({claim['source_url']})")
            if claim["evidence"]:
                report.append(f"  Evidence: {claim['evidence']}")
    stats = {key: research_usage[key] + verify_usage[key] for key in total}
    stats.update(run_id=run_id, failed_workers=failures, claims=claim_map, stale=stale)
    payload = {"topic": topic, "k": k, "started_at": started, "finished_at": now(),
               "partial": bool(failures), "partial_reason": reason, "report_md": "\n".join(report),
               "developments": ranked, "dropped_ids": dropped, "stats": stats}
    store.save_run(payload)
    return payload


if __name__ == "__main__":
    store = Store()
    store.login()
    print(run(load_config(), store)["report_md"])
