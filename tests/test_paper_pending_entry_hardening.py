"""Remaining PR #3 (fix/paper-pending-entry-persistence-20260928) guarantees,
adapted to the modern pending-entry model (flat `PendingEntryState`,
migration 0019 already present, schema 29 unchanged):

- versioned serialization (`state_version`), boolean/unknown versions fail closed;
- typed validation of the OPTIONAL fields (features, versions, symbol, regime);
- a session key cannot be reused for another resolution;
- a pending entry the risk sizer rejects is consumed exactly once;
- a pending entry survives a real reconnect (restart) and fills exactly once.

The other PR #3 invariants already have modern equivalents: chunked == continuous
(`test_incremental_paper_cycles_match_a_single_continuous_run`), first-new-bar fill,
open+pending rejection, gap expiry, rollback (`test_paper_pending_entry_validation.py`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace

import pytest

from adaptive_scalper.backtest.engine import run_backtest
from adaptive_scalper.backtest.types import REJECT_SIZING
from adaptive_scalper.paper.engine import PaperSessionConfigMismatchError, run_paper_cycle
from adaptive_scalper.paper.state import (
    PENDING_ENTRY_STATE_VERSION,
    PaperStateError,
    get_or_create_session,
    get_paper_trades,
    get_session,
    save_session_state,
)
from adaptive_scalper.persistence import connect, migrate
from tests.test_backtest_correctness_regressions import (  # noqa: F401  (pytest fixtures)
    FIRE_INDEX,
    RES,
    START,
    SYMBOL,
    _bars,
    _config,
    _seed,
    _spec,
    db,
    script,
)
from tests.test_paper_pending_entry_validation import CURSOR, KEY, _pending, _write_raw


@pytest.fixture()
def session_db(db):
    get_or_create_session(db, KEY, "XAUUSD", "M5", initial_equity=10_000.0, now_utc=1000)
    return db


# --- versioned serialization -------------------------------------------------

def test_saved_pending_entry_carries_the_state_version(session_db):
    save_session_state(session_db, KEY, equity=10_000.0, last_processed_bar_time_utc=CURSOR, open_position=None,
                       pending_entry=_pending(), now_utc=2000)
    raw = session_db.execute("SELECT pending_entry_json FROM paper_session_state WHERE session_key = ?",
                             (KEY,)).fetchone()[0]
    assert json.loads(raw)["state_version"] == PENDING_ENTRY_STATE_VERSION
    assert get_session(session_db, KEY).pending_entry == _pending()


def test_legacy_unversioned_pending_entry_still_loads(session_db):
    _write_raw(session_db, json.dumps(asdict(_pending())))
    assert get_session(session_db, KEY).pending_entry == _pending()


@pytest.mark.parametrize("version", [True, False, "1", 1.0, 2, 0, None])
def test_unsupported_or_mistyped_state_version_fails_closed(session_db, version):
    _write_raw(session_db, json.dumps({"state_version": version, **asdict(_pending())}))
    with pytest.raises(PaperStateError, match="version"):
        get_session(session_db, KEY)


# --- typed optional fields ---------------------------------------------------

@pytest.mark.parametrize("overrides", [
    {"entry_features": {"return_1": "0.5"}},
    {"entry_features": {"return_1": True}},
    {"entry_features": [0.5]},
    {"strategy_version": True},
    {"strategy_version": 0},
    {"strategy_version": "1"},
    {"canonical_symbol": 5},
    {"canonical_symbol": ""},
    {"regime": None},
    {"expected_duration_seconds": -60},
    {"estimated_cost_price": "0.1"},
])
def test_mistyped_optional_pending_field_fails_closed(session_db, overrides):
    _write_raw(session_db, json.dumps({**asdict(_pending()), **overrides}))
    with pytest.raises(PaperStateError):
        get_session(session_db, KEY)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_entry_feature_fails_closed(session_db, token):
    raw = json.dumps(asdict(_pending())).replace('"return_1": 0.5', f'"return_1": {token}')
    assert token in raw
    _write_raw(session_db, raw)
    with pytest.raises(PaperStateError, match="finite"):
        get_session(session_db, KEY)


def test_save_refuses_a_non_finite_entry_feature_and_writes_nothing(session_db):
    with pytest.raises(PaperStateError, match="finite"):
        save_session_state(session_db, KEY, equity=10_000.0, last_processed_bar_time_utc=CURSOR,
                           open_position=None, pending_entry=_pending(entry_features={"x": float("nan")}),
                           now_utc=2000)
    assert get_session(session_db, KEY).last_processed_bar_time_utc is None


# --- session identity --------------------------------------------------------

def test_a_session_key_cannot_be_reused_for_another_resolution(script, db):
    script()
    bars = _bars()
    run_paper_cycle(db, bars, SYMBOL, RES, _spec(), config=_config(), session_key="shared", now_utc=START)
    with pytest.raises(PaperSessionConfigMismatchError):
        run_paper_cycle(db, bars, SYMBOL, "M1", _spec(), config=_config(), session_key="shared", now_utc=START)


# --- one-shot consumption ----------------------------------------------------

def test_a_pending_entry_rejected_by_risk_sizing_is_consumed_exactly_once(script):
    script()
    bars = _bars()
    lookback = _config().feature_lookback
    first = run_backtest(bars[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=_config(), now_utc=START,
                         force_close_at_range_end=False)
    assert first.pending_entry is not None
    unsizeable = replace(_spec(), volume_min=100.0, volume_max=100.0)  # no volume fits 0.25 % risk
    second = run_backtest(bars[FIRE_INDEX + 1 - lookback - 1:], SYMBOL, RES, unsizeable, config=_config(),
                          now_utc=START, resume_pending_entry=first.pending_entry,
                          resume_regime_tracker=first.final_regime_tracker_state, force_close_at_range_end=False)
    assert [r.reason_code for r in second.entry_rejections] == [REJECT_SIZING]
    assert second.entry_rejections[0].attempted_fill_time_utc == bars[FIRE_INDEX + 1].time
    assert second.open_position is None and second.pending_entry is None and second.trades == ()


# --- restart -----------------------------------------------------------------

def test_a_pending_entry_survives_a_reconnect_and_fills_exactly_once(script, tmp_path):
    script()
    bars = _bars()
    config = _config()
    path = tmp_path / "restart.sqlite3"
    conn = connect(path)
    migrate(conn)
    _seed(conn, bars, config)
    first = run_paper_cycle(conn, bars[:FIRE_INDEX + 1], SYMBOL, RES, _spec(), config=config, now_utc=START)
    assert first.open_position is None
    conn.close()  # process restart: only the durable row survives

    conn = connect(path)
    try:
        assert get_session(conn, first.session_key).pending_entry is not None
        second = run_paper_cycle(conn, bars[:FIRE_INDEX + 3], SYMBOL, RES, _spec(), config=config, now_utc=START)
        assert second.open_position is not None
        assert second.open_position.entry_time_utc == bars[FIRE_INDEX + 1].time
        assert get_session(conn, first.session_key).pending_entry is None

        # The identical call again (e.g. a retried cycle) is a no-op: no second entry.
        third = run_paper_cycle(conn, bars[:FIRE_INDEX + 3], SYMBOL, RES, _spec(), config=config, now_utc=START)
        assert third.open_position == second.open_position
        assert get_paper_trades(conn, first.session_key) == []

        # Run out the rest of the bars: at most ONE trade, from that single signal.
        run_paper_cycle(conn, bars, SYMBOL, RES, _spec(), config=config, now_utc=START)
        entries = [row["entry_time_utc"] for row in get_paper_trades(conn, first.session_key)]
        assert entries in ([], [bars[FIRE_INDEX + 1].time])
    finally:
        conn.close()


def test_restart_after_every_bar_matches_continuous_processing(script, tmp_path):
    """Same bars, same config: one bar per cycle with a reconnect after every
    cycle == one continuous PAPER session."""
    script()
    bars = _bars()
    config = _config()

    def run(path, stops):
        conn = connect(path)
        migrate(conn)
        _seed(conn, bars, config)
        conn.close()
        for stop in stops:
            conn = connect(path)
            run_paper_cycle(conn, bars[:stop], SYMBOL, RES, _spec(), config=config, now_utc=START)
            conn.close()
        conn = connect(path)
        try:
            session = get_session(conn, f"PAPER:{SYMBOL}:{RES}")
            trades = [
                (r["entry_time_utc"], r["entry_price"], r["exit_time_utc"], r["exit_price"], r["exit_reason"],
                 r["realized_pnl"])
                for r in get_paper_trades(conn, session.session_key)
            ]
            return session.equity, session.open_position, session.pending_entry, trades
        finally:
            conn.close()

    lookback = config.feature_lookback
    continuous = run(tmp_path / "continuous.sqlite3", [len(bars)])
    restarted = run(tmp_path / "restarted.sqlite3", list(range(lookback + 3, len(bars) + 1)))
    assert restarted == continuous
    assert continuous[1] is not None or continuous[3], "the scripted signal must actually trade"
