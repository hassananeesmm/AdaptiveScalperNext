"""Runtime bar persistence (runtime/bar_persistence.py, `[runtime] persist_closed_bars`).

The sink copies the closed bars the runtime cycles ALREADY fetched into the
`bars` table. These tests pin the safety contract: off by default, zero extra
gateway calls, identical trading decisions on or off, closed + UTC-aligned
bars only, DST-adjacent bars never written, idempotent across restarts, a
locked or failing database never raises into the scheduler or delays it,
PAPER session fingerprints unchanged.
"""

from __future__ import annotations

import ast
import math
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from runtime_helpers import (
    STEP,
    T0,
    FakeClock,
    app_config,
    build_engine,
    step,
    trending_bars,
)

from adaptive_scalper.backtest.fingerprint import compute_config_fingerprint
from adaptive_scalper.config.loader import load_config
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.gateway.server_time import RULE_UTC, RULE_UTC2_US_DST
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.history.store import insert_bars
from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.runtime import bar_persistence
from adaptive_scalper.runtime.bar_persistence import (
    DISABLED,
    FAILED,
    IDLE,
    OK,
    SKIPPED,
    STATE_KEY,
    LiveBarSink,
    bar_problem,
    near_dst_change,
)
from adaptive_scalper.runtime.paper import paper_config, session_key
from adaptive_scalper.runtime.state import get_state

REPO_ROOT = Path(__file__).resolve().parent.parent
START_AT = T0 + 150 * STEP + 7      # mid-bar: the bar opened at T0+150*STEP is still forming


# ---------------------------------------------------------------------- helpers
def _db(tmp_path, name="sink.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    return conn


def _sink(conn, bars_by_symbol, *, now, rule=RULE_UTC, **kw):
    holder = {"bars": bars_by_symbol}
    sink = LiveBarSink(conn, resolution="M5", server_time_rule=rule, source=lambda: holder["bars"],
                       clock=lambda: float(now() if callable(now) else now), **kw)
    return sink, holder


def _stored(conn, symbol="XAUUSD"):
    return [r[0] for r in conn.execute(
        "SELECT ts_utc FROM bars WHERE canonical_symbol = ? AND resolution = 'M5' ORDER BY ts_utc", (symbol,))]


def _events(conn, event):
    return conn.execute("SELECT * FROM runtime_events WHERE event = ?", (event,)).fetchall()


def _closed(n, start=T0):
    return trending_bars(n, start_price=2000.0, step_price=0.5, start=start)


def _started(tmp_path, mode, **runtime):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode=mode, clock=clock, **runtime)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def _decisions(conn):
    return [tuple(r) for r in conn.execute(
        "SELECT canonical_symbol, stage, decision, reason FROM entry_decisions ORDER BY id")]


# ---------------------------------------------------------------------- defaults
def test_disabled_by_default_in_code_and_in_the_shipped_config():
    assert app_config(Path("."), mode="DEMO").runtime.persist_closed_bars is False
    assert load_config(REPO_ROOT / "config" / "default.toml").runtime.persist_closed_bars is False


@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_disabled_registers_no_task_and_writes_no_bars(tmp_path, mode):
    clock, engine, conn, _ = _started(tmp_path, mode)
    step(engine, clock, seconds=STEP * 2, tick=2)
    assert engine.bar_sink is None
    assert "bar_persistence" not in {t.name for t in engine.scheduler.tasks}
    assert conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 0


def test_the_flag_is_not_part_of_the_paper_fingerprint_or_session_key(tmp_path):
    off = app_config(tmp_path, mode="PAPER")
    on = app_config(tmp_path, mode="PAPER", persist_closed_bars=True)
    for symbol in ("XAUUSD", "GBPJPY", "BTCUSD"):
        prints = [compute_config_fingerprint(paper_config(c, symbol, ()), canonical_symbol=symbol,
                                             resolutions=("M5",), strategies=(("s", 1),)) for c in (off, on)]
        assert prints[0] == prints[1]
        assert session_key(off, symbol) == session_key(on, symbol)


# ---------------------------------------------------------------------- runtime integration
@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_enabled_runtime_persists_closed_utc_aligned_bars(tmp_path, mode):
    clock, engine, conn, _ = _started(tmp_path, mode, persist_closed_bars=True)
    step(engine, clock, seconds=STEP * 3, tick=2)
    task = next(t for t in engine.scheduler.tasks if t.name == "bar_persistence")
    assert task.priority == 3 and task.runs >= 3 and task.failures == 0
    for symbol in ("XAUUSD", "GBPJPY", "BTCUSD"):
        stored = _stored(conn, symbol)
        assert stored, symbol
        assert all(t % STEP == 0 for t in stored)
        assert max(stored) + STEP <= clock.now                      # never the forming bar
        assert stored == sorted(set(stored))
    state = get_state(conn, STATE_KEY)
    assert state["rows_written"] == conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    assert engine.component_health["bar_persistence"]["status"] == "OK"
    assert "bar_persistence" in get_state(conn, "engine")["tasks"]          # duration / lag published
    coverage = conn.execute("SELECT * FROM historical_bar_coverage WHERE canonical_symbol = 'XAUUSD'").fetchone()
    assert coverage["latest_utc"] == max(_stored(conn))


@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_enabled_makes_zero_extra_gateway_calls_and_changes_no_decision(tmp_path, mode):
    runs = {}
    for flag in (False, True):
        clock, engine, conn, gateway = _started(tmp_path / str(flag), mode, persist_closed_bars=flag)
        step(engine, clock, seconds=STEP * 3, tick=2)
        runs[flag] = (dict(gateway.calls), _decisions(conn),
                      {n: (s["priority"], s["last_lag_seconds"]) for n, s in engine.scheduler.snapshot().items()
                       if n != "bar_persistence"})
        assert gateway.calls["order_send"] == 0
    assert runs[True][0] == runs[False][0]          # identical MT5 traffic
    assert runs[True][1] == runs[False][1]          # identical entry decisions
    assert runs[True][2] == runs[False][2]          # other tasks: same priorities, same scheduling lag


def test_a_failing_sink_never_raises_into_the_scheduler_or_changes_decisions(tmp_path):
    runs = {}
    for broken in (False, True):
        clock, engine, conn, gateway = _started(tmp_path / str(broken), "DEMO", persist_closed_bars=True)
        if broken:
            def boom():
                raise RuntimeError("simulated sink fault")
            engine.bar_sink.source = boom
        step(engine, clock, seconds=STEP * 2, tick=2)
        runs[broken] = (dict(gateway.calls), _decisions(conn))
        task = next(t for t in engine.scheduler.tasks if t.name == "bar_persistence")
        assert task.failures == 0                                  # never raised
        if broken:
            assert engine.component_health["bar_persistence"]["status"] == "DEGRADED"
            assert engine.component_health["bar_persistence"].get("criticality") == "ADVISORY"
            assert not _events(conn, "TASK_FAILED")
            assert get_state(conn, "engine")["safety_critical_degraded"] == []
    assert runs[True] == runs[False]


def test_ai_desk_reports_fresh_bars_once_the_runtime_persists_them(tmp_path):
    from adaptive_scalper.ai_desk.configuration import AiDeskConfig
    from adaptive_scalper.ai_desk.journal import Journal
    from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator
    from adaptive_scalper.config.loader import AppConfig

    states = {}
    for flag in (False, True):
        clock, engine, _conn, _ = _started(tmp_path / str(flag), "DEMO", persist_closed_bars=flag)
        step(engine, clock, seconds=STEP * 2, tick=2)
        db =str(tmp_path / str(flag) / "runtime.sqlite3")
        cfg = AiDeskConfig(journal_path=str(tmp_path / str(flag) / "ai.sqlite3"))
        journal = Journal(cfg.journal_path, trading_db_path=db)
        report = DeskOrchestrator(cfg, trading_db_path=db, journal=journal, trading_config=AppConfig()).run_cycle(
            as_of_utc=int(clock.now), agents={"market_analyst"})
        xau = next(r for r in report.results if r.agent == "market_analyst" and r.symbol == "XAUUSD")
        states[flag] = xau.findings["data_freshness"]["state"]
        journal.close()
    assert states[True] == "FRESH_BARS"
    assert states[False] != "FRESH_BARS"


# ---------------------------------------------------------------------- closed / UTC / validation
def test_bar_problem_rules():
    good = _closed(1)[0]
    now = good.time + STEP
    assert bar_problem(good, STEP, now) is None
    assert bar_problem(good, STEP, now - 1) == bar_persistence.NOT_CLOSED
    assert "aligned" in bar_problem(Bar(**{**good.__dict__, "time": good.time + 1}), STEP, now + 1)
    assert "price" in bar_problem(Bar(**{**good.__dict__, "close": math.nan}), STEP, now)
    assert "price" in bar_problem(Bar(**{**good.__dict__, "low": 0.0}), STEP, now)
    assert "bound" in bar_problem(Bar(**{**good.__dict__, "high": good.open - 1}), STEP, now)
    assert "negative" in bar_problem(Bar(**{**good.__dict__, "tick_volume": -1}), STEP, now)


def test_a_forming_bar_is_never_stored_even_if_the_source_holds_it(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(10)
    now = bars[-1].time + 10                      # newest bar still forming (e.g. a tick clock ahead)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=now)
    assert sink.run()["status"] == OK
    assert _stored(conn) == [b.time for b in bars[:-1]]
    assert _events(conn, "BAR_NOT_PERSISTED")


def test_a_stale_tick_only_delays_persistence(tmp_path):
    """A stale tick makes the runtime's source hold fewer bars; the sink just
    writes what is there and catches up when the source does."""
    conn = _db(tmp_path)
    bars = _closed(20)
    sink, holder = _sink(conn, {"XAUUSD": bars[:12]}, now=bars[-1].time + STEP)
    sink.run()
    holder["bars"] = {"XAUUSD": bars}
    sink.run()
    assert _stored(conn) == [b.time for b in bars]


def test_invalid_bars_are_rejected_and_reported(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(5)
    bad = Bar(**{**bars[2].__dict__, "high": bars[2].low - 1})
    sink, _ = _sink(conn, {"XAUUSD": [bars[0], bars[1], bad, bars[3], bars[4]]}, now=bars[-1].time + STEP)
    sink.run()
    assert _stored(conn) == [bars[i].time for i in (0, 1, 3, 4)]
    assert sink.stats.rejected == 1 and _events(conn, "BAR_REJECTED")


def test_a_non_utc_database_disables_the_sink(tmp_path):
    conn = _db(tmp_path)
    conn.execute("UPDATE mt5_time_basis SET basis = 'SERVER_UNCONVERTED' WHERE id = 1")
    bars = _closed(5)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    assert sink.run()["status"] == DISABLED
    assert sink.run()["status"] == DISABLED
    assert _stored(conn) == [] and "time basis" in sink.disabled_reason


# ---------------------------------------------------------------------- DST
def _utc(*args):
    return int(datetime(*args, tzinfo=UTC).timestamp())


@pytest.mark.parametrize("transition", [_utc(2025, 3, 9, 7), _utc(2025, 11, 2, 6)])   # US DST start / end
def test_bars_within_an_hour_of_a_server_clock_dst_change_are_not_stored(tmp_path, transition):
    start = transition - 3 * 3600
    bars = trending_bars(72, start_price=60000.0, step_price=1.0, start=start)    # 6 h of M5 around it
    conn = _db(tmp_path)
    sink, _ = _sink(conn, {"BTCUSD": bars}, now=bars[-1].time + STEP, rule=RULE_UTC2_US_DST)
    sink.run()
    stored = set(_stored(conn, "BTCUSD"))
    skipped = {b.time for b in bars} - stored
    # Conservative, inclusive window: every bar whose span comes within one hour of the change.
    assert skipped == {b.time for b in bars if transition - 3600 - STEP <= b.time < transition + 3600}
    assert all(near_dst_change(RULE_UTC2_US_DST, t, STEP) for t in skipped)
    assert _events(conn, "BAR_DST_WINDOW_SKIPPED")
    # On a UTC server clock nothing is skipped.
    conn2 = _db(tmp_path, "utc.sqlite3")
    _sink(conn2, {"BTCUSD": bars}, now=bars[-1].time + STEP, rule=RULE_UTC)[0].run()
    assert len(_stored(conn2, "BTCUSD")) == len(bars)


# ---------------------------------------------------------------------- dedup / restart / gaps
def test_rerunning_is_idempotent_and_a_restart_reseeds_the_watermark(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(50)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    assert sink.run()["rows"] == 50
    assert sink.run()["status"] == IDLE                     # nothing new: no transaction at all
    restarted, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    assert restarted.run()["status"] == IDLE
    assert restarted.watermarks["XAUUSD"] == bars[-1].time
    assert len(_stored(conn)) == 50


def test_overlap_with_bootstrapped_history_keeps_the_first_write(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(30)
    first = [Bar(**{**b.__dict__, "tick_volume": 7}) for b in bars[:20]]
    insert_bars(conn, "XAUUSD", "M5", first)                # as `history bootstrap` would
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    sink.run()
    assert len(_stored(conn)) == 30
    volumes = [r[0] for r in conn.execute("SELECT tick_volume FROM bars ORDER BY ts_utc")]
    assert volumes[:20] == [7] * 20 and volumes[20:] == [100] * 10


def test_restart_within_the_cache_fills_the_gap_and_beyond_it_reports_one(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(400)
    insert_bars(conn, "XAUUSD", "M5", bars[:100])
    covered, _ = _sink(conn, {"XAUUSD": bars[50:350]}, now=bars[349].time + STEP)   # cache reaches back
    covered.run()
    assert len(_stored(conn)) == 350 and not _events(conn, "BAR_GAP")

    conn2 = _db(tmp_path, "gap.sqlite3")
    insert_bars(conn2, "XAUUSD", "M5", bars[:50])
    gapped, _ = _sink(conn2, {"XAUUSD": bars[100:400]}, now=bars[-1].time + STEP)   # down longer than cache
    gapped.run()
    gaps = _events(conn2, "BAR_GAP")
    assert len(gaps) == 1 and str(bars[49].time) in gaps[0]["detail"] and str(bars[99].time) in gaps[0]["detail"]
    assert len(_stored(conn2)) == 350                       # the gap stays a gap; nothing is invented
    gapped.run()
    assert len(_events(conn2, "BAR_GAP")) == 1


# ---------------------------------------------------------------------- locking / failure isolation
def test_a_locked_database_is_skipped_quickly_and_retried(tmp_path):
    conn = _db(tmp_path)
    other = connect(tmp_path / "sink.sqlite3")
    bars = _closed(20)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    before = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    other.execute("BEGIN IMMEDIATE")                        # another process holds the write lock
    started = time.monotonic()
    result = sink.run()
    elapsed = time.monotonic() - started
    assert result["status"] == SKIPPED and elapsed < 1.0
    assert sink.stats.lock_skips == 1 and not conn.in_transaction
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == before          # restored
    other.execute("ROLLBACK")
    assert sink.run()["status"] == OK and len(_stored(conn)) == 20
    other.close()


def test_a_write_failure_rolls_back_and_keeps_the_watermark(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    bars = _closed(10)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)

    def broken(*_a, **_k):
        raise ValueError("disk full (simulated)")
    monkeypatch.setattr(bar_persistence, "insert_bars", broken)
    assert sink.run()["status"] == FAILED
    assert not conn.in_transaction and _stored(conn) == [] and sink.watermarks["XAUUSD"] is None
    monkeypatch.undo()
    assert sink.run()["status"] == OK and len(_stored(conn)) == 10


def test_the_sink_never_nests_inside_an_open_transaction(tmp_path):
    conn = _db(tmp_path)
    bars = _closed(5)
    sink, _ = _sink(conn, {"XAUUSD": bars}, now=bars[-1].time + STEP)
    conn.execute("BEGIN")
    assert sink.run()["status"] == SKIPPED
    conn.execute("ROLLBACK")


# ---------------------------------------------------------------------- performance / growth
def test_scheduler_cost_and_database_growth_are_bounded(tmp_path):
    conn = _db(tmp_path)
    market = {s: trending_bars(300, start_price=p, step_price=0.1) for s, p in
              (("XAUUSD", 2000.0), ("GBPJPY", 190.0), ("BTCUSD", 60000.0))}
    now = T0 + 300 * STEP
    sink, holder = _sink(conn, market, now=lambda: now, monotonic=time.monotonic)
    first = sink.run()
    assert first["status"] == OK and first["rows"] == 900
    assert first["duration_ms"] < 2000                      # cold start: 3 x 300 rows + coverage
    assert sink.run()["duration_ms"] < 100                  # steady state with nothing new: no transaction
    grown = {s: [*bars, *trending_bars(1, start_price=bars[-1].close, step_price=0.1, start=bars[-1].time + STEP)]
             for s, bars in market.items()}
    holder["bars"] = grown
    now += STEP
    incremental = sink.run()
    assert incremental["rows"] == 3 and incremental["duration_ms"] < 250
    assert sink.stats.db_bytes and sink.stats.db_bytes > 0
    assert get_state(conn, STATE_KEY)["rows_written"] == 903


# ---------------------------------------------------------------------- audit
def test_the_sink_module_cannot_reach_mt5_or_execution():
    source = (REPO_ROOT / "adaptive_scalper" / "runtime" / "bar_persistence.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    allowed_gateway = {"adaptive_scalper.gateway.server_time", "adaptive_scalper.gateway.types"}
    for module in imported:
        assert "MetaTrader5" not in module
        assert not module.startswith(("adaptive_scalper.execution", "adaptive_scalper.core", "adaptive_scalper.risk"))
        if module.startswith("adaptive_scalper.gateway"):
            assert module in allowed_gateway
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"copy_rates_range", "copy_rates_from_pos", "symbol_info_tick", "symbol_info", "order_send",
                         "order_check", "positions_get", "initialize", "account_info"}
