# ADX/ATR pullback continuation -- pre-registration DRAFT (2026-10-08)

Branch `research/adx-pullback-hypothesis`, base `hardening/flat-shadow-release` @ `a073ec1`. Committed BY ITSELF:
no signal, indicator, simulation or statistics code exists for this hypothesis, and no market data was read to
write it. Working name **ADX-PB** (it would be ledger family `v2-H10:<SYMBOL>` if ever authorized).

## 0. Status -- read first

- **NOT AUTHORIZED TO RUN.** Strategy research has been **FROZEN** since H9 was REJECTED on 2026-10-02
  (`docs/research/V2_H9_RESULTS_h9r1.md` on `research/h9-btc-vol-trend`; H9 pre-registration section 11:
  "REJECTED -> NO VALIDATED EDGE -- STRATEGY RESEARCH FROZEN. No forward PAPER, no H10/H9b/variants"). This
  document records the operator's request (2026-10-06) to write the idea down **as a spec only**. Running it
  needs a separate, explicit, dated operator decision recorded in WORKLOG that lifts the freeze **for this
  hypothesis alone**. Until then: no code, no data access, no ledger row.
- **No release code changes.** V1 strategies (including `pullback_continuation` v1), the selector, the risk
  governor, the exits, the regime classifier and `tests/test_v1_strategy_freeze.py` are untouched. If ADX-PB is
  ever implemented, it lives under `adaptive_scalper/research/` only and opens no broker order.
- **Rescue risk, declared.** ADX-PB overlaps V1 `pullback_continuation` (trend + counter-move entry) and H6/H8
  (pullback families). It is admitted only as a NEW family whose every rule is fixed below, never as a tuning of
  V1 or of any earlier H. Multiple testing: the ledger already holds 262 trials (H1-H9); ADX-PB's deflated
  Sharpe must count all of them in its trial total, not only its own family.
- **Data exposure.** Every bar before the reserved OOS was already exposed by H1-H9. A development pass is
  therefore **not validation**. The reserved OOS `2026-07-01 00:00 UTC <= t < 2026-09-19 00:00 UTC` stays
  **SEALED**; nothing here authorizes opening it.
- **One primary parameterization** (section 3). No threshold, period, multiplier, session, direction or
  subgroup variant is run after a failure.

## 1. Hypothesis

In an established intraday trend (M5 ADX(14) at or above 22 and ATR above its own 50-bar mean), a pullback that
touches the M5 EMA(20) and closes with a reversal candle back in trend direction, on the trend side of the
session VWAP, has a positive expectancy **net of** the measured DEMO costs, at a 1.5 ATR stop and a 2.25 ATR
target with a trailing stop armed only after +1.0 ATR.

Null: mean net R per trade <= 0.

## 2. Data (to be fixed and checksummed BEFORE any code, at authorization time)

| Item | Rule |
|---|---|
| Symbols | XAUUSD and BTCUSD, evaluated as **two separate trials** (no pooling); GBPJPY excluded (no cost evidence) |
| Source | research DB copy `data/research/v2_20260929.sqlite3`, table `bars`, IC Markets DEMO history, UTC |
| Timeframes | M5 (signal + execution); the "HTF" VWAP is the **UTC-session VWAP built from M5 bars** (section 3.1) -- no other timeframe |
| Range | every M5 bar with open time `< 2026-07-01 00:00 UTC` present in the copy; first/last bar, count and `compute_bars_checksum` recorded in an amendment committed before the runner exists; the runner aborts on any mismatch |
| Volume | MT5 tick volume (`bar.volume`); a bar with volume 0 contributes nothing to VWAP |
| Folds | 12 contiguous index folds (`fold_index_ranges(n, 12, feature_lookback=60)`), trading flat at each fold start, as H6/H8/H9 |

## 3. The locked rule (ADX-PB-PRIMARY)

`x_t` = completed M5 bar t. Decisions use bars `<= t` only. `decision_time(t) = x_t.time + 300`.

### 3.1 Indicators (all causal)

1. **ATR14**: Wilder RMA of true range, period 14 (`TR_k = max(h-l, |h-c_{k-1}|, |l-c_{k-1}|)`), seeded with the
   simple mean of the first 14 TRs.
2. **ATR baseline**: simple mean of ATR14 over bars `t-49..t` (SMA 50 of ATR14).
3. **ADX14**: Wilder's definition, period 14 (+DM/-DM, Wilder RMA smoothing of TR/+DM/-DM, DX, ADX = Wilder RMA
   of DX over 14), seeded with simple means. Defined only after 28 bars.
4. **EMA20** of close, `alpha = 2/21`, seeded with the SMA of the first 20 closes.
5. **Session VWAP**: per UTC calendar day, `sum(typical_k * volume_k) / sum(volume_k)` over the day's bars
   `<= t`, `typical = (h + l + c) / 3`; undefined until the day has >= 6 bars with volume > 0.

Any undefined indicator at t -> `NO_SIGNAL_WARMUP`.

### 3.2 Regime gate (blocks every signal when false)

`ADX14_t >= 22.0` AND `ATR14_t >= ATR_baseline_t`. Otherwise `REGIME_CONSOLIDATION` (no signal). Thresholds are
fixed; no other value is evaluated.

### 3.3 Entry conditions

LONG at t iff all hold:
1. regime gate true;
2. `close_t > VWAP_t`;
3. pullback touch: `low_t <= EMA20_t`;
4. bullish reversal candle: `close_t > open_t` AND `close_t > EMA20_t` AND `close_t >= low_t + 0.6 * (high_t - low_t)`;
5. trend slope: `EMA20_t > EMA20_{t-5}`.

SHORT mirrored (`close_t < VWAP_t`, `high_t >= EMA20_t`, `close_t < open_t`, `close_t < EMA20_t`,
`close_t <= high_t - 0.6 * (high_t - low_t)`, `EMA20_t < EMA20_{t-5}`).

At most one open position per symbol; a signal while a position is open is `POSITION_OPEN` (not queued).

### 3.4 Execution model

- Entry at the **next M5 bar OPEN** (`x_{t+1}`), the engine's `simulate_fill(at="open")` convention;
  `STALE_SIGNAL` if `x_{t+1}.time - decision_time > 300`.
- **No post-only / maker model.** IC Markets MT5 CFDs offer no post-only flag, and a resting limit at VWAP would
  need a queue/fill model this project has no evidence for. BTCUSD is therefore simulated as a market (taker)
  entry with the full spread + DEMO p90 slippage, like XAUUSD. A limit-at-VWAP variant is out of scope and may
  not be added after results are seen.

### 3.5 Exits (frozen at the fill)

- `d = 1.5 * ATR14_t` (decision bar). Stop: BUY `fill - d`, SELL `fill + d`.
- Target: BUY `fill + 2.25 * ATR14_t`, SELL `fill - 2.25 * ATR14_t`.
- Trailing: inactive until a bar's favourable extreme reaches `fill +/- 1.0 * ATR14_t`; from the NEXT bar on, the
  stop trails at `best_price -/+ 1.5 * ATR14_t` (only ever tightened; never beyond the target).
- Stop and target touched in the same bar: the stop is assumed first (conservative).
- Time stop: close at the open of the first bar `>= entry_time + 4 h` (`TIME_EXIT_4H`).
- Gap-through and exit-spread handling exactly as H9 sections 3.10-3.11 (exit spread = max(exit bar, fill bar)).

### 3.6 Costs, permission, sizing

Frozen `config/default.toml` costs at authorization time (XAUUSD commission 7.03/lot round trip, slippage
0.41/fill; BTCUSD commission 0, slippage 11.97/fill), uncertainty margin 10 %. Spread 0 = unknown ->
`COST_UNKNOWN`. A hold that can cross the broker rollover with unknown swap -> `COST_UNKNOWN_SWAP`. News: the
shipped HIGH-impact window ([-30, +30) minutes as of 2026-10-08). Spread cap: the shipped `max_spread_price`
(XAUUSD 0.20, BTCUSD 15.0) applied to the fill bar's spread (`SPREAD_ABOVE_CAP`). Sizing:
`calculate_safe_volume` at 0.25 % of fold equity on `d`; `evaluate_risk_gate` including the daily loss limit
(realized + floating). No `raw_confidence`, no EV gate.

## 4. Primary metric and pass criteria (all must hold, per symbol)

- >= 100 accepted trades;
- mean **net** R > 0 and the episode-clustered bootstrap 95 % CI lower bound of mean net R > 0;
- mean net R > 0 at 1.5x costs;
- net R > 0 in >= 8 of 12 folds;
- deflated Sharpe ratio >= 0.95 counting **all** ledger trials (>= 263);
- PBO not computed (single configuration) -- recorded as such.

Outcome labels: REJECTED (any criterion fails) / DEVELOPMENT CANDIDATE (all pass). A DEVELOPMENT CANDIDATE is
still exposed-data evidence: the only next step is a non-trading shadow observer with a forward gate of >= 30
calendar days AND >= 50 accepted shadow trades. Never OOS access, DEMO entries or a release from this result.

## 5. After the run

Exactly one run per symbol, from a tagged clean tree. Results, ledger rows and the funnel are committed whatever
the outcome. If REJECTED: strategy research returns to FROZEN and this family is closed.
