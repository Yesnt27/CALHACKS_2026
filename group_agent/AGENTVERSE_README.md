# Lore Keeper — Group agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

Plans hangouts for a friend group from shared history and each member's private constraints.

## What it does

- **Plans**: "plan something for the crew this weekend" → asks every member's personal agent, privately, for free time and a yes / stretch / no per idea, then shows 3 options as a voting carousel.
- **Remembers**: keeps the group's lore (what you did, where, who came, quotes kept word for word) and uses it to suggest new things ("you've done karaoke 3×") and to quietly make room for people who've missed recent hangouts.
- **Answers lore questions**: "what did we do last summer?", "anything from a year ago?"

## Privacy

The Group agent never sees anyone's budget, curfew, drinking preferences, home address or calendar titles. Member agents only send free time slots and per-option verdicts (`yes` / `stretch` / `no` + travel minutes) — never reasons. Options anyone said `no` to are never shown.

## Try it

1. `join <invite code> as <your name>`
2. `plan something this weekend` (or tonight / tomorrow / friday)
3. Tap **Vote** on a card, or say `vote 1`
4. `lock it in` → **Confirm**
5. Afterwards: `recap: it was great, "quote" — Sam`
6. Ask: `what did we do a year ago?`

## Protocols

- AgentChatProtocol (ASI:One chat + Interactive Cards)
- `lore-plan` 0.1.0: `AvailabilityRequest/Reply`, `EvaluateRequest/Reply` with Member agents
