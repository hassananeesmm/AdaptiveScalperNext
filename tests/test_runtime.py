"""Runtime orchestrator (Phase 3) and runtime-level chaos.

Driven by `runtime_helpers.LiveMarketGateway`: pre-generated M5 bars for
the three canonical symbols, revealed as a fake clock advances, with
every broker call still routed through the chaos fault plan.
"""

from __future__ import annotations

import dataclasses

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.kill_switch import get_state as kill_switch_state
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.execution.reconciliation import record_incident
from adaptive_scalper.gateway.types import TradeMode
from adaptive_scalper.journal.queries import get_chain_events
from adaptive_scalper.runtime.engine import RuntimeComponents, RuntimeStartupError
from adaptive_scalper.runtime.scheduler import Scheduler
from adaptive_scalper.runtime.state import get_state
from chaos_harness import demo_account
from edge_fixtures import TEST_PUBLIC_KEY, FixtureValidatedProvider
from runtime_helpers import STEP, T0, FakeClock, LiveMarketGateway, StaticNewsProvider, build_engine, default_market, step

START_AT = T0 + 60 * STEP + 10  # 60 closed bars already exist


def _operator_bootstrap(conn):
    # Tests stand in for the human operator; the runtime itself never does this.
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")


def _decisions(conn, **where):
    sql = "SELECT * FROM entry_decisions"
    if where:
        sql += " WHERE " + " AND ".join(f"{k} = ?" for k in where)
    return conn.execute(sql, tuple(where.values())).fetchall()


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["PAPER", "DEMO"])
@pytest.mark.parametrize("trade_mode", [TradeMode.REAL, TradeMode.CONTEST])
def test_startup_refuses_a_non_demo_account_in_every_mode(tmp_path, mode, trade_mode):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market(), account=demo_account(trade_mode=trade_mode))
    engine, _, _ = build_engine(tmp_path, mode=mode, clock=clock, gateway=gateway)
    with pytest.raises(RuntimeStartupError, match="REAL-MONEY EXECUTION IS DISABLED"):
        engine.startup()


def test_startup_refuses_when_the_terminal_cannot_initialize(tmp_path):
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    gateway.initialize = lambda: False
    engine, _, _ = build_engine(tmp_path, mode="DEMO", clock=clock, gateway=gateway)
    with pytest.raises(RuntimeStartupError, match="could not be initialized"):
        engine.startup()


def test_startup_refuses_quotes_in_the_future_under_the_server_time_rule(tmp_path):
    # BUG_BACKLOG #14: a UTC+3 server read with rule "UTC" puts every quote
    # three hours in the future -- refuse rather than shift every time.
    clock = FakeClock(START_AT)
    gateway = LiveMarketGateway(clock, default_market())
    live_tick = gateway._live_tick

    def server_clock_tick(name):
        tick = live_tick(name)
        return dataclasses.replace(tick, time=tick.time + 3 * 3600) if tick else None

    gateway._live_tick = server_clock_tick
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, gateway=gateway)
    with pytest.raises(RuntimeStartupError, match="server_time_rule"):
        engine.startup()
    assert get_state(conn, "server_clock")["verdict"] == "MISMATCH"


def test_startup_records_a_verified_server_clock(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock)
    assert engine.startup()["server_clock"] == "VERIFIED"


def test_startup_refuses_history_still_in_server_time(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock)
    conn.execute("UPDATE mt5_time_basis SET basis = 'SERVER_UNCONVERTED'")
    with pytest.raises(RuntimeStartupError, match="convert-server-time"):
        engine.startup()


def test_startup_never_bootstraps_or_clears_the_kill_switch(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock)
    summary = engine.startup()
    step(engine, clock, seconds=STEP * 3, tick=4)
    assert summary["kill_switch"] == "UNINITIALIZED"
    assert kill_switch_state(conn).status.value == "UNINITIALIZED"
    assert conn.execute("SELECT COUNT(*) FROM configuration_audit").fetchone()[0] == 0


def test_an_unresolvable_symbol_is_excluded_never_substituted(tmp_path):
    clock = FakeClock(START_AT)
    market = default_market()
    del market["BTCUSD"]
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, bars=market)
    summary = engine.startup()
    assert set(summary["symbols"]) == {"XAUUSD", "GBPJPY"}
    assert "BTCUSD" in summary["excluded_symbols"]
    assert conn.execute("SELECT COUNT(*) FROM runtime_events WHERE event = 'SYMBOL_EXCLUDED'").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# PAPER
# ---------------------------------------------------------------------------

def test_paper_never_calls_order_check_or_order_send(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="PAPER", clock=clock, edge_evidence=FixtureValidatedProvider())
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 25, tick=4)
    assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] > 0
    assert gateway.calls["order_check"] == 0 and gateway.calls["order_send"] == 0 and not gateway.order_send_calls
    assert all(r["origin"] == "PAPER_LIVE_DATA" for r in conn.execute("SELECT origin FROM paper_trades"))


def test_paper_new_entries_wait_for_the_operator_but_the_engine_keeps_running(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock)
    engine.startup()
    step(engine, clock, seconds=STEP * 10, tick=4)
    assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert get_state(conn, "why_no_trade")["global_block"]["decision"] == "BLOCK_KILL_SWITCH"
    sessions = conn.execute("SELECT last_processed_bar_time_utc FROM paper_session_state").fetchall()
    assert len(sessions) == 3 and all(s[0] is not None for s in sessions)  # still processing bars


def test_paper_news_outage_blocks_new_simulated_entries(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock, news=[StaticNewsProvider(fail=True)])
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 10, tick=4)
    assert get_state(conn, "why_no_trade")["global_block"]["decision"] == "BLOCK_NEWS_CALENDAR_UNAVAILABLE"
    assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert kill_switch_state(conn).status.value == "DISENGAGED"  # a news outage never trips the kill switch


def test_paper_config_change_halts_that_session_instead_of_mixing_state(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="PAPER", clock=clock)
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 2, tick=4)
    engine.config.costs["XAUUSD"].slippage_price = 0.5  # a deliberate config change mid-session
    step(engine, clock, seconds=STEP * 2, tick=4)
    assert get_state(conn, "why_no_trade")["symbols"]["XAUUSD"]["decision"] == "SESSION_HALTED"
    assert conn.execute(
        "SELECT COUNT(*) FROM runtime_events WHERE event = 'PAPER_SESSION_CONFIG_MISMATCH'"
    ).fetchone()[0] == 1


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------

def test_demo_full_pipeline_fills_journals_and_manages_a_position(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)

    filled = _decisions(conn, stage="EXECUTION", decision="FILLED")
    assert filled, "a natural proposal passing every gate should have filled"
    chain = filled[0]["chain_key"]
    events = [e.event_type for e in get_chain_events(conn, chain)]
    for expected in ("SIGNAL_CREATED", "RAG_USED", "MODEL_USED", "PROPOSAL_CREATED", "ENTRY_ALLOWED",
                     "ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED"):
        assert expected in events, (expected, events)
    assert events.count("ENTRY_ALLOWED") == 2  # final permission evaluated fresh twice before send
    context = conn.execute("SELECT * FROM position_entry_context").fetchone()
    assert context["chain_key"] == chain and context["strategy_key"]
    reviews = conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type = 'POSITION_REVIEWED'").fetchone()[0]
    assert reviews > 0
    assert gateway.calls["order_send"] >= 1


def test_demo_uninitialized_kill_switch_blocks_before_any_strategy_runs(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    assert {r["decision"] for r in _decisions(conn)} == {"BLOCK_KILL_SWITCH"}
    assert conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type = 'SIGNAL_CREATED'").fetchone()[0] == 0
    assert gateway.calls["order_check"] == 0 and gateway.calls["order_send"] == 0
    assert get_state(conn, "reconciliation")["status"] == "CLEAN"  # position/reconciliation cycle still ran


def test_demo_unknown_costs_block_at_the_selector(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, costs=False)
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    assert _decisions(conn, stage="SELECTOR", decision="BLOCK_COST")
    assert gateway.calls["order_send"] == 0


def test_demo_dangerous_unknown_blocks_every_new_entry(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _operator_bootstrap(conn)
    engine.startup()
    record_incident(conn, "UNKNOWN_OUTCOME", "simulated unresolved UNKNOWN", now_utc=START_AT)
    step(engine, clock, seconds=STEP * 4, tick=4)
    assert {r["decision"] for r in _decisions(conn)} == {"BLOCK_UNKNOWN_ORDER"}
    assert gateway.calls["order_send"] == 0


def test_demo_account_switching_to_real_mid_run_blocks_every_broker_mutation(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 3, tick=4)
    sends_before = gateway.calls["order_send"]
    gateway.set_account(demo_account(trade_mode=TradeMode.REAL))
    step(engine, clock, seconds=STEP * 6, tick=4)
    assert gateway.calls["order_send"] == sends_before  # no entry, no close, no stop move on a REAL account
    assert "BLOCK_ACCOUNT_NOT_DEMO" in {r["decision"] for r in _decisions(conn, stage="GLOBAL")}


def test_demo_kill_switch_engaged_mid_run_blocks_entries_but_keeps_managing(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 3, tick=4)
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] >= 1
    engage_kill_switch(conn, "operator STOP TRADING", "operator")
    entries_before = len(_decisions(conn, stage="EXECUTION"))
    reviews_before = conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type='POSITION_REVIEWED'").fetchone()[0]
    step(engine, clock, seconds=STEP * 3, tick=4)
    assert len(_decisions(conn, stage="EXECUTION")) == entries_before
    reviews_after = conn.execute("SELECT COUNT(*) FROM journal_events WHERE event_type='POSITION_REVIEWED'").fetchone()[0]
    open_now = conn.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN'").fetchone()[0]
    assert reviews_after > reviews_before or open_now == 0  # management continued (or everything already exited)


# ---------------------------------------------------------------------------
# runtime-level chaos: advisory / news / task failures
# ---------------------------------------------------------------------------

class _Broken:
    def query_similar(self, *a, **k):
        raise RuntimeError("rag down")

    def rebuild_index(self, conn):
        raise RuntimeError("rag down")

    def score(self, *a, **k):
        raise RuntimeError("model down")

    def advise(self, **k):
        raise RuntimeError("okf down")

    def record(self, *a, **k):
        raise RuntimeError("rag down")

    def status(self, conn):
        return {}


def _execution_outcomes(tmp_path, components):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock, components=components)
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    return [(r["canonical_symbol"], r["decision"]) for r in _decisions(conn, stage="EXECUTION")], conn, engine


def test_rag_ml_and_okf_failures_degrade_advisory_context_but_never_change_decisions(tmp_path):
    healthy, _, _ = _execution_outcomes(tmp_path / "a", RuntimeComponents(edge_evidence=FixtureValidatedProvider(), certificate_public_key=TEST_PUBLIC_KEY))
    broken = _Broken()
    degraded, conn, engine = _execution_outcomes(
        tmp_path / "b", RuntimeComponents(rag=broken, observer=broken, okf=broken, edge_evidence=FixtureValidatedProvider(),
                          certificate_public_key=TEST_PUBLIC_KEY),
    )
    assert healthy and degraded == healthy
    statuses = {e.payload["status"] for e in get_chain_events(conn, _decisions(conn, stage="EXECUTION")[0]["chain_key"])
                if e.event_type in ("RAG_USED", "MODEL_USED")}
    assert statuses == {"DEGRADED"}
    assert "rag" in get_state(conn, "engine")["degraded_components"]


def test_a_failing_entry_cycle_never_starves_position_management(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock, edge_evidence=FixtureValidatedProvider())
    _operator_bootstrap(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 3, tick=4)
    engine.demo.entry_cycle = lambda: (_ for _ in ()).throw(RuntimeError("scanner crashed"))
    for task in engine.scheduler.tasks:
        if task.name == "entry_cycle":
            task.run = engine.demo.entry_cycle
    positions_calls = gateway.calls["positions_get"]
    step(engine, clock, seconds=60, tick=1)
    assert gateway.calls["positions_get"] > positions_calls
    failed = conn.execute("SELECT occurrence_count FROM runtime_events WHERE event = 'TASK_FAILED'").fetchone()
    assert failed is not None and failed[0] >= 10  # deduplicated into one row, not one per cycle


def test_broker_disconnect_mid_run_blocks_entries_and_is_survivable(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _operator_bootstrap(conn)
    engine.startup()
    from chaos_harness import demo_terminal
    gateway.set_terminal(demo_terminal(connected=False))
    step(engine, clock, seconds=STEP * 2, tick=4)
    assert "BLOCK_MT5_DISCONNECTED" in {r["decision"] for r in _decisions(conn, stage="GLOBAL")}
    gateway.set_terminal(demo_terminal())
    step(engine, clock, seconds=STEP * 2, tick=4)
    assert get_state(conn, "engine")["state"] in ("RUNNING", "DEGRADED")


# ---------------------------------------------------------------------------
# scheduler
# ---------------------------------------------------------------------------

def test_scheduler_runs_due_tasks_in_priority_order_and_isolates_failures():
    now = [0.0]
    order, errors = [], []
    scheduler = Scheduler(monotonic=lambda: now[0], on_error=lambda task, exc: errors.append(task.name))
    scheduler.add("entry", 4, 1, lambda: order.append("entry"))
    scheduler.add("position", 1, 0, lambda: order.append("position"))
    scheduler.add("broken", 1, 0, lambda: 1 / 0)
    scheduler.run_due()
    assert order == ["position", "entry"] and errors == ["broken"]
    now[0] = 1.0
    scheduler.run_due()
    assert order == ["position", "entry", "position"]
    now[0] = 4.0
    scheduler.run_due()
    assert order[-2:] == ["position", "entry"]


def test_startup_clears_a_previous_runs_stale_global_block_diagnostics(tmp_path):
    """BUG_BACKLOG 26: a PAPER run that stopped while blocked must not show
    its kill-switch block as the current DEMO block after a restart."""
    from adaptive_scalper.runtime.state import record_event

    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _operator_bootstrap(conn)
    record_event(conn, "BLOCKED", "entry", "GLOBAL_BLOCK", "BLOCK_KILL_SWITCH: kill switch ENGAGED",
                 dedup_key="paper:global_block", now_utc=START_AT - 86400)
    open_blocks = ("SELECT COUNT(*) FROM runtime_events WHERE dedup_key = 'paper:global_block' "
                   "AND cleared_at_utc IS NULL")
    assert conn.execute(open_blocks).fetchone()[0] == 1
    engine.startup()
    assert conn.execute(open_blocks).fetchone()[0] == 0
    assert kill_switch_state(conn).status.value == "DISENGAGED"  # diagnostics only; the switch is untouched
