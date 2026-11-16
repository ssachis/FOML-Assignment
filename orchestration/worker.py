"""HTTP worker authentication and in-process request deduplication."""

import hmac
import json
import os
from threading import Lock
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool


def worker_app(path, token_env, schema, execute):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    requests = {}
    lock = Lock()

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post(path)
    async def handle(request: Request):
        started = monotonic()
        token = os.getenv(token_env, "")
        supplied = request.headers.get("authorization", "").encode()
        if not token or not hmac.compare_digest(supplied, f"Bearer {token}".encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body = await request.json()
            envelope = schema.model_validate(body)
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid request"}, status_code=400)

        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        request_id = envelope.request_id
        with lock:
            previous = requests.get(request_id)
            if previous:
                original, result = previous
                if canonical != original:
                    return JSONResponse({"error": "request ID conflict"}, status_code=409)
                if result is None:
                    return JSONResponse({"error": "request in progress"}, status_code=503,
                                        headers={"Retry-After": "1"})
                status, response = result
                return JSONResponse(response, status_code=status)
            requests[request_id] = (canonical, None)

        try:
            deadline = started + envelope.time_budget_ms / 1000
            status, response = await run_in_threadpool(execute, envelope, deadline)
        except Exception:
            status = 500
            response = {
                "status": "failed",
                "error": {"code": "worker_failed", "message": "Worker execution failed",
                          "retryable": False},
            }
        with lock:
            requests[request_id] = (canonical, (status, response))
        return JSONResponse(response, status_code=status)

    return app
