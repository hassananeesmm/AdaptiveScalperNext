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

# Used ONLY to decide whether a trailing leftover buffer (after the main
# splitting loop, which is itself sqlite3-native and comment-aware) is
# "just a trailing comment" — harmless — versus genuinely incomplete SQL
# that must raise. Not used for statement splitting itself.
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


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    """Read-only connection for observers (the dashboard): SQLite itself
    rejects every write, so an observer bug can never modify trading
    state. Raises `sqlite3.OperationalError` if the database does not
    exist yet (an observer never creates it)."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 2000")
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
    """Split a migration file into individual complete SQL statements.

    Uses `sqlite3.complete_statement()` — the SQLite C library's own
    statement-boundary oracle (`sqlite3_complete()`) — as the test for
    "is this a whole statement yet", rather than a hand-rolled tokenizer.
    We scan character by character; each time we hit a `;`, we test
    whether the buffer accumulated so far is a complete statement. If it
    is, that buffer IS one full statement (including any leading
    comments) — emit it and reset. If not — e.g. the `;` was inside a
    quoted string literal, inside a `--` comment, or inside a
    `CREATE TRIGGER ... BEGIN ... END` body's internal statements — we
    keep accumulating.

    This is what makes a trigger body's own internal semicolons (e.g.
    `SELECT RAISE(ABORT, '...');` inside `BEGIN ... END;`) correctly
    stay part of ONE statement instead of being split into invalid
    fragments — the previous regex-based splitter could not do this (see
    BUG_BACKLOG.md's now-fixed entry on it) and explicitly forbade adding
    a migration with a trigger body against it. `sqlite3.complete_statement()`
    is comment- and string-literal-aware on its own, so there is no
    separate comment-stripping step here (there previously was, and it
    was itself a source of a real bug — see BUG_BACKLOG.md).

    Relies on our migration files' convention of never putting two
    complete top-level statements on the same physical `;` boundary
    inside a single accumulated test — true by construction here since
    we test at every individual `;` occurrence, not per-line, so this
    also correctly splits e.g. "CREATE TABLE a(x); CREATE TABLE b(y);"
    written on one line into two statements.

    Raises `MigrationError` if the file ends with trailing SQL that
    never became complete (a missing final ';', or an unterminated
    trigger body) rather than silently dropping it.
    """
    statements: list[str] = []
    buffer = ""
    for ch in sql:
        buffer += ch
        if ch == ";" and sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            if stmt:
                statements.append(stmt)
            buffer = ""
    remainder = buffer.strip()
    if remainder and _LINE_COMMENT_RE.sub("", remainder).strip():
        raise MigrationError(
            f"migration file ends with an incomplete SQL statement "
            f"(missing terminating ';', or an unterminated trigger/quote?): {remainder!r}"
        )
    return statements


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


def quick_check(conn: sqlite3.Connection) -> str:
    """SQLite's `PRAGMA quick_check`: page/record structure without the
    O(N log N) index cross-verification of `integrity_check` (ASN-008:
    about 0.7 s instead of 10 s on the live 275 MB database). Returns 'ok'
    or a diagnostic. Used for interactive status output; startup, doctor
    and preflight keep the full check."""
    row = conn.execute("PRAGMA quick_check").fetchone()
    return row[0] if row else "unknown"


def integrity_check(conn: sqlite3.Connection) -> str:
    """Run SQLite's built-in integrity check. Returns 'ok' or a diagnostic."""
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return row[0] if row else "unknown"
