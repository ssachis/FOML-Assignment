"""Independently fetch cited pages and check literal claims."""

import re
from time import monotonic

import requests

from tracker.config import load_config
from tracker.errors import Terminal, Transient
from tracker.guardrails import Rejected
from tracker.tools import fetch_article

from .contract import Envelope, Verify
from .evidence import checked_evidence
from .telemetry import trace
from .worker import worker_app


class BudgetExhausted(Exception):
    pass


def verify(envelope, deadline, fetch=fetch_article):
    cfg = load_config()
    usage = {"steps": 0, "fetches": 0, "tokens": 0}
    verdicts = []
    pages = {}

    def count_request():
        if monotonic() >= deadline:
            raise BudgetExhausted("time_budget_exhausted")
        if usage["fetches"] >= envelope.budget.max_fetches:
            raise BudgetExhausted("fetch_budget_exhausted")
        usage["fetches"] += 1

    error = None
    for claim in envelope.payload.claims:
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise BudgetExhausted("time_budget_exhausted")
            if claim.source_url not in pages:
                cfg["limits"]["fetch_timeout_seconds"] = min(
                    cfg["limits"]["fetch_timeout_seconds"], remaining
                )
                started = monotonic()
                fetch_status = "stopped"
                try:
                    page = fetch(claim.source_url, cfg, prefilter=False,
                                 before_request=count_request)
                    pages[claim.source_url] = page["text"]
                    fetch_status = "ok"
                except (Rejected, Terminal, Transient, requests.RequestException):
                    pages[claim.source_url] = None
                    fetch_status = "unreachable"
                finally:
                    trace("verifier", "out", "fetch_article", fetch_status, started,
                          run_id=envelope.run_id, request_id=envelope.request_id)
            if monotonic() >= deadline:
                raise BudgetExhausted("time_budget_exhausted")
        except BudgetExhausted as exc:
            error = {"code": str(exc), "message": f"Checked {len(verdicts)} claims",
                     "retryable": False}
            break

        page_text = pages[claim.source_url]
        evidence = None
        verdict = "unreachable"
        if page_text is not None:
            verdict = "unsupported"
            for line in page_text.splitlines():
                match = re.search(re.escape(claim.text), line, flags=re.IGNORECASE)
                if match:
                    evidence = checked_evidence(match.group(), page_text)
                    if evidence:
                        verdict = "supported"
                        break
        verdicts.append({"claim_id": claim.claim_id, "verdict": verdict, "evidence": evidence})

    return 200, {
        "status": "partial" if error else "complete",
        "usage": usage,
        "result": {"verdicts": verdicts},
        "error": error,
    }


app = worker_app("/verify", "VERIFIER_TOKEN", Envelope[Verify], verify)
