"""Load data/seed_hangouts.json into the lore DB.  Usage: python scripts/seed_lore.py [--reset]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from group_agent.lore_db import LoreDB  # noqa: E402
from shared.config import ROOT, env, resolve_path  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="delete existing hangouts first")
    ap.add_argument("--file", default=str(ROOT / "data" / "seed_hangouts.json"))
    args = ap.parse_args()
    db = LoreDB(resolve_path(env("DB_PATH", "./data/lore.db")))
    n = db.load_seed(args.file, reset=args.reset)
    total = len(db.hangouts(limit=1000))
    print(f"loaded {n} hangouts ({total} total)" if n else
          f"lore already has {total} hangouts; use --reset to reload")
    print("recent categories:", db.category_counts())


if __name__ == "__main__":
    main()
