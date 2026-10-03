"""Integrated FLAT/SHADOW release candidate: the remaining required controls
(INTEGRATED_FORWARD_EDGE_RELEASE_AUDIT.md, controls 15 and 19-22).

15  microstructure_acceleration stays non-executable -- even with verified edge evidence
19  migration 31 -> 32 succeeds on a database that already holds data
20  a fresh database migrates to 32 with the shadow tables and their guards
21  an incompatible PAPER economic fingerprint (pre-v3 fill model) is refused
22  the kill switch stays fail-closed when verified evidence exists
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from adaptive_scalper.config.loader import load_config
from adaptive_scalper.core.final_permission import BLOCK_STRATEGY_SUSPENDED, evaluate_final_permission
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.persistence import connect, database, migrate
from edge_fixtures import FixtureValidatedProvider, fixture_validated_evidence
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

MICRO = "microstructure_acceleration"
START_AT = T0 + 60 * STEP + 10


# --- 15: microstructure non-executable ------------------------------------------------

def test_shipped_config_suspends_microstructure():
    root = Path(__file__).resolve().parents[1]
    assert load_config(root / "config" / "default.toml").strategies.entry_suspended == [MICRO]


def test_final_permission_blocks_a_suspended_strategy_even_with_verified_evidence():
    from test_final_permission import _full_allow_input, _signal

    signal = _signal(strategy_key=MICRO)
    evidence = fixture_validated_evidence(strategy_key=MICRO)
    blocked = _full_allow_input(signal=signal, edge_evidence=evidence, entry_suspended_strategy_keys=frozenset({MICRO}))
    assert evaluate_final_permission(blocked).decision == BLOCK_STRATEGY_SUSPENDED
    # control: identical input without the suspension is allowed
    assert evaluate_final_permission(_full_allow_input(signal=signal, edge_evidence=evidence)).decision == "ALLOW"


def test_demo_with_verified_evidence_for_everything_never_trades_microstructure_and_still_observes_it(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    engine.config.strategies.entry_suspended = [MICRO]
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 10, tick=4)
    assert MICRO not in {r[0] for r in conn.execute("SELECT strategy_key FROM positions")}
    for disposition, reason in conn.execute(
            "SELECT selector_disposition, rejection_reason FROM shadow_candidates WHERE strategy_key = ?", (MICRO,)):
        assert disposition == "NOT_EVALUATED" or (disposition, reason) == ("REJECTED", "strategy_entry_suspended")


# --- 19 / 20: migrations -----------------------------------------------------------------

def _names(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')")}


def test_migration_31_to_32_on_a_populated_database(tmp_path, monkeypatch):
    old_dir = tmp_path / "migrations_31"
    old_dir.mkdir()
    for path in sorted(database.MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) <= 31:
            shutil.copy(path, old_dir / path.name)
    db_path = tmp_path / "prod_like.sqlite3"
    monkeypatch.setattr(database, "MIGRATIONS_DIR", old_dir)
    conn = connect(db_path)
    migrate(conn)
    assert max(database.applied_versions(conn)) == 31
    conn.execute("INSERT INTO runtime_state (key, value_json, updated_at_utc) VALUES ('probe', '{\"x\": 1}', 1)")
    conn.commit()
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("runtime_state", "positions", "orders", "deals")}
    conn.close()

    monkeypatch.undo()
    conn = connect(db_path)
    migrate(conn)
    assert max(database.applied_versions(conn)) == 32
    assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before} == before
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert {"shadow_candidates", "shadow_outcomes", "shadow_lifecycle_outcomes"} <= _names(conn)
    migrate(conn)                                   # idempotent
    assert max(database.applied_versions(conn)) == 32


def test_a_fresh_database_migrates_to_32_with_append_only_shadow_tables(tmp_path):
    conn = connect(tmp_path / "fresh.sqlite3")
    migrate(conn)
    assert max(database.applied_versions(conn)) == 32
    names = _names(conn)
    for trigger in ("trg_shadow_candidates_no_update", "trg_shadow_candidates_no_delete",
                    "trg_shadow_outcomes_no_update", "trg_shadow_outcomes_no_delete",
                    "trg_shadow_lifecycle_no_update", "trg_shadow_lifecycle_no_delete"):
        assert trigger in names
    insert = ("INSERT INTO shadow_candidates (candidate_key, observer_version, lifecycle_version, observed_at_utc, "
              "mode, canonical_symbol, resolution, bar_seconds, decision_bar_time_utc, decision_time_utc, "
              "strategy_key, strategy_version, direction, raw_score, stop_distance, target_distance, "
              "edge_model, selector_disposition, final_permission_result, chain_key, features_json) VALUES "
              "('k','v','l',1,'DEMO','XAUUSD','M5',300,1,301,'s',1,'BUY',0.5,1,2,'NONE','REJECTED',?,'c','{}')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):    # the final-permission column is constrained
        conn.execute(insert, ("ALLOW",))
    conn.execute(insert, ("NOT_REACHED",))                        # control: the same row with a legal value


# --- 21: incompatible PAPER fingerprint ------------------------------------------------------

def test_a_paper_session_from_the_previous_fill_model_is_refused(tmp_path, monkeypatch):
    from adaptive_scalper.backtest import fingerprint
    from adaptive_scalper.paper.engine import PaperSessionConfigMismatchError, run_paper_cycle
    from edge_fixtures import v1_replay_config
    from sim_helpers import RES, START, SYMBOL, random_walk_bars, spec

    conn = connect(tmp_path / "paper.sqlite3")
    migrate(conn)
    bars = random_walk_bars(200, seed=12)
    monkeypatch.setattr(fingerprint, "FILL_MODEL_VERSION", "fill_model/v2")   # a session created by 0.2.7/0.2.8
    run_paper_cycle(conn, bars[:150], SYMBOL, RES, spec(), config=v1_replay_config(), now_utc=START)
    monkeypatch.undo()
    with pytest.raises(PaperSessionConfigMismatchError):
        run_paper_cycle(conn, bars, SYMBOL, RES, spec(), config=v1_replay_config(), now_utc=START)


# --- 22: kill switch -------------------------------------------------------------------------

def test_kill_switch_blocks_new_exposure_even_with_verified_evidence(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engage_kill_switch(conn, "test: engaged", "test-operator")
    engine.startup()
    step(engine, clock, seconds=STEP * 6, tick=4)
    assert gateway.calls["order_send"] == 0
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_uninitialized_kill_switch_blocks_even_with_verified_evidence(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    engine.startup()                                 # the operator never bootstrapped the kill switch
    step(engine, clock, seconds=STEP * 6, tick=4)
    assert gateway.calls["order_send"] == 0
