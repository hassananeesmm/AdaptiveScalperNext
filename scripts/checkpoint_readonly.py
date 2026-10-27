"""Read-only checkpoint: WAL-safe online backup of a database plus a summary.

    python scripts/checkpoint_readonly.py --source data/adaptive_scalper.sqlite3 --out-dir <new-dir> [--repo <checkout>]

The source is opened read-only (`mode=ro`) and copied with the SQLite online
backup API, so the WAL is included and the source is never written. The
output directory must not exist yet: nothing is ever overwritten. `--repo`
additionally records the SHA-256 of that checkout's uncommitted diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True, type=Path, help="database to copy (opened read-only)")
    parser.add_argument("--out-dir", required=True, type=Path, help="new directory for the copy and report")
    parser.add_argument("--repo", type=Path, help="optional checkout whose uncommitted diff is fingerprinted")
    args = parser.parse_args(argv)
    source = args.source.resolve(strict=True)
    destination = args.out_dir.resolve()
    if destination.exists():
        raise SystemExit(f"refusing to reuse existing output directory {destination}")
    destination.mkdir(parents=True)
    snapshot = destination / "database-copy.sqlite3"
    with (closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
          closing(sqlite3.connect(snapshot)) as target):
        src.backup(target)
        target.row_factory = sqlite3.Row
        report = {
            "source": str(source),
            "schema": target.execute("SELECT max(version) FROM schema_migrations").fetchone()[0],
            "integrity": target.execute("PRAGMA integrity_check").fetchone()[0],
            "runtime": [dict(row) for row in target.execute(
                "SELECT * FROM runtime_state WHERE key IN ('engine','peak_equity','broker_truth','reconciliation')")],
            "kill": [dict(row) for row in target.execute("SELECT * FROM app_state WHERE key='kill_switch'")],
        }
    if report["integrity"] != "ok":
        raise SystemExit("backup integrity check failed")
    if args.repo is not None:
        diff = subprocess.run(["git", "diff", "--binary", "HEAD"], cwd=args.repo, capture_output=True,
                              check=True).stdout
        report["repo_diff_sha256"] = hashlib.sha256(diff).hexdigest()
    report["backup_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    (destination / "inspection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"runtime", "kill"}}, indent=2))


if __name__ == "__main__":
    main()
