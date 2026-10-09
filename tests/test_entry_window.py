"""Operator entry-hours window (config `[entry_window]`, risk/entry_window.py).

Shipped policy (2026-10-09): NEW entries only during the London/New York
overlap [12:00, 20:00) UTC, every symbol, PAPER and DEMO. The window only
ever blocks entries; disabled (the code default) it changes nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from adaptive_scalper.config.loader import EntryWindowConfig, load_config
from adaptive_scalper.core.final_permission import ALLOW, evaluate_final_permission
from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.risk.entry_window import BLOCK_SESSION, entry_window_block
from runtime_helpers import STEP, T0, FakeClock, build_engine, step
from test_final_permission import _full_allow_input

DAY = 1_750_032_000  # 2025-06-16 00:00:00 UTC (a Monday)
LONDON_NY = (12, 20)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _at(hh: int, mm: int = 0, ss: int = 0) -> int:
    return DAY + hh * 3600 + mm * 60 + ss


# --------------------------------------------------------------------------
# the pure rule
# --------------------------------------------------------------------------

@pytest.mark.parametrize("now", [_at(12), _at(15, 30), _at(19, 59, 59)])
def test_inside_the_window_allows(now):
    assert entry_window_block(now, LONDON_NY) is None


@pytest.mark.parametrize("now", [_at(0), _at(3, 15), _at(7), _at(11, 59, 59), _at(20), _at(23, 59, 59)])
def test_outside_the_window_blocks_including_asian_hours(now):
    decision, reason = entry_window_block(now, LONDON_NY)
    assert decision == BLOCK_SESSION
    assert "outside the entry window [12:00, 20:00) UTC" in reason


def test_window_is_half_open_and_repeats_every_day():
    for day in range(7):
        base = 86_400 * day
        assert entry_window_block(_at(11, 59, 59) + base, LONDON_NY) is not None
        assert entry_window_block(_at(12) + base, LONDON_NY) is None
        assert entry_window_block(_at(19, 59, 59) + base, LONDON_NY) is None
        assert entry_window_block(_at(20) + base, LONDON_NY) is not None


def test_no_window_configured_never_blocks():
    assert entry_window_block(_at(3), None) is None
    assert entry_window_block(None, None) is None


def test_unknown_time_with_a_window_blocks():
    decision, reason = entry_window_block(None, LONDON_NY)
    assert decision == BLOCK_SESSION
    assert "time is unknown" in reason


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def test_code_default_is_disabled():
    assert EntryWindowConfig().hours() is None


def test_shipped_config_restricts_entries_to_london_new_york_overlap():
    config = load_config(REPO_ROOT / "config" / "default.toml")
    assert config.entry_window.enabled is True
    assert config.entry_window.hours() == LONDON_NY


@pytest.mark.parametrize("start,end", [(20, 12), (12, 12), (-1, 20), (12, 25)])
def test_invalid_windows_are_rejected(start, end):
    with pytest.raises(ValidationError):
        EntryWindowConfig(enabled=True, start_hour_utc=start, end_hour_utc=end)


# --------------------------------------------------------------------------
# final permission (re-evaluated fresh before every order_send)
# --------------------------------------------------------------------------

def test_final_permission_blocks_outside_the_window():
    result = evaluate_final_permission(_full_allow_input(now_utc=_at(3), entry_window_hours=LONDON_NY))
    assert result.decision == BLOCK_SESSION


def test_final_permission_allows_inside_the_window():
    result = evaluate_final_permission(_full_allow_input(now_utc=_at(14), entry_window_hours=LONDON_NY))
    assert result.decision == ALLOW, result.reason


def test_final_permission_without_a_window_is_unchanged():
    assert evaluate_final_permission(_full_allow_input(now_utc=_at(3))).decision == ALLOW


def test_final_permission_blocks_a_window_with_unknown_time():
    result = evaluate_final_permission(_full_allow_input(now_utc=None, entry_window_hours=LONDON_NY))
    assert result.decision == BLOCK_SESSION


# --------------------------------------------------------------------------
# runtimes: the global entry block
# --------------------------------------------------------------------------

START_AT = T0 + 60 * STEP + 10  # 20:10:10 UTC with the runtime helpers' T0


def _with_window(runtime, start: int, end: int) -> None:
    runtime.config = runtime.config.model_copy(update={
        "entry_window": EntryWindowConfig(enabled=True, start_hour_utc=start, end_hour_utc=end)})


def _started(tmp_path, mode: str):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode=mode, clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    return clock, engine, conn, gateway


def test_start_at_is_outside_the_shipped_window():
    assert entry_window_block(START_AT, LONDON_NY) is not None


def test_demo_global_block_outside_the_window_and_no_order_sent(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path, "DEMO")
    _with_window(engine.demo, *LONDON_NY)

    step(engine, clock, seconds=STEP * 3, tick=4)

    rows = conn.execute("SELECT reason FROM entry_decisions WHERE stage = 'GLOBAL' AND decision = ?",
                        (BLOCK_SESSION,)).fetchall()
    assert rows and all("outside the entry window" in r["reason"] for r in rows)
    assert gateway.calls["order_send"] == 0


def test_demo_global_block_lifts_inside_the_window(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path, "DEMO")
    _with_window(engine.demo, 20, 21)
    assert engine.demo.global_entry_block(int(clock.now)) is None


def test_paper_global_block_outside_the_window(tmp_path):
    clock, engine, conn, gateway = _started(tmp_path, "PAPER")
    _with_window(engine.paper, *LONDON_NY)
    decision, _ = engine.paper.global_entry_block(int(clock.now))
    assert decision == BLOCK_SESSION

    _with_window(engine.paper, 20, 21)
    assert engine.paper.global_entry_block(int(clock.now)) is None
