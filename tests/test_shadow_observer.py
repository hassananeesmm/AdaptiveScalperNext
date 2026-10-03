"""Prospective shadow observer (migration 0032; adaptive_scalper/shadow/observer.py).

Forward-only evidence: every candidate is recorded (selected, rejected or
never evaluated), outcomes appear only after their horizon has elapsed in
closed bars, rows are append-only, and nothing in the observer can reach an
order path or change a trading decision.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.shadow import observer as obs
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

ROOT = Path(__file__).resolve().parents[1] / "adaptive_scalper"
BAR = 300
D = 1_700_000_100 - 1_700_000_100 % BAR   # decision time = first fill bar open
START_AT = T0 + 60 * STEP + 10


def _bar(i, o, h, lo, c):
    return Bar(time=D + i * BAR, open=o, high=h, low=lo, close=c, tick_volume=1, spread=10, real_volume=0)


def _outcome(bars, *, direction="BUY", horizon=900, cost=0.2, stop=1.0, target=2.0):
    return obs.evaluate_outcome(direction=direction, decision_time_utc=D, stop_distance=stop, target_distance=target,
                                estimated_cost_price=cost, bar_seconds=BAR, horizon_seconds=horizon, bars=bars)


# --- outcome arithmetic ----------------------------------------------------------

def test_target_first_long():
    bars = [_bar(0, 100, 100.5, 99.5, 100.4), _bar(1, 100.4, 102.2, 100.3, 101.8), _bar(2, 101.8, 102.0, 101.0, 101.5)]
    o = _outcome(bars)
    assert o.status == obs.RESOLVED and o.first_touch == "TARGET" and o.first_touch_seconds == BAR
    assert o.gross_r == pytest.approx(1.5) and o.net_r_estimated == pytest.approx(1.3)
    assert o.mfe_r == pytest.approx(2.2) and o.time_to_mfe_seconds == BAR
    assert o.mae_r == pytest.approx(0.5) and o.time_to_mae_seconds == 0 and o.bars_used == 3


def test_stop_first_short_and_same_bar_ambiguity_assumes_the_stop():
    bars = [_bar(0, 100, 101.2, 99.9, 101.0), _bar(1, 101, 101, 97, 97.5), _bar(2, 97.5, 98, 97, 97.8)]
    assert _outcome(bars, direction="SELL").first_touch == "STOP"
    both = [_bar(0, 100, 102.5, 97.5, 100), _bar(1, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100)]  # target 102, stop 99
    assert _outcome(both).first_touch == "BOTH_SAME_BAR_STOP_ASSUMED"


def test_unsupported_horizons_are_never_interpolated():
    bars = [_bar(i, 100, 100, 100, 100) for i in range(6)]
    assert _outcome(bars, horizon=60).status == obs.UNSUPPORTED_RESOLUTION
    assert _outcome(bars, horizon=180).status == obs.UNSUPPORTED_RESOLUTION


def test_gaps_are_insufficient_data():
    assert _outcome([_bar(1, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100), _bar(3, 100, 100, 100, 100)]
                    ).status == obs.INSUFFICIENT_DATA                  # fill bar missing
    assert _outcome([_bar(0, 100, 100, 100, 100), _bar(2, 100, 100, 100, 100), _bar(3, 100, 100, 100, 100)]
                    ).status == obs.INSUFFICIENT_DATA                  # hole inside the window


def test_unknown_cost_leaves_net_r_unknown():
    bars = [_bar(i, 100, 100, 100, 100) for i in range(3)]
    assert _outcome(bars, cost=None).net_r_estimated is None


# --- persistence: causal, append-only ----------------------------------------------

@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "shadow.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _candidate(**kw):
    base = dict(canonical_symbol="XAUUSD", resolution="M5", bar_seconds=BAR, decision_bar_time_utc=D - BAR,
                strategy_key="microstructure_acceleration", strategy_version=1, direction="BUY", raw_score=0.9,
                stop_distance=1.0, target_distance=2.0, expected_duration_seconds=180, raw_regime="RANGE",
                confirmed_regime="RANGE", regime_confidence=0.7, session="LONDON", news_status="ALLOW",
                news_detail="", spread_points=10, spread_percentile=0.4, atr=1.0, realized_volatility=0.01,
                movement_to_cost=3.0, estimated_round_trip_cost_price=0.2, cost_horizon="FULL_ROUND_TRIP",
                cost_provenance="BROKER_DEMO_CONFIRMED", edge_model="NONE", scheduler_lag_seconds=0.1,
                selector_disposition=obs.REJECTED, rejection_reason="no_validated_edge_evidence",
                model_observer_score=None, features={"atr": 1.0, "bad": float("nan")})
    base.update(kw)
    return obs.ShadowCandidate(**base)


def test_outcomes_are_written_only_after_their_horizon_elapsed(db):
    assert obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D) == 1
    assert obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D) == 0       # idempotent
    bars = [_bar(i, 100, 100.5, 99.5, 100.2) for i in range(7)]
    # 10 minutes after the decision: 5m and 10m are due; 15m/30m are not; 1m/3m are unsupported at M5.
    obs.resolve_due(db, "XAUUSD", bars, now_utc=D + 600)
    got = dict(db.execute("SELECT horizon_seconds, status FROM shadow_outcomes").fetchall())
    assert got == {60: "UNSUPPORTED_RESOLUTION", 180: "UNSUPPORTED_RESOLUTION", 300: "RESOLVED", 600: "RESOLVED"}
    obs.resolve_due(db, "XAUUSD", bars, now_utc=D + 900)
    assert db.execute("SELECT status FROM shadow_outcomes WHERE horizon_seconds = 900").fetchone()[0] == "RESOLVED"
    # a forming (unclosed) bar is never used
    assert db.execute("SELECT bars_used FROM shadow_outcomes WHERE horizon_seconds = 900").fetchone()[0] == 3


def test_candidate_rows_carry_no_future_fields_and_drop_non_finite_features(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    row = db.execute("SELECT decision_time_utc, features_json, raw_score_is_probability FROM shadow_candidates").fetchone()
    assert row[0] == D and '"bad": null' in row[1] and row[2] == 0


def test_shadow_tables_are_append_only_and_raw_score_is_never_a_probability(db):
    obs.record_candidates(db, [_candidate()], mode="DEMO", now_utc=D)
    obs.resolve_due(db, "XAUUSD", [_bar(i, 100, 100, 100, 100) for i in range(7)], now_utc=D + 600)
    for sql in ("UPDATE shadow_candidates SET raw_score = 0", "DELETE FROM shadow_candidates",
                "UPDATE shadow_outcomes SET gross_r = 9", "DELETE FROM shadow_outcomes"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO shadow_candidates (candidate_key, observer_version, observed_at_utc, mode, "
                   "canonical_symbol, resolution, bar_seconds, decision_bar_time_utc, decision_time_utc, strategy_key, "
                   "strategy_version, direction, raw_score, raw_score_is_probability, stop_distance, target_distance, "
                   "edge_model, selector_disposition, features_json) VALUES "
                   "('k','v',1,'DEMO','XAUUSD','M5',300,1,301,'s',1,'BUY',0.9,1,1,2,'NONE','REJECTED','{}')")


# --- no order path ---------------------------------------------------------------------

def test_the_shadow_package_cannot_reach_an_order_path():
    for path in (ROOT / "shadow").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert all(m == "adaptive_scalper.gateway.types" or not m.startswith(
            ("adaptive_scalper.gateway", "adaptive_scalper.execution", "adaptive_scalper.core", "MetaTrader5"))
            for m in modules), (path.name, modules)
        names = {n.attr if isinstance(n, ast.Attribute) else n.id
                 for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))}
        assert not names & {"order_send", "order_check", "submit_new_entry", "close_position"}, path.name


# --- runtime integration ---------------------------------------------------------------

def _boot(conn):
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")


def test_flat_demo_runtime_collects_rejected_candidates_and_never_sends(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _boot(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 12, tick=4)
    rows = conn.execute("SELECT selector_disposition, rejection_reason, edge_model FROM shadow_candidates").fetchall()
    assert rows, "the shadow observer should record natural candidates"
    assert {(r[0], r[1], r[2]) for r in rows} == {("REJECTED", "no_validated_edge_evidence", "NONE")}
    assert conn.execute("SELECT COUNT(*) FROM shadow_outcomes WHERE status = 'RESOLVED'").fetchone()[0] > 0
    assert gateway.calls["order_send"] == 0 and gateway.calls["order_check"] == 0


def test_globally_blocked_demo_still_records_candidates_as_not_evaluated(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)   # kill switch never bootstrapped
    engine.startup()
    step(engine, clock, seconds=STEP * 6, tick=4)
    dispositions = {r[0] for r in conn.execute("SELECT selector_disposition FROM shadow_candidates")}
    assert dispositions == {"NOT_EVALUATED"}
    assert gateway.calls["order_send"] == 0


def test_an_observer_failure_never_changes_a_decision(tmp_path, monkeypatch):
    def run(path, broken):
        clock = FakeClock(START_AT)
        engine, conn, _ = build_engine(path, mode="DEMO", clock=clock)
        _boot(conn)
        if broken:
            import adaptive_scalper.runtime.demo as demo

            def boom(*_a, **_k):
                raise RuntimeError("shadow disk full")
            monkeypatch.setattr(demo, "record_candidates", boom)
        engine.startup()
        step(engine, clock, seconds=STEP * 6, tick=4)
        decisions = conn.execute("SELECT stage, decision, canonical_symbol, bar_time_utc FROM entry_decisions "
                                 "ORDER BY id").fetchall()
        failures = conn.execute("SELECT COUNT(*) FROM runtime_events WHERE event = 'SHADOW_OBSERVER_FAILED'"
                                ).fetchone()[0]
        return [tuple(d) for d in decisions], failures

    healthy, ok_failures = run(tmp_path / "a", broken=False)
    degraded, failures = run(tmp_path / "b", broken=True)
    assert healthy and degraded == healthy
    assert ok_failures == 0 and failures >= 1
