"""Run the existing tracker without access to persisted account state."""

from tempfile import TemporaryDirectory
from time import monotonic

from tracker import agent, tools
from tracker.config import env, load_config
from tracker.guardrails import Rejected

from .contract import Envelope, Research
from .worker import worker_app


class RunMemory:
    def load_state(self):
        return {"fetched_urls": [], "developments": [], "last_top_k": []}

    def save_run(self, payload):
        pass


def research(envelope, deadline):
    if not env("GROQ_API_KEY", required=False):
        return 500, {"status": "failed", "error": {"code": "missing_model_key",
                     "message": "Researcher model key is missing", "retryable": False}}
    cfg = load_config()
    cfg.update(topic=envelope.payload.topic, k=envelope.payload.k)
    budget = envelope.budget
    cfg["limits"].update(max_steps=budget.max_steps, max_fetches=budget.max_fetches,
                         max_total_tokens=budget.max_tokens, max_retries=0, max_search_calls=0)
    fetches = 0

    def count_fetch():
        nonlocal fetches
        if fetches >= budget.max_fetches or monotonic() >= deadline:
            raise Rejected("Researcher fetch budget exhausted")
        fetches += 1

    def fetch(url, config, **kwargs):
        config["limits"]["fetch_timeout_seconds"] = min(
            config["limits"]["fetch_timeout_seconds"], max(0.001, deadline - monotonic()))
        return tools.fetch_article(url, config, before_request=count_fetch, **kwargs)

    def chat(messages, schemas, config, log):
        from tracker import llm
        from tracker.errors import Terminal
        if monotonic() >= deadline:
            raise Terminal("Researcher time budget exhausted")
        config["model"]["timeout_seconds"] = max(0.001, deadline - monotonic())
        try:
            return llm.chat(messages, schemas, config, log)
        except llm.requests.RequestException as exc:
            raise Terminal("Research model connection failed") from exc

    with TemporaryDirectory() as directory:
        run = agent.run(cfg, RunMemory(), trace_path=f"{directory}/research.jsonl",
                        llm_fn=chat, fetch_fn=fetch)
    developments = []
    for job in run["developments"]:
        claims = []
        if job["evidence"] and job["sources"]:
            claims.append({"claim_id": job["key"] + "-listing", "text": job["evidence"],
                           "source_url": job["sources"][0]})
        developments.append({"id": job["key"], "title": job["title"], "summary": job["summary"],
                             "company": job["company"], "location": job["location"],
                             "sources": job["sources"], "claims": claims})
    usage = {key: run["stats"][key] for key in ("steps", "tokens")}
    usage["fetches"] = fetches
    return 200, {"status": "partial" if run["partial"] else "complete",
                 "usage": usage, "result": {"developments": developments},
                 "error": {"code": "research_incomplete", "message": "Research ended early",
                           "retryable": False} if run["partial"] else None}


app = worker_app("/research", "RESEARCHER_TOKEN", Envelope[Research], research)
