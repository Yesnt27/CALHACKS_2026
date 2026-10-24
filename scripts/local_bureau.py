"""Real uAgents round trip on one machine, no Agentverse: a Bureau running the Group
agent, two Member agents and a tester agent that plays an ASI:One user.

Usage: python scripts/local_bureau.py [--seconds 40]

It joins, asks for a plan, waits for the carousel, votes, locks and confirms,
logging AvailabilityRequest/EvaluateRequest round trips on both sides.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared  # noqa: E402,F401  (CA bundle before aiohttp loads)
from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from uagents_core.registration import BatchRegistrationPolicy  # noqa: E402
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage,  # noqa: E402
                                                 chat_protocol_spec)

from group_agent import agent as group_agent_mod  # noqa: E402
from group_agent.brain import GroupBrain, GroupConfig  # noqa: E402
from group_agent.lore_db import LoreDB  # noqa: E402
from member_agent import agent as member_agent_mod  # noqa: E402
from member_agent.brain import MemberBrain  # noqa: E402
from scripts.simulate_plan import PROFILES  # noqa: E402
from shared.cards import card_of, text_message  # noqa: E402
from shared.config import ROOT  # noqa: E402
from shared.llm import LLM  # noqa: E402
from shared.runtime import setup_logging  # noqa: E402

INVITE = "crew42"


class _NoRegistration(BatchRegistrationPolicy):
    """Keep the local test offline: no Almanac / Agentverse registration."""

    async def register(self):
        return None

    def add_agent(self, agent_info, identity):
        return None


def _offline(agent: Agent) -> Agent:
    async def _noop(*_a, **_k):
        return None
    agent._update_agent_status = _noop
    agent._report_events = False
    return agent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=40)
    ap.add_argument("--port", type=int, default=8100)
    args = ap.parse_args()
    setup_logging()
    tmp = Path(tempfile.mkdtemp(prefix="lore-bureau-"))
    llm = LLM(api_key="")

    db = LoreDB(tmp / "lore.db")
    db.load_seed(ROOT / "data" / "seed_hangouts.json")
    group_brain = GroupBrain(db, llm, GroupConfig(invite_code=INVITE))
    group = group_agent_mod.build_agent(group_brain, seed="bureau-test-group", port=args.port,
                                        mailbox=False, publish=False)

    members = []
    for name in ("Alex", "Sam"):
        brain = MemberBrain(name, tmp / f"member_{name.lower()}.json", group.address, llm)
        p = PROFILES[name]
        brain.state.update(owner_sender="nobody-yet", pair_code=None, home=p["home"],
                           ical_url=p["ical_url"], profile={**brain.state["profile"], **p["profile"]})
        members.append(member_agent_mod.build_agent(brain, seed=f"bureau-test-{name}", port=args.port,
                                                    mailbox=False, publish=False))
    db.sync_members([{"name": n, "member_agent_address": a.address}
                     for n, a in zip(("Alex", "Sam"), members)])

    tester = Agent(name="tester", seed="bureau-test-user", port=args.port, publish_agent_details=False)
    chat = Protocol(spec=chat_protocol_spec)
    script = [f"join {INVITE} as Alex", "plan something for the crew this weekend"]
    state = {"step": 0, "plan_id": None, "done": False}

    @chat.on_message(ChatMessage)
    async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        card = card_of(msg)
        print(f"\n>>> GROUP -> USER: {msg.text()}" + (f"\n    [{card[0]} card]" if card else ""))
        if card and card[0] == "carousel":
            item = card[1]["items"][0]
            sel = item["primary_cta"]["selection"]
            state["plan_id"] = sel["plan_id"]
            await ctx.send(group.address, text_message(json.dumps(sel)))   # vote via selection JSON
            await ctx.send(group.address, text_message("lock it in"))
        elif card and card[0] == "review":
            await ctx.send(group.address, text_message(json.dumps(card[1]["approve_cta"]["selection"])))
        elif msg.text().startswith("Locked in"):
            state["done"] = True
            print("\n=== ROUND TRIP COMPLETE ===")

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    @tester.on_interval(period=2.0)
    async def drive(ctx: Context):
        if state["step"] < len(script):
            text = script[state["step"]]
            state["step"] += 1
            print(f"\n<<< USER -> GROUP: {text}")
            await ctx.send(group.address, text_message(text))

    tester.include(chat)

    bureau = Bureau(port=args.port, endpoint=[f"http://127.0.0.1:{args.port}/submit"],
                    registration_policy=_NoRegistration())
    for a in [group, *members, tester]:
        bureau.add(_offline(a))

    async def stop_later():
        for _ in range(args.seconds * 2):
            await asyncio.sleep(0.5)
            if state["done"]:
                await asyncio.sleep(1)
                break
        rows = db.hangouts("status='planned'", limit=1)
        print("\nplanned hangout row:", rows[0]["title"] if rows else None,
              "| plan_replies left:", db.count_replies(), flush=True)
        os._exit(0 if state["done"] else 1)  # skip noisy Bureau teardown

    loop = asyncio.get_event_loop_policy().get_event_loop()
    loop.create_task(stop_later())
    bureau.run()


if __name__ == "__main__":
    main()
