import json
import os
import socket
import subprocess
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from group_agent.discord_lore import import_chat, looks_private, member_map
from group_agent.lore_db import LoreDB
from group_agent.sources.discord_chat import (FIXTURE_TEXT, READ_TOOLS, DiscordMCP, DiscordMCPError,
                                              fetch_chat, parse_messages, snowflake_time)
from scripts.simulate_plan import Simulation
from shared.cards import card_of
from shared.config import ROOT, TZ
from shared.llm import LLM
from tests.helpers import at, run
from tests.test_llm import fake_llm

CHAT = json.loads((ROOT / "group_agent/sources/fixtures/discord_group_chat.json").read_text(encoding="utf-8"))
FIXTURE_MSGS = CHAT["channels"][0]["messages"]
CHANNEL = CHAT["channels"][0]["id"]
USERNAMES = "alex.codes=Alex,samwise_k=Sam,jordo=Jordan,priya.r=Priya,riley_b=Riley"
TODAY = date(2026, 10, 24)
SERVER_SCRIPT = ROOT / "scripts" / "discord_mcp_fixture_server.py"


def lore_db() -> LoreDB:
    db = LoreDB(":memory:")
    db.load_seed(ROOT / "data" / "seed_hangouts.json")
    db.sync_members([{"name": n, "member_agent_address": f"agent1q-{n.lower()}"}
                     for n in ("Alex", "Sam", "Jordan", "Priya", "Riley")])
    return db


def chat() -> list[dict]:
    return parse_messages(FIXTURE_TEXT.read_text(encoding="utf-8"))


def all_stored_text(db: LoreDB) -> str:
    return json.dumps(db.hangouts(limit=1000), ensure_ascii=False)


# ------------------------------------------------------------------ parsing


def test_snowflake_time_matches_discord_docs_example():
    assert snowflake_time(175928847299117063).astimezone(TZ).isoformat(timespec="milliseconds") \
        == datetime.fromtimestamp(1462015105.796, TZ).isoformat(timespec="milliseconds")


def test_parse_real_server_output_round_trips_the_fixture():
    msgs = chat()
    assert len(msgs) == len(FIXTURE_MSGS)
    for got, src in zip(msgs, FIXTURE_MSGS):
        assert got["created_at"] == datetime.fromisoformat(src["at"]).astimezone(TZ).isoformat()
        assert got["author"] == src["author"]
        assert got["content"] == src.get("clean_content", src["content"])
        assert got["has_attachments"] == bool(src.get("attachments"))
    multi = next(m for m in msgs if m["content"].startswith("final scores"))
    assert multi["content"].count("\n") == 3
    reply = next(m for m in msgs if m["content"].startswith("that seagull"))
    assert reply["reply_to"] == next(m["message_id"] for m in msgs if "ENTIRE s'more" in m["content"])


def test_parse_ignores_status_text():
    assert parse_messages("No messages found matching 'x'. Try broadening your search.") == []


def test_fetch_chat_uses_fixture_offline():
    assert run(fetch_chat(limit=5)) == chat()[-5:]


# ------------------------------------------------------------------ client guardrails


class FakeSession:
    def __init__(self, text="ok"):
        self.text, self.calls = text, []

    async def call_tool(self, name, args, read_timeout_seconds=None):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], isError=False)


def client(text="ok", channels=(CHANNEL,)):
    dc = DiscordMCP(url="http://127.0.0.1:1/mcp", allowed_channels=set(channels))
    dc.session = FakeSession(text)
    return dc


@pytest.mark.parametrize("name", ["send_message", "delete_message", "kick_member", "list_dm_channels",
                                  "run_discrawl", "get_message_attachments"])
def test_only_read_tools_are_callable(name):
    dc = client()
    with pytest.raises(DiscordMCPError, match="not allowed"):
        run(dc.call(name, {"channel_id": CHANNEL}))
    assert dc.session.calls == []


def test_reads_are_limited_to_the_lore_channels():
    with pytest.raises(DiscordMCPError, match="not in DISCORD_LORE_CHANNEL_IDS"):
        run(client().call("read_messages", {"channel_id": "1"}))
    with pytest.raises(DiscordMCPError, match="is empty"):
        run(client(channels=()).call("read_messages", {"channel_id": CHANNEL}))


@pytest.mark.parametrize("text", ["Error reading messages: 401 Unauthorized (error code: 0): 401: Unauthorized",
                                  "Channel not found", "Access denied to channel",
                                  "Discord connection is not ready yet. Please try again in a few seconds."])
def test_in_band_errors_raise(text):
    with pytest.raises(DiscordMCPError):
        run(client(text).read_channel(CHANNEL))


# ------------------------------------------------------------------ import


def test_member_map_and_private_lines():
    m = member_map(["Alex", "Sam"], "samwise_k=Sam, ghost=Nobody")
    assert m["samwise_k"] == "Sam" and m["alex"] == "Alex" and "ghost" not in m
    assert looks_private("can't tonight, keeping it under $25 this week 😩")
    assert looks_private("I don't drink, fyi") and looks_private("I need to be home by 11")
    assert not looks_private("bowled a 43 and I have never been prouder")


def test_template_import_attaches_verbatim_quotes_and_is_idempotent():
    db = lore_db()
    s = run(import_chat(db, LLM(api_key=""), chat(), member_map(db.member_names(), USERNAMES), TODAY))
    assert s["skipped"] == {"empty": 1, "private": 1, "non_member": 1}
    assert s["hangouts_added"] == []
    bonfire = db.hangouts("title=?", ("Ocean Beach bonfire",))[0]
    texts = [q["text"] for q in bonfire["quotes"]]
    assert texts.count("this is the best night of the year, I'm calling it") == 1  # seed quote not duplicated
    assert "that seagull had a plan and we were not part of it 🐦" in texts
    board = db.hangouts("title=?", ("Board game night at Sam's",))[0]
    assert {"text": "final scores:\nSam 10\nPriya 9\nJordan 4 (robber placement was personal)",
            "speaker": "Priya"} in board["quotes"]
    stored = all_stored_text(db)
    assert "kev" not in stored.lower() and "$25" not in stored
    assert "bowling tonight" not in stored and "bring layers" not in stored  # logistics
    again = run(import_chat(db, LLM(api_key=""), chat(), member_map(db.member_names(), USERNAMES), TODAY))
    assert again["quotes_added"] == 0


def test_llm_import_validates_ids_and_never_prompts_private_lines():
    msgs = chat()
    ids = {m["content"][:12]: m["message_id"] for m in msgs}
    kev = next(m["message_id"] for m in msgs if m["author"] == "kev.from.work")
    budget = next(m["message_id"] for m in msgs if "$25" in m["content"])
    reply = json.dumps({"hangouts": [
        {"hangout_id": None, "date": "2026-10-17", "title": "Dumpling night at Riley's", "category": "food",
         "place": "Riley's place", "attendees": ["Riley", "Priya", "Sam", "Jordan", "Kev"],
         "quote_ids": [ids["Riley's dump"], ids["folding coun"], kev, budget, "123"]},
        {"hangout_id": None, "date": "2026-10-10", "title": "Bowling", "category": "bowling",
         "place": "Presidio Bowl", "attendees": [], "quote_ids": [ids["bowled a 43 "]]},
    ]})
    llm, comp = fake_llm(reply)
    prompts = []
    llm.on_prompt(lambda purpose, messages: prompts.append(json.dumps(messages, ensure_ascii=False)))
    db = lore_db()
    s = run(import_chat(db, llm, msgs, member_map(db.member_names(), USERNAMES), TODAY))
    assert comp.calls == 1 and len(prompts) == 1
    assert "kev.from.work" not in prompts[0] and "why was I added" not in prompts[0]
    assert "$25" not in prompts[0]
    assert s["hangouts_added"] == ["2026-10-17 Dumpling night at Riley's"]
    new = db.hangouts("title=?", ("Dumpling night at Riley's",))[0]
    assert new["attendees"] == ["Riley", "Priya", "Sam", "Jordan"] and new["categories"] == ["food"]
    assert [q["speaker"] for q in new["quotes"]] == ["Priya", "Sam"]
    assert new["quotes"][1]["text"] == "folding count: me 12, Jordan 3. Jordan's dumplings: structurally unsound 🥟"
    bowling = db.hangouts("title=?", ("Bowling night",))  # same-day proposal merged, not duplicated
    assert len(bowling) == 1 and bowling[0]["quotes"][-1]["text"] == "bowled a 43 and I have never been prouder"


def test_imported_quote_reaches_the_lore_card():
    sim = Simulation(at(2026, 10, 24, 12, 0))
    run(import_chat(sim.db, sim.llm, chat(), member_map(sim.db.member_names(), USERNAMES), TODAY))
    out = run(sim.say("user:riley", "anything from a year ago?"))
    kind, payload = card_of(out[-1])
    lines = [c["value"] for c in payload["root"]["children"]]
    assert kind == "custom" and payload["root"]["title"] == "One year ago this week"
    assert "“the seagull took my ENTIRE s'more. not a bite. the whole thing” — Riley" in lines


# ------------------------------------------------------------------ real server (opt-in)

SERVER_PY = os.environ.get("DISCORD_MCP_PYTHON", "")
real_server = pytest.mark.skipif(not SERVER_PY or not Path(SERVER_PY).exists(),
                                 reason="set DISCORD_MCP_PYTHON to a python with discord-py-self-mcp installed")


async def _read_through(dc: DiscordMCP) -> tuple[list[str], str, str]:
    async with dc:
        tools = sorted(t["name"] for t in await dc.list_tools())
        who = await dc.wait_ready()
        text = await dc.call("read_messages", {"channel_id": CHANNEL, "limit": 200})
    return tools, who, text


@real_server
def test_real_server_over_stdio(tmp_path):
    with open(tmp_path / "server.log", "w") as err:
        tools, who, text = run(_read_through(DiscordMCP(
            command=[SERVER_PY, str(SERVER_SCRIPT)], allowed_channels={CHANNEL}, errlog=err)))
    assert len(tools) >= 70 and READ_TOOLS <= set(tools) and "send_message" in tools
    assert who == "User: Alex (@alex.codes) (290000000000000001)"
    assert text == FIXTURE_TEXT.read_text(encoding="utf-8")


@real_server
def test_real_server_over_streamable_http(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = open(tmp_path / "server.log", "w")
    proc = subprocess.Popen([SERVER_PY, str(SERVER_SCRIPT), "--transport", "streamable-http",
                             "--port", str(port)], stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        _, _, text = run(_read_through(DiscordMCP(url=f"http://127.0.0.1:{port}/mcp",
                                                  allowed_channels={CHANNEL})))
        assert parse_messages(text) == chat()
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()
