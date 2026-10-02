"""H9 (docs/research/V2_H9_PREREGISTRATION_2026-10-02.md): HAC trend t-stat,
RV24 baseline, threshold-cross episodes, causality controls, cost/swap gate
(rejects, never widens), fixed volatility stop, 4 h time exit, one-shot
runner, statistics and strict classification. Synthetic bars only, inside the
H9 dataset range; nothing here reads a database of market data."""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from adaptive_scalper.backtest.dataset import compute_bars_checksum
from adaptive_scalper.backtest.reserved_oos import RESERVED_OOS_INTERVALS, ReservedOosOverlapError
from adaptive_scalper.backtest.types import BacktestConfig, RiskState
from adaptive_scalper.gateway.types import Bar, SymbolSpec, SymbolTradeMode
from adaptive_scalper.research.v2 import h9
from adaptive_scalper.simulation.fill_model import COST_EXPLICIT_TEST_FIXTURE, FillAssumptions

ROOT = Path(__file__).resolve().parents[1]
START = 1709251200          # 2024-03-01 00:00 UTC, inside the H9 dataset
N = 3400                    # > 35 days of M15 bars
SPREAD = 1000               # 10.00 USD
SLIP = 11.97
T = 33 * 96 + 23            # bar opening 05:45 UTC on day 33 -> decision 06:00 UTC (no rollover in the hold)
ATR = 600.0                 # d = 900 -> cost_R ~ 0.041, volume 0.02


def btc_spec(**kw) -> SymbolSpec:
    return SymbolSpec(name="BTCUSD", description="Bitcoin", currency_base="USD", currency_profit="USD",
                      currency_margin="USD", digits=2, point=0.01, trade_contract_size=1.0, volume_min=0.01,
                      volume_max=10.0, volume_step=0.01, trade_tick_size=0.01, trade_tick_value=0.01, spread=SPREAD,
                      visible=True, trade_mode=SymbolTradeMode.FULL, **kw)


def cfg(**overrides) -> BacktestConfig:
    base = dict(fill_assumptions=FillAssumptions(slippage_price=SLIP, commission_monetary_per_lot=0.0,
                                                  provenance=COST_EXPLICIT_TEST_FIXTURE), initial_equity=10_000.0)
    base.update(overrides)
    return BacktestConfig(**base)


def make_bars(n=N, *, overrides=None, spreads=None, times=None, start=START, closes=None):
    """Quiet bars around 60,000 (range +-20); overrides[i] = (o, h, l, c)."""
    overrides, spreads, times = overrides or {}, spreads or {}, times or {}
    closes = closes or [60_000.0 + 50.0 * math.sin(i / 7.0) for i in range(n)]
    out, t, prev = [], start, closes[0]
    for i in range(n):
        if i in times:
            t = times[i]
        o, c = prev, closes[i]
        o, h, l, c = overrides.get(i, (o, max(o, c) + 20.0, min(o, c) - 20.0, c))
        out.append(Bar(time=t, open=o, high=h, low=l, close=c, tick_volume=100, spread=spreads.get(i, SPREAD),
                       real_volume=0))
        prev, t = c, t + 900
    return out


def series(n=N, *, rv=None, atr=ATR):
    """Baseline RV24 = 1.0 everywhere; rv = {index: value} overrides (2.0 = high volatility)."""
    rv24 = [None] * h9.TREND_BARS + [1.0] * (n - h9.TREND_BARS)
    for k, v in (rv or {}).items():
        rv24[k] = v
    return h9.Series([None] * n, rv24, [None] * h9.ATR_LENGTH + [atr] * (n - h9.ATR_LENGTH))


def event(bars, t=T, direction="BUY"):
    dt = bars[t].time + 900
    return h9.Event(t, direction, 2.5 if direction == "BUY" else -2.5, 1.5 if direction == "BUY" else -1.5, dt,
                    h9.fingerprint_of(direction, dt))


def run(bars=None, *, events=None, ser=None, fold=None, config=None, spec=None, store=None, t=T):
    bars = bars if bars is not None else make_bars()
    ser = ser if ser is not None else series(len(bars), rv={t: 2.0})
    events = events if events is not None else [event(bars, t)]
    return h9.run_h9_fold(bars, ser, events, fold or (0, len(bars)), spec or btc_spec(), config or cfg(), fold=0,
                          store=store if store is not None else h9.FingerprintStore())


def reasons(out):
    return [r["reason"] for r in out["rejected"]]


def _risk():
    return RiskState(peak_equity=10_000.0, day_utc=0, day_realized_pnl=0.0)


# =============================================================== locked constants

def test_constants_match_the_preregistration():
    assert (h9.TREND_BARS, h9.HAC_MAX_LAG, h9.T_THRESHOLD, h9.VOL_PERCENTILE, h9.BASELINE_SECONDS) == \
        (96, 3, 2.0, 75.0, 30 * 86_400)
    assert (h9.ATR_LENGTH, h9.STOP_ATR, h9.MAX_COST_R, h9.HOLD_SECONDS, h9.MAX_FILL_DELAY_SECONDS) == \
        (14, 1.5, 0.05, 14_400, 900)
    assert (h9.DATA_START_UTC, h9.DATA_END_UTC, h9.EXPECTED_BARS) == (1698796800, 1782777600, 92_660)
    assert h9.EXPECTED_CHECKSUM == "ede3898a45df163d66207fb5ad6ed745fde3853d358010da4e648de3315d03a8"
    assert (h9.BOOTSTRAP_RESAMPLES, h9.BOOTSTRAP_SEED, h9.N_FOLDS, h9.COST_MULTIPLIERS) == \
        (10_000, 20261002, 12, (1.0, 1.2, 1.5, 2.0))
    assert h9.RISK_LIMITS == {"risk_per_trade_pct": 0.25, "max_total_open_risk_pct": 0.75, "max_daily_loss_pct": 2.0,
                              "max_drawdown_pct": 5.0, "max_open_positions": 2, "max_positions_per_symbol": 1}
    prereg = (ROOT / "docs" / "research" / "V2_H9_PREREGISTRATION_2026-10-02.md").read_text(encoding="utf-8")
    for name in h9.REQUIRED_CRITERIA:
        assert f"`{name}`" in prereg
    assert len(h9.REQUIRED_CRITERIA) == 13


# =============================================================== HAC t-statistic

def _naive_hac_t(y, lag):
    """Independent full-matrix sandwich (numpy): (X'X)^-1 S (X'X)^-1, Bartlett weights."""
    n = len(y)
    X = np.column_stack([np.ones(n), np.arange(n, dtype=float)])
    beta = np.linalg.solve(X.T @ X, X.T @ np.asarray(y))
    e = np.asarray(y) - X @ beta
    S = np.zeros((2, 2))
    for t in range(n):
        S += e[t] ** 2 * np.outer(X[t], X[t])
    for lg in range(1, lag + 1):
        w = 1 - lg / (lag + 1)
        for t in range(lg, n):
            S += w * e[t] * e[t - lg] * (np.outer(X[t], X[t - lg]) + np.outer(X[t - lg], X[t]))
    inv = np.linalg.inv(X.T @ X)
    V = inv @ S @ inv
    return beta[1] / math.sqrt(V[1, 1])


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("lag", [0, 1, 3])
def test_hac_tstat_matches_an_independent_sandwich_implementation(seed, lag):
    rng = random.Random(seed)
    y = [math.log(60_000.0)]
    for _ in range(95):
        y.append(y[-1] + rng.gauss(0.0002 * (seed - 2), 0.002) + 0.0005 * math.sin(len(y)))
    assert h9.hac_slope_tstat(y, lag) == pytest.approx(_naive_hac_t(y, lag), rel=1e-8)


def test_hac_tstat_invariances_and_degenerate_input():
    rng = random.Random(7)
    y = [0.001 * i + rng.gauss(0, 0.003) for i in range(96)]
    t = h9.hac_slope_tstat(y)
    assert h9.hac_slope_tstat([v + 11.0 for v in y]) == pytest.approx(t, rel=1e-8)        # level shift
    assert h9.hac_slope_tstat([3.0 * v for v in y]) == pytest.approx(t, rel=1e-8)         # scale
    assert h9.hac_slope_tstat(y[::-1]) == pytest.approx(-t, rel=1e-8)                     # time reversal
    assert h9.hac_slope_tstat([1.0] * 96) is None                                         # zero variance


def test_percentile_is_numpy_linear():
    rng = random.Random(3)
    for n in (1, 2, 7, 100, 2881):
        values = [rng.random() for _ in range(n)]
        for q in (0, 25, 50, 75, 100):
            assert h9.percentile_linear(values, q) == pytest.approx(float(np.percentile(values, q, method="linear")))


# =============================================================== series and causality

def _walk(n=N, seed=11):
    rng = random.Random(seed)
    closes, p = [], 60_000.0
    for i in range(n):
        drift = 0.0015 if (i // 300) % 3 == 0 else -0.0012 if (i // 300) % 3 == 1 else 0.0
        p *= math.exp(rng.gauss(drift / 10, 0.002))
        closes.append(p)
    return make_bars(n, closes=closes)


def test_rv24_atr14_and_tstat_definitions():
    bars = _walk(400)
    s = h9.compute_series(bars)
    t = 300
    logs = [math.log(b.close) for b in bars]
    assert s.rv24[t] == pytest.approx(math.sqrt(sum((logs[k] - logs[k - 1]) ** 2 for k in range(t - 95, t + 1))))
    tr = [max(bars[k].high - bars[k].low, abs(bars[k].high - bars[k - 1].close), abs(bars[k].low - bars[k - 1].close))
          for k in range(t - 13, t + 1)]
    assert s.atr14[t] == pytest.approx(sum(tr) / 14)
    assert s.tstat[t] == pytest.approx(h9.hac_slope_tstat(logs[t - 95:t + 1]))
    assert s.tstat[94] is None and s.tstat[95] is not None and s.rv24[95] is None and s.rv24[96] is not None


def test_signal_is_independent_of_every_bar_after_the_decision():
    bars = _walk()
    base = h9.compute_series(bars)
    events = h9.detect_events(bars, base.tstat)
    assert events, "the synthetic walk must cross the threshold at least once"
    for ev in events[:5]:
        t = ev.index
        rng = random.Random(t)
        future = [dataclasses.replace(b, open=b.open * rng.uniform(0.5, 2), high=b.high * 3, low=b.low / 3,
                                      close=b.close * rng.uniform(0.5, 2), spread=rng.randint(0, 99_999))
                  for b in bars[t + 1:]]
        changed = bars[:t + 1] + future
        alt = h9.compute_series(changed)
        assert alt.tstat[:t + 1] == base.tstat[:t + 1]
        assert alt.rv24[:t + 1] == base.rv24[:t + 1] and alt.atr14[:t + 1] == base.atr14[:t + 1]
        assert h9.volatility_condition(changed, alt.rv24, t) == h9.volatility_condition(bars, base.rv24, t)
        alt_events = [e for e in h9.detect_events(changed, alt.tstat) if e.index <= t]
        assert alt_events == [e for e in events if e.index <= t]
        assert alt_events[-1].fingerprint == ev.fingerprint and alt_events[-1].direction == ev.direction


def test_volatility_baseline_is_the_previous_30_days_before_the_window_only():
    bars = make_bars()
    t = T
    w0 = bars[t - 95].time
    rv = [None] * h9.TREND_BARS + [1.0] * (N - h9.TREND_BARS)
    for s in range(t - 95, t):
        rv[s] = 1e9                                   # inside the current window: must be ignored
    for s in range(h9.TREND_BARS, t - 95):
        if bars[s].time < w0 - h9.BASELINE_SECONDS:
            rv[s] = 1e9                               # older than 30 days: must be ignored
    for i, s in enumerate(range(t - 95 - 400, t - 95)):
        rv[s] = 1.0 + i / 400                         # the baseline proper
    rv[t] = 1.76
    out = h9.volatility_condition(bars, rv, t)
    baseline = [rv[s] for s in range(h9.TREND_BARS, t - 95) if bars[s].time >= w0 - h9.BASELINE_SECONDS]
    assert out["p75"] == pytest.approx(float(np.percentile(baseline, 75)))
    assert out["status"] == (h9.VOL_OK if 1.76 >= out["p75"] else h9.REJECT_VOLATILITY)
    assert out["baseline_n"] == len(baseline) and max(baseline) < 1e9


def test_volatility_equal_to_p75_passes_and_below_fails():
    bars = make_bars()
    rv = series(rv={}).rv24
    assert h9.volatility_condition(bars, rv, T)["status"] == h9.VOL_OK          # 1.0 >= P75 of all-1.0
    rv[T] = 0.999
    assert h9.volatility_condition(bars, rv, T)["status"] == h9.REJECT_VOLATILITY


def test_less_than_30_days_of_history_is_no_signal():
    bars = make_bars()
    rv = series(rv={}).rv24
    early = 96 + 30 * 96 - 10                     # window start less than 30 days after bar 96
    assert h9.volatility_condition(bars, rv, early)["status"] == h9.REJECT_INSUFFICIENT_HISTORY
    assert h9.volatility_condition(bars, rv, 100)["status"] == h9.REJECT_INSUFFICIENT_HISTORY
    assert h9.volatility_condition(bars, rv, 96 + 31 * 96 + 96)["status"] == h9.VOL_OK


# =============================================================== episodes

def test_events_fire_only_on_threshold_crossings():
    bars = make_bars(14)
    ts = [0.0, 1.0, 2.5, 3.0, 2.1, 1.9, 2.2, -1.0, -2.5, -3.0, -1.5, -2.0, None, 2.5]
    got = [(e.index, e.direction) for e in h9.detect_events(bars, ts)]
    assert got == [(2, "BUY"), (6, "BUY"), (8, "SELL"), (11, "SELL")]
    assert h9.detect_events(make_bars(2), [1.99, 2.0])[0].direction == "BUY"     # exactly 2.0 is a crossing
    assert h9.detect_events(make_bars(2), [-1.99, -2.0])[0].direction == "SELL"
    e = h9.detect_events(bars, ts)[0]
    assert e.decision_time == bars[2].time + 900 and e.fingerprint == f"H9|BTCUSD|BUY|{bars[2].time + 900}"


def test_one_trade_maximum_per_episode_while_the_trend_persists():
    bars = make_bars()
    ts = [None] * N
    for k in range(T - 5, N):
        ts[k] = 1.0 if k < T else 3.0                 # crosses once at T, then stays above
    events = h9.detect_events(bars, ts)
    assert [e.index for e in events] == [T]
    out = run(bars, events=events, ser=dataclasses.replace(series(rv={k: 2.0 for k in range(T, N)}), tstat=ts))
    assert len(out["trades"]) == 1 and out["rejected"] == []


def test_volatility_not_met_at_the_crossing_consumes_the_episode():
    bars = make_bars()
    ts = [None] * N
    for k in range(T - 5, N):
        ts[k] = 1.0 if k < T else 3.0
    ser = h9.Series(ts, series(rv={k: 2.0 for k in range(T + 1, N)}).rv24, series().atr14)   # high vol only AFTER
    ser.rv24[T] = 0.5
    store = h9.FingerprintStore()
    out = run(bars, events=h9.detect_events(bars, ts), ser=ser, store=store)
    assert reasons(out) == [h9.REJECT_VOLATILITY] and out["trades"] == []
    assert store.is_consumed(h9.fingerprint_of("BUY", bars[T].time + 900))


def test_a_rejected_event_never_refires_even_after_a_restart(tmp_path):
    import sqlite3
    bars = make_bars(spreads={T: 100_000})            # decision cost_R far above 0.05
    conn = sqlite3.connect(str(tmp_path / "r.sqlite3"))
    out = run(bars, store=h9.SqliteFingerprintStore(conn))
    assert reasons(out) == [h9.REJECT_COST_R]
    cheap = make_bars()                               # same event, a cheaper spread later
    again = run(cheap, store=h9.SqliteFingerprintStore(conn))
    assert reasons(again) == [h9.REJECT_ALREADY_CONSUMED] and again["trades"] == []


# =============================================================== entry causality

def test_entry_fills_at_the_next_open_after_the_decision_close():
    bars = make_bars()
    out = run(bars)
    tr = out["trades"][0].trade
    assert tr.entry_time_utc == bars[T + 1].time >= bars[T].time + 900
    assert tr.entry_price == pytest.approx(bars[T + 1].open + SPREAD * 0.01 / 2 + SLIP)
    assert tr.signal_time_utc == bars[T].time + 900


def test_fill_bar_lookahead_negative_control():
    """Same fill-bar time/open/spread, radically different high/low/close: same permission and entry."""
    calm = make_bars()
    wild = list(calm)
    f = wild[T + 1]
    wild[T + 1] = dataclasses.replace(f, high=f.open + 50_000, low=f.open - 50_000, close=f.open - 40_000)
    for direction in ("BUY", "SELL"):
        a = h9.open_h9_entry(direction, 900.0, calm[T].time + 900, calm[T + 1], equity=10_000.0,
                             risk_state=_risk(), symbol_spec=btc_spec(), config=cfg(), fingerprint="x")
        b = h9.open_h9_entry(direction, 900.0, calm[T].time + 900, wild[T + 1], equity=10_000.0,
                             risk_state=_risk(), symbol_spec=btc_spec(), config=cfg(), fingerprint="x")
        assert a[1] == b[1]                                           # fill-time cost evidence identical
        assert a[0].to_state() == b[0].to_state()                     # identical opened position
    out_calm, out_wild = run(calm), run(wild)
    assert out_calm["trades"][0].trade.entry_price == out_wild["trades"][0].trade.entry_price
    assert out_calm["trades"][0].fill_cost_r == out_wild["trades"][0].fill_cost_r
    # only AFTER entry does the wild range act: it hits the stop inside the fill bar
    assert out_wild["trades"][0].trade.exit_reason == h9.STOP_LOSS_HIT
    assert out_calm["trades"][0].trade.exit_reason == h9.TIME_EXIT_REASON


def test_staleness_is_measured_from_the_decision_close():
    bars = make_bars(times={T + 1: START + (T + 1) * 900 + 900})        # one missing bar: delay 900 -> allowed
    assert len(run(bars)["trades"]) == 1
    bars = make_bars(times={T + 1: START + (T + 1) * 900 + 1800})       # two missing bars: stale
    assert reasons(run(bars)) == [h9.REJECT_STALE]


def test_fill_time_cost_gate_rejects_and_never_widens_the_stop():
    out = run(make_bars(spreads={T + 1: 800_000}))
    r = out["rejected"][0]
    assert r["reason"] == h9.REJECT_COST_R_AT_FILL and r["stop_distance"] == pytest.approx(1.5 * ATR)
    assert out["trades"] == []


def test_decision_cost_gate_rejects_with_the_volatility_stop_unchanged():
    out = run(make_bars(), ser=series(rv={T: 2.0}, atr=200.0))           # d = 300 -> cost_R ~ 0.12
    r = out["rejected"][0]
    assert r["reason"] == h9.REJECT_COST_R and r["stop_distance"] == pytest.approx(300.0)
    assert r["decision_cost_r"] == pytest.approx(h9.round_trip_cost_price(make_bars()[T], btc_spec(), cfg()) / 300.0)


def test_unknown_spread_is_cost_unknown_at_decision_and_at_fill():
    assert reasons(run(make_bars(spreads={T: 0}))) == [h9.REJECT_COST_UNKNOWN]
    assert reasons(run(make_bars(spreads={T + 1: 0}))) == [h9.REJECT_COST_UNKNOWN_AT_FILL]
    no_slip = cfg(fill_assumptions=FillAssumptions(slippage_price=0.0, commission_monetary_per_lot=0.0,
                                                    provenance=COST_EXPLICIT_TEST_FIXTURE))
    assert reasons(run(make_bars(), config=no_slip)) == [h9.REJECT_COST_UNKNOWN]


def test_round_trip_cost_components():
    bar = make_bars()[T]
    assert h9.round_trip_cost_price(bar, btc_spec(), cfg()) == pytest.approx((SPREAD * 0.01 + 2 * SLIP) * 1.10)


def test_holds_crossing_the_broker_rollover_are_swap_unknown():
    # US DST (UTC+3): server midnight = 21:00 UTC; winter (UTC+2): 22:00 UTC
    summer = 1719792000                                                  # 2024-07-01 00:00 UTC
    assert h9.crosses_rollover(summer + 17 * 3600 + 15 * 60)           # 17:15 -> 21:15
    assert not h9.crosses_rollover(summer + 16 * 3600 + 45 * 60)       # 16:45 -> 20:45
    assert h9.crosses_rollover(summer + 21 * 3600)                     # starts AT the rollover
    winter = 1704067200                                                  # 2024-01-01 00:00 UTC
    assert h9.crosses_rollover(winter + 18 * 3600 + 30 * 60) and not h9.crosses_rollover(winter + 22 * 3600 + 15 * 60)
    t = 33 * 96 + 71                                                     # decision 18:00 UTC (DST) -> crosses 21:00
    bars = make_bars()
    assert reasons(run(bars, ser=series(rv={t: 2.0}), events=[event(bars, t)])) == [h9.REJECT_COST_UNKNOWN_SWAP]


def test_local_server_clock_equals_the_gateway_rule_over_the_whole_dataset():
    from adaptive_scalper.gateway.server_time import RULE_UTC2_US_DST, utc_to_server
    for ts in range(h9.DATA_START_UTC - 86_400, h9.DATA_END_UTC + 86_400, 900):
        assert h9.server_time(ts) == utc_to_server(RULE_UTC2_US_DST, ts)


def test_sizing_rounds_down_never_up_and_risk_stays_under_the_ceiling():
    out = run(make_bars())
    tr = out["trades"][0].trade
    assert tr.volume == pytest.approx(0.02) and tr.initial_monetary_risk == pytest.approx(0.02 * 900)
    assert tr.initial_monetary_risk <= 10_000 * 0.0025
    huge = run(make_bars(), ser=series(rv={T: 2.0}, atr=5_000.0))       # d 7,500 -> 0.0033 lots < min
    assert reasons(huge) == [h9.REJECT_SIZING]


def test_stop_inside_the_broker_stops_level_is_rejected_not_repaired():
    assert reasons(run(make_bars(), spec=btc_spec(trade_stops_level=100_000))) == [h9.REJECT_STOP_INVALID]


def test_news_window_blocks_the_fill():
    bars = make_bars()
    out = run(bars, config=cfg(news_windows=((bars[T + 1].time - 60, bars[T + 1].time + 60),)))
    assert reasons(out) == [h9.REJECT_NEWS]


# =============================================================== exits

def test_stop_is_fill_minus_d_and_never_moves_and_time_exit_after_16_bars():
    bars = make_bars()
    out = run(bars)
    h = out["trades"][0]
    tr = h.trade
    assert h.stop_price_at_entry == pytest.approx(tr.entry_price - 900.0)
    assert tr.exit_reason == h9.TIME_EXIT_REASON and tr.exit_time_utc == bars[T + 17].time
    assert tr.exit_time_utc - tr.entry_time_utc == 16 * 900
    assert tr.exit_price == pytest.approx(bars[T + 17].open - SPREAD * 0.01 / 2 - SLIP)
    assert h9.safety_violations(out["trades"], cfg()) == []


def test_sell_mirror():
    bars = make_bars()
    out = run(bars, events=[event(bars, T, "SELL")])
    h = out["trades"][0]
    assert h.trade.direction == "SELL" and h.stop_price_at_entry == pytest.approx(h.trade.entry_price + 900.0)
    assert h.trade.entry_price == pytest.approx(bars[T + 1].open - SPREAD * 0.01 / 2 - SLIP)


def test_time_exit_after_a_data_gap_uses_the_first_open_after_4h():
    bars = make_bars()
    # bars T+15..T+17 missing: the next bar after T+14 opens past the horizon
    gap = bars[:T + 15] + [dataclasses.replace(b, time=b.time + 3 * 900) for b in bars[T + 18:]]
    tr = run(gap)["trades"][0].trade
    assert tr.exit_reason == h9.TIME_EXIT_REASON and tr.exit_time_utc == bars[T + 18].time + 3 * 900


def test_stop_hit_and_gap_through_fill():
    bars = make_bars()
    fill = bars[T + 1].open + 5.0 + SLIP
    stop = fill - 900.0
    o = bars[T + 3].open
    tr = run(make_bars(overrides={T + 3: (o, o + 10, stop - 50, o)}))["trades"][0].trade
    assert tr.exit_reason == h9.STOP_LOSS_HIT and tr.exit_price == pytest.approx(stop - SLIP)
    tr = run(make_bars(overrides={T + 3: (stop - 600, stop - 500, stop - 700, stop - 550)}))["trades"][0].trade
    assert tr.exit_price == pytest.approx(stop - 600 - 5.0 - SLIP)    # min(stop, open - half spread) - slippage


def test_zero_spread_exit_bar_is_charged_the_fill_bar_spread():
    bars = make_bars(spreads={T + 17: 0})
    tr = run(bars)["trades"][0].trade
    assert tr.exit_price == pytest.approx(bars[T + 17].open - SPREAD * 0.01 / 2 - SLIP)
    assert tr.exit_spread_cost > 0


def test_position_open_rejects_a_second_event_and_fold_end_handling():
    bars = make_bars()
    out = run(bars, events=[event(bars, T), event(bars, T + 4, "SELL")], ser=series(rv={T: 2.0, T + 4: 2.0}))
    assert reasons(out) == [h9.REJECT_POSITION_OPEN] and len(out["trades"]) == 1
    assert reasons(run(bars, fold=(0, T + 1))) == [h9.REJECT_NO_NEXT_BAR]
    tr = run(bars, fold=(0, T + 6))["trades"][0].trade
    assert tr.exit_reason == h9.BACKTEST_RANGE_ENDED and tr.exit_time_utc == bars[T + 5].time + 900


def test_events_outside_the_fold_are_ignored():
    out = run(make_bars(), fold=(T + 1, N))
    assert out["trades"] == [] and out["rejected"] == [] and out["events"] == 0


def test_risk_halt_blocks_new_decisions(monkeypatch):
    monkeypatch.setattr(h9, "_risk_halt_reason", lambda *a, **k: "halted")
    assert reasons(run()) == [h9.REJECT_RISK_HALT]


# =============================================================== data gate and OOS guard

def test_reserved_oos_and_out_of_range_reads_are_refused():
    oos_start, _ = RESERVED_OOS_INTERVALS[0]
    assert oos_start == 1782864000
    with pytest.raises(ReservedOosOverlapError):
        h9.assert_development_range(h9.DATA_START_UTC, oos_start)
    with pytest.raises(ValueError):
        h9.assert_development_range(h9.DATA_START_UTC - 900, h9.DATA_END_UTC)
    with pytest.raises(ValueError):
        h9.assert_development_range(h9.DATA_START_UTC, h9.DATA_END_UTC + 900)
    h9.assert_development_range(h9.DATA_START_UTC, h9.DATA_END_UTC)
    probe = make_bars(3, start=oos_start - 900)          # synthetic timestamps only
    assert h9.forbidden_bars(probe) == [b.time for b in probe]
    assert h9.forbidden_bars(make_bars(3, start=h9.DATA_END_UTC - 1800)) == []


def test_data_gate_catches_structural_defects():
    good = make_bars(200)
    gate = h9.data_gate(good)
    assert gate["checks"]["strictly_increasing"] and gate["checks"]["aligned_900s"] and not gate["checks"]["count"]
    assert not gate["passes"] and gate["checksum"] == compute_bars_checksum(good)
    dup = good[:50] + [good[49]] + good[50:]
    assert not h9.data_gate(dup)["checks"]["strictly_increasing"]
    mis = [dataclasses.replace(good[0], time=good[0].time + 7)] + good[1:]
    assert not h9.data_gate(mis)["checks"]["aligned_900s"]
    bad = good[:10] + [dataclasses.replace(good[10], high=good[10].low - 1)] + good[11:]
    assert not h9.data_gate(bad)["checks"]["ohlc_consistent"]
    nan = good[:10] + [dataclasses.replace(good[10], close=float("nan"))] + good[11:]
    assert not h9.data_gate(nan)["checks"]["finite_positive_ohlc"]
    neg = good[:10] + [dataclasses.replace(good[10], spread=-1)] + good[11:]
    assert not h9.data_gate(neg)["checks"]["spread_nonnegative"]


# =============================================================== statistics and classification

ALL_TRUE = {name: True for name in h9.REQUIRED_CRITERIA}


def test_classification_is_strict():
    ok = dict(trades=150, gross_r=0.2, net_r=0.1, gross_ci_lower=0.05)
    assert h9.classify(ALL_TRUE, **ok) == h9.CANDIDATE
    for name in h9.REQUIRED_CRITERIA:
        broken = {**ALL_TRUE, name: False}
        expected = h9.REJECTED if name in ("zero_safety_violations", "zero_forbidden_data_access") else h9.MARGINAL
        assert h9.classify(broken, **ok) == expected
        assert h9.classify({k: v for k, v in ALL_TRUE.items() if k != name}, **ok) != h9.CANDIDATE
        assert h9.classify({**ALL_TRUE, name: None}, **ok) != h9.CANDIDATE
    assert h9.classify(ALL_TRUE, **{**ok, "trades": 99}) == h9.REJECTED
    for key in ("gross_r", "net_r", "gross_ci_lower"):
        for bad in (0.0, -0.1, None, float("nan"), float("inf")):
            assert h9.classify(ALL_TRUE, **{**ok, key: bad}) == h9.REJECTED
    assert h9.classify({**ALL_TRUE, "extra": False}, **ok) == h9.CANDIDATE


def _fake_results(n=120, seed=5, gross=0.3):
    from adaptive_scalper.backtest.types import SimulatedTrade
    rng = random.Random(seed)
    out = []
    for i in range(n):
        g = rng.gauss(gross, 1.0)
        cost = 0.04
        entry = START + i * 86_400 + 6 * 3600
        tr = SimulatedTrade(strategy_key=h9.STRATEGY_KEY, direction="BUY" if i % 2 else "SELL", entry_time_utc=entry,
                            entry_price=60_000.0, volume=0.02, initial_monetary_risk=18.0, entry_regime="UNKNOWN",
                            exit_time_utc=entry + 14_400, exit_price=60_000.0, exit_reason=h9.TIME_EXIT_REASON,
                            exit_regime="UNKNOWN", realized_r=g - cost, realized_pnl=(g - cost) * 18.0,
                            total_cost=cost * 18.0, gross_pnl=g * 18.0, entry_spread_cost=0.1, entry_slippage_cost=0.2,
                            exit_spread_cost=0.1, exit_slippage_cost=0.32, commission_cost=0.0, swap_cost=0.0,
                            fee_cost=0.0)
        ev = h9.Event(i, tr.direction, 2.5, 1.5, entry, h9.fingerprint_of(tr.direction, entry))
        sign = 1.0 if tr.direction == "BUY" else -1.0
        out.append(h9.H9Trade(ev, tr, i * 12 // n, 900.0, 600.0, {"rv24": 0.02 + i / 1e4, "p75": 0.01,
                                                                    "rv_rank": 0.9}, 37.0, 0.041, 37.0, 0.041,
                              10_000.0, 60_000.0 - sign * 900.0, 1.2, 0.5))
    return out


def test_evaluate_reports_every_statistic_and_fails_closed_without_pbo_or_dsr():
    results = _fake_results()
    kw = dict(config=cfg(), prior_fold_net_r=None, family_trials=40, family_sharpe_variance=None,
              forbidden_access=False, span_seconds=120 * 86_400)
    r = h9.evaluate(results, [], **kw)
    assert r["trades"] == 120 and r["episodes"] == 120 and len(r["fold_net_r"]) == 12
    assert set(r["checks"]) == set(h9.REQUIRED_CRITERIA)
    assert r["checks"]["pbo_le_020"] is False and r["checks"]["dsr_ge_095"] is False
    assert r["classification"] != h9.CANDIDATE
    assert set(r["cost_stress_mean_net_r"]) == {"x1", "x1.2", "x1.5", "x2"}
    assert r["cost_stress_mean_net_r"]["x1"] == pytest.approx(r["net_r"])
    for key in ("gross_r_episode", "net_r_episode", "gross_r_day", "net_r_day"):
        assert r["ci95"][key][0] < r["ci95"][key][1]
    assert h9.evaluate(results, [], **kw)["ci95"] == r["ci95"]               # seeded, deterministic
    assert {"by_direction", "by_session", "by_volatility_quartile", "trades_per_week"} <= set(r["diagnostics"])


def test_evaluate_uses_39_prior_fold_vectors_and_flags_forbidden_access():
    results = _fake_results()
    prior = [[random.Random(j * 100 + f).gauss(0, 1) for f in range(12)] for j in range(39)]
    r = h9.evaluate(results, [], config=cfg(), prior_fold_net_r=prior, family_trials=40, family_sharpe_variance=0.01,
                    forbidden_access=True, span_seconds=120 * 86_400)
    assert r["pbo"]["members"] == 40 and r["pbo"]["computable"] and r["dsr"] is not None
    assert r["checks"]["zero_forbidden_data_access"] is False and r["classification"] == h9.REJECTED
    short = h9.evaluate(results, [], config=cfg(), prior_fold_net_r=prior[:38], family_trials=40,
                        family_sharpe_variance=0.01, forbidden_access=False, span_seconds=1)
    assert short["checks"]["pbo_le_020"] is False                         # not the pre-registered universe


def test_gross_cost_ratio_needs_positive_aggregate_gross():
    r = h9.evaluate(_fake_results(gross=-0.5), [], config=cfg(), prior_fold_net_r=None, family_trials=40,
                    family_sharpe_variance=None, forbidden_access=False, span_seconds=1)
    assert r["checks"]["gross_cost_ratio_ge_3"] is False and r["classification"] == h9.REJECTED


def test_safety_violations_detect_a_moved_stop_and_excess_risk():
    results = _fake_results(3)
    assert h9.safety_violations(results, cfg()) == []
    moved = dataclasses.replace(results[0], stop_price_at_entry=50_000.0)
    big = dataclasses.replace(results[1], trade=dataclasses.replace(results[1].trade, initial_monetary_risk=26.0))
    problems = h9.safety_violations([moved, big, results[2]], cfg())
    assert any("stop not at" in p for p in problems) and any("ceiling" in p for p in problems)


# =============================================================== isolation and V1 semantics

def test_h9_cannot_trade_and_is_not_a_runtime_strategy():
    for path in (ROOT / "adaptive_scalper" / "research" / "v2" / "h9.py", ROOT / "scripts" / "research_v2_h9.py"):
        source = path.read_text(encoding="utf-8")
        imported = {n.module for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom) and n.module}
        assert not any(m.startswith(("adaptive_scalper.execution", "adaptive_scalper.runtime.demo",
                                     "adaptive_scalper.gateway.mt5")) for m in imported), path
        assert "order_send" not in source and "MetaTrader5" not in source
    source = (ROOT / "adaptive_scalper" / "research" / "v2" / "h9.py").read_text(encoding="utf-8")
    assert not any(n.module.startswith("adaptive_scalper.gateway") for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.ImportFrom) and n.module)
    from adaptive_scalper.strategies import build_active_registry
    assert h9.STRATEGY_KEY not in {s.key for s in build_active_registry().all_active()}


def test_v1_fill_revalidation_still_applies_the_expected_edge_gate():
    """H9 bypasses the raw_confidence EV gate in its OWN wrapper only; the engine is untouched."""
    import inspect

    from adaptive_scalper.backtest import engine
    from adaptive_scalper.costs import edge
    assert "evaluate_cost_gate(signal, cost, config.min_net_edge_price)" in inspect.getsource(engine._revalidate_and_open)
    assert "p * signal.target_distance - (1 - p) * signal.stop_distance" in inspect.getsource(
        edge.expected_gross_edge_price)
    source = inspect.getsource(h9.open_h9_entry)
    assert "evaluate_cost_gate" not in source and "expected_gross_edge" not in source
    assert "raw_confidence" not in source.replace('"raw_confidence": "NOT USED"', "").replace(
        "entry_raw_confidence=None", "")


# =============================================================== one-shot runner

def _runner():
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("research_v2_h9", ROOT / "scripts" / "research_v2_h9.py")
    module = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(module)
    return module


def _research_db(tmp_path):
    from adaptive_scalper.gateway.spec_store import save_symbol_spec
    from adaptive_scalper.history.store import insert_bars
    from adaptive_scalper.persistence import connect, migrate

    db = tmp_path / "research.sqlite3"
    conn = connect(str(db))
    migrate(conn)
    conn.execute("UPDATE mt5_time_basis SET basis = 'UTC' WHERE id = 1")
    save_symbol_spec(conn, "BTCUSD", btc_spec(), now_utc=1)
    insert_bars(conn, "BTCUSD", "M15", make_bars(400))
    conn.commit()
    conn.close()
    return db


def _main(module, tmp_path, db):
    return module.main(["--research-db", str(db), "--tag", "t1", "--out", str(tmp_path / "out.json"),
                        "--h6-results", str(tmp_path / "missing.json")])


def test_runner_refuses_a_dirty_tree(tmp_path, monkeypatch):
    module = _runner()
    monkeypatch.setattr(module, "_code_sha", lambda: "abc-dirty")
    monkeypatch.setattr(module, "open_research_db", lambda *a, **k: pytest.fail("the DB was opened"))
    assert _main(module, tmp_path, _research_db(tmp_path)) == 2


def test_runner_is_one_shot(tmp_path, monkeypatch):
    from adaptive_scalper.persistence import connect
    from adaptive_scalper.research.ledger import record_trial

    db = _research_db(tmp_path)
    conn = connect(str(db))
    record_trial(conn, trial_id="v2h9:h9r1:BTCUSD:H9-PRIMARY", family="v2-H9:BTCUSD", kind="X",
                 strategy_versions={}, params={}, status="COMPLETED", now_utc=1)
    conn.close()
    module = _runner()
    monkeypatch.setattr(module, "_code_sha", lambda: "abc")
    monkeypatch.setattr(module, "get_bars", lambda *a, **k: pytest.fail("bars were loaded"))
    assert _main(module, tmp_path, db) == 2


def test_runner_blocks_on_the_data_gate_before_any_rule_and_records_nothing(tmp_path, monkeypatch):
    from adaptive_scalper.persistence import connect

    module = _runner()
    monkeypatch.setattr(module, "_code_sha", lambda: "abc")
    monkeypatch.setattr(module.h9, "compute_series", lambda *a, **k: pytest.fail("a rule was evaluated"))
    db = _research_db(tmp_path)                                   # 400 bars, not the H6 dataset
    assert _main(module, tmp_path, db) == 3
    report = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert report["status"] == "H9 DATA BLOCKED" and report["data_gate"]["passes"] is False
    assert connect(str(db)).execute("SELECT COUNT(*) FROM research_trials").fetchone()[0] == 0


def test_h6_fold_vectors_require_the_preregistered_hash(tmp_path):
    module = _runner()
    p = tmp_path / "h6.json"
    p.write_text(json.dumps({"bars_checksum": h9.EXPECTED_CHECKSUM, "folds": 12, "trials": {}}), encoding="utf-8")
    vectors, note = module.load_h6_fold_vectors(str(p))
    assert vectors is None and "sha256" in note
    assert module.load_h6_fold_vectors(str(tmp_path / "nope.json"))[0] is None
