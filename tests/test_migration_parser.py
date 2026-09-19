"""Tests for the SQLite-aware migration statement splitter
(`adaptive_scalper.persistence.database._split_statements`), which uses
`sqlite3.complete_statement()` as its statement-boundary oracle instead
of a hand-rolled tokenizer. See BUG_BACKLOG.md for the defect history
this replaces (a naive ';'-split that could not safely handle a
CREATE TRIGGER ... BEGIN ... END body's internal semicolons).
"""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.persistence.database import MigrationError, _split_statements, connect, migrate


# --------------------------------------------------------------------------
# 1. Ordinary statements still split correctly
# --------------------------------------------------------------------------

def test_single_create_table_is_one_statement():
    sql = "CREATE TABLE t (id INTEGER PRIMARY KEY);"
    stmts = _split_statements(sql)
    assert len(stmts) == 1
    assert stmts[0].startswith("CREATE TABLE t")


# --------------------------------------------------------------------------
# 2. Multiple normal statements separated correctly
# --------------------------------------------------------------------------

def test_multiple_statements_split_correctly():
    sql = """
    CREATE TABLE a (id INTEGER PRIMARY KEY);
    CREATE TABLE b (id INTEGER PRIMARY KEY);
    CREATE INDEX idx_a ON a (id);
    """
    stmts = _split_statements(sql)
    assert len(stmts) == 3
    assert "CREATE TABLE a" in stmts[0]
    assert "CREATE TABLE b" in stmts[1]
    assert "CREATE INDEX idx_a" in stmts[2]


def test_two_statements_on_one_physical_line_still_split():
    sql = "CREATE TABLE a (id INTEGER); CREATE TABLE b (id INTEGER);"
    stmts = _split_statements(sql)
    assert len(stmts) == 2


# --------------------------------------------------------------------------
# 3. A semicolon inside a quoted string literal stays inside one statement
# --------------------------------------------------------------------------

def test_semicolon_inside_quoted_string_literal_does_not_split():
    sql = "INSERT INTO x (value) VALUES ('hello;world');"
    stmts = _split_statements(sql)
    assert len(stmts) == 1
    assert "hello;world" in stmts[0]


def test_semicolon_inside_a_comment_does_not_split():
    sql = """
    -- one row per symbol; re-resolution overwrites it
    CREATE TABLE t (id INTEGER PRIMARY KEY);
    """
    stmts = _split_statements(sql)
    assert len(stmts) == 1
    assert "CREATE TABLE t" in stmts[0]


# --------------------------------------------------------------------------
# 4. A trigger body with multiple internal semicolons is ONE statement
# --------------------------------------------------------------------------

def test_trigger_body_with_multiple_internal_semicolons_is_one_statement():
    sql = """
    CREATE TABLE t (id INTEGER PRIMARY KEY, touched_at TEXT);

    CREATE TRIGGER trg_no_update
    BEFORE UPDATE ON t
    BEGIN
        SELECT RAISE(ABORT, 'no updates allowed');
        SELECT RAISE(ABORT, 'this is a second statement in the body');
    END;

    CREATE INDEX idx_t_id ON t (id);
    """
    stmts = _split_statements(sql)
    assert len(stmts) == 3
    assert "CREATE TABLE t" in stmts[0]
    assert stmts[1].count("RAISE(ABORT") == 2
    assert stmts[1].strip().startswith("CREATE TRIGGER")
    assert stmts[1].strip().endswith("END;")
    assert "CREATE INDEX idx_t_id" in stmts[2]


# --------------------------------------------------------------------------
# 5. A migration with CREATE TABLE + CREATE TRIGGER + another CREATE TABLE
#    applies successfully end to end
# --------------------------------------------------------------------------

@pytest.fixture()
def tmp_migrations_dir(tmp_path, monkeypatch):
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    monkeypatch.setattr("adaptive_scalper.persistence.database.MIGRATIONS_DIR", mig_dir)
    return mig_dir


def _write_migration(mig_dir, filename: str, sql: str) -> None:
    (mig_dir / filename).write_text(sql, encoding="utf-8")


def test_migration_with_table_trigger_table_applies_successfully(tmp_migrations_dir, tmp_path):
    _write_migration(tmp_migrations_dir, "0001_initial.sql", """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
    """)
    _write_migration(tmp_migrations_dir, "0002_journal_like.sql", """
        CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT);

        CREATE TRIGGER trg_events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events is append-only');
        END;

        CREATE TABLE other (id INTEGER PRIMARY KEY);
    """)
    conn = connect(tmp_path / "test.sqlite3")
    applied = migrate(conn)
    assert applied == [1, 2]

    conn.execute("INSERT INTO events (id, payload) VALUES (1, 'x')")
    with pytest.raises(sqlite3.Error):
        conn.execute("UPDATE events SET payload = 'y' WHERE id = 1")

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"events", "other", "schema_migrations"} <= tables
    conn.close()


# --------------------------------------------------------------------------
# 6. Migration transactionality: a later statement failing rolls back the
#    earlier statements AND the schema_migrations bookkeeping row together
# --------------------------------------------------------------------------

def test_migration_rolls_back_earlier_statements_if_a_later_one_fails(tmp_migrations_dir, tmp_path):
    _write_migration(tmp_migrations_dir, "0001_initial.sql", """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
    """)
    _write_migration(tmp_migrations_dir, "0002_bad.sql", """
        CREATE TABLE should_not_persist (id INTEGER PRIMARY KEY);
        THIS IS NOT VALID SQL;
    """)
    conn = connect(tmp_path / "test.sqlite3")
    with pytest.raises(MigrationError):
        migrate(conn)

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "should_not_persist" not in tables  # rolled back together with the bookkeeping row
    versions = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    assert 2 not in versions
    conn.close()


# --------------------------------------------------------------------------
# 7. Incomplete SQL does not silently disappear
# --------------------------------------------------------------------------

def test_incomplete_trailing_sql_raises_rather_than_vanishing():
    sql = "CREATE TABLE t (id INTEGER PRIMARY KEY)"  # missing trailing ';'
    with pytest.raises(MigrationError):
        _split_statements(sql)


def test_unterminated_trigger_body_raises():
    sql = """
    CREATE TRIGGER trg
    BEFORE UPDATE ON t
    BEGIN
        SELECT 1;
    """  # missing END;
    with pytest.raises(MigrationError):
        _split_statements(sql)


def test_empty_or_whitespace_only_sql_produces_no_statements():
    assert _split_statements("") == []
    assert _split_statements("   \n\n  -- just a comment\n") == []


# --------------------------------------------------------------------------
# 8. Migrations remain idempotent under the new splitter
# --------------------------------------------------------------------------

def test_migrate_is_idempotent_with_trigger_migration(tmp_migrations_dir, tmp_path):
    _write_migration(tmp_migrations_dir, "0001_initial.sql", """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        );
    """)
    _write_migration(tmp_migrations_dir, "0002_journal_like.sql", """
        CREATE TABLE events (id INTEGER PRIMARY KEY);

        CREATE TRIGGER trg_events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'no deletes');
        END;
    """)
    conn = connect(tmp_path / "test.sqlite3")
    first = migrate(conn)
    second = migrate(conn)
    assert first == [1, 2]
    assert second == []
    conn.close()
