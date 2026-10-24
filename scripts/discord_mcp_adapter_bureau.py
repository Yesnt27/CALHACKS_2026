"""Experiment: Fetch's uagents-adapter MCPServerAdapter in front of discord.py-self-mcp.

MCPServerAdapter (uagents-adapter 0.6.2) only wraps an in-process FastMCP object;
discord.py-self-mcp is a low-level mcp Server, so this wraps an MCP *client*
session in a shim with the two methods the adapter calls. A local, offline
Bureau then runs the adapter agent plus a client agent that sends MCPProtocol
ListTools / CallTool messages, the way the Group agent would.

Needs `pip install uagents-adapter==0.6.2` (not in requirements.txt) and a running server, e.g. the fixture one:
    /path/to/dmcp-venv/bin/python scripts/discord_mcp_fixture_server.py --transport streamable-http --port 7781
    python scripts/discord_mcp_adapter_bureau.py [--url http://127.0.0.1:7781/mcp] [--unsafe]

--unsafe passes every tool through unfiltered (what the adapter does by default).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shared  # noqa: E402,F401
from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from uagents_adapter import MCPServerAdapter  # noqa: E402
from uagents_adapter.mcp.protocol import (CallTool, CallToolResponse, ListTools,  # noqa: E402
                                          ListToolsResponse, mcp_protocol_spec)

from group_agent.sources.discord_chat import DiscordMCP, READ_TOOLS  # noqa: E402
from scripts.local_bureau import _NoRegistration, _offline  # noqa: E402
from shared.runtime import setup_logging  # noqa: E402

CHANNEL = "1291000000000000001"


class ClientShim:
    """Duck-types the FastMCP methods MCPServerAdapter calls, backed by a remote MCP session."""

    def __init__(self, dc: DiscordMCP, unsafe: bool):
        self.dc, self.unsafe = dc, unsafe

    async def list_tools(self):
        tools = (await self.dc.session.list_tools()).tools
        return tools if self.unsafe else [t for t in tools if t.name in READ_TOOLS]

    async def call_tool(self, name: str, args: dict):
        if self.unsafe:
            res = await self.dc.session.call_tool(name, args)
            return [c.text for c in res.content if getattr(c, "type", "") == "text"]
        return [await self.dc.call(name, args)]  # plain strings: the adapter str()s each item


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:7781/mcp")
    ap.add_argument("--unsafe", action="store_true")
    ap.add_argument("--port", type=int, default=8150)
    args = ap.parse_args()
    setup_logging()

    dc = DiscordMCP(url=args.url, allowed_channels={CHANNEL})
    adapter = MCPServerAdapter(mcp_server=ClientShim(dc, args.unsafe),
                               asi1_api_key=os.environ.get("ASI_ONE_API_KEY", ""), model="asi1")
    server = Agent(name="discord-mcp-adapter", seed="bureau-discord-mcp-adapter", port=args.port,
                   publish_agent_details=False)
    for proto in adapter.protocols:
        server.include(proto)

    @server.on_event("startup")
    async def connect(ctx: Context):
        await dc.__aenter__()  # held open for the agent's lifetime, as in Fetch's remote-MCP example
        ctx.logger.info(f"adapter connected to {args.url} over {dc.transport}")

    client = Agent(name="group-like-client", seed="bureau-discord-mcp-client", port=args.port,
                   publish_agent_details=False)
    # The adapter's spec gives the client role no interactions, so a spec-locked
    # Protocol(spec=mcp_protocol_spec, role="client") cannot handle the responses.
    mcp_client = Protocol(name=mcp_protocol_spec.name, version=mcp_protocol_spec.version)
    calls = [CallTool(tool="read_messages", args={"channel_id": CHANNEL, "limit": 3}),
             CallTool(tool="send_message", args={"channel_id": CHANNEL, "content": "sent by any agent"})]
    state = {"sent": 0, "done": 0}

    @mcp_client.on_message(ListToolsResponse)
    async def on_tools(ctx: Context, sender: str, msg: ListToolsResponse):
        names = sorted(t["name"] for t in msg.tools or [])
        print(f"\n>>> ListToolsResponse: {len(names)} tools; send_message exposed: {'send_message' in names}",
              flush=True)
        for c in calls:
            await ctx.send(sender, c)

    @mcp_client.on_message(CallToolResponse)
    async def on_result(ctx: Context, sender: str, msg: CallToolResponse):
        state["done"] += 1
        print(f"\n>>> CallToolResponse #{state['done']}: error={msg.error!r}\n{msg.result}", flush=True)

    @client.on_interval(period=2.0)
    async def start(ctx: Context):
        if not state["sent"] and dc.session is not None:
            state["sent"] = 1
            await ctx.send(server.address, ListTools())

    client.include(mcp_client)
    bureau = Bureau(port=args.port, endpoint=[f"http://127.0.0.1:{args.port}/submit"],
                    registration_policy=_NoRegistration())
    for a in (server, client):
        bureau.add(_offline(a))

    async def stop_later():
        for _ in range(60):
            await asyncio.sleep(0.5)
            if state["done"] >= len(calls):
                break
        os._exit(0 if state["done"] >= len(calls) else 1)

    asyncio.get_event_loop_policy().get_event_loop().create_task(stop_later())
    bureau.run()


if __name__ == "__main__":
    main()
