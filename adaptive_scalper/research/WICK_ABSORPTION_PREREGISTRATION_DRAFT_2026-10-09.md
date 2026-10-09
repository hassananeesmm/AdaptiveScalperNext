# Wick-absorption trend continuation -- pre-registration DRAFT (2026-10-09)

Branch `research/adx-pullback-hypothesis`. Committed BY ITSELF: no signal, simulation or statistics code exists for
this hypothesis, and no market data was read to write it. Working name **WICK-ABS** (ledger family
`v2-H11:<SYMBOL>` if ever authorized; ADX-PB holds H10).

## 0. Status -- read first

- **NOT AUTHORIZED TO RUN.** Strategy research is **FROZEN** since H9 was REJECTED on 2026-10-02 ("no H10/H9b/
  variants"). This records the operator's 2026-10-09 spec as a fixed rule only. Running it needs a separate, dated
  operator decision in WORKLOG lifting the freeze **for this hypothesis alone**. Until then: no code, no data access,
  no ledger row.
- **No release code changes.** V1 strategies, selector, regime classifier and `tests/test_v1_strategy_freeze.py` are
  untouched. If ever implemented it lives under `adaptive_scalper/research/` and opens no broker order.
- **Live building blocks already exist** (branch `hardening/risk-and-blackout-tightening`, observation/gates only):
  `features/candle.candle_properties` (wick/body ratios), `features/ema.ema_last`, `features/adx.wilder_adx`. A run
  MUST import these exact functions so the test measures what the live system computes.
- **Overlap / rescue risk, declared.** WICK-ABS overlaps V1 `pullback_continuation`, H6/H8 pullback families and
  ADX-PB (EMA20 + ADX trend filter + rejection candle). It is admitted only as a NEW family fixed below; it is not a
  tuning of any of them. Deflated Sharpe counts **all** ledger trials (>= 263 now, >= 264 if ADX-PB has run).
- **Data exposure.** Every bar before the sealed OOS (`2026-07-01 <= t < 2026-09-19`, still SEALED) was exposed by
  H1-H9; a development pass is **not validation**.
- **One parameterization.** Every number below is fixed. No threshold, multiplier, timeframe, session or direction
  variant is run after a result is seen. Values the operator did not state are marked *(default)* and are fixed now.

## 1. Hypothesis

On M5, in a directional regime (ADX(14) > 20), a closed bar on the trend side of EMA(20) whose wick shows absorption
of the counter-move (lower wick >= 50 % of range for longs, upper wick for shorts) has positive expectancy **net of
measured DEMO costs** with a 1.0 ATR stop, a 1.8 ATR target and a break-even stop after 50 % of the target distance.

Null: mean net R per trade <= 0.

## 2. Data (fixed and checksummed at authorization, before any code)

| Item | Rule |
|---|---|
| Symbols | XAUUSD and BTCUSD, two separate trials, no pooling; GBPJPY excluded (no spread cap / cost evidence) |
| Source | research DB copy `data/research/v2_20260929.sqlite3`, table `bars`, IC Markets DEMO history, UTC |
| Timeframe | **M5** *(default; the operator heading said "1-Minute / 5-Minute"; M5 is the system's entry resolution)* -- signal, ATR, EMA, ADX and fills all on M5 |
| Range | every M5 bar `< 2026-07-01 00:00 UTC` in the copy; first/last bar, count and `compute_bars_checksum` recorded in an amendment before the runner exists |
| Folds | 12 contiguous index folds (`fold_index_ranges(n, 12, feature_lookback=60)`), flat at each fold start |

## 3. The locked rule (WICK-ABS-PRIMARY)

`x_t` = completed M5 bar t; decisions use bars `<= t` only.

### 3.1 Indicators

- `EMA20_t` = `ema_last(closes[..t], 20)` (alpha 2/21, SMA seed).
- `ADX14_t` = `wilder_adx(bars[..t], 14)`.
- `ATR14_t` = Wilder ATR(14) (sum-seeded Wilder smoothing of true range, same TR as `wilder_adx`).
- `P_t` = `candle_properties(x_t)`; `None` (no range / inconsistent bar) -> `NO_SIGNAL_UNDEFINED_BAR`.

### 3.2 Entry conditions

LONG at t iff all hold:
1. `close_t > EMA20_t`;
2. `ADX14_t > 20.0`;
3. `P_t.lower_wick_ratio >= 0.50` *(default; operator's first spec)*;
4. spread of `x_t` <= the symbol's shipped `max_spread_price` (XAUUSD 0.20, BTCUSD 15.0) *(default)*.

SHORT at t iff all hold: `close_t < EMA20_t`; `ADX14_t > 20.0`; `P_t.upper_wick_ratio >= 0.50`; spread condition 4.

Both conditions true is impossible (only one EMA side holds). At most one open position per symbol; a signal while
one is open is `POSITION_OPEN`.

Live gates applied as in DEMO (each is a block, never a signal): entry window [12:00, 20:00) UTC; HIGH-impact news
[-30, +30) minutes; daily-loss 2 % (realized + floating) with same-UTC-day lock.

### 3.3 Execution

Market entry at the **next M5 bar OPEN** (`x_{t+1}`); `STALE_SIGNAL` if `x_{t+1}.time - (x_t.time + 300) > 300`.
Full spread + DEMO p90 slippage per fill + commission. No limit / maker model.

### 3.4 Exits (frozen at the fill; `A = ATR14_t`)

- Stop: BUY `fill - 1.0 A`, SELL `fill + 1.0 A`.
- Target: BUY `fill + 1.8 A`, SELL `fill - 1.8 A`.
- Break-even: when a bar's favourable extreme reaches `fill +/- 0.9 A` (50 % of the target distance), from the NEXT
  bar the stop is `fill + d_be` (BUY) / `fill - d_be` (SELL), `d_be` = 2 pips = **XAUUSD 0.20, BTCUSD 2.0**
  *(default)*. Never loosened. Caveat recorded in advance: on BTCUSD 2.0 is below the median spread (5.0), so a
  break-even exit is usually a small net loss.
- Hard cut-loss: BTCUSD closes if the close-side price is more than 0.5 % against the fill (live `[hard_stop]`).
- Stop and target (or break-even stop) touched in the same bar: the adverse level is assumed first.
- Time stop: close at the open of the first bar `>= entry_time + 2 h` *(default)*.
- Gap-through and exit-spread handling exactly as H9 sections 3.10-3.11.

### 3.5 Sizing and costs

`calculate_safe_volume` at **0.25 %** of fold equity on `1.0 A` (the hard ceiling; the operator's 1 % is refused:
it exceeds `HARD_RISK_CEILINGS` and the 0.75 % total-open-risk limit). `evaluate_risk_gate` including the daily
loss limit. The primary metric is in R, so it is independent of the risk percentage. Costs: frozen
`config/default.toml` at authorization, uncertainty margin 10 %; unknown spread -> `COST_UNKNOWN`.

## 4. Primary metric and pass criteria (all must hold, per symbol)

- >= 100 accepted trades;
- mean **net** R > 0 and the episode-clustered bootstrap 95 % CI lower bound > 0;
- mean net R > 0 at 1.5x costs;
- net R > 0 in >= 8 of 12 folds;
- deflated Sharpe >= 0.95 counting **all** ledger trials;
- PBO not computed (single configuration), recorded as such.

Reference, not a criterion: with 1.8 R targets the gross break-even win rate is 1 / 2.8 = 35.7 %.

REJECTED (any criterion fails) / DEVELOPMENT CANDIDATE (all pass). A DEVELOPMENT CANDIDATE only earns a non-trading
shadow observer (>= 30 calendar days AND >= 50 shadow trades); never OOS access, DEMO entries or a release.

## 5. After the run

Exactly one run per symbol from a tagged clean tree; results, ledger rows and the funnel committed whatever the
outcome. If REJECTED: research returns to FROZEN and this family is closed.
