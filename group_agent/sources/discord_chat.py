"""Discord group chat as a lore source, read through the discord.py-self MCP server.

The server (github.com/Microck/discord.py-self-mcp) logs in as a Discord *user*
account and exposes ~76 tools, most of which write (send, delete, kick, ...).
This client only ever calls the read tools in READ_TOOLS, and only on channels
listed in DISCORD_LORE_CHANNEL_IDS.

Transports, picked by DiscordMCP.from_env():
  DISCORD_MCP_URL      streamable HTTP endpoint, e.g. http://127.0.0.1:7781/mcp. The
                       token stays in the server process; one long-lived gateway login.
  DISCORD_MCP_COMMAND  stdio: spawn the server per session; DISCORD_TOKEN is passed to
                       the child explicitly (the MCP SDK does not inherit the parent env).

Tool output is plain text, one message per line, oldest first:
    <username> (message_id=<id>[, reply_to=<id>]): <clean content> [Attachment 0] ... url=...
Failures also arrive as text (isError stays false), so callers must check for them.
"""

import asyncio
import logging
import re
import shlex
import sys
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.config import TZ, env, use_fixtures

log = logging.getLogger("lore.discord")

FIXTURE_TEXT = Path(__file__).parent / "fixtures" / "discord_read_messages.txt"
DISCORD_EPOCH_MS = 1420070400000
MAX_HISTORY = 200  # the server caps read_messages at 200 and has no before/after cursor

READ_TOOLS = frozenset({"get_user_info", "read_messages", "search_messages", "read_thread_messages"})
# Setup-only discovery (lists every DM/server the account can see); never used by the agent.
DISCOVERY_TOOLS = frozenset({"list_dm_channels", "list_guilds", "list_channels"})
CHANNEL_ARGS = ("channel_id", "thread_id")

NOT_READY = "Discord connection is not ready yet"
ERROR_PREFIXES = ("Error ", "Channel not found", "Access denied", "Thread not found", "Cannot ",
                  "Unknown tool", NOT_READY)

HEADER_RE = re.compile(r"^(?P<author>.+?) \(message_id=(?P<id>\d{15,21})"
                       r"(?:, reply_to=(?P<reply>\d{15,21}))?\): ?(?P<body>.*)$")
ATTACHMENT_RE = re.compile(r"\s*\[Attachment \d+\] .*$", re.S)
EMBED_RE = re.compile(r"\s*\[Embed\]: .*$", re.S)


class DiscordMCPError(RuntimeError):
    pass


def snowflake_time(message_id: int | str) -> datetime:
    """Creation time encoded in a Discord id (read_messages prints no timestamps)."""
    ms = (int(message_id) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TZ)


def parse_messages(text: str) -> list[dict]:
    """Parse read_messages / search_messages / read_thread_messages output.

    Lines that don't start a new message belong to the previous one (multi-line
    content). Returns [{message_id, author, reply_to, created_at, content,
    has_attachments}] with content exactly as Discord rendered it (clean_content).
    """
    out: list[dict] = []
    for line in text.split("\n"):
        if m := HEADER_RE.match(line):
            out.append({"message_id": m["id"], "author": m["author"], "reply_to": m["reply"],
                        "body": m["body"]})
        elif out:
            out[-1]["body"] += "\n" + line
    for msg in out:
        body = msg.pop("body")
        content = EMBED_RE.sub("", ATTACHMENT_RE.sub("", body))
        msg["content"] = "" if content == "[No content]" else content
        msg["has_attachments"] = bool(ATTACHMENT_RE.search(body))
        msg["created_at"] = snowflake_time(msg["message_id"]).isoformat(timespec="seconds")
    return out


def _check(text: str) -> str:
    if text.startswith(ERROR_PREFIXES):
        raise DiscordMCPError(text.strip()[:300])
    return text


class DiscordMCP:
    """Read-only MCP client session. Use as `async with DiscordMCP.from_env() as dc:`."""

    def __init__(self, *, url: str | None = None, command: list[str] | None = None,
                 token: str | None = None, allowed_channels: set[str] | None = None,
                 timeout: float = 25.0, allow_discovery: bool = False, errlog=None):
        if not url and not command:
            raise DiscordMCPError("set DISCORD_MCP_URL or DISCORD_MCP_COMMAND")
        self.url, self.command, self.token = url, command, token
        self.allowed_channels = {str(c) for c in (allowed_channels or ())}
        self.timeout = timeout
        self.tools = READ_TOOLS | (DISCOVERY_TOOLS if allow_discovery else frozenset())
        self.errlog = errlog
        self.session = None
        self._stack = AsyncExitStack()

    @classmethod
    def from_env(cls, **kw) -> "DiscordMCP":
        cmd = env("DISCORD_MCP_COMMAND")
        channels = {c for c in re.split(r"[,\s]+", env("DISCORD_LORE_CHANNEL_IDS")) if c}
        return cls(url=env("DISCORD_MCP_URL") or None, command=shlex.split(cmd) if cmd else None,
                   token=env("DISCORD_TOKEN") or None, allowed_channels=channels, **kw)

    @property
    def transport(self) -> str:
        return "streamable-http" if self.url else "stdio"

    async def __aenter__(self) -> "DiscordMCP":
        from mcp import ClientSession

        try:
            if self.url:
                from mcp.client.streamable_http import streamable_http_client
                read, write, _ = await self._stack.enter_async_context(streamable_http_client(self.url))
            else:
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client
                child_env = {k: v for k, v in (("DISCORD_TOKEN", self.token),
                                               ("RATE_LIMIT_ENABLED", "true")) if v}
                params = StdioServerParameters(command=self.command[0], args=self.command[1:], env=child_env)
                read, write = await self._stack.enter_async_context(
                    stdio_client(params, errlog=self.errlog or sys.stderr))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), self.timeout)
            self.session = session
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[dict]:
        res = await self.session.list_tools()
        return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                for t in res.tools]

    async def call(self, name: str, args: dict | None = None) -> str:
        """One allow-listed tool call; returns the text output or raises DiscordMCPError."""
        args = dict(args or {})
        if name not in self.tools:
            raise DiscordMCPError(f"tool {name!r} is not allowed (read-only allowlist)")
        for key in CHANNEL_ARGS:
            if key in args and self.allowed_channels and str(args[key]) not in self.allowed_channels:
                raise DiscordMCPError(f"{key} {args[key]} is not in DISCORD_LORE_CHANNEL_IDS")
        if name in ("read_messages", "search_messages", "read_thread_messages") and not self.allowed_channels:
            raise DiscordMCPError("DISCORD_LORE_CHANNEL_IDS is empty; refusing to read arbitrary channels")
        # The server's limiter sleeps (default 60 s cooldown) instead of failing, so bound every call.
        res = await self.session.call_tool(name, args, read_timeout_seconds=timedelta(seconds=self.timeout))
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if res.isError:
            raise DiscordMCPError(text[:300] or f"{name} failed")
        return _check(text)

    async def wait_ready(self, timeout: float = 30.0) -> str:
        """The gateway login runs in the background after spawn; poll until it is up."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            res = await self.session.call_tool("get_user_info", {})
            text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
            if not text.startswith(NOT_READY):
                return _check(text)
            if loop.time() > deadline:
                raise DiscordMCPError(f"Discord login not ready after {timeout:.0f}s; check DISCORD_TOKEN")
            await asyncio.sleep(1.5)

    async def read_channel(self, channel_id: str, limit: int = MAX_HISTORY) -> list[dict]:
        text = await self.call("read_messages", {"channel_id": str(channel_id),
                                                 "limit": max(1, min(int(limit), MAX_HISTORY))})
        return parse_messages(text)


async def fetch_chat(channel_id: str | None = None, limit: int = MAX_HISTORY,
                     client: DiscordMCP | None = None) -> list[dict]:
    """Recent messages of the lore channel.

    USE_FIXTURES=1 parses the captured fixture output. In live mode errors are raised
    rather than replaced by fixture data: importing fake chat into a real lore DB
    would be worse than importing nothing.
    """
    if use_fixtures() and client is None:
        return parse_messages(FIXTURE_TEXT.read_text(encoding="utf-8"))[-limit:]
    channel_id = channel_id or next(iter(sorted(
        c for c in re.split(r"[,\s]+", env("DISCORD_LORE_CHANNEL_IDS")) if c)), None)
    if not channel_id:
        raise DiscordMCPError("no channel: pass one or set DISCORD_LORE_CHANNEL_IDS")
    if client is not None:
        return await client.read_channel(channel_id, limit)
    async with DiscordMCP.from_env() as dc:
        await dc.wait_ready()
        return await dc.read_channel(channel_id, limit)
