"""Independent per-strategy research, trade analysis and the selector study
(research/independent.py, research/trade_analysis.py, research/selector_study.py).

Deterministic: synthetic bars, temp SQLite. No broker is reachable from any
code under test here (the research modules take no gateway at all).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import LOST_TO_HIGHER_EDGE, CandidateRecord
from adaptive_scalper.persistence.database import connect, migrate
from adaptive_scalper.research import independent as ind
from adaptive_scalper.research import trade_analysis as ta
from adaptive_scalper.research.ledger import family_sharpe_variance, record_trial
from adaptive_scalper.research.selector_study import selector_study
from adaptive_scalper.strategies import build_active_registry, select_active_strategies
from sim_helpers import RES, SYMBOL, config, random_walk_bars, spec

ALL_KEYS = tuple(s.key for s in build_active_registry().all_active())


@pytest.fixture(scope="module")
def study_bars():
    return random_walk_bars(1500, seed=7)


@pytest.fixture(scope="module")
def study(study_bars):
    return ind.run_independent_study(study_bars, SYMBOL, RES, spec(), config=config(), n_folds=3, now_utc=1)


# ---------------------------------------------------------------------------
# strategy subset
# ---------------------------------------------------------------------------

def test_subset_narrows_to_exactly_the_named_active_strategies():
    assert [s.key for s in select_active_strategies(("range_breakout",))] == ["range_breakout"]
    assert len(select_active_strategies(None)) == 6


@pytest.mark.parametrize("keys", [("failed_breakout_fade",), ("support_resistance_reaction",), ("no_such",),
                                  ("range_breakout", "range_breakout"), ()])
def test_subset_refuses_retired_unknown_duplicate_or_empty(keys):
    with pytest.raises(ValueError):
        select_active_strategies(keys)


def test_a_single_strategy_run_only_ever_trades_that_strategy_and_has_its_own_fingerprint(study_bars):
    full = run_backtest(study_bars, SYMBOL, RES, spec(), config=config(), now_utc=1)
    for key in ALL_KEYS:
        solo = run_backtest(study_bars, SYMBOL, RES, spec(), config=config(), now_utc=1, strategy_keys=(key,))
        assert {t.strategy_key for t in solo.trades} <= {key}
        assert solo.config_fingerprint != full.config_fingerprint


# ---------------------------------------------------------------------------
# independent sessions
# ---------------------------------------------------------------------------

def test_every_session_saw_identical_input_and_is_not_pooled(study):
    assert study.inputs_identical
    assert set(study.sessions) == set(ALL_KEYS) | {ind.SELECTOR_SESSION}
    fingerprints = {s.config_fingerprint for s in study.sessions.values()}
    assert len(fingerprints) == len(study.sessions)  # one configuration per session
    for key in ALL_KEYS:
        session = study.sessions[key]
        assert session.error is None
        assert {t.strategy_key for t in session.trades} <= {key}
        for fold in session.folds:
            # each fold's equity starts at the configured initial equity: net P/L is the session's own
            net = sum(t.realized_pnl for t in fold.trades if t.realized_pnl is not None)
            assert fold.metrics.final_equity == pytest.approx(config().initial_equity + net)


def test_sessions_use_identical_fold_boundaries(study):
    ranges = {s.fold_index_ranges for s in study.sessions.values()}
    assert len(ranges) == 1 and len(next(iter(ranges))) == 3


def test_the_study_is_deterministic(study_bars, study):
    again = ind.run_independent_study(study_bars, SYMBOL, RES, spec(), config=config(), n_folds=3, now_utc=1)
    for key, session in study.sessions.items():
        assert [dataclasses.astuple(t) for t in session.trades] == [dataclasses.astuple(t) for t in again.sessions[key].trades]
    assert study.candidates == again.candidates


def test_a_failing_session_is_kept_as_failed_not_dropped(monkeypatch, study_bars):
    real = ind.run_backtest

    def flaky(*args, **kwargs):
        if kwargs.get("strategy_keys") == ("range_breakout",):
            raise RuntimeError("synthetic failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(ind, "run_backtest", flaky)
    s = ind.run_independent_study(study_bars[:400], SYMBOL, RES, spec(), config=config(), now_utc=1)
    assert s.sessions["range_breakout"].error == "RuntimeError: synthetic failure"
    assert s.sessions["momentum_continuation"].error is None


# ---------------------------------------------------------------------------
# no future-data leakage
# ---------------------------------------------------------------------------

def test_decisions_before_a_cut_do_not_depend_on_later_bars(study_bars):
    """Truncation invariance: every candidate the selector saw and every
    trade closed before the cut is identical whether or not later bars exist."""
    cut = 900
    short_log: list[CandidateRecord] = []
    long_log: list[CandidateRecord] = []
    short = run_backtest(study_bars[:cut], SYMBOL, RES, spec(), config=config(), now_utc=1, candidate_log=short_log)
    long = run_backtest(study_bars, SYMBOL, RES, spec(), config=config(), now_utc=1, candidate_log=long_log)
    horizon = study_bars[cut - 2].time
    assert short_log, "the synthetic walk must produce candidates for this test to mean anything"
    assert [c for c in short_log if c.bar_time_utc < horizon] == [c for c in long_log if c.bar_time_utc < horizon]

    def early(result):
        return [dataclasses.astuple(t) for t in result.trades if t.exit_time_utc < horizon]

    assert early(short) == early(long)


def test_candidate_records_carry_only_bar_close_knowledge(study):
    fields = {f.name for f in dataclasses.fields(CandidateRecord)}
    assert not fields & {"realized_pnl", "realized_r", "exit_time_utc", "exit_price", "outcome"}
    selected = [c for c in study.candidates if c.selected]
    assert selected and all(not c.rejected for c in selected)
    assert all(c.rejection_reason == LOST_TO_HIGHER_EDGE for c in study.candidates if not c.selected and not c.rejected)


# ---------------------------------------------------------------------------
# reserved OOS and production-database guards
# ---------------------------------------------------------------------------

def test_research_refuses_the_reserved_oos_interval():
    oos_start, _ = ind.RESERVED_OOS_INTERVALS[0]
    bars = random_walk_bars(100, seed=1, start=oos_start - 50 * 300)
    with pytest.raises(ind.ReservedOosOverlapError):
        ind.run_independent_study(bars, SYMBOL, RES, spec(), config=config(), now_utc=1)
    ind.assert_outside_reserved_oos(oos_start - 10_000, oos_start - 1)  # just before: allowed


def test_persisting_into_the_production_database_is_refused(tmp_path, study_bars):
    prod = tmp_path / "prod.sqlite3"
    conn = connect(prod)
    migrate(conn)
    small = ind.run_independent_study(study_bars[:300], SYMBOL, RES, spec(), config=config(), now_utc=1)
    with pytest.raises(ind.ProductionDatabaseError):
        ind.persist_study(conn, small, study_bars[:300], run_tag="t", production_db_path=str(prod))
    assert conn.execute("SELECT COUNT(*) FROM research_trials").fetchone()[0] == 0
    conn.close()


def test_persisting_records_every_session_including_failures(tmp_path, monkeypatch, study_bars):
    conn = connect(tmp_path / "research.sqlite3")
    migrate(conn)
    real = ind.run_backtest

    def flaky(*args, **kwargs):
        if kwargs.get("strategy_keys") == ("volatility_expansion",):
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(ind, "run_backtest", flaky)
    bars = study_bars[:500]
    s = ind.run_independent_study(bars, SYMBOL, RES, spec(), config=config(), n_folds=2, now_utc=1)
    ind.persist_study(conn, s, bars, run_tag="t1", production_db_path=str(tmp_path / "prod.sqlite3"), now_utc=5)
    rows = {r[0]: r[1] for r in conn.execute("SELECT trial_id, status FROM research_trials")}
    assert len(rows) == 7
    assert rows[f"indep:t1:{SYMBOL}:{RES}:volatility_expansion"] == "FAILED"
    assert sum(1 for v in rows.values() if v == "COMPLETED") == 6
    runs = conn.execute("SELECT COUNT(*) FROM backtest_runs WHERE run_id LIKE 'indep:t1:%'").fetchone()[0]
    assert runs == 6 * 2  # five strategies + selector, two folds each
    conn.close()


def test_dsr_variance_can_exclude_tiny_samples_without_changing_the_trial_count(tmp_path):
    conn = connect(tmp_path / "r.sqlite3")
    migrate(conn)
    for i, (sharpe, n) in enumerate(((0.05, 200), (-0.02, 150), (-3.0, 3))):
        record_trial(conn, trial_id=f"x{i}", family="fam", kind="K", strategy_versions={}, params={},
                     status="COMPLETED", sharpe=sharpe, n_observations=n, now_utc=1)
    assert family_sharpe_variance(conn, "fam") > family_sharpe_variance(conn, "fam", min_observations=30)
    out = ind.deflated_sharpe_for_family(conn, "fam", 0.05, 200)
    assert out["family_trials"] == 3 and out["dsr"] is not None
    conn.close()


# ---------------------------------------------------------------------------
# gross / net / cost accounting
# ---------------------------------------------------------------------------

def test_simulated_costs_are_itemised_and_gross_equals_net_plus_cost(study):
    for session in study.sessions.values():
        for fold in session.folds:
            for t in fold.trades:
                parts = (t.entry_spread_cost + t.entry_slippage_cost + t.exit_spread_cost + t.exit_slippage_cost
                         + t.commission_cost + t.swap_cost + t.fee_cost)
                assert t.total_cost == pytest.approx(parts)
                assert t.gross_pnl == pytest.approx(t.realized_pnl + t.total_cost)
        summary = ta.summarize(session.trades)
        assert summary["gross_pnl"] == pytest.approx(summary["net_pnl"] + summary["total_cost"])


def _view(net, cost, risk=10.0, **kw):
    base = dict(symbol="XAUUSD", strategy_key="s", direction="BUY", entry_time_utc=0, exit_time_utc=600,
                exit_reason="X", entry_regime="RANGE", initial_risk=risk, net=net, total_cost=cost)
    base.update(kw)
    return ta.TradeView(**base)


@pytest.mark.parametrize("net,cost,expected", [
    (5.0, 1.0, ta.WIN), (0.004, 1.0, ta.BREAKEVEN), (-0.004, 1.0, ta.BREAKEVEN),
    (-1.0, 2.0, ta.COST_ONLY_LOSS),        # gross +1: only costs made it a loser
    (-3.0, 2.0, ta.COST_DOMINATED_LOSS),   # gross -1, cost 2 > 1
    (-10.0, 2.0, ta.ADVERSE_MARKET_LOSS),  # gross -8, cost 2 < 8
])
def test_loss_classes_separate_market_outcome_from_cost(net, cost, expected):
    assert ta.outcome_class(_view(net, cost)) == expected


def test_summary_handles_no_losses_no_risk_and_empty_honestly():
    empty = ta.summarize([])
    assert empty["trades"] == 0 and empty["win_rate"] is None and empty["avg_net_r"] is None
    only_wins = ta.summarize([_view(5.0, 1.0), _view(3.0, 1.0)])
    assert only_wins["profit_factor"] is None and only_wins["win_rate"] == 1.0
    no_risk = ta.summarize([_view(5.0, 1.0, risk=0.0)])
    assert no_risk["avg_net_r"] is None and no_risk["r_sample"] == 0 and no_risk["net_pnl"] == 5.0
    mixed = ta.summarize([_view(6.0, 1.0), _view(-2.0, 1.0)])
    assert mixed["profit_factor"] == pytest.approx(3.0)
    assert mixed["avg_cost_r"] == pytest.approx(0.1) and mixed["avg_gross_r"] == pytest.approx(0.3)


def test_session_duration_and_confidence_buckets():
    assert ta.session_bucket(_view(1, 0, entry_time_utc=13 * 3600)) == "LONDON/NY OVERLAP 12-16 UTC"
    assert ta.session_bucket(_view(1, 0, entry_time_utc=23 * 3600 + 59)) == "LATE 21-24 UTC"
    assert ta.duration_bucket(_view(1, 0, exit_time_utc=299)) == "< 5 min"
    assert ta.duration_bucket(_view(1, 0, exit_time_utc=5 * 3600)) == ">= 4 h"
    assert ta.confidence_bucket(_view(1, 0, raw_confidence=1.0)) == "1.00 (capped)"
    assert ta.confidence_bucket(_view(1, 0)) == "UNKNOWN"


def test_calibration_compares_stated_probability_with_realized_gross_hits():
    trades = [_view(-1.0, 2.0, raw_confidence=1.0), _view(-5.0, 1.0, raw_confidence=1.0),
              _view(4.0, 1.0, raw_confidence=1.0)]
    row = ta.calibration(trades)[0]
    assert row["bucket"] == "1.00 (capped)" and row["stated_p"] == 1.0
    assert row["realized_gross_hit_rate"] == pytest.approx(2 / 3)
    assert row["realized_net_win_rate"] == pytest.approx(1 / 3)


def test_trade_row_view_reads_stored_research_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (canonical_symbol, strategy_key, direction, entry_time_utc, exit_time_utc, "
                 "exit_reason, entry_regime, initial_monetary_risk, realized_pnl, total_cost, entry_spread_cost, "
                 "exit_spread_cost, evidence_json)")
    conn.execute("INSERT INTO t VALUES ('BTCUSD','s','SELL',0,60,'STOP_LOSS_HIT','RANGE',10,-12,2,0.5,0.5,?)",
                 (json.dumps({"strategy": {"raw_confidence": 0.9}}),))
    conn.execute("INSERT INTO t VALUES ('BTCUSD','s','SELL',0,NULL,NULL,'RANGE',10,NULL,0,0,0,NULL)")
    views = [ta.from_trade_row(r) for r in conn.execute("SELECT * FROM t").fetchall()]
    assert views[1] is None  # an open trade is never analysed as closed
    v = views[0]
    assert v.gross == -10 and v.spread_cost == 1.0 and v.raw_confidence == 0.9
    assert ta.outcome_class(v) == ta.ADVERSE_MARKET_LOSS


# ---------------------------------------------------------------------------
# selector study
# ---------------------------------------------------------------------------

def test_selector_study_counts_every_candidate_and_matches_outcomes_causally(study):
    out = selector_study(study)
    assert out["candidates"] == len(study.candidates)
    per = out["per_strategy"]
    assert set(per) == set(ALL_KEYS)
    assert sum(v["signals"] for v in per.values()) == len(study.candidates)
    assert sum(v["selected"] for v in per.values()) == out["selected"]
    for v in per.values():
        assert v["selected"] + v["lost_to_higher_edge"] + v["filter_rejected"] == v["signals"]
        assert v["selected_matched"] <= v["selected"]
    shares = [v["selection_share"] for v in per.values() if v["selection_share"] is not None]
    assert not shares or sum(shares) == pytest.approx(1.0)


def test_selector_study_expected_versus_realized_quantiles_are_ordered(study):
    q = selector_study(study, n_quantiles=3)["expected_vs_realized_quantiles"]
    means = [row["expected_net_r_mean"] for row in q]
    assert means == sorted(means)


def test_pbo_is_reported_or_explicitly_not_computable(study):
    out = ind.pbo_across_strategies(study, n_blocks=6, n_groups=4)
    assert out["computable"] is (out["pbo"] is not None)
    assert out["strategies"] == [k for k in study.sessions if k != ind.SELECTOR_SESSION]


# ---------------------------------------------------------------------------
# large dataset
# ---------------------------------------------------------------------------

def test_a_large_history_runs_in_folds_without_error():
    bars = random_walk_bars(8000, seed=11)
    s = ind.run_independent_study(bars, SYMBOL, RES, spec(), config=config(), n_folds=4,
                                  strategies=("statistical_reversion", "microstructure_acceleration"), now_utc=1)
    assert s.inputs_identical and s.n_folds == 4
    assert all(r.error is None for r in s.sessions.values())
    assert sum(len(f.trades) for f in s.sessions[ind.SELECTOR_SESSION].folds) > 0
