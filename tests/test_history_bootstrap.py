"""Tests for the historical bootstrap pipeline (directive sections 46-50):
idempotent storage, chunked+resumable download, and derived coverage.
"""

from __future__ import annotations

import pytest

from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.types import Bar, Tick
from adaptive_scalper.history import bootstrap, coverage, jobs, store
from adaptive_scalper.history.resolutions import RESOLUTION_SECONDS
from adaptive_scalper.persistence import connect, migrate


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test.sqlite3"
    conn = connect(path)
    migrate(conn)
    yield conn
    conn.close()


def _bars(start: int, count: int, step: int = 60) -> list[Bar]:
    return [
        Bar(time=start + i * step, open=1.0, high=1.1, low=0.9, close=1.05,
            tick_volume=10, spread=2, real_volume=0)
        for i in range(count)
    ]


def _ticks(start: int, count: int, step: int = 1) -> list[Tick]:
    return [
        Tick(time=start + i * step, bid=1.0, ask=1.001, last=1.0005, volume=1.0,
             time_msc=(start + i * step) * 1000)
        for i in range(count)
    ]


class _FlakyGateway:
    """Wraps a real gateway, raising after `fail_after` copy_rates_range
    calls, so bootstrap resumability can be exercised deterministically."""

    def __init__(self, inner: FakeGateway, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self._calls = 0

    def copy_rates_range(self, name, resolution, date_from_utc, date_to_utc):
        self._calls += 1
        if self._calls > self._fail_after:
            raise RuntimeError("simulated broker/network failure")
        return self._inner.copy_rates_range(name, resolution, date_from_utc, date_to_utc)

    @property
    def rates_range_calls(self):
        return self._inner.rates_range_calls


# --- storage idempotency ---------------------------------------------------

def test_insert_bars_is_idempotent(db):
    bars = _bars(1_700_000_000, 100)
    first = store.insert_bars(db, "XAUUSD", "M1", bars)
    second = store.insert_bars(db, "XAUUSD", "M1", bars)
    assert first == 100
    assert second == 0
    count = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert count == 100


def test_get_bars_returns_ascending_bars_in_range(db):
    bars = _bars(1_700_000_000, 10)
    store.insert_bars(db, "XAUUSD", "M1", bars)
    result = store.get_bars(db, "XAUUSD", "M1", 1_700_000_000, 1_700_000_000 + 9 * 60)
    assert [b.time for b in result] == [b.time for b in bars]
    assert result[0].open == 1.0


def test_get_bars_excludes_out_of_range_and_other_symbol_resolution(db):
    bars = _bars(1_700_000_000, 10)
    store.insert_bars(db, "XAUUSD", "M1", bars)
    store.insert_bars(db, "XAUUSD", "M5", bars)
    store.insert_bars(db, "GBPJPY", "M1", bars)

    narrow = store.get_bars(db, "XAUUSD", "M1", 1_700_000_000 + 2 * 60, 1_700_000_000 + 4 * 60)
    assert len(narrow) == 3

    empty = store.get_bars(db, "XAUUSD", "M1", 0, 10)
    assert empty == []


def test_insert_ticks_is_idempotent(db):
    ticks = _ticks(1_700_000_000, 50)
    first = store.insert_ticks(db, "XAUUSD", ticks)
    second = store.insert_ticks(db, "XAUUSD", ticks)
    assert first == 50
    assert second == 0
    count = db.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    assert count == 50


# --- single-chunk bootstrap --------------------------------------------------

def test_bootstrap_bars_completes_in_one_chunk_and_updates_coverage(db):
    start = 1_700_000_000
    bars = _bars(start, 60, step=60)  # 1 hour of M1 bars
    end = start + 60 * 60 - 1
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})

    job = bootstrap.bootstrap_bars(db, gw, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=10_000)

    assert job.status == jobs.COMPLETE
    assert job.cursor_utc == end
    row_count = db.execute("SELECT COUNT(*) FROM bars WHERE canonical_symbol='XAUUSD'").fetchone()[0]
    assert row_count == 60

    cov = coverage.get_bar_coverage(db, "XAUUSD", "M1")
    assert cov.bar_count == 60
    assert cov.earliest_utc == start
    assert cov.latest_utc == bars[-1].time
    assert cov.gap_count == 0


def test_bootstrap_ticks_completes_and_updates_coverage(db):
    start = 1_700_000_000
    end = start + 100
    ticks = _ticks(start, 100, step=1)
    gw = FakeGateway(tick_history={"XAUUSDm": ticks})

    job = bootstrap.bootstrap_ticks(db, gw, "XAUUSD", "XAUUSDm", start, end, chunk_seconds=1000)

    assert job.status == jobs.COMPLETE
    count = db.execute("SELECT COUNT(*) FROM ticks WHERE canonical_symbol='XAUUSD'").fetchone()[0]
    assert count == 100

    cov = coverage.get_tick_coverage(db, "XAUUSD")
    assert cov.tick_count == 100
    assert cov.earliest_utc == start


# --- chunking ---------------------------------------------------------------

def test_bootstrap_bars_requests_multiple_chunks_when_range_exceeds_chunk_size(db):
    start = 1_700_000_000
    bars = _bars(start, 1000, step=60)  # ~16.6 hours of M1 bars
    end = bars[-1].time
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})

    # Force several chunks: 1000 bars * 60s = 60000s span, chunk of 10000s.
    job = bootstrap.bootstrap_bars(db, gw, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=10_000)

    assert job.status == jobs.COMPLETE
    assert len(gw.rates_range_calls) > 1
    row_count = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert row_count == 1000


# --- resumability ------------------------------------------------------------

def test_bootstrap_bars_resumes_from_checkpoint_after_failure(db):
    start = 1_700_000_000
    bars = _bars(start, 300, step=60)  # 5 hours
    end = bars[-1].time
    inner = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})
    flaky = _FlakyGateway(inner, fail_after=1)

    with pytest.raises(RuntimeError):
        bootstrap.bootstrap_bars(db, flaky, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=3_000)

    partial_job = jobs.get_job(db, "XAUUSD", jobs.BAR, "M1")
    assert partial_job.status == jobs.FAILED
    assert partial_job.cursor_utc > start  # first chunk's progress was persisted
    assert partial_job.cursor_utc < end
    partial_count = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert 0 < partial_count < 300

    # "Restart": a fresh, unbroken gateway resumes from the persisted cursor.
    calls_before_resume = len(inner.rates_range_calls)
    resumed_job = bootstrap.bootstrap_bars(db, inner, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=3_000)

    assert resumed_job.status == jobs.COMPLETE
    final_count = db.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert final_count == 300
    # The resumed run's first request started at the failed run's checkpoint,
    # not back at `start` — proof the full range was never re-requested from
    # scratch.
    resumed_first_call = inner.rates_range_calls[calls_before_resume]
    assert resumed_first_call[2] == partial_job.cursor_utc


def test_bootstrap_bars_rerun_after_completion_makes_no_further_gateway_calls(db):
    start = 1_700_000_000
    bars = _bars(start, 60, step=60)
    end = bars[-1].time
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})

    bootstrap.bootstrap_bars(db, gw, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=10_000)
    calls_after_first_run = len(gw.rates_range_calls)

    job = bootstrap.bootstrap_bars(db, gw, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=10_000)

    assert job.status == jobs.COMPLETE
    assert len(gw.rates_range_calls) == calls_after_first_run


def test_bootstrap_persists_across_a_fresh_connection(tmp_path):
    """Simulates a real process restart: the job checkpoint must be readable
    from a brand-new connection to the same database file."""
    path = tmp_path / "test.sqlite3"
    conn1 = connect(path)
    migrate(conn1)
    start = 1_700_000_000
    bars = _bars(start, 300, step=60)
    end = bars[-1].time
    inner = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})
    flaky = _FlakyGateway(inner, fail_after=1)
    with pytest.raises(RuntimeError):
        bootstrap.bootstrap_bars(conn1, flaky, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=3_000)
    conn1.close()

    conn2 = connect(path)
    job = jobs.get_job(conn2, "XAUUSD", jobs.BAR, "M1")
    assert job.status == jobs.FAILED
    assert job.cursor_utc > start
    resumed = bootstrap.bootstrap_bars(conn2, inner, "XAUUSD", "XAUUSDm", "M1", start, end, chunk_seconds=3_000)
    assert resumed.status == jobs.COMPLETE
    conn2.close()


# --- get_or_create_job edge cases -------------------------------------------

def test_get_or_create_job_rejects_widening_start_backward(db):
    jobs.get_or_create_job(db, "XAUUSD", jobs.BAR, 2_000, 3_000, resolution="M1")
    with pytest.raises(ValueError):
        jobs.get_or_create_job(db, "XAUUSD", jobs.BAR, 1_000, 3_000, resolution="M1")


def test_get_or_create_job_extends_end_and_reopens_completed_job(db):
    start, mid = 1_700_000_000, 1_700_000_060
    bars = _bars(start, 1, step=60)
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {"M1": bars}})
    job = bootstrap.bootstrap_bars(db, gw, "XAUUSD", "XAUUSDm", "M1", start, mid, chunk_seconds=1000)
    assert job.status == jobs.COMPLETE

    extended = jobs.get_or_create_job(db, "XAUUSD", jobs.BAR, start, mid + 3600, resolution="M1")
    assert extended.status == jobs.IN_PROGRESS
    assert extended.requested_end_utc == mid + 3600
    assert extended.cursor_utc == job.cursor_utc  # progress preserved, not reset


# --- gap reporting -----------------------------------------------------------

def test_bar_coverage_reports_gap_count_for_missing_bars(db):
    start = 1_700_000_000
    step = RESOLUTION_SECONDS["M1"]
    # Bars at t, t+step, then a jump of 5 missing bars, then continues.
    present = [start, start + step, start + step * 7, start + step * 8]
    bars = [
        Bar(time=t, open=1.0, high=1.1, low=0.9, close=1.0, tick_volume=1, spread=1, real_volume=0)
        for t in present
    ]
    store.insert_bars(db, "XAUUSD", "M1", bars)
    cov = coverage.refresh_bar_coverage(db, "XAUUSD", "M1")
    assert cov.bar_count == 4
    assert cov.expected_bar_count == 9  # (present[-1]-present[0])/step + 1
    assert cov.gap_count == 5


def test_bar_coverage_is_none_before_any_import(db):
    assert coverage.get_bar_coverage(db, "XAUUSD", "M1") is None


# --- bootstrap_all skips unresolved symbols ---------------------------------

def test_bootstrap_all_only_touches_resolved_symbols(db):
    start = 1_700_000_000
    end = start + 60
    bars = _bars(start, 1, step=60)
    gw = FakeGateway(bars_by_resolution={"XAUUSDm": {r: bars for r in RESOLUTION_SECONDS}})

    results = bootstrap.bootstrap_all(
        db, gw, {"XAUUSD": "XAUUSDm"}, now_utc=end,
        resolutions=("M1",), bar_years=0, include_ticks=False,
    )
    # bar_years=0 with now_utc=end means bar_start == now_utc == end, an
    # empty (zero-length) window; assert only the resolved symbol appears.
    assert set(results.keys()) == {"XAUUSD"}
    assert "GBPJPY" not in results
