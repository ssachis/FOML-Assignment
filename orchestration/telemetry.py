"""Structured service events. Request bodies and headers stay out of logs."""

import json
from time import monotonic

from tracker.agent import now


def trace(service, direction, endpoint, status, started, *, run_id=None,
          request_id=None, attempt=1, tokens=0):
    print(json.dumps({
        "ts": now(), "service": service, "run_id": str(run_id) if run_id else None,
        "request_id": str(request_id) if request_id else None, "step": endpoint,
        "direction": direction, "endpoint_or_tool": endpoint, "status": status,
        "latency_ms": round((monotonic() - started) * 1000),
        "attempt": attempt, "tokens": tokens,
    }), flush=True)
