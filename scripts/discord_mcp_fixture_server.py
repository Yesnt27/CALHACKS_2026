"""Run the real discord.py-self-mcp server with a fixture Discord account instead of a login.

Everything except the Discord gateway client is the unmodified server: the MCP
transport, the tool registry, every tool handler, the output formatting and the
rate limiter. Only the `client` object the tools read from is replaced by one
built from group_agent/sources/fixtures/discord_group_chat.json, so no token is
needed and nothing reaches discord.com.

Run it with the Python that has discord-py-self-mcp installed (its own venv; it
is not a Lore Keeper dependency):

    /path/to/dmcp-venv/bin/python scripts/discord_mcp_fixture_server.py                      # stdio
    /path/to/dmcp-venv/bin/python scripts/discord_mcp_fixture_server.py \
        --transport streamable-http --port 7781                                             # http://127.0.0.1:7781/mcp
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

FIXTURE = Path(__file__).resolve().parent.parent / "group_agent" / "sources" / "fixtures" / "discord_group_chat.json"
DISCORD_EPOCH_MS = 1420070400000

os.environ.setdefault("DISCORD_TOKEN", "fixture-mode-not-a-token")

import discord  # noqa: E402

from discord_py_self_mcp import main as server_main  # noqa: E402
from discord_py_self_mcp.bot import client as real_client  # noqa: E402
from discord_py_self_mcp.logging_utils import log_to_stderr  # noqa: E402


def snowflake(at: datetime, seq: int) -> int:
    return ((int(at.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22) | (seq & 0xFFF)


class FakeUser:
    discriminator = "0"

    def __init__(self, uid: str, name: str, global_name: str | None):
        self.id, self.name, self.global_name, self.bot = int(uid), name, global_name, False

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


class FakeAttachment:
    def __init__(self, mid: int, i: int, a: dict):
        self.id = mid + i + 1
        self.filename = a["filename"]
        self.content_type = a.get("content_type")
        self.size = a.get("size")
        self.width, self.height = a.get("width"), a.get("height")
        self.description = None
        self.url = f"https://cdn.discordapp.com/attachments/fixture/{self.id}/{self.filename}"

    async def read(self) -> bytes:
        return b""


class FakeMessage:
    def __init__(self, mid: int, author: FakeUser, content: str, clean: str | None,
                 at: datetime, reply_to: int | None, attachments: list[dict]):
        self.id, self.author, self.content = mid, author, content
        self.clean_content = clean if clean is not None else content
        self.created_at = at.astimezone(timezone.utc)
        self.embeds = []
        self.attachments = [FakeAttachment(mid, i, a) for i, a in enumerate(attachments)]
        self.reference = SimpleNamespace(message_id=reply_to) if reply_to else None


class _History:
    def __init__(self, items):
        self._it = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class FixtureGroupChannel(discord.abc.Messageable):
    """A group DM: passes the tools' isinstance(..., Messageable) and type checks."""

    type = discord.ChannelType.group

    def __init__(self, cid: str, name: str, recipients: list[FakeUser], messages: list[FakeMessage]):
        self.id, self.name, self.recipients = int(cid), name, recipients
        self.messages = sorted(messages, key=lambda m: m.id)
        self.last_message_id = self.messages[-1].id if self.messages else None
        self.read_state = None
        self.sent: list[str] = []

    def history(self, *, limit: int | None = 100, before=None, after=None, oldest_first=None, **_):
        items = [m for m in self.messages
                 if (before is None or m.id < getattr(before, "id", before))
                 and (after is None or m.id > getattr(after, "id", after))]
        items = items if oldest_first else list(reversed(items))  # Discord default: newest first
        return _History(items[:limit] if limit else items)

    async def send(self, content=None, **_):
        self.sent.append(content)
        log_to_stderr(f"[FIXTURE] send_message to {self.id} recorded, not delivered: {content!r}")
        return SimpleNamespace(id=snowflake(datetime.now(timezone.utc), len(self.sent)))

    async def _get_channel(self):
        return self


class FixtureClient:
    def __init__(self, data: dict):
        self.users = {u["name"]: FakeUser(u["id"], u["name"], u.get("global_name")) for u in data["users"]}
        self.user = self.users[data["self"]]
        self.guilds: list = []
        self.private_channels = []
        seq = 0
        for ch in data["channels"]:
            keyed: dict[str, int] = {}
            msgs = []
            for m in ch["messages"]:
                seq += 1
                at = datetime.fromisoformat(m["at"])
                mid = snowflake(at, seq)
                if "key" in m:
                    keyed[m["key"]] = mid
                msgs.append(FakeMessage(mid, self.users[m["author"]], m["content"], m.get("clean_content"),
                                        at, keyed.get(m.get("reply_to")), m.get("attachments", [])))
            self.private_channels.append(FixtureGroupChannel(
                ch["id"], ch.get("name"), [self.users[r] for r in ch["recipients"]], msgs))
        self._by_id = {c.id: c for c in self.private_channels}

    def is_ready(self) -> bool:
        return True

    def get_channel(self, channel_id: int):
        return self._by_id.get(int(channel_id))

    async def fetch_channel(self, channel_id: int):
        if c := self.get_channel(channel_id):
            return c
        raise discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel")

    def get_guild(self, guild_id: int):
        return None


def install(data: dict) -> FixtureClient:
    fake = FixtureClient(data)
    patched = []
    for name, mod in list(sys.modules.items()):
        if name.startswith("discord_py_self_mcp") and getattr(mod, "client", None) is real_client:
            mod.client = fake
            patched.append(name)

    async def no_login() -> None:
        log_to_stderr(f"[FIXTURE] Discord login disabled; serving {FIXTURE.name} "
                      f"({sum(len(c.messages) for c in fake.private_channels)} messages)")

    server_main.start_discord_client = no_login
    log_to_stderr(f"[FIXTURE] patched client in {len(patched)} modules")
    return fake


if __name__ == "__main__":
    install(json.loads(FIXTURE.read_text(encoding="utf-8")))
    server_main.main()
