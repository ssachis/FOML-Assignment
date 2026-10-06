import argparse
import sys

from .api import Store
from .config import load_config
from .errors import Terminal


def main():
    ap = argparse.ArgumentParser(prog="python -m tracker", description="Job tracker agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the agent once")
    r.add_argument("--report", help="write the markdown report here, e.g. reports/run1.md")
    r.add_argument("--trace", help="write the JSONL trace here, e.g. traces/run1.jsonl")
    sub.add_parser("reset", help="delete this user's saved tracker state via the API")
    a = ap.parse_args()
    try:
        cfg, store = load_config(), Store()
        store.login()
        if a.cmd == "reset":
            store.reset()
            print("Tracker state cleared.")
        else:
            from .agent import run
            run(cfg, store, report_path=a.report, trace_path=a.trace)
    except Terminal as e:
        print(f"\nSTOPPED (terminal): {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
