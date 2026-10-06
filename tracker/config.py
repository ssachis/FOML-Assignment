import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def load_config(path=None) -> dict:
    cfg = yaml.safe_load(Path(path or ROOT / "config.yaml").read_text())
    if not 3 <= int(cfg["k"]) <= 10:
        raise SystemExit("config.yaml: k must satisfy 3 <= k <= 10")
    return cfg


def env(name: str, required=True) -> str:
    v = os.getenv(name, "")
    if required and not v:
        raise SystemExit(f"Missing environment variable {name}. See .env.example.")
    return v
