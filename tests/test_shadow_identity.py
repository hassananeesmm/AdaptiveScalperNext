"""Shadow candidate / outcome IDENTITY (migration 0032; release hardening P1).

A candidate is one (mode, symbol, resolution, decision bar, strategy key,
strategy version, direction, observer version, lifecycle version). Rows that
differ in any of those must never collapse; the same identity twice is a
no-op; outcomes belong to exactly one candidate and to that candidate's own
observer / lifecycle version; every table stays append-only.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.shadow import lifecycle as lc
from adaptive_scalper.shadow import observer as obs
from adaptive_scalper.shadow.lifecycle_counterfactual import LIFECYCLE_EVALUATOR_VERSION
from test_shadow_observer import BAR, D, _bar, _candidate


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "identity.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _rows(db):
    return db.execute("SELECT mode, canonical_symbol, resolution, strategy_version, direction, observer_version, "
                      "lifecycle_version, candidate_key FROM shadow_candidates ORDER BY id").fetchall()


def test_same_identity_twice_is_idempotent(db):
    assert obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D) == 1
    assert obs.record_candidates(db, [_candidate(raw_score=0.1)], mode="DEMO", now_utc=D + 5) == 0
    assert len(_rows(db)) == 1


def test_paper_and_demo_are_distinct_candidates(db):
    assert obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D) == 1
    assert obs.record_candidates(db, [_candidate()], mode="PAPER", now_utc=D) == 1
    assert {r[0] for r in _rows(db)} == {"DEMO", "PAPER"}


def test_strategy_versions_are_distinct_candidates(db):
    assert obs.record_candidates(db, [_candidate(strategy_version=1), _candidate(strategy_version=2)],
                                 mode="DEMO", now_utc=D) == 2
    assert sorted(r[3] for r in _rows(db)) == [1, 2]


def test_opposite_directions_are_distinct_candidates(db):
    assert obs.record_candidates(db, [_candidate(direction="BUY"), _candidate(direction="SELL")],
                                 mode="DEMO", now_utc=D) == 2


def test_resolutions_are_distinct_candidates(db):
    assert obs.record_candidates(db, [_candidate(resolution="M5"), _candidate(resolution="M1", bar_seconds=60)],
                                 mode="DEMO", now_utc=D) == 2


def test_observer_versions_are_distinct_candidates(db, monkeypatch):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    monkeypatch.setattr(obs, "OBSERVER_VERSION", "shadow_observer/v2")
    assert obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D) == 1
    assert sorted(r[5] for r in _rows(db)) == ["shadow_observer/v1", "shadow_observer/v2"]


def test_lifecycle_versions_are_distinct_candidates(db, monkeypatch):
    key = "microstructure_acceleration"
    v1 = lc.lifecycle_for(key).lifecycle_version
    obs.record_candidates(db, [_candidate(strategy_key=key)], mode="DEMO", now_utc=D)
    monkeypatch.setitem(lc.V1_LIFECYCLES, key, dataclasses.replace(lc.lifecycle_for(key), lifecycle_version="V2_TEST/1"))
    assert obs.record_candidates(db, [_candidate(strategy_key=key)], mode="DEMO", now_utc=D) == 1
    assert sorted(r[6] for r in _rows(db)) == sorted([v1, "V2_TEST/1"])


def test_identity_key_names_every_component():
    key = _candidate().identity_key("PAPER")
    for part in ("PAPER", "XAUUSD", "M5", str(D - BAR), "microstructure_acceleration", "v1", "BUY",
                 obs.OBSERVER_VERSION, _candidate().lifecycle_version):
        assert part in key.split("|"), part


def test_the_composite_unique_holds_even_with_a_different_key_string(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    columns = [r[1] for r in db.execute("PRAGMA table_info(shadow_candidates)") if r[1] != "id"]
    row = dict(zip(columns, db.execute(f"SELECT {', '.join(columns)} FROM shadow_candidates").fetchone()))
    row["candidate_key"] = "forged-different-key"
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(f"INSERT INTO shadow_candidates ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                   tuple(row.values()))


def test_unknown_mode_is_refused(db):
    with pytest.raises(ValueError):
        obs.record_candidates(db, [_candidate()], mode="REAL", now_utc=D)
    assert _rows(db) == []


# --- outcome foreign keys and version binding ---------------------------------------------

def _cid(db):
    return db.execute("SELECT id FROM shadow_candidates").fetchone()[0]


def test_outcomes_must_reference_an_existing_candidate(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO shadow_outcomes (candidate_id, horizon_seconds, status, observer_version, "
                   "resolved_at_utc) VALUES (999, 300, 'RESOLVED', ?, 0)", (obs.OBSERVER_VERSION,))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO shadow_lifecycle_outcomes (candidate_id, status, evaluator_version, "
                   "lifecycle_version, resolved_at_utc) VALUES (999, 'RESOLVED', 'e', 'l', 0)")


def test_outcome_observer_version_must_equal_its_candidates(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    with pytest.raises(sqlite3.IntegrityError, match="observer_version"):
        db.execute("INSERT INTO shadow_outcomes (candidate_id, horizon_seconds, status, observer_version, "
                   "resolved_at_utc) VALUES (?, 300, 'RESOLVED', 'shadow_observer/v9', 0)", (_cid(db),))


def test_lifecycle_outcome_version_binding_and_uniqueness(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    cid, version = db.execute("SELECT id, lifecycle_version FROM shadow_candidates").fetchone()
    insert = ("INSERT INTO shadow_lifecycle_outcomes (candidate_id, status, evaluator_version, lifecycle_version, "
              "resolved_at_utc) VALUES (?, 'RESOLVED', ?, ?, 0)")
    with pytest.raises(sqlite3.IntegrityError, match="lifecycle_version"):
        db.execute(insert, (cid, LIFECYCLE_EVALUATOR_VERSION, "SOME_OTHER/1"))
    db.execute(insert, (cid, LIFECYCLE_EVALUATOR_VERSION, version))
    with pytest.raises(sqlite3.IntegrityError):                         # same (candidate, lifecycle, evaluator)
        db.execute(insert, (cid, LIFECYCLE_EVALUATOR_VERSION, version))
    db.execute(insert, (cid, "lifecycle_counterfactual/v2-test", version))   # a new evaluator version is its own row
    assert db.execute("SELECT COUNT(*) FROM shadow_lifecycle_outcomes").fetchone()[0] == 2


def test_resolve_due_ignores_candidates_of_another_observer_version(db, monkeypatch):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    monkeypatch.setattr(obs, "OBSERVER_VERSION", "shadow_observer/v2")
    bars = [_bar(i, 100, 100, 100, 100) for i in range(8)]
    assert obs.resolve_due(db, "XAUUSD", bars, now_utc=D + 8 * BAR) == 0
    assert db.execute("SELECT COUNT(*) FROM shadow_outcomes").fetchone()[0] == 0


def test_every_identity_table_is_append_only(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    obs.resolve_due(db, "XAUUSD", [_bar(i, 100, 100, 100, 100) for i in range(8)], now_utc=D + 8 * BAR)
    cid, version = db.execute("SELECT id, lifecycle_version FROM shadow_candidates").fetchone()
    db.execute("INSERT INTO shadow_lifecycle_outcomes (candidate_id, status, evaluator_version, lifecycle_version, "
               "resolved_at_utc) VALUES (?, 'RESOLVED', 'e', ?, 0)", (cid, version))
    for table in ("shadow_candidates", "shadow_outcomes", "shadow_lifecycle_outcomes"):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] >= 1, table
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(f"UPDATE {table} SET id = id")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(f"DELETE FROM {table}")
