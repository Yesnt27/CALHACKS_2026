"""Start the Group agent and every Member agent from group_agent/members.json on this machine.

Usage: python scripts/run_all.py [--only Alex,Sam]

Addresses are derived from seeds (GROUP_AGENT_SEED, MEMBER_SEED_<NAME>, else dev
seeds), members.json is refreshed, and each agent gets its own port (8000, 8001, …),
state file and log in data/logs/. Ctrl-C stops everything.
On first start, open each agent's inspector link from its log and choose
Connect -> Mailbox (free tier: 8 local agents per Agentverse account).
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.print_addresses import MEMBERS, address  # noqa: E402
from shared.config import ROOT, dev_seed, env  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated member names to start")
    args = ap.parse_args()
    logs = ROOT / "data" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    group_seed = env("GROUP_AGENT_SEED") or dev_seed("group")
    group_addr = address(group_seed)
    members = json.loads(MEMBERS.read_text())
    seeds = {}
    for m in members:
        seeds[m["name"]] = env(f"MEMBER_SEED_{m['name'].upper()}") or dev_seed(f"member:{m['name']}")
        m["member_agent_address"] = address(seeds[m["name"]])
    MEMBERS.write_text(json.dumps(members, indent=2) + "\n")

    base_port = int(env("GROUP_AGENT_PORT", "8000"))
    only = {n.strip().lower() for n in args.only.split(",") if n.strip()}
    procs = []
    spec = [("group", {"GROUP_AGENT_SEED": group_seed}, ["-m", "group_agent.agent"])]
    for i, m in enumerate(members, 1):
        if only and m["name"].lower() not in only:
            continue
        spec.append((m["name"].lower(), {
            "MEMBER_NAME": m["name"], "MEMBER_PORT": str(base_port + i),
            "MEMBER_AGENT_SEED": seeds[m["name"]], "GROUP_AGENT_ADDRESS": group_addr,
            "MEMBER_STATE_PATH": f"./data/member_{m['name'].lower()}.json"}, ["-m", "member_agent.agent"]))
    for name, extra, cmd in spec:
        log = open(logs / f"{name}.log", "a")
        procs.append((name, subprocess.Popen([sys.executable, *cmd], cwd=ROOT, env={**os.environ, **extra},
                                             stdout=log, stderr=subprocess.STDOUT)))
        print(f"started {name:<8} -> data/logs/{name}.log")
    print(f"\ngroup address: {group_addr}\nCtrl-C to stop. Pair codes and inspector links are in the logs.")
    try:
        while all(p.poll() is None for _, p in procs):
            time.sleep(1)
        for name, p in procs:
            if p.poll() is not None:
                print(f"{name} exited with {p.returncode}; see data/logs/{name}.log")
    except KeyboardInterrupt:
        pass
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for _, p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
