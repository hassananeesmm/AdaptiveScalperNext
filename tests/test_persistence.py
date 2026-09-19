import sqlite3

import pytest

from adaptive_scalper.persistence import MigrationError, connect, integrity_check, migrate
from adaptive_scalper.persistence.database import _discover_migrations, applied_versions


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    yield conn
    conn.close()


def test_connect_sets_required_pragmas(db):
    assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_creates_expected_tables(db):
    migrate(db)
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"schema_migrations", "app_state", "configuration_audit", "symbol_mapping"} <= tables


def test_migrate_applies_every_discovered_migration_exactly_once():
    all_versions = [v for v, _, _ in _discover_migrations()]
    assert all_versions == sorted(all_versions), "migrations must be discovered in version order"


def test_migrate_is_idempotent(db):
    all_versions = sorted(v for v, _, _ in _discover_migrations())
    first = migrate(db)
    second = migrate(db)
    assert first == all_versions
    assert second == []


def test_migrate_records_applied_versions(db):
    all_versions = set(v for v, _, _ in _discover_migrations())
    migrate(db)
    assert applied_versions(db) == all_versions


def test_integrity_check_reports_ok_after_migration(db):
    migrate(db)
    assert integrity_check(db) == "ok"


def test_fresh_db_has_no_applied_versions_before_migrate(db):
    assert applied_versions(db) == set()
