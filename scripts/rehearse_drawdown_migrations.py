"""Rehearse migrations 0033 + 0034 on a copy of a checkpoint, never on its source.

    python scripts/rehearse_drawdown_migrations.py --source <checkpoint.sqlite3> --target <new-copy.sqlite3>

The source is opened read-only and copied with the SQLite online backup API;
only the new target (which must not exist yet) is migrated. The run fails
unless exactly [33, 34] apply, integrity holds, a re-run is a no-op, every
pre-existing table keeps its row count, the 0034 breach latch is present and
no historical peak was invented.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from adaptive_scalper.persistence.database import connect, integrity_check, migrate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="checkpoint copy (opened read-only)")
    parser.add_argument("--target", required=True, type=Path, help="new file to migrate (must not exist)")
    args = parser.parse_args(argv)
    source_path = args.source.resolve(strict=True)
    target_path = args.target.resolve()
    if target_path.exists() or target_path == source_path:
        raise SystemExit(f"refusing to migrate an existing file or the source: {target_path}")
    with (closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source,
          closing(sqlite3.connect(target_path)) as target):
        source.backup(target)
    with closing(connect(target_path)) as conn:
        names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                  for name in names if name not in ("schema_migrations", "sqlite_sequence")}
        applied = migrate(conn)
        if applied != [33, 34]:
            raise SystemExit(f"expected migrations [33, 34], applied {applied}")
        if integrity_check(conn) != "ok" or migrate(conn) != []:
            raise SystemExit("integrity check failed or the migration is not idempotent")
        if not all(conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] == count
                   for name, count in before.items()):
            raise SystemExit("a pre-existing table changed row count")
        latch = conn.execute("SELECT latched, reason FROM project_drawdown_events").fetchone()
        if latch is None or latch["latched"] != 1:
            raise SystemExit("0034 breach latch missing")
        if conn.execute("SELECT COUNT(*) FROM peak_equity_history").fetchone()[0] != 0:
            raise SystemExit("a historical peak was written by the migration")
        print(json.dumps({"applied": applied, "integrity": "ok", "existing_table_counts_unchanged": True,
                          "latch": dict(latch), "historical_peak_invented": False}, indent=2))


if __name__ == "__main__":
    main()
