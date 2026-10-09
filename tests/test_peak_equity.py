"""Account-bound, append-only drawdown baseline (ASN-034, migration 0033,
risk/peak_equity.py).

Regression tests for the 2026-10-09 audit findings: the DEMO peak was one
unbound runtime_state value, written only when the global entry block
reached the drawdown step (never during BLOCK_SESSION, news, the kill
switch or the daily-loss lock), with no history and no sanctioned reset;
and an unknown peak (<= 0) silently skipped the drawdown check."""

from __future__ import annotations

import math
import sqlite3
from types import SimpleNamespace

import pytest

from adaptive_scalper.config.loader import EntryWindowConfig
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.kill_switch import engage as engage_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.core import peak_equity_reset as reset_mod
from adaptive_scalper.persistence.database import connect, migrate
from adaptive_scalper.risk import peak_equity as pe
from adaptive_scalper.risk.governor import BLOCK_RISK, RiskGateInput, RiskLimits, evaluate_risk_gate
from adaptive_scalper.risk.entry_window import BLOCK_SESSION
from adaptive_scalper.runtime.state import get_state, put_state
from chaos_harness import demo_account
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

LOGIN, SERVER = 1234567, "Example-Demo"
NOW = 1_791_000_000


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "peak.sqlite3")
    migrate(c)
    yield c
    c.close()


def _observe(conn, equity, *, login=LOGIN, server=SERVER, now=NOW):
    return pe.observe_equity(conn, login=login, server=server, equity=equity, now_utc=now)


def _rows(conn):
    return [dict(r) for r in pe.history(conn)]


# --------------------------------------------------------------------------
# initialization, persistence, raising
# --------------------------------------------------------------------------

def test_first_observation_on_an_empty_database_initializes_the_baseline(conn):
    status = _observe(conn, 10000.0)
    assert (status.peak_equity, status.blocked, status.appended_event) == (10000.0, False, pe.EVENT_INITIALIZED)
    [row] = _rows(conn)
    assert (row["account_login"], row["account_server"], row["event_type"]) == (LOGIN, SERVER, "INITIALIZED")
    assert get_state(conn, pe.LEGACY_STATE_KEY) == 10000.0      # mirrored for the dashboard


def test_a_new_high_appends_one_raised_row_and_a_lower_equity_appends_nothing(conn):
    _observe(conn, 10000.0)
    raised = _observe(conn, 10125.5, now=NOW + 1)
    assert (raised.peak_equity, raised.appended_event) == (10125.5, pe.EVENT_RAISED)
    lower = _observe(conn, 9900.0, now=NOW + 2)
    assert (lower.peak_equity, lower.appended_event) == (10125.5, None)
    rows = _rows(conn)
    assert [r["event_type"] for r in rows] == ["INITIALIZED", "RAISED"]
    assert rows[1]["previous_peak_equity"] == 10000.0 and rows[1]["observed_equity"] == 10125.5


def test_the_baseline_persists_across_a_fresh_connection(tmp_path):
    path = tmp_path / "persist.sqlite3"
    c = connect(path)
    migrate(c)
    pe.observe_equity(c, login=LOGIN, server=SERVER, equity=10200.0, now_utc=NOW)
    c.close()
    c = connect(path)
    status = pe.observe_equity(c, login=LOGIN, server=SERVER, equity=9000.0, now_utc=NOW + 60)
    assert status.peak_equity == 10200.0
    assert pe.drawdown_pct(status.peak_equity, 9000.0) == pytest.approx(11.7647, abs=1e-4)
    c.close()


def test_the_pre_0033_unbound_value_is_adopted_once_and_never_below_equity(conn):
    put_state(conn, pe.LEGACY_STATE_KEY, 9759.63, now_utc=NOW - 10)
    status = _observe(conn, 9252.63)
    assert (status.peak_equity, status.appended_event) == (9759.63, pe.EVENT_LEGACY_ADOPTED)
    assert pe.drawdown_pct(9759.63, 9252.63) == pytest.approx(5.19, abs=0.005)   # the production figure
    [row] = _rows(conn)
    assert row["previous_peak_equity"] == 9759.63


def test_a_legacy_value_below_current_equity_is_raised_to_equity(conn):
    put_state(conn, pe.LEGACY_STATE_KEY, 9000.0, now_utc=NOW - 10)
    assert _observe(conn, 9500.0).peak_equity == 9500.0


# --------------------------------------------------------------------------
# account binding (fail closed)
# --------------------------------------------------------------------------

def test_an_account_change_never_creates_a_baseline(conn):
    _observe(conn, 10000.0)
    status = _observe(conn, 50000.0, login=7654321, now=NOW + 5)
    assert status.blocked and status.peak_equity is None
    assert "an account change never creates a baseline automatically" in status.blocked_reason
    assert "7654321" not in status.blocked_reason and "***321" in status.blocked_reason   # login masked
    assert len(_rows(conn)) == 1
    events = conn.execute("SELECT event FROM runtime_events WHERE event = 'PEAK_EQUITY_ACCOUNT_UNBOUND'").fetchall()
    assert len(events) == 1


def test_the_same_login_on_another_server_is_another_account(conn):
    _observe(conn, 10000.0)
    assert _observe(conn, 10000.0, server="Other-Demo").blocked


def test_a_legacy_value_is_not_adopted_for_a_second_account(conn):
    put_state(conn, pe.LEGACY_STATE_KEY, 9759.63, now_utc=NOW - 10)
    _observe(conn, 9252.63)
    assert _observe(conn, 9252.63, login=999999).blocked


@pytest.mark.parametrize("login,server,equity", [
    (0, SERVER, 10000.0), (None, SERVER, 10000.0), (True, SERVER, 10000.0), (LOGIN, "", 10000.0),
    (LOGIN, None, 10000.0), (LOGIN, SERVER, 0.0), (LOGIN, SERVER, -1.0), (LOGIN, SERVER, math.nan),
    (LOGIN, SERVER, math.inf), (LOGIN, SERVER, None),
])
def test_an_unusable_identity_or_equity_blocks_and_writes_nothing(conn, login, server, equity):
    status = pe.observe_equity(conn, login=login, server=server, equity=equity, now_utc=NOW)
    assert status.blocked and status.peak_equity is None
    assert _rows(conn) == []


# --------------------------------------------------------------------------
# append-only storage
# --------------------------------------------------------------------------

def test_history_rows_cannot_be_updated_or_deleted(conn):
    _observe(conn, 10000.0)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE peak_equity_history SET peak_equity = 1.0")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM peak_equity_history")
    assert _rows(conn)[0]["peak_equity"] == 10000.0


def test_an_operator_reset_row_without_an_evidence_hash_is_rejected_by_the_schema(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO peak_equity_history (account_login, account_server, event_type, peak_equity, actor, "
                     "reason, recorded_at_utc) VALUES (1, 'S', 'OPERATOR_RESET', 1.0, 'op', 'r', 1)")


# --------------------------------------------------------------------------
# audited operator reset
# --------------------------------------------------------------------------

@pytest.fixture
def evidence(tmp_path):
    path = tmp_path / "mt5_account_history_export.html"
    path.write_text("synthetic broker statement used only by this test", encoding="utf-8")
    return path


def _engaged(conn):
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engage_kill_switch(conn, "baseline review", actor="test-operator")


def _reset(conn, evidence, **overrides):
    kwargs = dict(login=LOGIN, server=SERVER, new_peak=9300.0, authority=OperatorAuthority("test-operator"),
                  evidence_path=evidence,
                  reason="withdrawal of 450.00 confirmed in the broker statement", acknowledge_lower=True,
                  now_utc=NOW + 100)
    kwargs.update(overrides)
    return reset_mod.operator_reset(conn, **kwargs)


def test_reset_is_refused_unless_the_kill_switch_is_engaged(conn, evidence):
    _observe(conn, 10000.0)
    with pytest.raises(reset_mod.PeakEquityResetRefused, match="kill switch must be ENGAGED"):
        _reset(conn, evidence)                                   # UNINITIALIZED
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    with pytest.raises(reset_mod.PeakEquityResetRefused, match="kill switch must be ENGAGED"):
        _reset(conn, evidence)                                   # DISENGAGED
    assert len(_rows(conn)) == 1


@pytest.mark.parametrize("overrides,match", [
    (dict(reason="too short"), "reason of at least"),
    (dict(reason="   "), "reason of at least"),
    (dict(authority="test-operator"), "OperatorAuthority is required"),
    (dict(authority=None), "OperatorAuthority is required"),
    (dict(new_peak=math.nan), "positive finite"),
    (dict(new_peak=0.0), "positive finite"),
    (dict(login=0), "login must be"),
    (dict(server=""), "server is required"),
    (dict(acknowledge_lower=False), "explicit acknowledgement"),
])
def test_reset_preconditions_refuse_and_write_nothing(conn, evidence, overrides, match):
    _observe(conn, 10000.0)
    _engaged(conn)
    with pytest.raises(reset_mod.PeakEquityResetRefused, match=match):
        _reset(conn, evidence, **overrides)
    assert len(_rows(conn)) == 1


def test_reset_needs_an_existing_evidence_file(conn, tmp_path):
    _observe(conn, 10000.0)
    _engaged(conn)
    with pytest.raises(reset_mod.PeakEquityResetRefused, match="evidence file not found"):
        _reset(conn, tmp_path / "missing.html")


def test_reset_needs_a_flat_book(conn, evidence, monkeypatch):
    import adaptive_scalper.execution.reconciliation as reconciliation

    _observe(conn, 10000.0)
    _engaged(conn)
    monkeypatch.setattr(reconciliation, "get_open_positions", lambda c: [SimpleNamespace(id=1)])
    with pytest.raises(reset_mod.PeakEquityResetRefused, match="open positions"):
        _reset(conn, evidence)


def test_an_accepted_reset_appends_an_audited_row_and_keeps_history(conn, evidence):
    _observe(conn, 10000.0)
    _observe(conn, 10100.0, now=NOW + 1)
    _engaged(conn)
    row = _reset(conn, evidence)
    rows = _rows(conn)
    assert [r["event_type"] for r in rows] == ["INITIALIZED", "RAISED", "OPERATOR_RESET"]
    assert rows[0]["peak_equity"] == 10000.0 and rows[1]["peak_equity"] == 10100.0     # history kept
    assert (row["peak_equity"], row["previous_peak_equity"], row["actor"]) == (9300.0, 10100.0, "test-operator")
    assert row["evidence_sha256"] == reset_mod.evidence_sha256(evidence) and len(row["evidence_sha256"]) == 64
    assert row["evidence_path"].endswith("mt5_account_history_export.html")
    assert _observe(conn, 9252.63, now=NOW + 200).peak_equity == 9300.0
    assert conn.execute("SELECT COUNT(*) FROM runtime_events WHERE event = 'PEAK_EQUITY_OPERATOR_RESET'") \
        .fetchone()[0] == 1


def test_a_reset_can_bind_a_baseline_to_a_new_account(conn, evidence):
    _observe(conn, 10000.0)
    assert _observe(conn, 5000.0, login=7654321).blocked
    _engaged(conn)
    _reset(conn, evidence, login=7654321, new_peak=5000.0, acknowledge_lower=False)
    assert _observe(conn, 5000.0, login=7654321, now=NOW + 300).peak_equity == 5000.0


def test_there_is_no_automatic_reset_path():
    """Only core/peak_equity_reset.py appends OPERATOR_RESET, and only the
    operator CLI calls it -- the runtime, risk, learning, research never do."""
    from pathlib import Path

    root = Path(pe.__file__).resolve().parents[1]
    callers = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if "operator_reset(" in p.read_text(encoding="utf-8"))
    assert callers == ["cli/operator.py", "core/peak_equity_reset.py"]
    writers = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.py")
                     if "EVENT_OPERATOR_RESET" in p.read_text(encoding="utf-8"))
    assert writers == ["core/peak_equity_reset.py", "risk/peak_equity.py"]


# --------------------------------------------------------------------------
# governor: an unknown baseline blocks (it used to skip the check)
# --------------------------------------------------------------------------

def _gate_input(peak):
    return RiskGateInput(
        proposed_symbol="XAUUSD", proposed_monetary_risk=10.0, equity=10000.0, current_total_open_risk=0.0,
        current_total_pending_risk=0.0, current_positions_count=0, current_positions_for_symbol=0,
        daily_realized_pnl=0.0, peak_equity=peak,
    )


LIMITS = RiskLimits(risk_per_trade_pct=0.25, max_total_open_risk_pct=0.75, max_daily_loss_pct=2.0,
                    max_drawdown_pct=5.0, max_open_positions=2, max_positions_per_symbol=1)


@pytest.mark.parametrize("peak", [math.nan, math.inf, -math.inf, 0.0, -100.0])
def test_risk_gate_blocks_an_unknown_or_invalid_peak(peak):
    decision, reason = evaluate_risk_gate(_gate_input(peak), LIMITS)
    assert decision == BLOCK_RISK
    assert "drawdown baseline" in reason


def test_risk_gate_still_allows_a_valid_peak_within_the_limit():
    assert evaluate_risk_gate(_gate_input(10400.0), LIMITS)[0] == "ALLOW"     # 3.85 % < 5 %
    assert evaluate_risk_gate(_gate_input(10600.0), LIMITS)[0] == BLOCK_RISK  # 5.66 % >= 5 %


# --------------------------------------------------------------------------
# DEMO runtime wiring
# --------------------------------------------------------------------------

START_AT = T0 + 60 * STEP + 10   # 20:10:10 UTC: outside [12:00, 20:00)


def _started(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def test_the_position_cycle_raises_the_peak_while_entries_are_blocked_by_the_session(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    engine.demo.config = engine.demo.config.model_copy(update={
        "entry_window": EntryWindowConfig(enabled=True, start_hour_utc=12, end_hour_utc=20)})
    step(engine, clock, seconds=3)
    gateway.set_account(demo_account(balance=10000.0, equity=10500.0))   # floating gain after 20:00 UTC
    step(engine, clock, seconds=3)

    assert engine.demo.global_entry_block(int(clock.now))[0] == BLOCK_SESSION
    account = gateway.account_info()
    assert pe.current_peak(conn, account.login, account.server)["peak_equity"] == 10500.0
    assert [r["event_type"] for r in pe.history(conn)][-1] == "RAISED"
    assert gateway.calls["order_send"] == 0


def test_demo_global_block_reports_the_drawdown_against_the_bound_baseline(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    step(engine, clock, seconds=3)                                        # baseline 10000
    gateway.set_account(demo_account(balance=9400.0, equity=9400.0))
    step(engine, clock, seconds=3)
    decision, reason = engine.demo.global_entry_block(int(clock.now))
    assert decision == BLOCK_RISK and "drawdown 6.00% reached the limit" in reason


def test_demo_blocks_entries_after_an_account_change(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path)
    step(engine, clock, seconds=3)
    gateway.set_account(demo_account(login=987654, balance=20000.0, equity=20000.0))
    step(engine, clock, seconds=3)
    decision, reason = engine.demo.global_entry_block(int(clock.now))
    assert decision == BLOCK_RISK and "drawdown baseline unavailable" in reason
    assert math.isnan(engine.demo._risk_gate_peak(gateway.account_info(), int(clock.now)))
    assert gateway.calls["order_send"] == 0
