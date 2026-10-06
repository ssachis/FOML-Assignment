"""One JSON line per model call and tool call."""
import json
import time
from pathlib import Path


class Trace:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("w")
        self.rows = []

    def log(self, step, kind, tool, args, status, latency_ms, prompt_tokens=0, completion_tokens=0, credits=0, note=""):
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "step": step, "kind": kind, "tool": tool,
               "args": args, "status": status, "latency_ms": int(latency_ms),
               "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "credits": credits, "note": note}
        self.rows.append(row)
        self.f.write(json.dumps(row) + "\n")
        self.f.flush()
        print(f"[{step:>2}] {kind:<5} {tool:<14} {status:<9} {int(latency_ms):>5}ms {note}")
