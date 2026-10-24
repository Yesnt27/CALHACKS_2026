# Lore Keeper

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

One **Group agent** per friend group plans hangouts. Each friend has a **Member agent** that holds their private constraints (budget, no alcohol, curfew, travel limit), their calendar's busy times and their home area. The Group agent negotiates with Member agents and only ever receives free time slots and per-option verdicts (`yes` / `stretch` / `no` + travel minutes), never reasons. The group's shared history (lore) drives novelty ("you've done karaoke 3×") and quiet re-inclusion of people who've missed recent hangouts.

No custom frontend: everything happens in ASI:One chats via the Agent Chat Protocol and ASI:One Interactive Cards. Built for Cal Hacks 13.0 (Fetch.ai track).

## Architecture

```
 ASI:One user chats                                   (one per friend)
 ┌──────────────┐  chat + cards   ┌──────────────────┐  lore-plan   ┌──────────────────┐
 │ Alex, Sam, … │ ◀─────────────▶ │   Group agent    │ ◀──────────▶ │  Member agent ×N │◀─ owner chat only
 └──────────────┘                 │ intents · planner│  free slots  │ profile · iCal   │
                                  │ scoring · lore DB│  + verdicts  │ verdict rules    │
                                  └────────┬─────────┘   only       └──────────────────┘
                                           │
                       Open-Meteo · Ticketmaster · Places (fixtures by default) · ASI:One LLM
```

Plan pipeline (`group_agent/brain.py`, advanced by a 1 s `on_interval` tick; handlers only store replies):

1. **collecting_availability** — `AvailabilityRequest` to every member; wait for all or 10 s (missing = unknown).
2. **Windows** — 2.5 h slots on the hour; priority members (missed ≥ 2 of last 3) must be free, else relax; top 3 non-overlapping.
3. **Weather** per window (Open-Meteo), **IDEAS** (`asi1`, 8 ideas, ≥ 4 new categories), **grounding** on events (Ticketmaster) and venues (Places); ungroundable ideas are dropped.
4. **evaluating** — up to 12 `OptionSpec`s; each member is asked only about options in windows they're free; wait for all or 10 s.
5. **Score** (`group_agent/scoring.py`) after dropping any option with a `no`; top 3 distinct categories; **PITCH** (`asi1`) from public facts only.
6. **voting** — carousel to the planner; anyone can reopen it; majority → review card on the planner's next message; **Confirm** writes a `planned` hangout and purges `plan_replies`.

Code layout: each agent is a thin uAgents wrapper (`agent.py`) around a runtime-independent "brain" (`brain.py`), so the same logic runs in ASI:One, in a local Bureau, in the simulator and in tests.

```
shared/        protocol.py (lore-plan models) · cards.py (all card layouts) · llm.py (ASI:One + templates)
               geo.py · config.py · runtime.py
group_agent/   agent.py · brain.py · intents.py · planner.py · scoring.py · lore_db.py · sources/
member_agent/  agent.py · brain.py · profile.py (verdict rules) · calendar_ics.py · fixtures/
scripts/       seed_lore.py · simulate_plan.py · chat_repl.py · local_bureau.py · print_addresses.py
               run_all.py · run_member.sh
tests/         96 tests incl. every privacy invariant
```

## Agents

| Agent | Name on Agentverse | Address |
| --- | --- | --- |
| Group | `lore-keeper-group` | _fill in: `python scripts/print_addresses.py`_ |
| Member (Alex) | `lore-member-alex` | _fill in_ |
| Member (Sam) | `lore-member-sam` | _fill in_ |
| Member (Jordan) | `lore-member-jordan` | _fill in_ |
| Member (Priya) | `lore-member-priya` | _fill in_ |
| Member (Riley) | `lore-member-riley` | _fill in_ |

Addresses are derived from seeds, so they are final once `GROUP_AGENT_SEED` / `MEMBER_SEED_<NAME>` are set.

## Run it

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.11+
cp .env.example .env                    # USE_FIXTURES=1 works with no keys at all
.venv/bin/python -m pytest -q           # 96 tests, offline
.venv/bin/python scripts/simulate_plan.py            # full plan with 5 in-process members
.venv/bin/python scripts/simulate_plan.py --offline Riley
.venv/bin/python scripts/chat_repl.py               # chat as any friend in the terminal (cards as text)
.venv/bin/python scripts/local_bureau.py             # real uAgents round trip, no Agentverse
```

### On Agentverse / ASI:One

1. In `.env` set `GROUP_INVITE_CODE`, `GROUP_AGENT_SEED`, `MEMBER_SEED_<NAME>` for each friend in `group_agent/members.json`, and optionally `ASI_ONE_API_KEY`.
2. `python scripts/print_addresses.py --write` (fills `members.json`), then `python scripts/seed_lore.py`.
3. Start everything on one machine: `python scripts/run_all.py` (logs in `data/logs/`), or per process: `python -m group_agent.agent` and `MEMBER_NAME=Alex MEMBER_PORT=8001 GROUP_AGENT_ADDRESS=agent1q… ./scripts/run_member.sh`.
4. On first start open each agent's **inspector link** from its log → **Connect → Mailbox**. The free tier allows 8 local agents per Agentverse account (this setup uses 6).
5. Each friend chats with their Member agent on ASI:One: `pair <code from the log>`, then their limits, `calendar <secret iCal URL>`, `home <neighborhood>`.
6. Everyone chats with the Group agent: `join <invite code> as <name>`.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `ASI_ONE_API_KEY` | empty | ASI:One LLM; empty → template fallbacks everywhere |
| `ASI_MODEL_LARGE` / `ASI_MODEL_SMALL` | `asi1` / `asi1-mini` | model names (ideas, pitches, recaps, lore / intents, windows, constraints) |
| `USE_FIXTURES` | `1` | `1`: weather, events, places, geocoding from fixtures, no network |
| `GROUP_AGENT_SEED`, `GROUP_AGENT_PORT` | dev seed, `8000` | Group agent identity and port |
| `GROUP_INVITE_CODE` | empty | required for `join` |
| `HOME_BASE_LAT/LNG/LABEL` | Mission, SF | where ideas and venues are searched |
| `DB_PATH` | `./data/lore.db` | SQLite lore + plan state |
| `TICKETMASTER_API_KEY`, `PLACES_API_KEY` | empty | live events / Google Places (with `USE_FIXTURES=0`) |
| `MEMBER_NAME`, `MEMBER_AGENT_SEED`, `MEMBER_PORT` | —, dev seed, `8001` | one Member agent |
| `MEMBER_STATE_PATH` | `./data/member_<name>.json` | member's private state file |
| `GROUP_AGENT_ADDRESS` | empty | the only sender a Member agent accepts lore-plan messages from |
| `PUSH_ENABLED` | `0` | `1`: also send voting cards to other joined members (unprompted messages) |
| `MEMBER_SEED_<NAME>` | dev seed | used by `print_addresses.py`, `run_all.py`, `run_member.sh` |

Every external call has a fallback: API error or timeout → fixture; LLM error or bad JSON (after one retry) → template.

## Demo flow

1. Riley's agent: `pair ABC123` → "budget $25, no alcohol, home by 11" → read-back of what stays private.
2. Group chat (Alex): `plan something for the crew this weekend` → "Checking calendars and asking everyone's agent privately…" → carousel of 3 options with badges (*New for you*, *You've done this 3×*, *Saturday works for 4/5*, *Works for everyone*).
3. Sam, Jordan, Priya (three ASI:One accounts) tap **Vote** → "Vote counted (3/5 voted)."
4. Alex: `what's the plan` → majority → review card → **Confirm** → locked, hangout saved.
5. `recap: absolute chaos. "I am never doing that again 😂" — Jordan` → quotes saved word for word.
6. `anything from a year ago?` → "One year ago this week" quote card.

### Commands

Group agent: `join <code> as <name>` · `plan something <tonight|tomorrow|friday|this weekend>` · `what's the plan` / `vote` / `vote 2` · `lock it in` · `confirm` / `cancel` · `recap: …` · lore questions · `help`.
Member agent (owner only): `pair <code>` · free text limits · `fix: …` · `profile` · `forget` · `calendar <url>` · `home <place>`.

## Privacy invariants (enforced by tests)

| # | Invariant | Test |
| --- | --- | --- |
| 1 | Member replies carry only ids, ISO times, enums, ints, bools | `tests/test_protocol.py` |
| 2 | No profile value (incl. `other`, home, calendar titles) in any message or LLM prompt | `tests/test_privacy.py::test_invariant2…` (sentinel run) |
| 3 | An option with any `no` never appears in any card | `test_invariant3…` |
| 4 | No per-member verdicts in cards; counts only for time | `test_invariant4…` (badge allow-list) |
| 5 | `build_pitch_input` accepts only public fields | `tests/test_llm.py` |
| 6 | Recap quotes stored byte-for-byte (straight, curly, emoji) | `test_invariant6…` |
| 7 | Member agent: owner-only chat; lore-plan only from `GROUP_AGENT_ADDRESS` | `tests/test_member.py` |
| 8 | `plan_replies` purged on lock/cancel; lore never stores verdicts | `test_invariant8…` |

Extra privacy choices: event titles/descriptions/locations are stripped from calendars at parse time; free-slot edges snap to :00/:30 so exact private times (e.g. a 22:47 curfew) never leave; home coordinates are rounded to 2 decimals; verdict reasons are logged at DEBUG in the member's own terminal only.

## Open questions (built around, not assumed)

- **Unprompted messages**: voting is pull-based (`what's the plan`); pushes to other members are behind `PUSH_ENABLED`.
- **Sender stability**: both agents log every chat sender address and session; if ASI:One senders aren't stable per user, switch `join` to per-session pairing.
- **Card rendering in @mention vs planner-routed chats**: selections are read from card-response metadata, selection JSON in text, or prose (`vote opt_2`, `vote 2`).
- **Delayed card session**: the options card is sent from the interval handler, in the planner's original chat session (`shared/runtime.py`); confirm in ASI:One that it shows in the same chat.

## Troubleshooting

- `CERTIFICATE_VERIFY_FAILED` with python.org Python on macOS: the agents point `SSL_CERT_FILE` at certifi automatically (`shared/__init__.py`); or run *Install Certificates.command* once.
- `build_llm_message_history` needs the `uagents[llm]` extra (litellm); without it an identical inline version is used.
