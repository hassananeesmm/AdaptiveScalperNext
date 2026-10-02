"""H8 (docs/research/V2_H8_PREREGISTRATION_2026-09-30.md): causal M15 context,
deterministic M5 pullback, M1 resumption, cost gate, engine fill/risk path,
event fingerprints (consumed once, durable across restarts), data gate,
statistics and classification. Synthetic bars only, inside the development
window; nothing here reads a database of market data."""

from __future__ import annotations

import ast
import math
import sqlite3
from pathlib import Path

import pytest

from adaptive_scalper.backtest.reserved_oos import RESERVED_OOS_INTERVALS, ReservedOosOverlapError
from adaptive_scalper.backtest.types import BacktestConfig
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.research.v2 import h8
from adaptive_scalper.simulation.fill_model import COST_EXPLICIT_TEST_FIXTURE, FillAssumptions
from sim_helpers import spec

ROOT = Path(__file__).resolve().parents[1]
START = 1717372800          # 2024-06-03 00:00 UTC (inside the development window)
SPREAD_PTS = 10             # 0.10
SLIP = 0.05


def cfg(**overrides) -> BacktestConfig:
    base = dict(fill_assumptions=FillAssumptions(slippage_price=SLIP, commission_monetary_per_lot=0.0,
                                                  provenance=COST_EXPLICIT_TEST_FIXTURE))
    base.update(overrides)
    return BacktestConfig(**base)


# ---------------------------------------------------------------- bar builder

def path(segments, *, warmup=360, base=2000.0):
    """Minute closes: an oscillating warm-up around `base`, then linear
    segments [(minutes, end_price), ...]."""
    prices = [base + 0.3 * math.sin(2 * math.pi * m / 37) for m in range(warmup)]
    for minutes, end in segments:
        start = prices[-1]
        prices += [start + (end - start) * (i + 1) / minutes for i in range(minutes)]
    return prices


def m1_bars(prices, *, start=START, spreads=None, mirror=False):
    spreads = spreads or {}
    if mirror:
        prices = [4000.0 - p for p in prices]
    out, prev = [], prices[0]
    for i, close in enumerate(prices):
        o = prev
        out.append(Bar(time=start + 60 * i, open=o, high=max(o, close) + 0.05, low=min(o, close) - 0.05,
                       close=close, tick_volume=10, spread=spreads.get(i, SPREAD_PTS), real_volume=0))
        prev = close
    return out


def aggregate(m1, seconds):
    groups: dict = {}
    for b in m1:
        groups.setdefault(b.time - (b.time - START) % seconds, []).append(b)
    return [Bar(time=t, open=g[0].open, high=max(x.high for x in g), low=min(x.low for x in g), close=g[-1].close,
                tick_volume=sum(x.tick_volume for x in g), spread=g[-1].spread, real_volume=0)
            for t, g in sorted(groups.items()) if len(g) == seconds // 60]


def series(prices, **kw):
    m1 = m1_bars(prices, **kw)
    return aggregate(m1, 900), aggregate(m1, 300), m1


# breakout rally, extreme, pullback of 6, resumption to well above the 2R target
LONG = [(30, 2010.0), (30, 2012.0), (30, 2006.0), (50, 2021.0), (60, 2021.0)]


def run(prices=None, *, config=None, store=None, **kw):
    m15, m5, m1 = series(prices if prices is not None else path(LONG), **kw)
    return h8.run_h8_fold(m15, m5, m1, spec(), config or cfg(), fold=0, store=store)


def kinds(result, kind):
    return [e for e in result["events"] if e[0] == kind]


# ------------------------------------------------------ M15 context (Donchian N20)

def _m15(i, high, low, close):
    return Bar(time=START + 900 * i, open=close, high=high, low=low, close=close, tick_volume=1, spread=10,
               real_volume=0)


def test_breakout_uses_exactly_the_previous_20_completed_m15_bars():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    engine.on_m15(_m15(0, 2050.0, 1999.0, 2000.0))           # 21 bars back: must NOT be in the channel
    for i in range(1, 21):
        engine.on_m15(_m15(i, 2001.0 + (i == 7) * 0.5, 1999.0, 2000.0))
    engine.on_m15(_m15(21, 2001.6, 2000.0, 2001.5))          # == channel high 2001.5: not a breakout
    assert engine.context is None
    engine.on_m15(_m15(22, 2001.7, 2000.0, 2001.51))         # channel now bars 2..21 (high 2001.6)
    assert engine.context is None
    engine.on_m15(_m15(23, 2002.0, 2001.0, 2001.71))         # channel bars 3..22: high 2001.7
    assert engine.context is not None and engine.context.direction == "BUY"
    assert engine.context.level == pytest.approx(2001.7)
    assert engine.context.breakout_time == START + 900 * 23 + 900    # completion time, never the open
    assert engine.context.event_id == f"XAUUSD:M15:BUY:{START + 900 * 23}"


def test_no_breakout_before_20_completed_bars():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    for i in range(20):
        engine.on_m15(_m15(i, 2000.0 + i, 1999.0, 2000.0 + i))   # rising, but the window is never full
    assert engine.context is None


def test_completion_stream_never_shows_an_unfinished_m15_bar():
    m15, m5, m1 = series(path(LONG))
    seen_m15 = []
    for done, _, kind, bar in h8.completion_stream(m15, m5, m1):
        if kind == "M15":
            seen_m15.append(bar.time + 900)
        else:
            assert all(t <= done for t in seen_m15)               # nothing completes in the future


def test_every_decision_follows_its_breakout_and_its_pullback_confirmation():
    result = run()
    assert result["trades"]
    breakouts = {e[2]: e[1] for e in kinds(result, "BREAKOUT")}
    pullbacks = {e[2]: e[1] for e in kinds(result, "PULLBACK")}
    for t in result["trades"]:
        d = t.decision
        assert d.decided_at_utc > pullbacks[d.pullback_id] > breakouts[d.breakout_event_id]


# --------------------------------------------------------------- symmetry

def test_long_and_short_are_mirror_images():
    up, down = run(), run(mirror=True)
    assert [t.decision.direction for t in up["trades"]] == ["BUY"] * len(up["trades"])
    assert [t.decision.direction for t in down["trades"]] == ["SELL"] * len(down["trades"])
    assert len(up["trades"]) == len(down["trades"]) >= 1
    for a, b in zip(up["trades"], down["trades"]):
        assert a.trade.entry_time_utc == b.trade.entry_time_utc
        assert a.trade.exit_reason == b.trade.exit_reason
        assert a.stop["distance"] == pytest.approx(b.stop["distance"])
        assert a.trade.realized_pnl == pytest.approx(b.trade.realized_pnl)


# --------------------------------------------------------------- pullback

def test_pullback_is_detected_with_its_id_and_extreme():
    result = run()
    [pb] = kinds(result, "PULLBACK")[:1]
    assert ":PB:" in pb[2] and pb[3] >= h8.PULLBACK_MIN_ATR * pb[4]


def test_a_shallow_pullback_below_half_an_atr_is_rejected():
    shallow = path([(30, 2010.0), (30, 2012.0), (3, 2011.7), (50, 2026.0)])
    result = run(shallow)
    assert kinds(result, "BREAKOUT") and not kinds(result, "PULLBACK")
    assert result["decisions"] == 0 and not result["trades"]


def test_a_pullback_that_closes_back_through_the_breakout_level_invalidates_the_context():
    deep = path([(30, 2010.0), (30, 2012.0), (40, 1999.0), (50, 2015.0)])
    result = run(deep)
    assert any("breakout level" in e[3] for e in kinds(result, "INVALIDATED"))
    assert not any(t.decision.breakout_event_id == kinds(result, "BREAKOUT")[0][2] for t in result["trades"])


# ------------------------------------------------- M1 trigger, causal fill, cost

def test_the_trigger_is_an_m1_close_above_the_previous_3_highs_and_fills_at_the_next_open():
    m15, m5, m1 = series(path(LONG))
    result = h8.run_h8_fold(m15, m5, m1, spec(), cfg(), fold=0)
    t = result["trades"][0]
    k = m1.index(t.decision.bar)
    assert t.decision.bar.close > max(b.high for b in m1[k - 3:k])
    assert not t.decision.bar.close > max(b.high for b in m1[k - 4:k - 1]) or \
        m1[k - 1].time + 60 <= [e for e in result["events"] if e[0] == "PULLBACK"][0][1]
    nxt = m1[k + 1]
    assert t.decision.decided_at_utc == t.decision.bar.time + 60                    # the trigger bar CLOSE
    assert t.trade.signal_time_utc == t.decision.decided_at_utc and t.trade.entry_time_utc == nxt.time
    assert t.trade.entry_time_utc >= t.decision.decided_at_utc
    assert t.trade.entry_price == pytest.approx(nxt.open + SPREAD_PTS * 0.01 / 2 + SLIP)   # never the signal price


def test_round_trip_cost_includes_both_slippages_and_the_margin():
    bar = m1_bars([2000.0])[0]
    rt = h8.round_trip_cost_price(bar, spec(), cfg())
    assert rt == pytest.approx((0.10 + 2 * SLIP) * (1 + cfg().uncertainty_margin_pct))


def test_unknown_cost_is_rejected_and_consumes_the_fingerprint():
    prices = path(LONG)
    clean = run(prices)
    k = (clean["trades"][0].decision.bar.time - START) // 60
    store = h8.FingerprintStore()
    result = run(prices, spreads={k: 0}, store=store)
    assert [r["reason"] for r in result["rejected"]][:1] == [h8.REJECT_COST_UNKNOWN]
    assert store.is_consumed(clean["trades"][0].decision.fingerprint)
    assert all(t.decision.pullback_id != clean["trades"][0].decision.pullback_id for t in result["trades"])


def test_high_cost_widens_the_stop_to_the_friction_floor_never_below():
    t = run(spreads={i: 40 for i in range(2000)})["trades"][0]
    assert t.decision_stop["binding"] == h8.FRICTION and t.cost_r == pytest.approx(h8.MAX_COST_R)
    assert t.fill_cost_r <= h8.MAX_COST_R
    assert t.stop["distance"] >= t.stop["structural"] and t.stop["distance"] >= t.stop["volatility"]


def test_a_stop_too_wide_to_size_is_refused_never_rounded_up():
    result = run(spreads={i: 150 for i in range(2000)})
    assert not result["trades"] and result["rejected"][0]["reason"] == "BLOCK_RISK_SIZING"


def test_a_stop_below_the_friction_floor_is_rejected_by_the_cost_gate(monkeypatch):
    monkeypatch.setattr(h8, "stop_distance", lambda d, s, rt: {"distance": rt / 0.06, "binding": "structural",
                                                               "structural": 0, "volatility": 0, "friction": 0})
    result = run()
    assert not result["trades"] and result["rejected"][0]["reason"] == h8.REJECT_COST_R


def test_unknown_or_zero_slippage_assumption_is_unknown_cost():
    bar = m1_bars([2000.0])[0]
    zero = cfg(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0,
                                                provenance=COST_EXPLICIT_TEST_FIXTURE))
    assert h8.round_trip_cost_price(bar, spec(), zero) is None


# ------------------------------------------------------- sizing, risk, exits

def test_sizing_respects_the_per_trade_risk_ceiling_and_risk_limits_are_unchanged():
    c = cfg()
    limits = c.risk_limits
    assert (limits.risk_per_trade_pct, limits.max_total_open_risk_pct, limits.max_daily_loss_pct,
            limits.max_drawdown_pct) == (0.25, 0.75, 2.0, 5.0)
    for t in run()["trades"]:
        assert 0 < t.trade.initial_monetary_risk <= t.equity_at_entry * 0.0025 * 1.0001
    with pytest.raises(ValueError):
        BacktestConfig(risk_per_trade_pct=0.5)


def test_target_is_2r_and_a_winner_exits_at_the_target():
    t = run()["trades"][0]
    assert t.trade.exit_reason == "TAKE_PROFIT_HIT"
    assert abs(t.trade.exit_price - t.trade.entry_price) == pytest.approx(h8.TARGET_R * t.stop["distance"])


def test_time_stop_closes_at_the_first_open_after_45_minutes():
    flat_after = path([(30, 2010.0), (30, 2012.0), (30, 2006.0), (4, 2007.2), (120, 2007.4)])
    t = run(flat_after)["trades"][0]
    assert t.trade.exit_reason == h8.TIME_STOP_REASON
    assert t.trade.exit_time_utc == t.trade.entry_time_utc + h8.TIME_STOP_SECONDS


def test_only_one_xauusd_position_and_a_new_pullback_can_trade_after_it():
    # trade 1 stays open (no target, no stop) while a second pullback triggers -> POSITION_OPEN
    busy = path([(30, 2010.0), (30, 2012.0), (30, 2006.0), (6, 2008.0), (8, 2013.5), (12, 2009.5), (6, 2011.5),
                 (90, 2011.5)])
    result = run(busy)
    assert h8.REJECT_POSITION_OPEN in [r["reason"] for r in result["rejected"]]
    assert not h8.safety_violations(result["trades"], cfg())
    # after a closed trade, a NEW pullback (new id) trades again
    again = run(path(LONG + [(20, 2016.0), (30, 2030.0)]))
    ids = [t.decision.pullback_id for t in again["trades"]]
    assert len(ids) >= 2 and len(set(ids)) == len(ids)
    assert len({t.decision.breakout_event_id for t in again["trades"]}) == 1      # same episode, several scalps


def test_news_window_blocks_the_fill():
    prices = path(LONG)
    news = cfg(news_windows=((START, START + 60 * len(prices)),))
    result = run(prices, config=news)
    assert not result["trades"] and any("NEWS" in r["reason"] for r in result["rejected"])


def test_risk_halt_blocks_new_decisions(monkeypatch):
    monkeypatch.setattr(h8, "_risk_halt_reason", lambda *a: "daily loss limit")
    result = run()
    assert not result["trades"] and result["rejected"][0]["reason"] == h8.REJECT_RISK_HALT


# ---------------------------------------------------------- fingerprints

def test_a_consumed_fingerprint_never_retriggers_in_the_same_run():
    result = run(path(LONG + [(20, 2016.0), (30, 2030.0)]))
    fps = [t.decision.fingerprint for t in result["trades"]] + [r["fingerprint"] for r in result["rejected"]]
    assert len(fps) == len(set(fps))


def test_restart_preserves_consumed_state(tmp_path):
    db = tmp_path / "h8_shadow.sqlite3"
    first = run(store=h8.SqliteFingerprintStore(sqlite3.connect(db)))
    assert first["trades"]
    second = run(store=h8.SqliteFingerprintStore(sqlite3.connect(db)))        # a fresh process, same store
    assert second["decisions"] == 0 and not second["trades"]


def test_sqlite_store_consume_is_idempotent(tmp_path):
    store = h8.SqliteFingerprintStore(sqlite3.connect(tmp_path / "s.sqlite3"))
    assert store.consume("fp", "DECIDED", 1) is True
    assert store.consume("fp", "DECIDED", 2) is False and store.is_consumed("fp")


# ----------------------------------------------------- data safety and gate

def test_reserved_oos_and_out_of_window_ranges_are_refused():
    oos_start, _ = RESERVED_OOS_INTERVALS[0]
    with pytest.raises(ReservedOosOverlapError):
        h8.assert_development_range(oos_start - 10, oos_start + 10)
    with pytest.raises(ValueError):
        h8.assert_development_range(h8.DEV_START_UTC - 60, h8.DEV_START_UTC + 60)   # the consumed H7 holdout
    with pytest.raises(ValueError):
        run(start=h8.DEV_START_UTC - 86_400)


def test_coverage_gate_refuses_missing_lower_resolutions():
    m15, m5, m1 = series(path(LONG))
    ranges = [(0, len(m15) // 2), (len(m15) // 2, len(m15))]
    assert h8.coverage_gate(m15, m5, m1, ranges)["passes"] is True
    late_m1 = [b for b in m1 if b.time >= m15[len(m15) // 2].time]
    gate = h8.coverage_gate(m15, m5, late_m1, ranges)
    assert gate["passes"] is False and gate["folds"][0]["passes"] is False


# ------------------------------------------------------------- statistics

def test_cluster_bootstrap_resamples_whole_clusters_deterministically():
    values = [1.0, 1.0, 1.0, -1.0]
    clusters = ["a", "a", "a", "b"]
    ci = h8.cluster_bootstrap_ci(values, clusters, resamples=2000)
    assert ci == h8.cluster_bootstrap_ci(values, clusters, resamples=2000)
    assert ci[0] == pytest.approx(-1.0) and ci[1] == pytest.approx(1.0)    # only whole-cluster means occur
    assert h8.cluster_bootstrap_ci([1.0, 2.0], ["a", "a"]) is None


def test_cost_stress_and_classification():
    rows = [{"gross_r": 0.3, "cost_r": 0.1}] * 3
    assert h8.cost_stress(rows)["x1.2"] == pytest.approx(0.18)
    ok = {name: True for name in h8.REQUIRED_CRITERIA}
    assert h8.classify(ok, trades=150, gross_r=0.3, net_r=0.2) == "STRONG PASS"
    assert h8.classify({**ok, "psr_ge_095": False}, trades=150, gross_r=0.3, net_r=0.2) == "MARGINAL"
    assert h8.classify(ok, trades=99, gross_r=0.3, net_r=0.2) == "FAIL"
    assert h8.classify(ok, trades=150, gross_r=0.3, net_r=-0.01) == "FAIL"
    assert h8.classify({**ok, "zero_safety_violations": False}, trades=150, gross_r=0.3, net_r=0.2) == "FAIL"


# ------------------------------------------------------ no live path exists

def test_h8_cannot_trade_and_is_not_a_runtime_strategy():
    source = (ROOT / "adaptive_scalper" / "research" / "v2" / "h8.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("adaptive_scalper.execution", "adaptive_scalper.gateway", "adaptive_scalper.runtime"))
                   for m in imported)
    assert "order_send" not in source
    from adaptive_scalper.strategies import build_active_registry
    assert h8.STRATEGY_KEY not in {s.key for s in build_active_registry().all_active()}


# ---------------------------------- pullback rules isolated (each one necessary)

def _m5(i, o, h, l, c):
    return Bar(time=START + 300 * i, open=o, high=h, low=l, close=c, tick_volume=1, spread=10, real_volume=0)


def _armed_engine():
    from collections import deque
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    engine.context = h8.Context("XAUUSD:M15:BUY:0", "BUY", START, 1990.0)
    engine._tr = deque([2.0] * h8.ATR_LENGTH, maxlen=h8.ATR_LENGTH)          # ATR14 = 2.0 -> needs 1.0
    engine._prev_m5_close = 2000.0
    engine.on_m5(_m5(1, 2000.0, 2010.0, 1999.9, 2009.9))                       # the local extreme 2010
    engine._tr = deque([2.0] * h8.ATR_LENGTH, maxlen=h8.ATR_LENGTH)
    return engine


def _feed(engine, bars):
    from collections import deque
    for b in bars:
        engine.on_m5(b)
        engine._tr = deque([2.0] * h8.ATR_LENGTH, maxlen=h8.ATR_LENGTH)       # hold ATR fixed at 2.0
    return engine._pullback_id


def test_two_counter_closes_but_a_retracement_below_half_atr_is_not_a_pullback():
    e = _armed_engine()
    assert _feed(e, [_m5(2, 2009.9, 2009.95, 2009.5, 2009.6), _m5(3, 2009.6, 2009.7, 2009.3, 2009.4)]) is None
    assert e._counter == 2 and 2010.0 - e._swing < 1.0


def test_a_deep_retracement_with_one_counter_close_is_not_a_pullback():
    e = _armed_engine()
    assert _feed(e, [_m5(2, 2009.0, 2009.2, 2007.0, 2007.5), _m5(3, 2007.5, 2009.8, 2007.4, 2009.7)]) is None
    assert e._counter == 1 and 2010.0 - e._swing >= 1.0


def test_two_counter_closes_and_half_atr_retracement_confirm_the_pullback():
    e = _armed_engine()
    pid = _feed(e, [_m5(2, 2009.0, 2009.2, 2008.5, 2008.6), _m5(3, 2008.6, 2008.7, 2008.0, 2008.1)])
    assert pid == f"XAUUSD:M15:BUY:0:PB:{START + 600}" and e._swing == pytest.approx(2008.0)


def test_evaluate_reports_every_preregistered_statistic_and_classifies():
    result = run(path(LONG + [(20, 2016.0), (30, 2030.0)]))
    report = h8.evaluate(result["trades"], result["rejected"], n_folds=12, config=cfg(),
                         prior_fold_net_r=[[0.1 * f for f in range(12)], [-0.1] * 12], family_trials=40,
                         family_sharpe_variance=0.01, forbidden_access=False)
    assert report["trades"] == len(result["trades"]) and report["episodes"] == 1
    assert set(report["checks"]) == set(h8.REQUIRED_CRITERIA)
    assert set(report["stop_binding"]) == {h8.STRUCTURAL, h8.VOLATILITY, h8.FRICTION}
    assert sum(v["count"] for v in report["stop_binding"].values()) == report["trades"]
    assert set(report["medians"]) >= {"stop_distance", "structural_distance", "volatility_floor", "friction_floor",
                                      "target_distance", "holding_seconds"}
    assert "mfe_r_ohlc_bound_mean" in report and "ohlc" in report["excursion_note"].lower()
    assert report["classification"] == "FAIL"                       # < 100 trades
    assert report["pbo"]["members"] == 3
    assert report["gross_r"] == pytest.approx(report["net_r"] + report["cost_r"])
    assert report["cost_r"] == pytest.approx(report["entry_cost_r"] + report["exit_cost_r"]
                                             + report["commission_swap_r"])


# ------------------------------------------------------------------ runner

def _runner():
    import importlib.util

    spec_ = importlib.util.spec_from_file_location("research_v2_h8", ROOT / "scripts" / "research_v2_h8.py")
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    return module


def _research_db(tmp_path, *, with_m1=False):
    from adaptive_scalper.gateway.spec_store import save_symbol_spec
    from adaptive_scalper.history.store import insert_bars
    from adaptive_scalper.persistence import connect, migrate

    db = tmp_path / "research.sqlite3"
    conn = connect(str(db))
    migrate(conn)
    conn.execute("UPDATE mt5_time_basis SET basis = 'UTC' WHERE id = 1")
    save_symbol_spec(conn, "XAUUSD", spec(), now_utc=1)
    m15, m5, m1 = series(path([(30, 2010.0)] * 1, warmup=60 * 24 * 10))      # 10 days of M15
    insert_bars(conn, "XAUUSD", "M15", m15)
    insert_bars(conn, "XAUUSD", "M5", m5)
    if with_m1:
        insert_bars(conn, "XAUUSD", "M1", m1)
    conn.commit()
    conn.close()
    return db


def _main(module, tmp_path, db):
    return module.main(["--config", str(ROOT / "config" / "default.toml"), "--research-db", str(db), "--tag", "t1",
                        "--out", str(tmp_path / "out.json"), "--h6-results", str(tmp_path / "missing.json")])


def test_runner_refuses_a_dirty_tree(tmp_path, monkeypatch):
    module = _runner()
    monkeypatch.setattr(module, "_git_sha", lambda: "abc-dirty")
    assert _main(module, tmp_path, _research_db(tmp_path)) == 2


def test_runner_refuses_on_the_data_gate_before_any_rule_and_records_nothing(tmp_path, monkeypatch):
    import json

    from adaptive_scalper.persistence import connect

    module = _runner()
    monkeypatch.setattr(module, "_git_sha", lambda: "abc")
    monkeypatch.setattr(module.h8, "run_h8_fold", lambda *a, **k: pytest.fail("a rule was evaluated"))
    db = _research_db(tmp_path)                                  # no M1 at all
    assert _main(module, tmp_path, db) == 3
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["status"] == "REFUSED_DATA_GATE" and report["data_gate"]["passes"] is False
    assert connect(str(db)).execute("SELECT COUNT(*) FROM research_trials WHERE trial_id LIKE 'v2h8:%'").fetchone()[0] == 0


def test_runner_is_one_shot(tmp_path, monkeypatch):
    from adaptive_scalper.persistence import connect
    from adaptive_scalper.research.ledger import record_trial

    db = _research_db(tmp_path)
    conn = connect(str(db))
    record_trial(conn, trial_id="v2h8:t1:XAUUSD:H8-PRIMARY", family="v2-H8:XAUUSD", kind="X", strategy_versions={},
                 params={}, status="COMPLETED", now_utc=1)
    conn.close()
    module = _runner()
    monkeypatch.setattr(module, "_git_sha", lambda: "abc")
    monkeypatch.setattr(module, "get_bars", lambda *a, **k: pytest.fail("bars were loaded"))
    assert _main(module, tmp_path, db) == 2


# =====================================================================
# Amendment 2026-10-02 + Phase 6 coverage
# =====================================================================

def _trigger_index(prices=None):
    clean = run(prices)
    return (clean["trades"][0].decision.bar.time - START) // 60, clean


# ---- M15
def test_sell_breakout_uses_the_previous_20_lows_and_the_level():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    for i in range(20):
        engine.on_m15(_m15(i, 2001.0, 1999.0 - (i == 5) * 0.5, 2000.0))
    engine.on_m15(_m15(20, 2000.0, 1998.0, 1998.5))                            # == channel low: no breakout
    assert engine.context is None
    engine.on_m15(_m15(21, 2000.0, 1997.0, 1997.9))                            # channel bars 1..20: low 1998.0
    assert engine.context.direction == "SELL" and engine.context.level == pytest.approx(1998.0)


def test_same_direction_context_is_not_duplicated_and_opposite_breakout_replaces_it():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    for i in range(20):
        engine.on_m15(_m15(i, 2001.0, 1999.0, 2000.0))
    engine.on_m15(_m15(20, 2003.0, 2000.0, 2002.0))
    first = engine.context.event_id
    engine.on_m15(_m15(21, 2005.0, 2002.0, 2004.5))                            # higher again: same context
    assert engine.context.event_id == first and len([e for e in engine.events if e[0] == "BREAKOUT"]) == 1
    for i in range(22, 42):
        engine.on_m15(_m15(i, 2003.0, 2002.5, 2002.5))                         # stays above the level
    engine.on_m15(_m15(42, 2002.6, 1990.0, 1990.5))                            # below the 20-bar low
    assert engine.context is not None and engine.context.direction == "SELL"
    assert any(e[0] == "INVALIDATED" and e[2] == first for e in engine.events)


def test_m15_close_back_through_the_level_invalidates_the_context():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    for i in range(20):
        engine.on_m15(_m15(i, 2001.0, 1999.0, 2000.0))
    engine.on_m15(_m15(20, 2003.0, 2000.0, 2002.0))
    engine.on_m15(_m15(21, 2002.0, 2000.5, 2000.9))                            # below level 2001.0
    assert engine.context is None


# ---- M5
def test_atr14_is_the_mean_true_range_of_the_last_14_completed_m5_bars():
    engine = h8.H8SignalEngine(store=h8.FingerprintStore())
    bars = [_m5(i, 2000.0, 2000.0 + 1 + i * 0.1, 2000.0, 2000.5) for i in range(16)]
    for b in bars:
        engine.on_m5(b)
    trs = [max(b.high - b.low, abs(b.high - q.close), abs(b.low - q.close)) for q, b in zip(bars, bars[1:])]
    assert engine.atr() == pytest.approx(sum(trs[-14:]) / 14)
    fresh = h8.H8SignalEngine(store=h8.FingerprintStore())
    for b in bars[:14]:
        fresh.on_m5(b)
    assert fresh.atr() is None                                                  # only 13 true ranges yet


def test_non_consecutive_counter_closes_count():
    e = _armed_engine()
    pid = _feed(e, [_m5(2, 2009.9, 2009.95, 2009.0, 2009.1), _m5(3, 2009.1, 2009.6, 2009.05, 2009.5),
                    _m5(4, 2009.5, 2009.55, 2008.6, 2008.7)])
    assert pid is not None and e._counter == 2


def test_retracement_threshold_is_exactly_half_atr14():
    # bar 3 adds its own true range (2009.7 - low) to ATR14 before the check (13 x 2.0 held):
    # retrace x = 2010 - low must satisfy x >= 0.5 * (26 + x - 0.3) / 14, i.e. x >= 25.7 / 27
    threshold = 25.7 / 27
    for x, confirmed in ((threshold + 1e-6, True), (threshold - 1e-6, False)):
        e = _armed_engine()
        low = 2010.0 - x
        pid = _feed(e, [_m5(2, 2009.9, 2009.95, 2009.5, 2009.6), _m5(3, 2009.6, 2009.7, low, 2009.4)])
        assert (pid is not None) is confirmed


def test_a_new_local_extreme_discards_an_untriggered_pullback():
    e = _armed_engine()
    assert _feed(e, [_m5(2, 2009.0, 2009.2, 2008.5, 2008.6), _m5(3, 2008.6, 2008.7, 2008.0, 2008.1)]) is not None
    _feed(e, [_m5(4, 2008.1, 2011.0, 2008.0, 2010.8)])                         # new high 2011
    assert e._pullback_id is None and e._extreme == 2011.0


def test_an_m5_close_through_the_level_destroys_the_pullback_and_context():
    e = _armed_engine()
    _feed(e, [_m5(2, 2009.0, 2009.2, 2008.5, 2008.6), _m5(3, 2008.6, 2008.7, 2008.0, 2008.1)])
    _feed(e, [_m5(4, 2008.1, 2008.2, 1989.0, 1989.5)])                         # level 1990
    assert e.context is None and e._pullback_id is None


def test_the_pullback_extreme_keeps_updating_until_the_decision():
    e = _armed_engine()
    _feed(e, [_m5(2, 2009.0, 2009.2, 2008.5, 2008.6), _m5(3, 2008.6, 2008.7, 2008.0, 2008.1),
              _m5(4, 2008.1, 2008.3, 2007.2, 2007.3)])
    assert e._swing == pytest.approx(2007.2) and e._pullback_id is not None


def test_sell_pullback_is_the_mirror():
    from collections import deque
    e = h8.H8SignalEngine(store=h8.FingerprintStore())
    e.context = h8.Context("XAUUSD:M15:SELL:0", "SELL", START, 2010.0)
    e._tr = deque([2.0] * h8.ATR_LENGTH, maxlen=h8.ATR_LENGTH)
    e._prev_m5_close = 2000.0
    e.on_m5(_m5(1, 2000.0, 2000.1, 1990.0, 1990.1))                           # extreme low 1990
    pid = _feed(e, [_m5(2, 1990.1, 1991.5, 1990.0, 1991.4), _m5(3, 1991.4, 1992.0, 1991.3, 1991.9)])
    assert pid is not None and e._swing == pytest.approx(1992.0)


# ---- M1
def _m1(i, o, h, l, c):
    return Bar(time=START + 300 * 10 + 60 * i, open=o, high=h, low=l, close=c, tick_volume=1, spread=10,
               real_volume=0)


def _armed_with_pullback():
    e = _armed_engine()
    _feed(e, [_m5(2, 2009.0, 2009.2, 2008.5, 2008.6), _m5(3, 2008.6, 2008.7, 2008.0, 2008.1)])
    for b in (_m1(0, 2008.1, 2009.0, 2008.0, 2008.5), _m1(1, 2008.5, 2008.6, 2008.2, 2008.3),
              _m1(2, 2008.3, 2008.4, 2008.1, 2008.2)):
        assert e.on_m1(b) is None
    return e


def test_m1_trigger_compares_only_the_previous_3_completed_bars_and_fires_once():
    e = _armed_with_pullback()
    d = e.on_m1(_m1(3, 2008.2, 2012.0, 2008.2, 2009.01))                     # current high irrelevant; > 2009.0
    assert d is not None and d.decided_at_utc == START + 3000 + 240
    assert e.on_m1(_m1(4, 2009.0, 2013.0, 2009.0, 2012.9)) is None           # the same pullback never re-fires


def test_m1_close_equal_to_the_previous_3_high_does_not_trigger():
    e = _armed_with_pullback()
    assert e.on_m1(_m1(3, 2008.2, 2009.5, 2008.2, 2009.0)) is None


def test_no_m1_decision_before_the_pullback_is_confirmed():
    e = _armed_engine()
    for i, b in enumerate((_m1(0, 2009.0, 2009.1, 2008.9, 2009.0), _m1(1, 2009.0, 2009.1, 2008.9, 2009.0),
                           _m1(2, 2009.0, 2009.1, 2008.9, 2009.0), _m1(3, 2009.0, 2012.0, 2009.0, 2011.0))):
        assert e.on_m1(b) is None


# ---- execution: structural stop across gaps (amendment items 3 and 6)
@pytest.mark.parametrize("direction, level, fill, expected", [
    ("BUY", 1995.0, 2001.0, 6.0),      # BUY gap up: structural distance grows
    ("BUY", 1995.0, 1996.0, 1.0),      # BUY gap down: shrinks, stays beyond the level
    ("SELL", 2005.0, 1999.0, 6.0),     # SELL gap down
    ("SELL", 2005.0, 2004.0, 1.0),     # SELL gap up
])
def test_structural_distance_is_measured_from_the_fill_to_the_absolute_level(direction, level, fill, expected):
    stop = h8.fill_stop_distance(direction, fill, {"structural_stop_price": level, "volatility": 0.5,
                                                   "friction": 0.4})
    assert stop["structural"] == pytest.approx(expected) and stop["distance"] == pytest.approx(max(expected, 0.5))
    stop_price = fill - stop["distance"] if direction == "BUY" else fill + stop["distance"]
    assert (stop_price <= level + 1e-9) if direction == "BUY" else (stop_price >= level - 1e-9)


@pytest.mark.parametrize("direction, level, fill", [("BUY", 1995.0, 1994.9), ("SELL", 2005.0, 2005.1)])
def test_a_fill_through_the_structural_level_has_no_valid_stop(direction, level, fill):
    assert h8.fill_stop_distance(direction, fill, {"structural_stop_price": level, "volatility": 0.5,
                                                   "friction": 0.4}) is None


def test_a_gap_through_the_structural_level_rejects_the_entry():
    k, clean = _trigger_index()
    level = clean["trades"][0].stop["structural_stop_price"]
    m1 = m1_bars(path(LONG))
    b = m1[k + 1]
    m1[k + 1] = Bar(time=b.time, open=level - 1.0, high=max(b.high, level), low=level - 1.2, close=b.close,
                    tick_volume=1, spread=SPREAD_PTS, real_volume=0)
    result = h8.run_h8_fold(aggregate(m1, 900), aggregate(m1, 300), m1, spec(), cfg(), fold=0)
    assert h8.REJECT_GAP_THROUGH_STRUCTURAL in [r["reason"] for r in result["rejected"]]
    assert all(t.decision.pullback_id != clean["trades"][0].decision.pullback_id for t in result["trades"])


def test_the_stop_is_fixed_at_entry_and_consistent_with_the_fill_stop():
    for t in run()["trades"]:
        assert t.stop["distance"] >= t.stop["structural"] - 1e-12
        assert t.trade.entry_price - t.stop["distance"] <= t.stop["structural_stop_price"] + 1e-9
    source = (ROOT / "adaptive_scalper" / "research" / "v2" / "h8.py").read_text(encoding="utf-8")
    assert ".stop_price =" not in source and ".target_price =" not in source   # nothing moves an open stop


def test_range_end_closes_an_open_trade_at_the_last_close():
    prices = path([(30, 2010.0), (30, 2012.0), (30, 2006.0), (4, 2007.2), (10, 2007.4)])
    t = run(prices)["trades"][0]
    assert t.trade.exit_reason == "BACKTEST_RANGE_ENDED"


# ---- cost at the fill (amendment item 6)
def test_a_wider_fill_bar_spread_rejects_at_fill_and_never_rewidens_the_stop():
    k, clean = _trigger_index()
    store = h8.FingerprintStore()
    result = run(spreads={k + 1: 40}, store=store)
    rej = [r for r in result["rejected"] if r["reason"] == h8.REJECT_COST_R_AT_FILL]
    assert rej and rej[0]["fill_cost_r"] > h8.MAX_COST_R and rej[0]["cost_r"] <= h8.MAX_COST_R
    assert store.is_consumed(clean["trades"][0].decision.fingerprint)
    assert all(t.decision.pullback_id != clean["trades"][0].decision.pullback_id for t in result["trades"])


def test_a_narrower_fill_bar_spread_is_allowed():
    k, _ = _trigger_index()
    spreads = {i: 40 for i in range(2000)}
    spreads[k + 1] = 10
    t = run(spreads=spreads)["trades"][0]
    assert t.fill_cost_r < t.cost_r <= h8.MAX_COST_R
    assert t.fill_round_trip_cost < t.round_trip_cost


def test_unknown_fill_bar_spread_is_rejected():
    k, _ = _trigger_index()
    result = run(spreads={k + 1: 0})
    assert h8.REJECT_COST_UNKNOWN_AT_FILL in [r["reason"] for r in result["rejected"]]


def test_each_stop_component_can_bind():
    assert h8._combine(3.0, 1.0, 2.0)["binding"] == h8.STRUCTURAL
    assert h8._combine(1.0, 3.0, 2.0)["binding"] == h8.VOLATILITY
    assert h8._combine(1.0, 2.0, 3.0)["binding"] == h8.FRICTION


# ---- risk
def test_daily_loss_and_drawdown_ceilings_halt_new_entries():
    from adaptive_scalper.backtest.engine import _risk_halt_reason
    from adaptive_scalper.backtest.types import RiskState
    limits = cfg().risk_limits
    assert limits.max_total_open_risk_pct == 0.75
    assert _risk_halt_reason(RiskState(peak_equity=10000, day_utc=1, day_realized_pnl=-200.0), 9800, limits)
    assert _risk_halt_reason(RiskState(peak_equity=10000, day_utc=1, day_realized_pnl=0.0), 9500, limits)
    assert _risk_halt_reason(RiskState(peak_equity=10000, day_utc=1, day_realized_pnl=-10.0), 9990, limits) is None


def test_a_risk_halt_consumes_the_fingerprint(monkeypatch):
    _, clean = _trigger_index()
    monkeypatch.setattr(h8, "_risk_halt_reason", lambda *a: "drawdown")
    store = h8.FingerprintStore()
    run(store=store)
    assert store.is_consumed(clean["trades"][0].decision.fingerprint)


# ---- data bounds
def test_end_after_the_window_and_the_h7_interval_are_refused():
    with pytest.raises(ValueError):
        h8.assert_development_range(h8.DEV_START_UTC, h8.DEV_END_UTC + 1)
    with pytest.raises(ValueError):
        h8.assert_development_range(1655942400, 1717199999)                    # 2022-06-23 .. 2024-05-31
    h8.assert_development_range(h8.DEV_START_UTC, h8.DEV_END_UTC)


# ---- statistics
def test_day_cluster_bootstrap_is_reported_and_folds_are_counted():
    result = run(path(LONG + [(20, 2016.0), (30, 2030.0)]))
    r = h8.evaluate(result["trades"], result["rejected"], n_folds=12, config=cfg(), prior_fold_net_r=[[0.0] * 12],
                    family_trials=40, family_sharpe_variance=0.01, forbidden_access=False)
    assert "gross_r_day" in r["ci95"] and "net_r_day" in r["ci95"]
    assert len(r["by_fold_net_r"]) == 12


@pytest.mark.parametrize("missing", ["dsr_ge_095", "pbo_le_020", "gross_episode_ci_positive"])
def test_a_missing_or_none_criterion_is_never_strong_pass(missing):
    ok = {name: True for name in h8.REQUIRED_CRITERIA}
    assert h8.classify({k: v for k, v in ok.items() if k != missing}, trades=150, gross_r=0.3, net_r=0.2) == "MARGINAL"
    assert h8.classify({**ok, missing: None}, trades=150, gross_r=0.3, net_r=0.2) == "MARGINAL"


def test_extra_true_keys_cannot_create_a_strong_pass():
    ok = {name: True for name in h8.REQUIRED_CRITERIA}
    ok["psr_ge_095"] = False
    assert h8.classify({**ok, "anything_else": True}, trades=150, gross_r=0.3, net_r=0.2) == "MARGINAL"
    assert h8.classify({}, trades=150, gross_r=0.3, net_r=0.2) == "FAIL"     # safety keys absent: fail closed


def test_gross_cost_ratio_and_cost_stress_math():
    rows = [{"gross_r": 0.30, "cost_r": 0.09}, {"gross_r": 0.06, "cost_r": 0.02}]
    assert h8.cost_stress(rows)["x2"] == pytest.approx(((0.30 - 0.18) + (0.06 - 0.04)) / 2)
    assert sum(r["gross_r"] for r in rows) >= 3.0 * sum(r["cost_r"] for r in rows)


def test_the_strategy_signal_carries_the_decision_close_timestamp(monkeypatch):
    seen = []
    real = h8.StrategySignal

    def capture(**kwargs):
        seen.append(kwargs["data_timestamp"])
        return real(**kwargs)

    monkeypatch.setattr(h8, "StrategySignal", capture)
    t = run()["trades"][0]
    assert seen[0] == t.decision.decided_at_utc == t.decision.bar.time + 60


@pytest.mark.parametrize("key", ["zero_safety_violations", "zero_forbidden_data_access"])
def test_a_missing_safety_or_data_key_is_fail_not_marginal(key):
    ok = {name: True for name in h8.REQUIRED_CRITERIA if name != key}
    assert h8.classify(ok, trades=150, gross_r=0.3, net_r=0.2) == "FAIL"
    assert h8.classify({**ok, key: None}, trades=150, gross_r=0.3, net_r=0.2) == "FAIL"


@pytest.mark.parametrize("future", [(2030.0, 2001.0, 2029.0, 9999), (2006.5, 1990.0, 1991.0, 1)])
def test_fill_decision_ignores_the_fill_bar_future_high_low_close_volume(future):
    k, clean = _trigger_index()
    base = m1_bars(path(LONG))
    b = base[k + 1]
    high, low, close, volume = future
    variant = list(base)
    variant[k + 1] = Bar(time=b.time, open=b.open, high=max(high, b.open), low=min(low, b.open), close=close,
                         tick_volume=volume, spread=b.spread, real_volume=0)
    a = h8.run_h8_fold(aggregate(base, 900), aggregate(base, 300), base, spec(), cfg(), fold=0)
    v = h8.run_h8_fold(aggregate(variant, 900), aggregate(variant, 300), variant, spec(), cfg(), fold=0)
    first_a, first_v = a["trades"][0], v["trades"][0]
    assert first_a.decision.fingerprint == first_v.decision.fingerprint
    assert first_a.trade.entry_time_utc == first_v.trade.entry_time_utc == b.time
    assert first_a.trade.entry_price == first_v.trade.entry_price
    assert first_a.stop == first_v.stop and first_a.fill_cost_r == first_v.fill_cost_r
    assert first_a.trade.volume == first_v.trade.volume
