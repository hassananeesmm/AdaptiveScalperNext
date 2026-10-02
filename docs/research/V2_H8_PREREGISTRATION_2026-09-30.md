# Strategy research pre-registration, H8 (2026-09-30)

**Status: PRE-REGISTERED before any H8 code or run.** Parent commit `b6da097`
(`research/v2-on-0.2.6`, H6/H7 results). Research / PAPER only. Nothing here can reach
`order_send`; H8 is not registered in the runtime strategy registry and is off by default.
The reserved OOS (2026-07-01 .. 2026-09-18) is never read. The H7 holdout
(XAUUSD 2022-06-23 .. 2024-05-31) is CONSUMED and is never read again.

## 1. What H8 is (and is not)

H8 = **XAUUSD M15 breakout context -> new M5 pullback -> M1 resumption -> execution cost gate ->
existing risk governor -> PAPER research trade.** A continuous scalper: the engine scans every
completed M1 bar; during one directional episode it may take several scalps, but every trade needs a
genuinely NEW pullback and a NEW resumption. There is no frequency target and no cooldown.

It is **not** the liquidity-sweep "H8" of an older document, not a new meaning for H6 or H7 (whose
meanings are immutable), not a swing strategy and not a quota system.

## 2. Inference status (fixed before any result exists)

- **Donchian N20 is H6-derived / post-selection context.** H6 found, on this same development window,
  XAUUSD Donchian N20 gross +0.072 R (95 % CI +0.026..+0.118), cost 0.077 R, net -0.005 R (N48
  similar), within a 78-trial H6 programme (39 XAUUSD + 39 BTCUSD ledger trials) with selection /
  multiple-testing concerns. That is
  HYPOTHESIS-GENERATING evidence, never a validated edge. N20 is chosen (not N48) only to reduce
  researcher degrees of freedom; N is not optimized.
- H8 was designed AFTER observing H6 on the same data. **Any H8 development result - including an
  excellent one - is hypothesis-development evidence, not independent validation.** Independent
  evidence can only come from FUTURE forward PAPER / shadow data, or from the sealed OOS if and only
  if the operator later explicitly authorizes its one-shot use.
- Forbidden wording in any H8 output: "validated edge", "proven alpha", "profitable signal".

Known before writing this document: H1-H7 results (all seen); DEMO entry and stop-exit slippage
evidence (small samples; not used to change any assumption); a bounded data-COVERAGE query on the
research DB (counts and first/last timestamps per resolution strictly inside 2024-06-01 ..
2026-06-30, section 4). No H8 rule has been evaluated on any data.

## 3. Symbol, mode, risk

- Symbol: **XAUUSD only.** Mode: research backtest / PAPER only; runtime strategy off by default.
- Hard risk ceilings unchanged and enforced by the engine's own gates (`_revalidate_and_open`:
  cost gate, safe sizing, `evaluate_risk_gate`, portfolio gate, news windows): 0.25 % risk per
  trade, 0.75 % aggregate open + pending, 2.0 % daily loss, 5.0 % peak-to-trough, 2 simultaneous
  positions, **1 position per symbol** (so at most one open XAUUSD H8 trade). No martingale, grid,
  averaging down, scaling in, or stop widening.
- Each fold starts flat at 10,000 (as H6); equity, the day's realized P&L and peak equity evolve
  causally inside the fold and feed the risk gates.

## 4. Data (fixed)

| Use | Range | Resolutions |
|---|---|---|
| **H8 development** | 2024-06-01 00:00 .. 2026-06-30 23:59:59 UTC | XAUUSD M15, M5, M1 native broker bars |
| H7 holdout (consumed, refused) | 2022-06-23 .. 2024-05-31 | - |
| Reserved OOS (never) | 2026-07-01 .. 2026-09-18 | - |

- Research DB copy only (`assert_research_database` refuses the production DB). Bars are loaded
  with the range bounds above; the runner refuses any range that overlaps the reserved OOS or starts
  before 2024-06-01, before anything is loaded (and re-checks the loaded bars).
- **Folds:** 12 sequential folds = `fold_index_ranges(len(M15 bars), 12, feature_lookback)` over the
  M15 bars (identical construction to H6, so fold results are comparable with the H6 family for
  PBO). Each fold owns the M5/M1 bars whose open time lies in its M15 time span. No fold sees another
  fold's data; each starts flat.
- **Data-completeness precondition (run gate).** For every fold, M5 bars >= 0.90 x 3 x (M15 bars)
  and M1 bars >= 0.90 x 15 x (M15 bars) in that fold's span. If ANY fold fails, the run is
  **refused before any rule is evaluated**: no trial is recorded as COMPLETED and nothing about
  performance is computed. No substitution (other resolutions, a shorter window, another rule) is
  allowed under the H8 name.
- **Known at pre-registration:** the stored research DB currently FAILS this gate: inside the
  window, M15 has 49,099 bars (full span), M5 starts 2025-04-23, M1 starts 2026-06-09 (20,572 bars),
  and there are no ticks. Supplying the missing M5/M1 history (e.g. an operator-approved, read-only
  MT5 history import bounded to this window into a research DB copy) is an OPERATOR decision. The
  rules in this document do not depend on how the data is obtained; the data checksum is recorded.

## 5. Primary rules (ONE parameterization; nothing below is tuned)

Time convention: a bar's `time` is its OPEN time (MT5). A bar is COMPLETED at `time + length`.
At the close of M1 bar k (time `t_k + 60`) only bars completed at or before `t_k + 60` are visible.
Every decision is taken at an M1 bar close; M5 and M15 state only changes when an M5 / M15 bar
completes. No intrabar future knowledge anywhere.

### 5.1 M15 breakout context (Donchian N20)

- Long breakout at completed M15 bar b: `close_b > max(high)` of the 20 completed M15 bars before b.
  Short: `close_b < min(low)` of those 20 bars.
- A breakout creates a **breakout event** only when no context in that direction is active.
  Stored: `breakout_event_id = "XAUUSD:M15:<BUY|SELL>:<b.time>"`, `breakout_time` (b's completion
  time), `direction`, `breakout_level` (the broken 20-bar high / low).
- The context stays active until invalidated. Long context is invalidated by the first completed M5
  (or M15) close `< breakout_level`, or by a short breakout event; mirrored for short. A new
  same-direction breakout event can only start after invalidation.

### 5.2 M5 pullback (deterministic, mirrored)

For an active long context (short mirrored), using M5 bars completed after `breakout_time`:

- **Local extreme:** the running maximum M5 high since the breakout (or since the last pullback of
  this context was consumed); `extreme_time` = completion time of the bar that set it. A new higher
  high resets the extreme and discards any not-yet-triggered pullback.
- **Pullback confirmed** at completed M5 bar j when BOTH hold:
  1. at least **2** completed M5 bars after the extreme bar (up to and including j) closed below
     their own previous M5 close (not necessarily consecutive);
  2. retracement `extreme_high - min(low since the extreme bar)` >= **0.50 x ATR14(M5)**, where
     ATR14(M5) = arithmetic mean of the true range of the 14 M5 bars completed up to and including j
     (true range uses the previous M5 close).
- The pullback must not invalidate the context (5.1): any M5 close below `breakout_level` kills the
  context and every pullback in it.
- Stored: `pullback_id = "<breakout_event_id>:PB:<extreme_time>"`, `pullback_extreme` (the lowest low
  since the extreme bar; it keeps updating until the resumption decision), confirmation time.
- If the exact definition proves impossible to implement in this repository, work STOPS and the
  problem is documented before any other rule is used (a new pre-registration, never a silent edit).

### 5.3 M1 resumption trigger and the decision point

- After a pullback is confirmed, at the close of each completed M1 bar k (completed after the
  confirmation): long trigger if `close_k > max(high)` of the 3 completed M1 bars k-3..k-1. Short:
  `close_k < min(low)` of those bars.
- **The first trigger is the pullback's only decision point.** Whatever happens at it (accepted, or
  rejected by any gate), the fingerprint `XAUUSD | breakout_event_id | direction | pullback_id` is
  CONSUMED permanently and is never evaluated again (no retrying the same pullback bar after bar).
- A trigger while an XAUUSD position is open is rejected (`POSITION_OPEN`) and consumes the
  fingerprint. A second trade in the same context requires a NEW local extreme, a NEW confirmed
  pullback (new `pullback_id`) and a NEW M1 trigger.
- The consumed-fingerprint store is durable: a restart can never let a consumed fingerprint fire.
- Ticks (if any) may serve only as bid/ask, spread, freshness and execution-quality inputs. No OFI,
  VPIN, ML score or regime classifier is a trigger or a filter.

### 5.4 Cost gate (at the decision point)

- `round_trip_cost = spread + entry slippage + exit slippage + commission + uncertainty margin`,
  computed with the engine's own `estimate_cost` from the **configured** broker assumptions
  (`[costs.XAUUSD]`, `BROKER_DEMO_CONFIRMED` provenance): spread = decision M1 bar spread x point;
  slippage = 2 x the configured per-fill slippage (entry + exit); commission = configured round
  trip; uncertainty margin = the configured `uncertainty_margin_pct`; swap 0 (45-minute hold).
  No DEMO evidence and no "spread + 0.1 pip" shortcut replaces these assumptions.
- `cost_R = round_trip_cost / initial_price_risk` (the stop distance of 5.5). **Require
  `cost_R <= 0.05`.** Unknown or incomplete cost (missing or non-positive bar spread, missing
  assumption) -> REJECT. The friction floor in 5.5 makes the stop at least 20 x round-trip cost; it
  is not a "4 x friction stop".
- The engine's own fill-time revalidation (`_revalidate_and_open`: staleness <= 2 M1 bars, news,
  its cost/edge gate, sizing, risk, portfolio) still applies on top.

### 5.5 Entry, stop, target, time exit

- Entry: the next M1 bar's open via `_revalidate_and_open` (spread and configured slippage inside
  the fill; never the signal bar's price).
- Stop distance = **max** of: (1) structural: `close_k - pullback_extreme + spread_k` for a long
  (one spread beyond the pullback swing extreme; mirrored for short); (2) volatility floor:
  `0.50 x ATR14(M5)` at the decision; (3) friction floor: `round_trip_cost / 0.05`. The stop is set
  from the fill price and is **never widened** (nor moved) after entry.
- Target: fixed **2.0 R** (2 x stop distance from the fill).
- Time stop: **45 minutes** - at the first M1 bar whose open time is >= entry time + 2,700 s, the
  trade closes at that bar's open (`simulate_fill`, next-open semantics).
- Stop / target: `_intrabar_stop_or_target_hit` on M1 bars from the entry bar on (stop assumed first
  when both are in range; configured stop slippage). Range end: close at the last bar's close.
- No trailing, partial profit, break-even, dynamic/ML exit, averaging or scaling.

### 5.6 Session, news, regime

- Session (ASIA / LONDON / LONDON_NEW_YORK / NEW_YORK / LATE, UTC) is recorded as metadata only; no
  session filter. H7 failed its unseen replication; nothing about sessions is assumed.
- Existing news protection: the engine's news-window gate at fill time (as in H6) stays in force.
- Descriptive features (spread percentile state, ATR14(M5) tercile, the engine's M15 regime label)
  are logged for later analysis and decide nothing.

## 6. Trials, statistics and criteria

- **Exactly ONE counted trial:** `v2h8:h8r1:XAUUSD:H8-PRIMARY`, family `v2-H8:XAUUSD`, tag `h8r1`,
  appended to the research ledger (append-only). A run failure is recorded as FAILED. No variant
  is run; any variant later is a new pre-registered hypothesis and a new counted trial.
- Reported: trades; rejections by reason; independent breakout episodes; trades per episode; gross R,
  entry cost R, exit cost R, total cost R, net R; MFE, MAE; holding time; exit reasons; session,
  spread state, volatility state; P&L by fold, by breakout episode and by trading day (UTC).
- Trades from one episode are NOT independent. Confidence intervals: **cluster bootstrap by breakout
  episode** (primary) and by trading day (secondary); 10,000 resamples of whole clusters, seed
  20260930, percentile 95 % interval of the mean per-trade R.
- PSR: `probabilistic_sharpe_ratio` on per-trade net R against 0.
- DSR: `deflated_sharpe_ratio` with N = number of ledger trials in `v2-H6:XAUUSD` + `v2-H8:XAUUSD`
  (H8 was selected after the 39 H6 XAUUSD trials on this same window; N = 40) and the cross-trial Sharpe
  variance of those completed trials (`min_observations` 30).
- PBO: CSCV (`probability_of_backtest_overfitting`, 6 groups) over the 12-fold net-R matrix of all
  COMPLETED `v2-H6:XAUUSD` trials (`fold_net_r` from `v2h6_XAUUSD_v2r3.json`) plus H8. If it cannot
  be computed, the PBO criterion FAILS (it is never waived).
- Cost stress per trade: `net_R(k) = gross_R - k x cost_R` for k = 1.2 (criterion), 1.5 and 2.0
  (information only).

### STRONG PASS requires ALL of

1. >= 100 accepted trades;
2. mean gross R > 0;
3. episode-clustered 95 % CI lower bound of mean gross R > 0;
4. mean net R > 0;
5. episode-clustered 95 % CI lower bound of mean net R > 0;
6. >= 8 of 12 folds net-positive;
7. PSR >= 0.95; 8. DSR >= 0.95; 9. PBO <= 0.20;
10. aggregate gross R >= 3.0 x aggregate cost R;
11. mean net R > 0 at cost x 1.20;
12. zero safety violations (every open trade within the ceilings of section 3; stop never widened;
    never two open XAUUSD trades; every accepted trade has a known cost with cost_R <= 0.05);
13. zero forbidden data access (no bar loaded outside 2024-06-01 .. 2026-06-30).

**Classification.** FAIL: fewer than 100 accepted trades, or mean gross R <= 0, or mean net R <= 0,
or any violation of 12-13. MARGINAL: mean gross R > 0 and mean net R > 0 with >= 100 trades, but
at least one of criteria 3, 5-11 missed. STRONG PASS: all 13. These thresholds are not changed after
any result is seen. A MARGINAL result is not a promotion; a STRONG PASS is still not "validated".

## 7. Run policy and stopping rule

1. This document is committed ALONE before any H8 code.
2. Implementation, causal / unit / negative-control tests and the full regression suite follow.
3. H8 runs **once** (tag `h8r1`) from a clean, committed tree; the runner refuses a dirty tree, the
   OOS, the H7 holdout, a failed data gate, and a second run (an existing `v2h8:h8r1:*` ledger row).
4. The ledger row and a results document are written; H8 is never re-tuned afterwards.
- **FAIL -> "H8 REJECTED."** No tuning of the Donchian length, ATR multiple, M1 lookback, target,
  time stop or cost gate to rescue it. The next family is H9 (preferred: BTCUSD volatility /
  liquidity-conditioned momentum-vs-reversal research; BTC inherits no XAU Donchian assumption).
- **MARGINAL ->** frozen; no sealed OOS, no DEMO; decide whether genuinely new forward PAPER data can
  resolve the uncertainty; no parameter tuning.
- **STRONG PASS ->** still not validated: freeze source commit, config, this document, the ledger and
  dependencies; collect new forward PAPER / shadow evidence. No DEMO strategy orders without explicit
  operator approval; the OOS stays sealed until the operator authorizes its one-shot use.
- If the implementation changes the hypothesis materially, that is a NEW pre-registered hypothesis
  and trial, never a silent edit of H8.
