"""Failure classification. Transient -> retry with backoff (capped). Terminal -> stop, clear message."""
import re
import time

import requests


class Transient(Exception):
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        self.retry_after = retry_after


class Terminal(Exception):
    """Do not retry: bad key, exhausted daily quota, payment required, bad request."""


_DAILY = re.compile(r"per day|daily|\bTPD\b|\bRPD\b|per-day|exceeded your current quota|insufficient[_ ]quota", re.I)
_MINUTE = re.compile(r"per minute|\bTPM\b|\bRPM\b|per-minute|per second|\bTPS\b", re.I)


def _retry_after(resp):
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


def classify_http(resp, service: str, cfg: dict):
    """Raise the right exception for a non-2xx response; return None on success."""
    s = resp.status_code
    if s < 300:
        return
    body = (resp.text or "")[:400].replace("\n", " ")
    cap = cfg["limits"]["backoff_cap_seconds"]
    if s in (401, 403):
        if service == "fetch":
            raise Terminal(f"fetch: access denied (HTTP {s})")
        raise Terminal(f"{service}: key rejected (HTTP {s}). Check the API key in .env.")
    if s == 402:
        raise Terminal(f"{service}: payment required (HTTP 402). Add credits or use another provider.")
    if s in (432, 433):
        raise Terminal(f"{service}: plan/credit limit exhausted (HTTP {s}).")
    if s == 429:
        ra = _retry_after(resp)
        if _DAILY.search(body) and not _MINUTE.search(body):
            # A daily/monthly cap does not reset in a few seconds. Retrying it is a bug.
            raise Terminal(f"{service}: daily or quota limit exhausted (HTTP 429): {body}")
        if ra is not None and ra > 10 * cap:
            raise Terminal(f"{service}: rate limit asks for a {int(ra)}s wait, treating as exhausted.")
        raise Transient(f"{service}: per-minute rate limit (HTTP 429)", retry_after=ra)
    if s == 408 or s >= 500:
        raise Transient(f"{service}: server error HTTP {s}", retry_after=_retry_after(resp))
    raise Terminal(f"{service}: request failed (HTTP {s}): {body}")


def with_retry(fn, cfg: dict, label: str, log=print):
    """Run fn(); retry Transient (and network errors) with exponential backoff, capped."""
    lim = cfg["limits"]
    attempt = 0
    while True:
        try:
            return fn()
        except (Terminal,):
            raise
        except (Transient, requests.Timeout, requests.ConnectionError) as e:
            attempt += 1
            if attempt > lim["max_retries"]:
                raise Terminal(f"{label}: gave up after {lim['max_retries']} retries ({e})")
            wait = min(lim["backoff_cap_seconds"], lim["backoff_base_seconds"] * 2 ** (attempt - 1))
            ra = getattr(e, "retry_after", None)
            if ra:
                wait = min(lim["backoff_cap_seconds"], max(wait, ra))
            log(f"  retry {attempt}/{lim['max_retries']} for {label} in {wait:.0f}s: {e}")
            time.sleep(wait)
