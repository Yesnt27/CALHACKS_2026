"""Member agent: one per friend, owner-only chat, answers the Group agent privately.

Run: MEMBER_NAME=alex MEMBER_PORT=8001 python -m member_agent.agent
"""

from pathlib import Path

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (ChatAcknowledgement, ChatMessage,
                                                 chat_protocol_spec)

from member_agent.brain import MemberBrain
from shared.cards import ack, text_message
from shared.config import dev_seed, env, resolve_path
from shared.llm import LLM
from shared.protocol import (AvailabilityReply, AvailabilityRequest, EvaluateReply,
                             EvaluateRequest, new_plan_proto)
from shared.runtime import setup_logging

HERE = Path(__file__).parent


def build_agent(brain: MemberBrain, *, seed: str | None = None, port: int = 8001,
                mailbox: bool = True, publish: bool = True,
                endpoint: list[str] | None = None) -> Agent:
    agent = Agent(name=f"lore-member-{brain.name.lower()}", seed=seed or dev_seed(f"member:{brain.name}"),
                  port=port, mailbox=mailbox, endpoint=endpoint, publish_agent_details=publish,
                  readme_path=str(HERE / "AGENTVERSE_README.md"),
                  description="Personal, owner-only Lore Keeper agent. Holds private hangout limits.")
    chat = Protocol(spec=chat_protocol_spec)
    plan = new_plan_proto()

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        ctx.logger.info(f"chat from sender={sender} session={ctx.session}")
        await ack(ctx, sender, msg)
        for line in await brain.on_chat(sender, msg.text()):
            await ctx.send(sender, text_message(line))

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        ctx.logger.debug(f"ack from {sender}")

    @plan.on_message(AvailabilityRequest, replies={AvailabilityReply})
    async def on_availability(ctx: Context, sender: str, msg: AvailabilityRequest):
        ctx.logger.info(f"AvailabilityRequest from {sender} plan={msg.plan_id}")
        reply = await brain.on_availability(sender, msg)
        if reply is not None:
            await ctx.send(sender, reply)

    @plan.on_message(EvaluateRequest, replies={EvaluateReply})
    async def on_evaluate(ctx: Context, sender: str, msg: EvaluateRequest):
        ctx.logger.info(f"EvaluateRequest from {sender} plan={msg.plan_id} n={len(msg.options)}")
        reply = await brain.on_evaluate(sender, msg)
        if reply is not None:
            await ctx.send(sender, reply)

    @agent.on_event("startup")
    async def startup(ctx: Context):
        ctx.logger.info(brain.banner(agent.address))
        if not brain.group_address:
            ctx.logger.warning("GROUP_AGENT_ADDRESS is empty: all lore-plan messages will be ignored")

    agent.include(chat, publish_manifest=publish)
    agent.include(plan, publish_manifest=publish)
    return agent


def main() -> None:
    setup_logging()
    name = env("MEMBER_NAME")
    if not name:
        raise SystemExit("Set MEMBER_NAME (and MEMBER_PORT, MEMBER_AGENT_SEED).")
    state = env("MEMBER_STATE_PATH", "./data/member_<name>.json").replace("<name>", name.lower())
    brain = MemberBrain(name, resolve_path(state), env("GROUP_AGENT_ADDRESS"), LLM())
    endpoint = env("MEMBER_AGENT_ENDPOINT")  # public https://…/submit; empty = Agentverse mailbox
    agent = build_agent(brain, seed=env("MEMBER_AGENT_SEED") or None,
                        port=int(env("MEMBER_PORT", "8001")),
                        mailbox=not endpoint, endpoint=[endpoint] if endpoint else None)
    agent.run()


if __name__ == "__main__":
    main()
