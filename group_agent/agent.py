"""Group agent: ASI:One chat + lore-plan handlers + 1 s state-machine tick.

Run: python -m group_agent.agent
"""

import json
from pathlib import Path

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage,
                                                 chat_protocol_spec)

from group_agent.brain import GroupBrain, GroupConfig
from group_agent.lore_db import LoreDB
from shared.cards import ack
from shared.config import dev_seed, env, resolve_path
from shared.llm import LLM
from shared.protocol import AvailabilityReply, EvaluateReply, new_plan_proto
from shared.runtime import build_llm_message_history, make_send, setup_logging

HERE = Path(__file__).parent
MEMBERS_FILE = HERE / "members.json"


def load_members(path: Path = MEMBERS_FILE) -> list[dict]:
    if not path.exists():
        return []
    return [m for m in json.loads(path.read_text()) if m.get("member_agent_address")]


def build_agent(brain: GroupBrain, *, name: str = "lore-keeper-group", seed: str | None = None,
                port: int = 8000, mailbox: bool = True, publish: bool = True,
                endpoint: list[str] | None = None) -> Agent:
    agent = Agent(name=name, seed=seed or dev_seed("group"), port=port, mailbox=mailbox,
                  endpoint=endpoint, publish_agent_details=publish,
                  readme_path=str(HERE / "AGENTVERSE_README.md"),
                  description="Plans hangouts for a friend group from shared history and each "
                              "member's private constraints.")
    chat = Protocol(spec=chat_protocol_spec)
    plan = new_plan_proto()

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        ctx.logger.info(f"chat from sender={sender} session={ctx.session} text={msg.text()[:80]!r}")
        await ack(ctx, sender, msg)
        try:
            history = build_llm_message_history(ctx)
        except Exception:
            history = []
        await brain.on_chat(sender, msg, make_send(ctx), session=str(ctx.session), history=history)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.debug(f"ack from {sender} for {msg.acknowledged_msg_id}")

    @plan.on_message(AvailabilityReply)
    async def on_availability(ctx: Context, sender: str, msg: AvailabilityReply):
        ctx.logger.info(f"AvailabilityReply from {sender} plan={msg.plan_id} "
                        f"slots={len(msg.free_slots)} has_calendar={msg.has_calendar}")
        brain.on_availability(sender, msg)

    @plan.on_message(EvaluateReply)
    async def on_evaluate(ctx: Context, sender: str, msg: EvaluateReply):
        ctx.logger.info(f"EvaluateReply from {sender} plan={msg.plan_id} n={len(msg.verdicts)}")
        brain.on_evaluate(sender, msg)

    @agent.on_interval(period=1.0)
    async def tick(ctx: Context):
        await brain.tick(make_send(ctx))

    @agent.on_event("startup")
    async def startup(ctx: Context):
        ctx.logger.info(f"Group agent address: {agent.address}")
        ctx.logger.info(f"Members: {brain.db.member_names() or 'none — fill group_agent/members.json'}")
        if not brain.cfg.invite_code:
            ctx.logger.warning("GROUP_INVITE_CODE is empty: nobody can join")
        if not brain.db.recent_hangouts(1):
            ctx.logger.warning("Lore is empty — run: python scripts/seed_lore.py")
        ctx.logger.info(f"LLM: {'ASI:One live' if brain.llm.live else 'templates (no ASI_ONE_API_KEY)'}")

    agent.include(chat, publish_manifest=publish)
    agent.include(plan, publish_manifest=publish)
    return agent


def main() -> None:
    setup_logging()
    db = LoreDB(resolve_path(env("DB_PATH", "./data/lore.db")))
    db.sync_members(load_members())
    brain = GroupBrain(db, LLM(), GroupConfig.from_env())
    agent = build_agent(brain, seed=env("GROUP_AGENT_SEED") or None,
                        port=int(env("GROUP_AGENT_PORT", "8000")))
    agent.run()


if __name__ == "__main__":
    main()
