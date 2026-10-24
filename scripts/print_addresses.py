"""Print agent addresses derived from seeds (no network).

Usage: python scripts/print_addresses.py [--write]
  Member seeds come from MEMBER_SEED_<NAME> env vars, else the dev seed.
  --write fills group_agent/members.json with the member addresses.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from uagents.crypto import Identity  # noqa: E402

from shared.config import ROOT, dev_seed, env  # noqa: E402

MEMBERS = ROOT / "group_agent" / "members.json"


def address(seed: str) -> str:
    return Identity.from_seed(seed, 0).address


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    group_seed = env("GROUP_AGENT_SEED") or dev_seed("group")
    print(f"group   lore-keeper-group      {address(group_seed)}"
          + ("" if env("GROUP_AGENT_SEED") else "   (dev seed)"))
    members = json.loads(MEMBERS.read_text())
    for m in members:
        seed = env(f"MEMBER_SEED_{m['name'].upper()}") or dev_seed(f"member:{m['name']}")
        m["member_agent_address"] = address(seed)
        print(f"member  lore-member-{m['name'].lower():<10} {m['member_agent_address']}")
    if args.write:
        MEMBERS.write_text(json.dumps(members, indent=2) + "\n")
        print(f"wrote {MEMBERS}")
    print("\nSet GROUP_AGENT_ADDRESS in each member's env to the group address above.")


if __name__ == "__main__":
    main()
