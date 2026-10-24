"""Talk to the Group agent (and each friend's Member agent) in the terminal, offline.

Usage: python scripts/chat_repl.py [--now 2026-10-28T12:00]

Uses the same brains as the real agents with 5 in-process friends. Cards are
rendered as plain text. Type /help for commands.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.simulate_plan import Simulation  # noqa: E402
from shared.cards import card_of  # noqa: E402
from shared.config import TZ  # noqa: E402

HELP = """commands:
  <text>                 send as the current user to the Group agent
  /as <name>             switch user (Alex, Sam, Jordan, Priya, Riley)
  /me <text>             send <text> to your own Member agent (profile, budget $20, fix: …, forget)
  /tap <n>               tap Vote on item n of the last carousel
  /confirm, /reject      tap the buttons on the last review card
  /offline <name>        toggle a friend's Member agent offline
  /wait <seconds>        advance the fake clock (deadlines are 10 s)
  /lore                  list the lore table
  /quit"""


def render(msg) -> None:
    print("  " + msg.text().replace("\n", "\n  "))
    card = card_of(msg)
    if not card:
        return
    kind, p = card
    if kind == "carousel":
        print(f"  ┌ {p.get('title', '')} — {p.get('subtitle', '')}")
        for i, it in enumerate(p["items"], 1):
            badges = " ".join(f"[{b['label']}]" for b in it.get("badges", []))
            print(f"  │ {i}. {it['title']}\n  │    {it.get('subtitle', '')}\n  │    {it.get('secondary_text', '')}"
                  f"\n  │    {badges}  ({it['primary_cta']['label']}: /tap {i})")
        print("  └")
    elif kind == "review":
        print(f"  ┌ {p['title']}")
        for r in p["summary_rows"]:
            print(f"  │ {r['label']}: {r['value']}")
        print("  └ /confirm  /reject")
    elif kind == "custom":
        root = p["root"]
        print(f"  ┌ {root.get('title', '')}")
        for c in root["children"]:
            print(f"  │ {c['value']}")
        print("  └")
    else:
        print("  " + json.dumps(p, ensure_ascii=False))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default="2026-10-28T12:00")
    args = ap.parse_args()
    sim = Simulation(datetime.fromisoformat(args.now).replace(tzinfo=TZ))
    names = {b.name.lower(): b for b in sim.members.values()}
    user = "alex"
    last_carousel = last_review = None
    print(f"Lore Keeper REPL — it's {sim.clock():%a %b %d %H:%M}. LLM: "
          f"{'ASI:One' if sim.llm.live else 'templates'}. /help for commands.")
    while True:
        try:
            line = input(f"\n{user.capitalize()}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        sender = f"user:{user}"
        replies = []
        if line == "/quit":
            break
        elif line == "/help":
            print(HELP)
            continue
        elif line.startswith("/as "):
            if line[4:].strip().lower() in names:
                user = line[4:].strip().lower()
            else:
                print("  unknown name")
            continue
        elif line.startswith("/me "):
            for out in await names[user].on_chat(sender, line[4:]):
                print("  [your agent] " + out)
            continue
        elif line.startswith("/offline "):
            n = line[9:].strip().capitalize()
            sim.offline ^= {n}
            print(f"  offline: {sorted(sim.offline) or 'nobody'}")
            continue
        elif line.startswith("/wait"):
            sim.clock.advance(float(line[5:].strip() or 11))
        elif line == "/lore":
            for h in sim.db.hangouts(limit=50):
                print(f"  {h['date'][:10]} {h['status']:<7} {h['title']} @ {h['place']} {h['attendees']}")
            continue
        elif line.startswith("/tap") and last_carousel:
            i = int(line[4:].strip() or 1) - 1
            replies = await sim.say(sender, selection=last_carousel["items"][i]["primary_cta"]["selection"])
        elif line in ("/confirm", "/reject") and last_review:
            cta = last_review["approve_cta" if line == "/confirm" else "reject_cta"]
            replies = await sim.say(sender, selection=cta["selection"])
        else:
            replies = await sim.say(sender, line)
        before = {u: len(m) for u, m in sim.inbox.items()}
        for _ in range(15):   # let the state machine run (fake clock, 1 s per tick)
            await sim.group.tick(sim.send)
            await sim.deliver()
            if not sim.db.plans_in("collecting_availability", "evaluating"):
                break
            sim.clock.advance(1)
        late = sim.inbox.get(sender, [])[before.get(sender, 0):]
        for m in replies + late:
            render(m)
            if c := card_of(m):
                if c[0] == "carousel":
                    last_carousel = c[1]
                elif c[0] == "review":
                    last_review = c[1]


if __name__ == "__main__":
    asyncio.run(main())
