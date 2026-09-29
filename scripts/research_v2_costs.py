"""Cost model V2 diagnostics from DEMO broker fills (read-only).

Opens the database with a SQLite `mode=ro` URI: no migration, no write,
safe next to the running runtime (WAL readers do not block it).

    python scripts/research_v2_costs.py --db data\\adaptive_scalper.sqlite3 --out data\\research\\v2_costs.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from adaptive_scalper.research.v2.costs import cost_diagnostics, load_filled_observations  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    conn = sqlite3.connect(f"{Path(args.db).resolve().as_uri()}?mode=ro", uri=True)
    try:
        report = cost_diagnostics(load_filled_observations(conn))
    finally:
        conn.close()
    report["created_at_utc"] = int(time.time())
    Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({s: v["all"] for s, v in report["symbols"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
