"""Run a full plan in-process: Group brain + 5 Member brains, fake clock, no network.

Usage: python scripts/simulate_plan.py [--now 2026-10-28T12:00] [--offline Riley] [--ask "plan ..."]

The Simulation class is reused by the tests (privacy invariants, full loop).
"""

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents_core.contrib.protocols.chat import ChatMessage  # noqa: E402

from group_agent.brain import GroupBrain, GroupConfig  # noqa: E402
from group_agent.lore_db import LoreDB  # noqa: E402
from member_agent.brain import MemberBrain  # noqa: E402
from shared.cards import card_of, text_message  # noqa: E402
from shared.config import ROOT, TZ  # noqa: E402
from shared.llm import LLM  # noqa: E402
from shared.protocol import (AvailabilityRequest, EvaluateRequest)  # noqa: E402

GROUP = "agent1q-sim-group"
INVITE = "crew42"

# Five friends with different private limits (all stay inside their own brain).
PROFILES = {
    "Alex": {"profile": {"budget_cap_pp": 40, "max_travel_min": 40},
             "home": {"lat": 37.76, "lng": -122.42, "label": "Mission, SF"}, "ical_url": None},
    "Sam": {"profile": {}, "home": {"lat": 37.78, "lng": -122.40, "label": "SoMa, SF"},
            "ical_url": "fixture:busy_weeknights"},
    "Jordan": {"profile": {"no_alcohol": True, "latest_end": "23:00", "max_travel_min": 40},
               "home": {"lat": 37.75, "lng": -122.49, "label": "Sunset, SF"}, "ical_url": None},
    "Priya": {"profile": {"budget_cap_pp": 30}, "home": {"lat": 37.75, "lng": -122.43, "label": "Noe Valley, SF"},
              "ical_url": None},
    "Riley": {"profile": {"max_travel_min": 30}, "home": {"lat": 37.78, "lng": -122.48, "label": "Richmond, SF"},
              "ical_url": "fixture:sunday_brunch"},
}


class FakeClock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Simulation:
    def __init__(self, now: datetime, profiles: dict | None = None, offline: set[str] | None = None,
                 tmp: Path | None = None, llm: LLM | None = None, push: bool = False):
        self.tmp = Path(tmp or tempfile.mkdtemp(prefix="lore-sim-"))
        self.clock = FakeClock(now)
        self.llm = llm or LLM(api_key="")
        self.offline = offline or set()
        self.sent: list[tuple[str, str, object]] = []      # (from, to, message) — every message
        self.inbox: dict[str, list[ChatMessage]] = {}       # ASI:One user -> messages from the group
        self.db = LoreDB(":memory:")
        self.db.load_seed(ROOT / "data" / "seed_hangouts.json")
        profiles = profiles or PROFILES
        self.members: dict[str, MemberBrain] = {}
        for name, p in profiles.items():
            brain = MemberBrain(name, self.tmp / f"member_{name.lower()}.json", GROUP, self.llm)
            brain.state.update(owner_sender=f"user:{name.lower()}", pair_code=None,
                               profile={**brain.state["profile"], **p["profile"]},
                               home=p["home"], ical_url=p["ical_url"])
            self.members[f"agent1q-sim-{name.lower()}"] = brain
        self.db.sync_members([{"name": b.name, "member_agent_address": a} for a, b in self.members.items()])
        for b in self.members.values():
            self.db.bind_sender(b.name, f"user:{b.name.lower()}")
        self.group = GroupBrain(self.db, self.llm, GroupConfig(invite_code=INVITE, push_enabled=push),
                                clock=self.clock)
        self.queue: list[tuple[str, object]] = []

    async def send(self, dest: str, msg, session=None) -> None:
        self.sent.append((GROUP, dest, msg))
        if dest in self.members:
            self.queue.append((dest, msg))
        else:
            self.inbox.setdefault(dest, []).append(msg)

    async def deliver(self) -> None:
        while self.queue:
            dest, msg = self.queue.pop(0)
            member = self.members[dest]
            if member.name in self.offline:
                continue
            if isinstance(msg, AvailabilityRequest):
                reply = await member.on_availability(GROUP, msg)
                self.sent.append((dest, GROUP, reply))
                self.group.on_availability(dest, reply)
            elif isinstance(msg, EvaluateRequest):
                reply = await member.on_evaluate(GROUP, msg)
                self.sent.append((dest, GROUP, reply))
                self.group.on_evaluate(dest, reply)

    async def say(self, user: str, text: str | None = None, selection: dict | None = None,
                  session: str = "sess-1") -> list[ChatMessage]:
        """User message (text or a card selection) -> the group's replies to that user."""
        before = len(self.inbox.get(user, []))
        if selection is not None:
            from uagents_core.contrib.protocols.chat.cards import create_card_response_content
            msg = ChatMessage(content=[create_card_response_content(selection=selection)])
        else:
            msg = text_message(text)
        await self.group.on_chat(user, msg, self.send, session=session, history=[])
        await self.deliver()
        return self.inbox.get(user, [])[before:]

    async def run_until(self, state: str, max_ticks: int = 40) -> dict | None:
        for _ in range(max_ticks):
            await self.group.tick(self.send)
            await self.deliver()
            plan = self.db.plans_in(state)
            if plan:
                return plan[-1]
            self.clock.advance(1)
        return None


def show(msgs: list[ChatMessage], indent: str = "  ") -> None:
    for m in msgs:
        print(indent + m.text().replace("\n", "\n" + indent))
        if c := card_of(m):
            kind, payload = c
            print(f"{indent}[{kind} card]")
            if kind == "carousel":
                for it in payload["items"]:
                    badges = ", ".join(b["label"] for b in it.get("badges", []))
                    print(f"{indent}  - {it['id']}: {it['title']} | {it['subtitle']} | "
                          f"{it['secondary_text']} | badges: {badges}")
            else:
                print(indent + "  " + json.dumps(payload, ensure_ascii=False))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", default="2026-10-28T12:00")
    ap.add_argument("--offline", action="append", default=[])
    ap.add_argument("--ask", default="plan something for the crew this weekend")
    args = ap.parse_args()
    now = datetime.fromisoformat(args.now).replace(tzinfo=TZ)
    sim = Simulation(now, offline=set(args.offline))

    print(f"== now {now:%a %b %d %H:%M}, LLM: {'live' if sim.llm.live else 'templates'}")
    print("== recent categories (last 10):", sim.db.category_counts())
    print("== priority (missed >= 2 of last 3):", sim.db.priority_members() or "none")
    if args.offline:
        print("== offline members:", args.offline)

    print(f"\nAlex: {args.ask}")
    show(await sim.say("user:alex", args.ask))
    plan = await sim.run_until("voting")
    if plan is None:
        print("plan did not reach voting:", [(p["id"], p["state"]) for p in sim.db.plans_in(
            "cancelled", "collecting_availability", "evaluating")])
        show(sim.inbox.get("user:alex", [])[-1:])
        return
    w = plan["work"]
    print("\n== windows: " + "; ".join(f"{x['start'][5:16]} free={x['free_members']}" for x in w["windows"]))
    print(f"== {len(w['candidates'])} candidates sent for private evaluation; "
          f"{len(w['scored_ids'])} survived (no 'no' verdicts)")
    print("\nGroup -> Alex (final card):")
    show(sim.inbox["user:alex"][-1:])

    print("\nSam taps Vote on the first card; Jordan types 'vote 1'; Priya votes via prose id:")
    first = plan["options"][0]["option_id"]
    show(await sim.say("user:sam", selection={"action": "vote", "plan_id": plan["id"], "option_id": first}))
    show(await sim.say("user:jordan", "vote 1"))
    show(await sim.say("user:priya", f"vote {first} please"))

    print("\nAlex: what's the plan  (majority reached -> review card on next interaction)")
    show(await sim.say("user:alex", "what's the plan"))
    print("\nAlex taps Confirm:")
    show(await sim.say("user:alex", selection={"action": "confirm", "plan_id": plan["id"]}))
    h = sim.db.hangouts("status='planned'", limit=1)[0]
    print(f"\n== hangout row: {h['date']} {h['title']} @ {h['place']} attendees={h['attendees']} "
          f"status={h['status']}")
    print(f"== plan_replies rows left: {sim.db.count_replies(plan['id'])}")

    sim.clock.advance(3 * 24 * 3600)
    recap = 'recap: absolute chaos, 10/10. "I am never doing that again 😂" — Jordan, and Sam said “rematch next week”. with Alex, Sam and Jordan'
    print(f"\nAlex: {recap}")
    show(await sim.say("user:alex", recap))
    print("\nRiley: anything from a year ago?")
    show(await sim.say("user:riley", "anything from a year ago?"))


if __name__ == "__main__":
    asyncio.run(main())
