"""Tests for the PAPER cycle driver (directive section 132)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.paper.engine import run_paper_cycle
from adaptive_scalper.paper.state import get_paper_trades, get_session
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.simulation.fill_model import FillAssumptions

CANONICAL_SYMBOL = "XAUUSD"
RESOLUTION = "M5"


def _symbol_spec(**overrides) -> SymbolSpec:
    defaults = dict(
        name="XAUUSDm", description="Gold vs US Dollar", currency_base="XAU", currency_profit="USD",
        currency_margin="XAU", digits=2, point=0.01, trade_contract_size=100.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_tick_size=0.01,
        trade_tick_value=1.0, spread=10, visible=True, trade_mode=SymbolTradeMode.FULL,
    )
    defaults.update(overrides)
    return SymbolSpec(**defaults)


def _trending_bars(n: int, *, start_price: float = 2000.0, step: float = 0.5, start_time: int = 1_700_000_000) -> list[Bar]:
    bars = []
    price = start_price
    for i in range(n):
        open_price = price
        close_price = price + step
        bars.append(Bar(
            time=start_time + i * 300, open=open_price, high=max(open_price, close_price) + 0.05,
            low=min(open_price, close_price) - 0.05, close=close_price, tick_volume=100, spread=10, real_volume=0,
        ))
        price = close_price
    return bars


def _config(**overrides) -> BacktestConfig:
    defaults = dict(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0))
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def _seed(db, bars, config=None, **kwargs):
    """PAPER starts now: a new session decides only its most recent bar.
    Seeding at the first decidable bar lets later cycles be compared with
    one continuous run over the same range."""
    config = config or _config()
    return run_paper_cycle(db, bars[:config.feature_lookback + 2], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(),
                           config=config, now_utc=2_000_000_000, **kwargs)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_run_paper_cycle_creates_a_session_and_processes_the_first_window(db):
    bars = _trending_bars(200)
    result = run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert result.ran is True
    assert result.last_processed_bar_time_utc == bars[-1].time

    session = get_session(db, result.session_key)
    assert session is not None
    assert session.last_processed_bar_time_utc == bars[-1].time
    assert session.equity == result.equity


def test_run_paper_cycle_with_too_few_bars_is_a_safe_noop(db):
    bars = _trending_bars(5)  # far fewer than feature_lookback + 3
    result = run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert result.ran is False
    assert result.new_trades == ()
    # No session row mutation beyond creation -- equity stays at the default.
    assert result.equity == _config().initial_equity


def test_run_paper_cycle_labels_evidence_as_paper_live_data_via_persisted_trades(db):
    bars = _trending_bars(200)
    _seed(db, bars)
    run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    rows = get_paper_trades(db, f"PAPER:{CANONICAL_SYMBOL}:{RESOLUTION}")
    assert len(rows) >= 1
    assert all(r["origin"] == "PAPER_LIVE_DATA" for r in rows)


def test_run_paper_cycle_never_double_processes_bars_on_a_repeated_identical_call(db):
    bars = _trending_bars(200)
    first = run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    second = run_paper_cycle(db, bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_001_000)
    # No genuinely new bars were supplied the second time -- safe no-op.
    assert second.ran is False
    assert second.equity == first.equity
    rows = get_paper_trades(db, first.session_key)
    assert len(rows) == len(first.new_trades)  # not doubled


def test_run_paper_cycle_incremental_feeding_matches_a_single_shot_backtest(db):
    # The strongest correctness proof: cycling through the SAME bar range
    # in growing chunks must produce IDENTICAL final state (equity, every
    # recorded trade, the still-open position) to one non-incremental
    # run_backtest() call over the full range with force_close_at_range_end
    # =False -- incremental PAPER cycling must never diverge from what the
    # SAME decision engine would have done given the whole history at once.
    bars = _trending_bars(220)
    reference = run_backtest(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        force_close_at_range_end=False,
    )

    last_result = _seed(db, bars)
    for cutoff in (50, 100, 150, 200, 220):
        last_result = run_paper_cycle(
            db, bars[:cutoff], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000,
        )

    assert last_result.equity == pytest.approx(reference.metrics.final_equity)

    recorded = get_paper_trades(db, last_result.session_key)
    assert len(recorded) == len(reference.trades)
    recorded_entries = sorted((r["entry_time_utc"], r["direction"], round(r["realized_pnl"], 6)) for r in recorded)
    reference_entries = sorted((t.entry_time_utc, t.direction, round(t.realized_pnl, 6)) for t in reference.trades)
    assert recorded_entries == reference_entries

    if reference.open_position is not None:
        assert last_result.open_position is not None
        assert last_result.open_position.entry_time_utc == reference.open_position.entry_time_utc
        assert last_result.open_position.entry_price == reference.open_position.entry_price
    else:
        assert last_result.open_position is None


def test_run_paper_cycle_tracks_running_equity_not_the_static_config_default(db):
    # Equity persisted after a cycle with at least one profitable closed
    # trade must reflect that P/L -- never silently reset to
    # config.initial_equity on the NEXT cycle's call.
    bars = _trending_bars(220)
    _seed(db, bars)
    first = run_paper_cycle(db, bars[:150], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_000_000)
    assert len(first.new_trades) >= 1
    assert first.equity != _config().initial_equity

    second = run_paper_cycle(db, bars[:220], CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), config=_config(), now_utc=2_000_001_000)
    # The second cycle's starting point was the FIRST cycle's equity, not
    # the static default -- final equity keeps compounding forward.
    assert second.equity != _config().initial_equity
