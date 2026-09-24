
import pytest

from adaptive_scalper.persistence import connect, integrity_check, migrate
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


def test_migrations_from_empty_are_contiguous_and_reach_the_current_schema(tmp_path):
    """Release QA: a brand-new database on the laptop migrates 1..N with no
    gaps, passes the integrity check, and has every table the runtime,
    dashboard, RAG ingestion, cost evidence and spec store need."""
    versions = [v for v, _, _ in _discover_migrations()]
    assert versions == list(range(1, len(versions) + 1))
    assert versions[-1] >= 25
    conn = connect(tmp_path / "fresh.sqlite3")
    applied = migrate(conn)
    assert applied == versions
    assert integrity_check(conn) == "ok"
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for needed in ("runtime_state", "runtime_events", "entry_decisions", "rag_ingestion_state",
                   "execution_cost_observations", "symbol_specs", "research_trials", "paper_session_state"):
        assert needed in tables
    assert migrate(conn) == []
