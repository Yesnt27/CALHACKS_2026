# Lore Keeper — notes for Claude

Fetch.ai hackathon project (Cal Hacks 13.0, deadline Sun Oct 25 2026 09:00 PDT). The original
build brief is `docs/BRIEF.md` — it is the spec; follow its working rules and privacy invariants.
User-facing overview, env vars and run steps are in `README.md`.

## Status (as of Oct 24, 2026 ~04:00)

Milestones M1–M5 from the brief are implemented and pass offline checks. 96 tests pass.
Not yet done (needs the team's accounts/keys):
- Connect the 6 agents to Agentverse mailboxes and test in ASI:One (sender stability, whether the
  delayed options card lands in the same chat, card rendering in @mention vs routed chats).
- Tune prompts with a real `ASI_ONE_API_KEY` (no key → template fallbacks). Model names `asi1` /
  `asi1-mini` are unverified; configurable via `ASI_MODEL_LARGE` / `ASI_MODEL_SMALL`.
- Fill real agent addresses in README (`scripts/print_addresses.py`).
- Card visual design: the user will give direction. All card layouts live in `shared/cards.py`
  (payloads are validated against `uagents_core.contrib.protocols.chat.cards` schemas).

## Commands

```bash
.venv/bin/python -m pytest -q                 # all tests, offline
.venv/bin/python scripts/simulate_plan.py     # full plan, 5 in-process members (--offline Riley)
.venv/bin/python scripts/chat_repl.py         # chat as any friend in the terminal
.venv/bin/python scripts/local_bureau.py      # real uAgents round trip, fully offline
.venv/bin/python scripts/run_all.py           # start group + members on Agentverse mailboxes
```

## Architecture

- Each agent = thin uAgents wrapper (`*/agent.py`) around a runtime-independent brain (`*/brain.py`).
  Brains take a `send(dest, msg, session)` callable; tests and the simulator inject fakes.
- `group_agent/brain.py`: chat intents + `tick()` state machine (collecting_availability →
  evaluating → voting → locked/cancelled). Handlers only store replies; work happens in `tick()`.
- `shared/llm.py`: every LLM call goes through `LLM.json_call` (JSON-only, one retry, template
  fallback, prompt listeners used by the privacy tests).
- `scripts/simulate_plan.py::Simulation` is the in-process harness reused by tests and the REPL.

## Decisions that differ from or extend the brief

- Members are only asked to evaluate options in windows they reported free (otherwise one busy
  calendar vetoes everything).
- Free-slot edges snap to :00/:30 so exact private times (e.g. a 22:47 curfew) never leave.
- `plans` has two extra columns: `planner_session` and `work_json` (transient, cleared on lock/cancel).
- The options card is sent from the interval tick in the planner's original session by swapping
  `ctx._session` (`shared/runtime.py`) — private uAgents attribute.
- `build_llm_message_history` needs `uagents[llm]` (litellm); an identical inline fallback is used.
- `shared/__init__.py` sets `SSL_CERT_FILE` to certifi (python.org macOS Python lacks CA certs).
- `scripts/local_bureau.py` disables Almanac/Agentverse registration so it stays offline.

## Conventions

- Python 3.11+ (venv uses 3.12). Timezone `America/Los_Angeles`, ISO 8601 with offsets everywhere.
- Every external call has a fixture fallback; `USE_FIXTURES=1` must run with no network.
- Privacy invariants are enforced by tests (`tests/test_privacy.py`, `test_protocol.py`,
  `test_member.py`, `test_llm.py`); keep them passing. Never add a web frontend, payments,
  reservations, OAuth or a non-SQLite DB.
