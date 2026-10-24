"""Turn a Discord group chat into lore: verbatim quotes on known hangouts, plus new hangouts.

Only messages written by group members are used (DISCORD_MEMBER_MAP maps Discord
usernames to member names). Lines that state a personal limit (budget, drinking,
curfew, travel time) are dropped before any prompt and never stored: those belong
to Member agents. Quotes are the Discord message text byte-for-byte; the LLM only
picks message ids.
"""

import logging
import re
from collections import Counter
from datetime import date

from group_agent.lore_db import LoreDB
from shared.llm import LLM, template_constraints

log = logging.getLogger("lore.discord")

PRIVATE_KEYS = ("budget_cap_pp", "no_alcohol", "latest_end", "max_travel_min")
MAX_PROMPT_TEXT = 300


def member_map(names: list[str], raw: str = "") -> dict[str, str]:
    """Discord username (lowercase) -> member name, from "sam_k=Sam, jordo=Jordan".

    A username that equals a member name (any case) maps without configuration.
    """
    by_lower = {n.lower(): n for n in names}
    out = dict(by_lower)
    for pair in re.split(r"[,\s]+", raw.strip()):
        if "=" in pair:
            user, name = pair.split("=", 1)
            if name.strip().lower() in by_lower:
                out[user.strip().lower()] = by_lower[name.strip().lower()]
    return out


def looks_private(text: str) -> bool:
    found = template_constraints(text)
    return any(k in found for k in PRIVATE_KEYS)


async def import_chat(db: LoreDB, llm: LLM, messages: list[dict], usernames: dict[str, str],
                      today: date, per_hangout: int = 3) -> dict:
    """messages: parse_messages() output. Returns a summary of what changed."""
    skipped: Counter = Counter()
    used, by_id = [], {}
    for m in messages:
        speaker = usernames.get(m["author"].lower())
        if speaker is None:
            skipped["non_member"] += 1
        elif not m["content"].strip():
            skipped["empty"] += 1
        elif looks_private(m["content"]):
            skipped["private"] += 1
        else:
            used.append({"id": m["message_id"], "at": m["created_at"][:16], "speaker": speaker,
                         "text": m["content"][:MAX_PROMPT_TEXT]})
            by_id[m["message_id"]] = (m["content"], speaker)
    known = [{"id": h["id"], "date": h["date"][:10], "title": h["title"], "place": h["place"]}
             for h in db.hangouts("substr(date,1,10) <= ?", (today.isoformat(),), limit=200)]
    proposals = await llm.discord_lore(used, db.member_names(), known) if used else []

    summary = {"read": len(messages), "used": len(used), "skipped": dict(skipped),
               "quotes_added": 0, "hangouts_added": [], "hangouts_quoted": []}
    for p in proposals:
        quotes = [{"text": by_id[i][0], "speaker": by_id[i][1]} for i in p["quote_ids"] if i in by_id]
        hid = p["hangout_id"]
        if hid is None:
            same_day = next((h for h in known if h["date"] == p["date"]), None)
            hid = same_day["id"] if same_day else None
        if hid is None:
            if p["date"] > today.isoformat():
                continue
            attendees = p["attendees"] or sorted({q["speaker"] for q in quotes})
            hid = db.add_hangout(p["date"], p["title"], [p["category"]], p["place"], attendees)
            known.append({"id": hid, "date": p["date"], "title": p["title"], "place": p["place"]})
            summary["hangouts_added"].append(f"{p['date']} {p['title']}")
        added = db.add_quotes(hid, quotes, limit=per_hangout)
        if added:
            summary["quotes_added"] += added
            h = db.hangout(hid)
            summary["hangouts_quoted"].append(f"{h['date'][:10]} {h['title']} (+{added})")
    log.info("discord import: %s", summary)
    return summary
