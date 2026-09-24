"""Phase 0 simulation hardening: deferred-entry revalidation, historical
risk halts, causal fill references, per-component costs, provenance,
PAPER session immutability, "PAPER starts now", overlap-aware OOS
boundaries and the path-stress rename.

Prices are hand-computed from `sim_helpers` (mid 2000.00, half spread
0.05, 0.12 lots -> $12 per 1.0 price, $24 initial risk, 1R = 2.0).
"""

from __future__ import annotations

import sqlite3

import pytest

from adaptive_scalper.backtest.dataset import build_dataset_snapshot, record_dataset, record_dataset_usage
from adaptive_scalper.backtest.engine import STOP_LOSS_HIT, run_backtest
from adaptive_scalper.backtest.oos import DatasetContaminatedError, run_untouched_oos
from adaptive_scalper.backtest.persistence import record_backtest_run
from adaptive_scalper.backtest.types import (
    FILL_NEXT_BAR_OPEN,
    FILL_RANGE_END_CLOSE,
    FILL_STOP_TRIGGER,
    FILL_TARGET_TRIGGER,
    NOT_CONSULTED,
    REJECT_CORRELATION,
    REJECT_EXPECTED_EDGE,
    REJECT_NEWS,
    REJECT_RISK,
    REJECT_STALE_SIGNAL,
    BacktestConfig,
)
from adaptive_scalper.paper.engine import PaperSessionConfigMismatchError, run_paper_cycle
from adaptive_scalper.paper.state import get_paper_trades, get_session
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.portfolio.correlation import CorrelationResult
from adaptive_scalper.portfolio.exposure import PositionExposure
from adaptive_scalper.risk.governor import RiskLimits
from adaptive_scalper.simulation.fill_model import COST_UNVERIFIED_ASSUMPTION, FILL_MODEL_VERSION, FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin
from sim_helpers import (
    RES,
    START,
    STEP,
    SYMBOL,
    bars,
    config,
    install,
    random_walk_bars,
    spec,
)

FIRE = 30


def t(i: int, start: int = START) -> int:
    return start + i * STEP


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def _run(b, cfg=None, **kwargs):
    return run_backtest(b, SYMBOL, RES, spec(), config=cfg or config(), now_utc=START, **kwargs)


# ---------------------------------------------------------------------------
# 0.7 deferred-entry revalidation
# ---------------------------------------------------------------------------

def test_pending_entry_is_dropped_as_stale_when_the_next_bar_opens_after_a_gap(monkeypatch):
    # Signal at bar 30's close; bar 31 opens three days later (weekend /
    # data gap). The old decision is not a permanent authorization.
    install(monkeypatch, {t(FIRE): "BUY"})
    b = bars(times={31: t(FIRE) + 3 * 86400})
    result = _run(b)
    assert result.trades == ()
    assert [r.reason_code for r in result.entry_rejections] == [REJECT_STALE_SIGNAL]
    assert result.entry_rejections[0].signal_time_utc == t(FIRE)


def test_pending_entry_is_dropped_when_the_fill_bar_is_inside_a_news_window(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    b = bars()
    result = _run(b, config(news_windows=((b[31].time, b[31].time + 1),)))
    assert result.trades == ()
    assert result.entry_rejections[0].reason_code == REJECT_NEWS


def test_pending_entry_is_dropped_when_cost_at_the_fill_bar_destroys_the_edge(monkeypatch):
    # EV = 0.9*5 - 0.1*2 = 4.3 price. At bar 31 the spread is 500 points
    # (5.0 price, x1.1 uncertainty = 5.5) -> negative net edge at fill.
    install(monkeypatch, {t(FIRE): "BUY"})
    result = _run(bars(spreads={31: 500}))
    assert result.trades == ()
    assert result.entry_rejections[0].reason_code == REJECT_EXPECTED_EDGE


def test_a_revalidated_entry_records_both_decision_and_fill_evidence(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    trade = _run(bars()).trades[0]
    evidence = trade.entry_evidence
    assert evidence["decision"]["signal_time_utc"] == t(FIRE)
    assert evidence["decision"]["expected_net_edge_price"] > 0
    assert evidence["fill_revalidation"]["fill_time_utc"] == t(FIRE + 1)
    assert evidence["ml_observer"] == evidence["rag"] == evidence["okf"] == NOT_CONSULTED
    assert evidence["strategy"]["fingerprint"]


# ---------------------------------------------------------------------------
# 0.8 historical risk halts
# ---------------------------------------------------------------------------

_TIGHT_DAILY = RiskLimits(0.25, 0.75, 0.20, 5.00, 2, 1)
_TIGHT_DRAWDOWN = RiskLimits(0.25, 0.75, 2.00, 0.20, 2, 1)
_STOP_OUT = {32: (2000.0, 2000.01, 1990.0, 1995.0)}  # -$24 = 0.24% of equity


def test_daily_loss_ceiling_stops_new_entries_for_the_rest_of_the_day(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY", t(34): "BUY"})
    result = _run(bars(overrides=_STOP_OUT), config(risk_limits=_TIGHT_DAILY))
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == STOP_LOSS_HIT
    assert result.risk_halted_scans > 0
    assert result.final_risk_state.day_realized_pnl == pytest.approx(-24.0)


def test_daily_loss_ceiling_resets_on_the_next_utc_day(monkeypatch):
    start = (START // 86400 + 1) * 86400 - 36 * STEP  # bar 36 opens at 00:00 UTC
    install(monkeypatch, {t(FIRE, start): "BUY", t(34, start): "BUY", t(37, start): "BUY"})
    result = run_backtest(bars(start=start, overrides=_STOP_OUT), SYMBOL, RES, spec(),
                          config=config(risk_limits=_TIGHT_DAILY), now_utc=start)
    assert [tr.entry_time_utc for tr in result.trades] == [t(31, start), t(38, start)]


def test_drawdown_ceiling_survives_paper_cycles(monkeypatch, db):
    install(monkeypatch, {t(FIRE): "BUY", t(36): "BUY"})
    b = bars(overrides=_STOP_OUT)
    cfg = config(risk_limits=_TIGHT_DRAWDOWN)
    run_paper_cycle(db, b[:cfg.feature_lookback + 2], SYMBOL, RES, spec(), config=cfg, now_utc=START)
    first = run_paper_cycle(db, b[:34], SYMBOL, RES, spec(), config=cfg, now_utc=START)
    assert len(first.new_trades) == 1
    session = get_session(db, first.session_key)
    assert session.risk_state.peak_equity == pytest.approx(10_000.0)

    second = run_paper_cycle(db, b, SYMBOL, RES, spec(), config=cfg, now_utc=START)
    assert second.new_trades == ()
    assert second.open_position is None
    assert second.risk_halted_scans > 0


def test_research_config_cannot_raise_per_trade_risk_above_the_hard_ceiling():
    with pytest.raises(ValueError, match="hard ceiling"):
        BacktestConfig(risk_per_trade_pct=0.5)


def test_external_exposure_at_max_open_positions_blocks_the_entry(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    external = (PositionExposure("GBPJPY", "BUY", 20.0), PositionExposure("BTCUSD", "SELL", 20.0))
    result = _run(bars(), external_open_positions=external)
    assert result.trades == ()
    assert result.entry_rejections[0].reason_code == REJECT_RISK
    assert "max_open_positions" in result.entry_rejections[0].detail


def test_unknown_correlation_with_an_open_symbol_blocks_the_entry(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    result = _run(bars(), external_open_positions=(PositionExposure("GBPJPY", "BUY", 20.0),))
    assert result.entry_rejections[0].reason_code == REJECT_CORRELATION


def test_measured_low_correlation_allows_the_entry(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    low = CorrelationResult(0.1, 200)
    matrix = {("XAUUSD", "GBPJPY"): low, ("GBPJPY", "XAUUSD"): low}
    result = _run(bars(), external_open_positions=(PositionExposure("GBPJPY", "BUY", 20.0),),
                  correlation_matrix=matrix)
    assert len(result.trades) == 1
    assert result.entry_rejections == ()


# ---------------------------------------------------------------------------
# 0.3 / 0.4 causal fill references
# ---------------------------------------------------------------------------

def test_both_stop_and_target_inside_one_bar_resolves_to_the_stop(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    trade = _run(bars(overrides={32: (2000.0, 2010.0, 1990.0, 2000.0)})).trades[0]
    assert trade.exit_reason == STOP_LOSS_HIT
    assert trade.exit_fill_reference == FILL_STOP_TRIGGER


@pytest.mark.parametrize("seed", [31, 32, 33])
def test_no_simulated_fill_ever_precedes_its_decision(seed):
    b = random_walk_bars(600, seed=seed)
    cfg = BacktestConfig(fill_assumptions=FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=7.0))
    result = run_backtest(b, SYMBOL, RES, spec(), config=cfg, now_utc=START)
    assert len(result.trades) >= 3
    references = set()
    for trade in result.trades:
        references.add(trade.exit_fill_reference)
        assert trade.entry_fill_reference == FILL_NEXT_BAR_OPEN
        assert trade.entry_time_utc > trade.signal_time_utc
        if trade.exit_fill_reference == FILL_NEXT_BAR_OPEN:
            assert trade.exit_time_utc > trade.exit_decision_time_utc
        else:
            assert trade.exit_fill_reference in (FILL_STOP_TRIGGER, FILL_TARGET_TRIGGER, FILL_RANGE_END_CLOSE)
            assert trade.exit_time_utc == trade.exit_decision_time_utc
        assert trade.exit_time_utc >= trade.entry_time_utc
    assert FILL_NEXT_BAR_OPEN in references


# ---------------------------------------------------------------------------
# 0.5 persistent peak R with timestamp
# ---------------------------------------------------------------------------

def test_peak_r_timestamp_and_last_current_r_are_persisted(monkeypatch):
    install(monkeypatch, {t(FIRE): "BUY"})
    b = bars(overrides={32: (2000.0, 2001.71, 1999.99, 2001.70), 33: (2001.70, 2001.71, 2001.19, 2001.20)})
    result = _run(b[:34], force_close_at_range_end=False)
    state = result.open_position
    assert state.peak_r == pytest.approx(0.80)
    assert state.peak_r_time_utc == t(32)
    assert state.last_current_r == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# 0.6 cost breakdown / 0.13 provenance
# ---------------------------------------------------------------------------

def test_every_cost_component_is_reported_and_sums_to_total_cost(monkeypatch):
    # Thesis invalidated at 33 -> exit at 34 open (2001). Slippage 0.02,
    # commission 5.00/lot, 0.12 lots:
    #   entry spread 0.05*12=0.60, entry slippage 0.02*12=0.24
    #   exit  spread 0.60,         exit  slippage 0.24, commission 0.60
    install(monkeypatch, {t(FIRE): "BUY"}, invalidate_from=t(33))
    b = bars(overrides={i: (2001.0, 2001.01, 2000.99, 2001.0) for i in range(34, 40)})
    cfg = config(fill_assumptions=FillAssumptions(slippage_price=0.02, commission_monetary_per_lot=5.0))
    trade = _run(b, cfg).trades[0]
    assert trade.entry_spread_cost == pytest.approx(0.60)
    assert trade.entry_slippage_cost == pytest.approx(0.24)
    assert trade.exit_spread_cost == pytest.approx(0.60)
    assert trade.exit_slippage_cost == pytest.approx(0.24)
    assert trade.commission_cost == pytest.approx(0.60)
    assert trade.swap_cost == 0.0 and trade.fee_cost == 0.0
    assert trade.total_cost == pytest.approx(2.28)
    assert trade.gross_pnl == pytest.approx(12.0)
    assert trade.gross_pnl == pytest.approx(trade.realized_pnl + trade.total_cost)


def test_unverified_default_costs_are_labeled_as_such():
    b = random_walk_bars(300, seed=5)
    result = run_backtest(b, SYMBOL, RES, spec(), config=BacktestConfig(), now_utc=START)
    assert result.cost_provenance == COST_UNVERIFIED_ASSUMPTION
    assert result.fill_model_version == FILL_MODEL_VERSION
    assert all(tr.cost_provenance == COST_UNVERIFIED_ASSUMPTION for tr in result.trades)


def test_trades_carry_origin_fingerprint_and_fill_model(monkeypatch, db):
    install(monkeypatch, {t(FIRE): "BUY"})
    b = bars()
    backtest = _run(b).trades[0]
    assert backtest.origin == EvidenceOrigin.BACKTEST
    assert backtest.config_fingerprint and backtest.fill_model_version == FILL_MODEL_VERSION
    assert backtest.strategy_version == 1

    cfg = config()
    run_paper_cycle(db, b[:cfg.feature_lookback + 2], SYMBOL, RES, spec(), config=cfg, now_utc=START)
    paper = run_paper_cycle(db, b, SYMBOL, RES, spec(), config=cfg, now_utc=START)
    assert paper.open_position is not None
    assert paper.config_fingerprint == backtest.config_fingerprint


def test_backtest_persistence_stores_the_breakdown_and_references(monkeypatch, db):
    install(monkeypatch, {t(FIRE): "BUY"})
    b = bars()
    result = _run(b)
    record_backtest_run(db, result, b, run_id="bt-1", run_type="BACKTEST", used_for="VALIDATION",
                        strategies=("scripted",), feature_schema_version=1, now_utc=START)
    row = db.execute("SELECT * FROM backtest_trades WHERE run_id = 'bt-1'").fetchone()
    assert row["entry_fill_reference"] == FILL_NEXT_BAR_OPEN
    assert row["exit_fill_reference"] == FILL_RANGE_END_CLOSE
    assert row["signal_time_utc"] == t(FIRE)
    assert row["entry_spread_cost"] == pytest.approx(0.60)
    assert row["config_fingerprint"] == result.config_fingerprint
    run = db.execute("SELECT * FROM backtest_runs WHERE run_id = 'bt-1'").fetchone()
    assert run["fill_model_version"] == FILL_MODEL_VERSION


# ---------------------------------------------------------------------------
# 0.12 PAPER session immutability / PAPER starts now
# ---------------------------------------------------------------------------

def test_a_new_paper_session_decides_only_the_most_recent_bar(db):
    # Deep supplied history is feature context, never replayed as
    # PAPER_LIVE_DATA trades (BUG_BACKLOG #10).
    b = random_walk_bars(400, seed=11)
    result = run_paper_cycle(db, b, SYMBOL, RES, spec(), config=BacktestConfig(), now_utc=START)
    assert result.ran is True
    assert result.new_trades == ()
    assert result.last_processed_bar_time_utc == b[-1].time
    assert get_paper_trades(db, result.session_key) == []


def test_resuming_a_paper_session_under_a_different_config_fails_closed(db):
    b = random_walk_bars(200, seed=12)
    run_paper_cycle(db, b[:150], SYMBOL, RES, spec(), config=BacktestConfig(), now_utc=START)
    changed = BacktestConfig(fill_assumptions=FillAssumptions(slippage_price=0.05, commission_monetary_per_lot=0.0))
    with pytest.raises(PaperSessionConfigMismatchError, match="new PAPER session"):
        run_paper_cycle(db, b, SYMBOL, RES, spec(), config=changed, now_utc=START)
    fresh = run_paper_cycle(db, b, SYMBOL, RES, spec(), config=changed, session_key="PAPER:XAUUSD:M5:v2", now_utc=START)
    assert fresh.ran is True


def test_a_legacy_session_with_history_but_no_fingerprint_is_refused(db):
    b = random_walk_bars(200, seed=13)
    run_paper_cycle(db, b[:150], SYMBOL, RES, spec(), config=BacktestConfig(), now_utc=START)
    db.execute("UPDATE paper_session_state SET config_fingerprint = NULL")
    db.commit()
    with pytest.raises(PaperSessionConfigMismatchError):
        run_paper_cycle(db, b, SYMBOL, RES, spec(), config=BacktestConfig(), now_utc=START)


def test_a_legacy_session_without_history_is_bound_to_the_current_config(db):
    db.execute(
        "INSERT INTO paper_session_state (session_key, canonical_symbol, resolution, equity, "
        "regime_candidate_count, created_at_utc, updated_at_utc) VALUES ('PAPER:XAUUSD:M5', 'XAUUSD', 'M5', "
        "10000.0, 0, 1, 1)"
    )
    db.commit()
    result = run_paper_cycle(db, random_walk_bars(100, seed=14), SYMBOL, RES, spec(), config=BacktestConfig(),
                             now_utc=START)
    assert get_session(db, result.session_key).config_fingerprint == result.config_fingerprint


def test_a_session_key_cannot_be_reused_for_another_symbol(db):
    b = random_walk_bars(100, seed=15)
    run_paper_cycle(db, b, SYMBOL, RES, spec(), config=BacktestConfig(), session_key="shared", now_utc=START)
    with pytest.raises(PaperSessionConfigMismatchError):
        run_paper_cycle(db, b, "GBPJPY", RES, spec(), config=BacktestConfig(), session_key="shared", now_utc=START)


# ---------------------------------------------------------------------------
# 0.9 OOS boundary conditions
# ---------------------------------------------------------------------------

_B = random_walk_bars(400, seed=41)


def _used(db, b, used_for, *, symbol=SYMBOL, resolution=RES, origin=EvidenceOrigin.BACKTEST):
    snapshot = build_dataset_snapshot(b, canonical_symbol=symbol, resolution=resolution, strategies=("x",),
                                      feature_schema_version=1, origin=origin, now_utc=START)
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "prior", used_for, now_utc=START)


def _oos(db, b, run_id="oos", **kwargs):
    return run_untouched_oos(b, SYMBOL, RES, spec(), conn=db, run_id=run_id, config=BacktestConfig(),
                             now_utc=START, **kwargs)


@pytest.mark.parametrize("oos_slice", [
    slice(100, 200),   # exact
    slice(150, 250),   # partial
    slice(120, 160),   # nested
    slice(199, 260),   # one shared bar
    slice(40, 101),    # one shared bar, from the other side
])
def test_oos_blocks_every_overlap_shape(db, oos_slice):
    _used(db, _B[100:200], "TRAINING")
    with pytest.raises(DatasetContaminatedError):
        _oos(db, _B[oos_slice])


@pytest.mark.parametrize("oos_slice", [slice(200, 260), slice(40, 100)])
def test_oos_allows_adjacent_non_overlapping_ranges(db, oos_slice):
    _used(db, _B[100:200], "TRAINING")
    _oos(db, _B[oos_slice])


def test_oos_blocks_overlap_from_a_different_provenance(db):
    _used(db, _B[100:200], "VALIDATION", origin=EvidenceOrigin.HISTORICAL_MT5_REPLAY)
    with pytest.raises(DatasetContaminatedError, match="HISTORICAL_MT5_REPLAY"):
        _oos(db, _B[150:250])


def test_analysis_only_reuse_is_recorded_separately_and_spends_the_range(db):
    _oos(db, _B[200:260], run_id="first", allow_oos_reuse=True)
    purposes = [r["used_for"] for r in db.execute("SELECT used_for FROM dataset_usage")]
    assert purposes == ["OOS_ANALYSIS_REUSE"]
    # A range that has been LOOKED AT is no longer an untouched holdout.
    with pytest.raises(DatasetContaminatedError, match="already run as OOS"):
        _oos(db, _B[200:260], run_id="second")


def test_the_usage_ledger_accepts_the_new_research_purposes(db):
    for purpose in ("MODEL_WALK_FORWARD", "PURGED_CV", "PATH_STRESS", "OOS_ANALYSIS_REUSE"):
        _used(db, _B[:50], purpose)
    with pytest.raises(sqlite3.IntegrityError):
        _used(db, _B[:50], "SOMETHING_ELSE")
