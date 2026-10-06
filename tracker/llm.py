"""Thin OpenAI-compatible chat client (Groq by default). No agent framework."""
import requests

from .config import env
from .errors import classify_http, with_retry


def chat(messages: list, tools: list, cfg: dict, log=print) -> dict:
    m = cfg["model"]
    key = env("GROQ_API_KEY")

    # one tool offered (the wrap-up phase) -> force it
    choice = {"type": "function", "function": {"name": tools[0]["function"]["name"]}} if len(tools) == 1 else "auto"

    def call():
        r = requests.post(
            m["base_url"].rstrip("/") + "/chat/completions", timeout=60,
            headers={"Authorization": f"Bearer {key}"},
            json={"model": m["name"], "messages": messages, "tools": tools, "tool_choice": choice,
                  "temperature": m.get("temperature", 0.1), "max_tokens": m.get("max_output_tokens", 1500)})
        if r.status_code == 400 and "tool_use_failed" in (r.text or ""):
            # malformed or disallowed tool call from the model: treat as "no call", the loop nudges it
            return {"choices": [{"message": {"content": "", "tool_calls": []}}], "usage": {}}
        classify_http(r, "model", cfg)
        return r.json()

    data = with_retry(call, cfg, "model call", log)
    u = data.get("usage") or {}
    return {"message": data["choices"][0]["message"],
            "prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0)}
