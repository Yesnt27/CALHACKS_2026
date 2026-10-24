# Lore Keeper — build brief for Claude Code

## Context

You are helping a 3–4 person team build a Fetch.ai hackathon project in about 26 hours (Cal Hacks 13.0, submission deadline Sun Oct 25, 09:00 PDT). Python only.

**Product.** One **Group agent** per friend group plans hangouts. Each friend has a **Member agent** that holds their private constraints (budget, no alcohol, curfew, travel limit), their calendar's busy times and their home area. The Group agent negotiates with Member agents and only ever receives free time slots and per-option verdicts (`yes` / `stretch` / `no` plus travel minutes), never reasons. Lore (the group's shared history) drives novelty ("you've done karaoke 3×") and quiet re-inclusion of people who've missed recent hangouts.

**No custom frontend.** Every user interaction happens in ASI:One chats through the Agent Chat Protocol and ASI:One Interactive Cards.

## Working rules

- Build milestone by milestone (see Milestones). After each one, run its acceptance check, report results, and stop for review.
- Every external call has a fixture fallback. With `USE_FIXTURES=1` the whole pipeline must run without network access except the ASI:One chat itself. If `ASI_ONE_API_KEY` is missing, LLM steps fall back to templates.
- Do not add: a web frontend, payments, reservations, OAuth, or any database other than SQLite.
- The Privacy invariants section is enforced by tests, not left as guidance.
- Verify APIs against the installed packages instead of assuming. If an import below fails, check the installed version and the docs (docs.agentverse.ai, innovationlab.fetch.ai, uagents.fetch.ai) before rewriting.
- Timezone everywhere: `America/Los_Angeles`. Store ISO 8601 strings with offsets.

## Stack

- Python 3.11; `uagents` (>= 0.23), `uagents-core` (chat protocol including `MetadataContent`), `openai`, `httpx`, `icalendar`, `recurring-ical-events`, `python-dotenv`, `pytest`. SQLite from the standard library.
- **ASI:One API** (OpenAI-compatible): `base_url="https://api.asi1.ai/v1"`. Model `asi1` for ideas, pitches and recaps; `asi1-mini` for intent and constraint parsing.
- **Every agent is a local uAgent** with `mailbox=True`, `publish_agent_details=True` and `readme_path=...`. Each agent needs its own seed and port. On first start, open the Agentverse inspector link the agent prints and choose Connect → Mailbox. Free tier allows 8 local agents per Agentverse account.
- Chat history for conversational lore questions only: `from uagents.experimental.chat_agent.protocol import build_llm_message_history`. Plan state lives in SQLite, not chat history, because votes arrive from different people's chats.

## Repo layout

```
lore-keeper/
  README.md
  .env.example
  requirements.txt
  shared/
    protocol.py         # lore-plan models + Protocol
    cards.py            # ChatMessage/card builders, selection parser, ack helper
    llm.py              # ASI:One client, prompts, JSON parsing, template fallbacks
    geo.py              # haversine, travel estimate, Open-Meteo geocoding
  group_agent/
    agent.py            # chat + lore-plan handlers, on_interval state machine
    intents.py          # rule-based intent router + asi1-mini fallback
    planner.py          # windows, ideas, grounding, options
    scoring.py
    lore_db.py          # schema, queries, seed loader
    sources/
      weather.py        # Open-Meteo
      events.py         # Ticketmaster Discovery
      places.py         # provider interface, fixture default
      fixtures/         # weather.json, events.json, places_sf.json
    members.json        # [{name, member_agent_address}]
    AGENTVERSE_README.md
  member_agent/
    agent.py            # owner chat + lore-plan handlers
    profile.py          # constraint parsing, verdict rules
    calendar_ics.py     # iCal fetch, recurring expansion, free slots
    AGENTVERSE_README.md
  scripts/
    seed_lore.py
    run_member.sh       # MEMBER_NAME=... MEMBER_PORT=... python -m member_agent.agent
    print_addresses.py
  tests/
```

## shared/protocol.py

```python
from uagents import Model, Protocol

class OptionSpec(Model):
    option_id: str
    start: str               # ISO 8601 with offset
    end: str
    cost_per_person: float   # estimate in USD
    alcohol_centric: bool
    late_night: bool         # ends after 23:00
    lat: float
    lng: float
    indoor: bool

class AvailabilityRequest(Model):
    plan_id: str
    window_start: str
    window_end: str

class AvailabilityReply(Model):
    plan_id: str
    free_slots: list[list[str]]   # [[start, end], ...] — no titles, no reasons
    has_calendar: bool

class EvaluateRequest(Model):
    plan_id: str
    options: list[OptionSpec]

class Verdict(Model):
    option_id: str
    fit: str                 # "yes" | "stretch" | "no"
    travel_min: int

class EvaluateReply(Model):
    plan_id: str
    verdicts: list[Verdict]

plan_proto = Protocol(name="lore-plan", version="0.1.0")
```

Group agent sends `AvailabilityRequest` and `EvaluateRequest`; Member agent answers with `AvailabilityReply` and `EvaluateReply`. Declare `replies=` on the Member agent handlers. Both agents `include` this protocol and the chat protocol with `publish_manifest=True`.

## shared/cards.py

```python
import json, re
from datetime import datetime, timezone
from uuid import uuid4
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement, ChatMessage, TextContent, MetadataContent,
)

def text_message(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                       content=[TextContent(type="text", text=text)])

def card_message(narration: str, card_kind: str, payload: dict,
                 terminal: bool = False) -> ChatMessage:
    meta = {  # wire type is dict[str, str]
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": card_kind,            # carousel | detail | form | review | custom
        "card_payload": json.dumps(payload),
        "preferred_drawer_width_px": "540",
    }
    if terminal:
        meta["is_terminal"] = "true"       # informational card, no input expected
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(),
                       content=[TextContent(type="text", text=narration),
                                MetadataContent(type="metadata", metadata=meta)])

async def ack(ctx, sender: str, msg: ChatMessage) -> None:
    await ctx.send(sender, ChatAcknowledgement(
        timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))

def parse_selection(text: str) -> dict:
    """Direct @mention delivers selection JSON; planner-routed chats deliver prose."""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (ValueError, TypeError):
        pass
    sel: dict = {}
    if m := re.search(r"\bp_[A-Za-z0-9]+\b", text): sel["plan_id"] = m.group(0)
    if m := re.search(r"\bopt_[A-Za-z0-9]+\b", text): sel["option_id"] = m.group(0)
    low = text.lower()
    for action in ("vote", "confirm", "cancel", "lock"):
        if action in low:
            sel.setdefault("action", action)
    return sel
```

Always send a `TextContent` narration with each card; if card validation fails, ASI:One falls back to showing that text. Payload must sit at the top level of `card_payload` (no extra envelope) and stay under 64 KB.

## Member agent

**State** (local JSON file per member, path from `MEMBER_STATE_PATH`):
`owner_sender`, `pair_code`, `profile {budget_cap_pp: float|null, no_alcohol: bool, latest_end: "HH:MM"|null, max_travel_min: int|null, other: str}`, `home {lat, lng, label}`, `ical_url`.

**Startup:** generate `pair_code` (6 chars) if no owner; print it with the agent address.

**Chat (owner only; any other sender gets "This is a personal agent." and nothing else):**

- `pair <code>` — first sender with the right code becomes owner.
- Free text about constraints — `asi1-mini` parses to the profile schema below; store; reply with a read-back: "Here's what I'll keep private: … Say 'fix: …' to change it."
- `calendar <url>` — fetch once to validate; reply "Got it. I only read busy times, never event titles."
- `home <neighborhood>` — geocode with Open-Meteo geocoding (`https://geocoding-api.open-meteo.com/v1/search?name=<text>&count=1`); store lat/lng rounded to 2 decimals.
- `profile` shows the stored profile; `forget` clears it.

Constraint parse output (JSON only):
```json
{"budget_cap_pp": 25, "no_alcohol": true, "latest_end": "23:00", "max_travel_min": 30, "other": "free text kept locally"}
```
`other` never leaves the Member agent.

**AvailabilityRequest:** fetch the iCal URL (cache 5 min), expand recurring events with `recurring_ical_events` inside the window, subtract busy blocks, clip to social hours (10:00–23:59) and to `latest_end`, return free slots. Drop event titles at parse time. No calendar → return evenings 17:00–23:00 on weekdays and 10:00–23:00 on weekends, with `has_calendar=False`.

**EvaluateRequest:** for each option compute `travel_min` from home (see geo) and apply, in order:

| Rule | Verdict |
| --- | --- |
| `no_alcohol` and `alcohol_centric` | no |
| option overlaps a busy block | no |
| `latest_end` set and option end + travel > `latest_end` | no |
| cost > cap × 1.2 | no |
| travel > max_travel × 1.5 | no |
| cost > cap, or travel > max_travel | stretch |
| otherwise | yes |

Return only `Verdict` objects. Reasons may be logged at DEBUG level in the member's own terminal only.

**shared/geo.py travel estimate:** haversine km; `minutes = round(10 + km / 15 * 60)`. Label it an estimate in code comments; a real transit API is a stretch goal.

## Group agent

**Config:** `members.json` (name → member_agent_address), `GROUP_INVITE_CODE`, `HOME_BASE_LAT`, `HOME_BASE_LNG`, `HOME_BASE_LABEL`.

**Schema (group_agent/lore_db.py):**

```sql
CREATE TABLE IF NOT EXISTS members (
  name TEXT PRIMARY KEY,
  member_agent_address TEXT NOT NULL,
  asi_sender TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS hangouts (
  id INTEGER PRIMARY KEY,
  date TEXT NOT NULL,
  title TEXT NOT NULL,
  categories TEXT NOT NULL,          -- JSON array
  place TEXT,
  attendees TEXT NOT NULL,           -- JSON array of member names
  recap TEXT,
  quotes TEXT,                       -- JSON array of {"text": ..., "speaker": ...}, verbatim
  status TEXT NOT NULL DEFAULT 'done' -- planned | done
);
CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,               -- p_xxxx
  planner_sender TEXT NOT NULL,
  window_start TEXT, window_end TEXT,
  state TEXT NOT NULL,               -- collecting_availability | evaluating | voting | locked | cancelled
  deadline TEXT,
  options_json TEXT,                 -- the 3 options shown (public fields only)
  chosen_option TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_replies (  -- transient; purged when a plan locks or cancels
  plan_id TEXT, member TEXT, kind TEXT, payload_json TEXT,
  PRIMARY KEY (plan_id, member, kind)
);
CREATE TABLE IF NOT EXISTS votes (
  plan_id TEXT, member TEXT, option_id TEXT,
  PRIMARY KEY (plan_id, member)
);
```

**Chat intents** (rules first; if none match, one `asi1-mini` call classifies into exactly one of these; no DB read before intent is known):

- `join <code> as <name>` — binds the ASI:One sender to a member row.
- **plan** ("plan something for the crew this weekend") — create plan, parse the window, ack, send a progress line ("Checking calendars and asking everyone's agent privately…"), set state `collecting_availability`.
- **vote_open** ("what's the plan", "vote") — send the current voting carousel.
- **vote_select** (card selection with `action: vote`) — upsert vote; reply "Vote counted (3/5 voted)."
- **lock** ("lock it in", or majority reached on the next interaction) — send a review card to the requester; on `confirm`, set `locked`, insert a `hangouts` row with `status='planned'`, purge `plan_replies`, reply with confirmation.
- **recap** ("recap: …") — attach to the most recent planned/done hangout. Extract quoted spans (straight and curly quotes) verbatim with a regex and store them untouched; `asi1` tidies only the remaining text into two lines; mark hangout `done` and record attendees if given.
- **lore_question** ("what did we do last summer", "anything from a year ago") — SQL retrieval first, then `asi1` with `build_llm_message_history` for follow-ups. Any quote is shown in a terminal custom card, never paraphrased.
- **help** — one-paragraph usage.

**Window parsing rules:** "tonight" = today 17:00–23:30; "tomorrow" = tomorrow 17:00–23:30; weekday name = next such day (weekdays 17:00–23:30, weekend days 10:00–23:30); "this weekend" = Fri 17:00 to Sun 22:00. Otherwise one `asi1-mini` call returns `{"start": ISO, "end": ISO}`.

**State machine:** `@agent.on_interval(period=1.0)` advances plans whose replies are complete or whose 10-second deadline passed. Handlers only store replies; they never wait. The final card is sent to the stored `planner_sender`.

**Planning pipeline:**

1. `collecting_availability`: send `AvailabilityRequest` to every member; wait for all replies or 10 s. Missing members count as unknown.
2. Candidate windows: 2.5-hour slots starting on the hour inside the window. Count members free in each. Priority members (missed ≥ 2 of the last 3 hangouts) must be free; if no window satisfies that, relax and keep the best. Keep the top 3 windows by count, then earliest.
3. Weather for each window from Open-Meteo: `https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&hourly=precipitation_probability,temperature_2m&timezone=America/Los_Angeles`. `rain_likely = max precipitation_probability >= 50`.
4. Ideas: one `asi1` call (prompt below) returns 8 ideas.
5. Grounding: events from Ticketmaster Discovery v2 (`https://app.ticketmaster.com/discovery/v2/events.json?apikey=..&latlong=LAT,LNG&radius=10&unit=miles&startDateTime=..Z&endDateTime=..Z&size=20`); venues and free spots from `places.py` (fixture by default, Google Places Text Search if `PLACES_API_KEY` set). Drop ideas that don't resolve to a real place. Cost: event `priceRanges` min if present, else band estimate (free 0, $ 15, $$ 35, $$$ 70).
6. `evaluating`: build up to 12 `OptionSpec` (window × grounded idea); send `EvaluateRequest`; wait for all or 10 s.
7. Score (group_agent/scoring.py), after dropping any option with a `no`:
   ```
   score = 3 * avail_frac
         + 2 * priority_all_free
         + novelty                        # 1.5 if category absent from last 10 hangouts, else -0.5 * count
         + weather_fit                    # rain_likely: indoor +1, outdoor -2; else outdoor +0.5
         - 0.5 * stretch_count
         - max(0, (worst_travel_min - 25) / 10)
   ```
   Keep the top 3 with distinct categories.
8. Pitch: one `asi1` call writes one line per option from public facts only (see invariants).
9. `voting`: send the carousel to the planner; any member can open it with vote_open.

**Carousel item (one per option):**

```json
{
  "id": "opt_2",
  "title": "Night market + board game café",
  "subtitle": "Fri 7:00 pm · indoor, rain from 8 pm",
  "badges": [{"label": "Works for everyone", "variant": "success"},
             {"label": "New for you", "variant": "info"}],
  "secondary_text": "$ · max 22 min for anyone",
  "primary_cta": {"label": "Vote",
                  "selection": {"action": "vote", "plan_id": "p_41", "option_id": "opt_2"}}
}
```

Badge rules: "Works for everyone" only when every responding member said `yes`; availability counts ("Friday works for 4/5") may appear for time only. Never a per-member verdict or a reason. Repeated categories show "You've done this 3×".

**Review card (lock-in):** `summary_rows` = option title, time, place, who's coming (names of members free in that window); `approve_cta.selection = {"action": "confirm", "plan_id": ...}`, `reject_cta.selection = {"action": "cancel", "plan_id": ...}`.

**Lore quote card (terminal custom):**

```json
{"root": {"type": "section", "title": "One year ago this week",
  "children": [
    {"type": "text", "value": "Ocean Beach bonfire · 5 of you", "style": "muted"},
    {"type": "text", "value": "\u201cexact quote as stored\u201d \u2014 Sam", "style": "emphasis"}
  ]}}
```

## shared/llm.py prompts

All calls: "Return only JSON." Parse defensively; retry once with the parse error; then fall back to a template. Log token usage.

- **IDEAS** (`asi1`). Input: home base label, windows, weather per window, recent category counts (last 10 hangouts), group size. Output: `{"ideas": [{"title", "category", "indoor": bool, "cost_band": "free|$|$$|$$$", "alcohol_centric": bool, "duration_h": float, "search_query", "source": "event|place"}]}`, exactly 8, at least 4 in categories absent from the recent counts.
- **PITCH** (`asi1`). Input built by `build_pitch_input(option_public: dict, weather: dict, novelty_note: str)` only. Output: `{"pitches": [{"option_id", "line"}]}`, one line under 90 characters each.
- **CONSTRAINTS** (`asi1-mini`, Member agent only). Output: the profile schema above.
- **INTENT** (`asi1-mini`). Output: `{"intent": "<one of the Group agent intents>"}`.
- **RECAP** (`asi1`). Input: recap text with quoted spans replaced by placeholders `[[Q1]]`; output keeps placeholders, which are swapped back for the verbatim quotes after.

## Privacy invariants (enforced by tests)

1. `Verdict`, `EvaluateReply` and `AvailabilityReply` contain only ids, ISO times, enums, ints and bools. Test the model field sets exactly.
2. No Member agent profile value (including `other`) appears in any outgoing message. Test by running a simulated plan with a sentinel string in every profile field and scanning every sent message and every LLM prompt.
3. An option with any `no` verdict never appears in any card.
4. No card shows per-member verdicts; counts appear only for time availability.
5. `build_pitch_input` accepts only public option fields, weather and the novelty note; its signature is tested.
6. Recap quotes are stored byte-for-byte; test with straight quotes, curly quotes and emoji.
7. A Member agent ignores chat from any sender except its owner; protocol messages are accepted only from the Group agent address in `GROUP_AGENT_ADDRESS`.
8. `plan_replies` rows are deleted when a plan locks or cancels; lore never stores verdicts.

## Environment (.env.example)

```
ASI_ONE_API_KEY=
USE_FIXTURES=1
TZ=America/Los_Angeles

GROUP_AGENT_SEED=
GROUP_AGENT_PORT=8000
GROUP_INVITE_CODE=
HOME_BASE_LAT=37.7599
HOME_BASE_LNG=-122.4148
HOME_BASE_LABEL=Mission, SF
DB_PATH=./data/lore.db
TICKETMASTER_API_KEY=
PLACES_API_KEY=

MEMBER_NAME=
MEMBER_AGENT_SEED=
MEMBER_PORT=8001
MEMBER_STATE_PATH=./data/member_<name>.json
GROUP_AGENT_ADDRESS=

PUSH_ENABLED=0
```

## Milestones and acceptance checks

**M1 Skeleton (target Sat 06:00)**
- Group agent and one Member agent run on mailboxes and reply to a plain message from ASI:One.
- `AvailabilityRequest` / `EvaluateRequest` round trip with stub data, logged on both sides.
- `seed_lore.py` loads 8–10 hangouts from `data/seed_hangouts.json`.
- Both agents log the sender address of every chat message (to check ASI:One sender stability across accounts).

**M2 Core pipeline (target Sat 13:00)**
- Member agent: pairing, constraint parse with read-back, `calendar` and `home` commands, verdict rules with unit tests for every table row.
- Group agent: window parsing, availability intersection, IDEAS call, grounding on fixtures, scoring with unit tests, PITCH call.
- `scripts/simulate_plan.py` runs a full plan with 5 in-process members and prints the 3 options.

**M3 Full loop (target Sat 19:00)**
- Carousel vote from 3 different ASI:One accounts, tally, review card, lock-in, hangout row written.
- Live Open-Meteo, Ticketmaster and places behind the fixture switch.
- Novelty badges and re-inclusion priority visible in `simulate_plan.py` output.

**M4 Harden (target Sun 00:00)**
- Member agent offline → unknown; API timeout → fixture; unreadable selection → resend the card with a short hint; LLM JSON failure → template.
- Recap with verbatim quotes; lore question with the terminal quote card.
- All privacy invariant tests pass.

**M5 Ship (target Sun 05:00)**
- `README.md`: what it is, architecture, every agent's name and address, run instructions, env vars, the demo flow, and these badges:
  ```
  ![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
  ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)
  ```
- `AGENTVERSE_README.md` for the Group agent written for discovery: "Plans hangouts for a friend group from shared history and each member's private constraints." Member agent README: personal, owner-only.

## Unknowns: build around them, don't assume

- Whether an agent can message an ASI:One user unprompted. Build pull-based voting; put any push behind `PUSH_ENABLED`.
- Whether ASI:One sender addresses are stable per user and distinct across accounts. M1 logs answer this; if not, pair per session instead.
- Whether cards render identically in @mention chats and planner-routed chats. The selection parser already handles both reply shapes.
