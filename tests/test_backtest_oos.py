"""Tests for untouched-OOS validation (directive section 65)."""

from __future__ import annotations

import pytest

from adaptive_scalper.backtest.dataset import build_dataset_snapshot, record_dataset, record_dataset_usage
from adaptive_scalper.backtest.oos import DatasetContaminatedError, run_untouched_oos
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.simulation.fill_model import FillAssumptions
from adaptive_scalper.simulation.types import EvidenceOrigin
from adaptive_scalper.strategies import build_active_registry

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
            time=start_time + i * 300, open=open_price, high=close_price + 0.05, low=open_price - 0.05,
            close=close_price, tick_volume=100, spread=10, real_volume=0,
        ))
        price = close_price
    return bars


def _config(**overrides) -> BacktestConfig:
    defaults = dict(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0))
    defaults.update(overrides)
    return BacktestConfig(**defaults)


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_run_untouched_oos_succeeds_on_a_genuinely_fresh_dataset(db):
    bars = _trending_bars(200)
    result = run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-1",
        config=_config(), now_utc=2_000_000_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL
    row = db.execute("SELECT run_type FROM backtest_runs WHERE run_id = 'oos-1'").fetchone()
    assert row["run_type"] == "OOS"


def test_run_untouched_oos_refuses_a_dataset_already_used_for_training(db):
    bars = _trending_bars(200)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "some-training-run", "TRAINING", now_utc=2_000_000_000)

    with pytest.raises(DatasetContaminatedError, match="TRAINING"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-2",
            config=_config(), now_utc=2_000_000_000,
        )


def test_run_untouched_oos_refuses_a_dataset_already_used_as_walk_forward_fold(db):
    bars = _trending_bars(200)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    snapshot = build_dataset_snapshot(
        bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "some-wf-run", "WALK_FORWARD_FOLD", now_utc=2_000_000_000)

    with pytest.raises(DatasetContaminatedError, match="WALK_FORWARD_FOLD"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-3",
            config=_config(), now_utc=2_000_000_000,
        )


def test_run_untouched_oos_refuses_to_be_run_twice_on_the_same_dataset(db):
    bars = _trending_bars(200)
    run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-4a",
        config=_config(), now_utc=2_000_000_000,
    )
    with pytest.raises(DatasetContaminatedError, match="already run as OOS"):
        run_untouched_oos(
            bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-4b",
            config=_config(), now_utc=2_000_001_000,
        )


def test_run_untouched_oos_allows_explicit_reuse_when_flagged(db):
    bars = _trending_bars(200)
    run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-5a",
        config=_config(), now_utc=2_000_000_000,
    )
    # Should NOT raise.
    result = run_untouched_oos(
        bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-5b",
        config=_config(), allow_oos_reuse=True, now_utc=2_000_001_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL


def test_run_untouched_oos_does_not_block_on_an_unrelated_dataset(db):
    # A DIFFERENT, non-overlapping time range used for TRAINING must not
    # block THIS range. (Same-period data with different content IS
    # contamination -- see test_backtest_correctness_regressions.py's
    # overlapping-OOS tests.)
    training_bars = _trending_bars(200, start_price=1000.0, start_time=1_700_000_000 - 400 * 300)
    strategies = tuple(s.key for s in build_active_registry().all_active())
    training_snapshot = build_dataset_snapshot(
        training_bars, canonical_symbol=CANONICAL_SYMBOL, resolution=RESOLUTION, strategies=strategies,
        feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=2_000_000_000,
    )
    record_dataset(db, training_snapshot)
    record_dataset_usage(db, training_snapshot.dataset_id, "some-training-run", "TRAINING", now_utc=2_000_000_000)

    oos_bars = _trending_bars(200, start_price=2000.0)
    result = run_untouched_oos(
        oos_bars, CANONICAL_SYMBOL, RESOLUTION, _symbol_spec(), conn=db, run_id="oos-6",
        config=_config(), now_utc=2_000_000_000,
    )
    assert result.canonical_symbol == CANONICAL_SYMBOL


# --------------------------------------------------------------------------
# atomic check-and-reserve (BUG_BACKLOG #11)
# --------------------------------------------------------------------------

def test_an_oos_run_records_exactly_one_usage_row_for_its_own_dataset(db):
    bars = _trending_bars(200)
    result = run_untouched_oos(bars, "XAUUSD", "M5", _symbol_spec(), conn=db, run_id="oos-a", config=_config())
    rows = db.execute("SELECT dataset_id, used_for FROM dataset_usage WHERE used_by_run_id = 'oos-a'").fetchall()
    assert [(r["dataset_id"], r["used_for"]) for r in rows] == [(result.dataset_id, "OOS")]
    run = db.execute("SELECT dataset_id FROM backtest_runs WHERE run_id = 'oos-a'").fetchone()
    assert run["dataset_id"] == result.dataset_id


def test_the_range_is_reserved_before_the_backtest_runs(db, monkeypatch):
    import adaptive_scalper.backtest.oos as oos_module

    def crash(*_a, **_k):
        raise RuntimeError("process died mid-backtest")

    monkeypatch.setattr(oos_module, "run_backtest", crash)
    bars = _trending_bars(200)
    with pytest.raises(RuntimeError):
        run_untouched_oos(bars, "XAUUSD", "M5", _symbol_spec(), conn=db, run_id="oos-crash", config=_config())
    monkeypatch.undo()
    # the crashed run already looked at nothing, but the range is spent: never a second "first" OOS
    with pytest.raises(DatasetContaminatedError):
        run_untouched_oos(bars, "XAUUSD", "M5", _symbol_spec(), conn=db, run_id="oos-retry", config=_config())


def test_a_refused_run_reserves_nothing(db):
    bars = _trending_bars(200)
    snapshot = build_dataset_snapshot(bars, canonical_symbol="XAUUSD", resolution="M5", strategies=("x",),
                                      feature_schema_version=1, origin=EvidenceOrigin.BACKTEST, now_utc=1)
    record_dataset(db, snapshot)
    record_dataset_usage(db, snapshot.dataset_id, "design", "TRAINING", now_utc=1)
    before = db.execute("SELECT COUNT(*) FROM dataset_usage").fetchone()[0]
    with pytest.raises(DatasetContaminatedError):
        run_untouched_oos(bars, "XAUUSD", "M5", _symbol_spec(), conn=db, run_id="oos-b", config=_config())
    assert db.execute("SELECT COUNT(*) FROM dataset_usage").fetchone()[0] == before
    assert not db.in_transaction


def test_two_connections_cannot_both_claim_the_same_range(tmp_path):
    import threading

    path = tmp_path / "race.sqlite3"
    setup = connect(path)
    migrate(setup)
    setup.close()
    bars = _trending_bars(200)
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name: str) -> None:
        conn = connect(path)
        conn.execute("PRAGMA busy_timeout = 10000")
        barrier.wait()
        try:
            run_untouched_oos(bars, "XAUUSD", "M5", _symbol_spec(), conn=conn, run_id=name, config=_config())
            outcomes.append("ran")
        except DatasetContaminatedError:
            outcomes.append("refused")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(f"oos-{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["ran", "refused"]
