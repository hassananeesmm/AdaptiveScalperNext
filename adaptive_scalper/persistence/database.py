"""SQLite connection + migration runner.

Per MASTER_BUILD_DIRECTIVE.md section 53: WAL mode, foreign keys on,
migrations, integrity checks. SQLite is the single authoritative local
store for the whole system.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Matches a `--` line comment through end-of-line. Stripped before
# splitting on ';' so a semicolon inside a comment (e.g. "one row per
# symbol; re-resolution overwrites it") doesn't get mistaken for a
# statement terminator. Does NOT account for '--' inside a string
# literal — not a concern for today's plain-DDL migrations (see
# _split_statements' docstring for the tracked limitation).
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


class MigrationError(Exception):
    """Raised when a migration file is malformed or fails to apply."""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with the pragmas this project requires everywhere.

    Every caller (app, tests, CLI, dashboard) must go through this — never
    call sqlite3.connect() directly — so WAL mode and foreign-key
    enforcement are never accidentally skipped in one code path.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _discover_migrations() -> list[tuple[int, str, Path]]:
    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        stem = path.stem  # e.g. "0001_initial"
        prefix = stem.split("_", 1)[0]
        try:
            version = int(prefix)
        except ValueError as exc:
            raise MigrationError(
                f"migration filename {path.name!r} must start with a "
                f"zero-padded integer version, e.g. 0001_initial.sql"
            ) from exc
        name = stem[len(prefix) + 1:] if "_" in stem else stem
        migrations.append((version, name, path))
    versions = [v for v, _, _ in migrations]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration version numbers in {MIGRATIONS_DIR}")
    return migrations


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not table_exists:
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row["version"] for row in rows}


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into individual ';'-terminated statements.

    Strips '--' line comments first (so a semicolon inside a comment isn't
    mistaken for a statement terminator — this broke migration 0002, whose
    comment read "...per symbol; re-resolution overwrites..."), then splits
    on every remaining top-level ';'.

    Deliberately NOT a general SQL tokenizer — it has no awareness of
    string literals or trigger bodies that embed their own semicolons or
    '--'. That is sufficient for the plain DDL our migrations contain
    today. If a future migration needs a trigger body or a string literal
    containing ';' or '--', this must be replaced with a real tokenizer
    first — do not add such a migration against this splitter.
    """
    without_comments = _LINE_COMMENT_RE.sub("", sql)
    return [stmt.strip() for stmt in without_comments.split(";") if stmt.strip()]


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply every pending migration in version order.

    Idempotent: migrations already recorded in schema_migrations are
    skipped. Each migration runs in its own transaction; a failure rolls
    back that migration's own statements and raises MigrationError instead
    of leaving the database half-migrated.

    Uses conn.execute() per statement rather than executescript(): the
    sqlite3 module's executescript() issues its own implicit COMMIT before
    running, which silently closes any transaction opened with an explicit
    BEGIN — that broke atomicity here (each migration's DDL and its
    schema_migrations bookkeeping row must commit or roll back together).
    """
    migrations = _discover_migrations()
    already = applied_versions(conn)
    applied_now: list[int] = []
    for version, name, path in migrations:
        if version in already:
            continue
        statements = _split_statements(path.read_text(encoding="utf-8"))
        try:
            conn.execute("BEGIN")
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise MigrationError(f"migration {path.name} failed: {exc}") from exc
        applied_now.append(version)
    return applied_now


def integrity_check(conn: sqlite3.Connection) -> str:
    """Run SQLite's built-in integrity check. Returns 'ok' or a diagnostic."""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "unknown"
