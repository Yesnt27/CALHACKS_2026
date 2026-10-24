import asyncio
import json
from datetime import datetime

from shared.config import TZ


def run(coro):
    return asyncio.run(coro)


def at(*args) -> datetime:
    return datetime(*args, tzinfo=TZ)


WED_NOON = at(2026, 10, 28, 12, 0)


def walk(obj, skip=("msg_id", "timestamp", "card_id", "acknowledged_msg_id")):
    """Yield every leaf value; JSON strings inside card metadata are decoded too."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip:
                continue
            if k == "card_payload" and isinstance(v, str):
                yield from walk(json.loads(v), skip)
            else:
                yield from walk(v, skip)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from walk(v, skip)
    else:
        yield obj


def as_dict(msg):
    return msg.dict() if hasattr(msg, "dict") else msg
