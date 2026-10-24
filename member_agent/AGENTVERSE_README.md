# Lore Keeper — personal Member agent

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

A personal, owner-only agent. It holds one friend's private hangout limits (budget, no alcohol, curfew, travel limit), their calendar's busy times and their home area, and answers the group's Lore Keeper agent with free time slots and yes / stretch / no verdicts only — never the reasons.

**This agent only talks to its owner.** Anyone else gets "This is a personal agent."

## Owner commands

- `pair <code>` — the code is printed when the agent starts; the first correct sender becomes the owner
- Plain words about your limits: "budget $25, no alcohol, home by 11, max 30 min travel"
- `fix: …` to change something, `profile` to see what's stored, `forget` to wipe it
- `calendar <secret iCal URL>` — only busy times are read, never event titles
- `home <neighborhood>` — stored rounded to ~1 km; only travel minutes are ever shared
