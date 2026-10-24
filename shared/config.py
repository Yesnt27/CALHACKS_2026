"""Environment loading and time helpers shared by both agents."""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TZ_NAME = "America/Los_Angeles"
TZ = ZoneInfo(TZ_NAME)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def use_fixtures() -> bool:
    return env_bool("USE_FIXTURES", True)


def now_local() -> datetime:
    return datetime.now(TZ)


def iso(dt: datetime) -> str:
    """ISO 8601 with offset, always in America/Los_Angeles."""
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def dev_seed(role: str) -> str:
    """Deterministic seed for local development when no seed is configured.

    Real deployments must set GROUP_AGENT_SEED / MEMBER_AGENT_SEED; anyone who
    knows the invite code could derive these.
    """
    return f"lore-keeper-dev::{role}::{env('GROUP_INVITE_CODE', 'nocode')}"


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else ROOT / path
