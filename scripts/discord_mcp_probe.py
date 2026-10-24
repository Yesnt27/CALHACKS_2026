"""Probe a discord.py-self MCP server: tools, login, and (optionally) channels or messages.

  python scripts/discord_mcp_probe.py --url http://127.0.0.1:7781/mcp
  DISCORD_TOKEN=... python scripts/discord_mcp_probe.py --command "/path/to/discord-py-self-mcp" --discover
  python scripts/discord_mcp_probe.py --url http://127.0.0.1:7781/mcp --channel <id>

--discover lists every DM and server the account can see, to find the lore channel
id; use it once during setup, on a throwaway account.
"""

import argparse
import asyncio
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent.sources.discord_chat import READ_TOOLS, DiscordMCP  # noqa: E402
from shared.config import env  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=env("DISCORD_MCP_URL") or None)
    ap.add_argument("--command", default=env("DISCORD_MCP_COMMAND") or None)
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--channel", default=None)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    dc = DiscordMCP(url=args.url, command=shlex.split(args.command) if args.command else None,
                    token=env("DISCORD_TOKEN") or None,
                    allowed_channels={args.channel} if args.channel else None,
                    allow_discovery=args.discover)
    async with dc:
        tools = await dc.list_tools()
        names = sorted(t["name"] for t in tools)
        print(f"transport: {dc.transport}; {len(names)} tools")
        print("  read-only allowlist:", ", ".join(n for n in names if n in READ_TOOLS))
        print(f"  never called ({len(names) - len(READ_TOOLS & set(names))}):",
              ", ".join(n for n in names if n not in READ_TOOLS))
        print("login:", await dc.wait_ready())
        if args.discover:
            print("\n" + await dc.call("list_dm_channels", {}))
            print("\nservers:\n" + await dc.call("list_guilds", {}))
        if args.channel:
            msgs = await dc.read_channel(args.channel, args.limit)
            print(f"\nlast {len(msgs)} messages in {args.channel}:")
            for m in msgs:
                print(f"  {m['created_at'][:16]} {m['author']}: {m['content'][:80]!r}"
                      + (" [attachment]" if m["has_attachments"] else ""))


if __name__ == "__main__":
    asyncio.run(main())
