"""Import a Discord group chat into the lore through the discord.py-self MCP server (spike).

Where the chat comes from:
  USE_FIXTURES=1 (default)                        captured fixture output, no server needed
  DISCORD_MCP_URL=http://127.0.0.1:7781/mcp       a running server (streamable HTTP)
  DISCORD_MCP_COMMAND="/path/discord-py-self-mcp"  spawn it over stdio (DISCORD_TOKEN goes to the child)
plus DISCORD_LORE_CHANNEL_IDS (allowlist; first one is read) and DISCORD_MEMBER_MAP
("discord_user=Member,..."). Any transport env var switches off the fixture.

By default this writes to an in-memory copy of the seed lore and then asks the
Group agent a lore question, so the quote card shows what was imported. --db PATH
imports into a real lore DB instead.

Usage: python scripts/discord_lore_import.py [--channel ID] [--limit 200] [--db PATH] [--ask "..."]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent.discord_lore import import_chat, member_map  # noqa: E402
from group_agent.lore_db import LoreDB  # noqa: E402
from group_agent.sources.discord_chat import DiscordMCP, fetch_chat  # noqa: E402
from scripts.simulate_plan import Simulation, show  # noqa: E402
from shared.config import env, now_local, resolve_path, use_fixtures  # noqa: E402
from shared.llm import LLM  # noqa: E402

FIXTURE_MEMBER_MAP = "alex.codes=Alex,samwise_k=Sam,jordo=Jordan,priya.r=Priya,riley_b=Riley"
FIXTURE_CHANNEL = "1291000000000000001"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default=None)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--db", default=None, help="import into this lore DB instead of an in-memory copy")
    ap.add_argument("--ask", default="anything from a year ago?")
    args = ap.parse_args()

    live = bool(env("DISCORD_MCP_URL") or env("DISCORD_MCP_COMMAND"))
    if live:
        os.environ["USE_FIXTURES"] = "0"
    os.environ.setdefault("DISCORD_LORE_CHANNEL_IDS", "" if live else FIXTURE_CHANNEL)
    raw_map = env("DISCORD_MEMBER_MAP") or ("" if live else FIXTURE_MEMBER_MAP)

    if use_fixtures():
        print("== source: captured fixture (USE_FIXTURES=1)")
        msgs = await fetch_chat(args.channel, args.limit)
    else:
        async with DiscordMCP.from_env() as dc:
            print(f"== source: discord.py-self MCP over {dc.transport}; {len(await dc.list_tools())} tools")
            print("== logged in as:", await dc.wait_ready())
            msgs = await fetch_chat(args.channel, args.limit, client=dc)
    print(f"== read {len(msgs)} messages "
          f"({msgs[0]['created_at'][:10]} .. {msgs[-1]['created_at'][:10]})" if msgs else "== read 0 messages")

    llm = LLM()
    print(f"== LLM: {'ASI:One live' if llm.live else 'templates (no ASI_ONE_API_KEY)'}")
    if args.db:
        db = LoreDB(resolve_path(args.db))
        summary = await import_chat(db, llm, msgs, member_map(db.member_names(), raw_map),
                                    now_local().date())
        print("== summary:", summary)
        return

    sim = Simulation(now_local(), llm=llm)
    summary = await import_chat(sim.db, llm, msgs, member_map(sim.db.member_names(), raw_map),
                                sim.clock().date())
    print(f"== used {summary['used']} lines; skipped {summary['skipped']}")
    print(f"== quotes added: {summary['quotes_added']} -> {summary['hangouts_quoted']}")
    print(f"== new hangouts: {summary['hangouts_added'] or 'none'}")
    print(f"\nRiley: {args.ask}")
    show(await sim.say("user:riley", args.ask))


if __name__ == "__main__":
    asyncio.run(main())
