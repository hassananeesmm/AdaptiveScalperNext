# H9 pre-registration -- BTCUSD volatility-conditioned sparse time-trend continuation (2026-10-02)

Committed BY ITSELF, before any H9 signal, simulation or statistics code exists. Branch
`research/h9-btc-vol-trend` (worktree `.worktrees/h9`), base `release/0.2.7` @ `5be15d8` with
`research/h8-xau-bpr` @ `249d978` merged in (`9639bdc`) for the research package, ledger code and H6/H8 history.

## 1. Status of this hypothesis -- read first

- H9 is a **NEW family** (`v2-H9:BTCUSD`). It is **not** an H6, H7 or H8 rescue: no Donchian level, no breakout
  context, no pullback, no M1/M5 trigger, no target, no V1 strategy logic.
- The BTCUSD M15 development data below **was exposed** during H6 (39 BTCUSD trials). It is labelled
  **EXPOSED DEVELOPMENT DATA**, never "unseen" and never "OOS". A development pass is therefore **not independent
  validation**.
- The only genuinely new evidence after this run is **future PAPER/shadow data collected after the frozen H9
  commit**.
- The reserved OOS `2026-07-01 00:00 UTC <= t < 2026-09-19 00:00 UTC` stays **SEALED**. The H7 XAUUSD holdout is
  consumed and irrelevant here (BTCUSD only). H8 is not rerun, modified or mined.
- Exactly **one** primary parameterization (section 3). **No rescue run** follows a failure: no H9b, no threshold,
  window, percentile, stop, horizon, session, direction or subgroup variant.
- Strategy research is **frozen** if H9 fails (section 11).

### 1.1 Disclosed pre-registration incident (sealed-interval metadata)

During the pre-implementation data audit (2026-10-02, this session, before this document), one read-only query on
the research DB copy ran `SELECT COUNT(*) FROM bars WHERE canonical_symbol='BTCUSD' AND resolution='M15' AND
ts_utc > 1782777600`, which returned **7760**. That count spans 2026-06-30 00:15 onward and therefore includes
rows inside the sealed OOS interval. **No price, spread, volume, timestamp list, hash or statistic of any such row
was read, and the number cannot inform any H9 rule** (every H9 rule is fixed by the request that specified H9 and by
this document). It is recorded here for human review rather than hidden. The H9 runner's own
`zero_forbidden_data_access` criterion (section 8) measures the runner's data access; the final report states this
incident explicitly beside it.

## 2. Data (identical to H6; verified before this document)

| Item | Value |
|---|---|
| Source | research DB copy `data/research/v2_20260929.sqlite3` (table `bars`), IC Markets DEMO terminal history (ICMarketsSC-Demo), broker symbol `BTCUSD`, canonical `BTCUSD`, server clock converted to UTC (`UTC+2/US_DST`) |
| Timeframe | M15 only |
| Range loaded | `get_bars(BTCUSD, M15, 1698796800, 1782777600)` (SQL-bounded, inclusive) = 2023-11-01 00:00 .. 2026-06-30 00:00 UTC (first/last bar OPEN times) |
| Bars | **92,660** |
| Checksum | `compute_bars_checksum` = **`ede3898a45df163d66207fb5ad6ed745fde3853d358010da4e648de3315d03a8`** (equals H6 `v2h6_BTCUSD_v2r3.json` and the 39 `v2-H6:BTCUSD` ledger `dataset_id`s) |
| Quality (verified) | strictly increasing, no duplicates, all aligned to 900 s, finite positive OHLC, high/low consistent, spread >= 0; 164 gaps (133 of 1 h maintenance, max 40,500 s); **32,502 bars carry spread 0** (missing spread evidence, concentrated in folds 2-5 and 8-9) |

The requested window `< 2026-07-01 00:00` is wider than the immutable H6 dataset, which ends with the bar opening
2026-06-30 00:00. H9 uses **exactly the H6 dataset**; it never loads a later bar. The runner aborts before any rule
is evaluated if the count, first/last time or checksum differ (`H9 DATA BLOCKED`), or if any loaded bar has
`time >= 1782864000`. No forward fill, no synthetic bars, no resampling, no other provider.

### 2.1 Folds (H6's, recovered, not redesigned)

`fold_index_ranges(92660, 12, feature_lookback=20)` = 12 contiguous index folds of 7,721 bars (the last 8 bars,
2026-06-29 22:15 .. 2026-06-30 00:00, are in no fold, exactly as in H6):

| Fold | Indices | First bar (UTC) | Last bar (UTC) |
|---|---|---|---|
| 0 | 0..7720 | 2023-11-01 00:00 | 2024-01-21 00:30 |
| 1 | 7721..15441 | 2024-01-21 00:45 | 2024-04-11 03:45 |
| 2 | 15442..23162 | 2024-04-11 04:00 | 2024-07-01 00:30 |
| 3 | 23163..30883 | 2024-07-01 00:45 | 2024-09-19 19:15 |
| 4 | 30884..38604 | 2024-09-19 19:30 | 2024-12-09 15:45 |
| 5 | 38605..46325 | 2024-12-09 16:00 | 2025-02-28 10:15 |
| 6 | 46326..54046 | 2025-02-28 10:30 | 2025-05-20 07:00 |
| 7 | 54047..61767 | 2025-05-20 07:15 | 2025-08-09 13:45 |
| 8 | 61768..69488 | 2025-08-09 15:45 | 2025-10-29 11:30 |
| 9 | 69489..77209 | 2025-10-29 11:45 | 2026-01-18 07:45 |
| 10 | 77210..84930 | 2026-01-18 08:00 | 2026-04-09 14:30 |
| 11 | 84931..92651 | 2026-04-09 14:45 | 2026-06-29 22:00 |

Warm-up: signal features are computed over the whole series **causally** (bar t uses only bars `<= t`), so folds
1-11 warm up from EARLIER bars (past data, never future). Fold 0's first ~31 days cannot produce a signal
(`NO_SIGNAL_INSUFFICIENT_VOL_HISTORY`). Trading is simulated per fold exactly like H6/H8: flat at fold start,
equity 10,000, fresh risk state; a decision belongs to the fold of its decision bar t; its fill bar must be in the
same fold (else `NO_NEXT_BAR_IN_FOLD`); a trade still open at the fold's last bar closes at that bar's close
(`BACKTEST_RANGE_ENDED`). The trend-episode state machine runs over the whole series (an episode never restarts at a
fold boundary).

## 3. The locked rule (H9-PRIMARY)

Symbol BTCUSD, M15 completed bars only. `x_k` = `bars[k]`; `decision_time(t) = x_t.time + 900`.

1. **Trend t-stat.** On the 96 bars `t-95..t` (by position, a 96-bar window), OLS `log(close_i) = a + b*i + e_i`,
   `i = 0..95`. HAC (Newey-West) variance of `b`: `V = (X'X)^-1 S (X'X)^-1`, `S = sum_t e_t^2 x_t x_t' +
   sum_{l=1..3} w_l sum_{t>l} e_t e_{t-l} (x_t x_{t-l}' + x_{t-l} x_t')`, `w_l = 1 - l/4` (Bartlett, max lag 3), no
   prewhitening, no small-sample/df correction. `trend_tstat = b / sqrt(V_bb)`. Non-finite or `V_bb <= 0` -> no
   evaluation at t (treated as "no previous evaluation" for the next bar).
2. **RV24.** `r_k = ln(close_k / close_{k-1})` between consecutive stored bars. `RV24_t = sqrt(sum_{k=t-95..t} r_k^2)`
   (defined for `t >= 96`).
3. **Volatility baseline.** `W0 = x_{t-95}.time` (the window's first bar). Baseline = `{RV24_s : s >= 96, s <= t-96,
   x_s.time >= W0 - 2,592,000}` (the previous 30 UTC days; `s <= t-96` guarantees no return of the current window).
   Sufficient history iff `x_96.time <= W0 - 2,592,000`; otherwise `NO_SIGNAL_INSUFFICIENT_VOL_HISTORY` (no shorter
   fallback). `P75` = 75th percentile, linear interpolation between closest ranks (Hyndman-Fan type 7,
   `numpy.percentile(..., method="linear")`), implemented deterministically in pure Python. Condition:
   `RV24_t >= P75`.
4. **Transition (episode start).** Evaluations at consecutive bars `t-1`, `t` (both defined). LONG event iff
   `tstat_{t-1} < +2.0` and `tstat_t >= +2.0`; SHORT event iff `tstat_{t-1} > -2.0` and `tstat_t <= -2.0`. No
   event while the statistic merely stays beyond the threshold; a new event of the same sign needs the statistic
   back inside `(-2, +2)` and a fresh crossing. Threshold exactly 2.0; no other value is evaluated.
5. **Direction.** LONG -> BUY, SHORT -> SELL. No reversal variant.
6. **Fingerprint / episode id.** `H9|BTCUSD|<BUY|SELL>|<decision_time>`. Consumed at the event, whatever its
   outcome (traded or rejected), in the run's store and persisted to the research DB table
   `h9_consumed_fingerprints`; a consumed event is never retried (no later entry in the same episode, not even
   with a cheaper spread).
7. **Volatility at the transition.** If the condition (3) is false at the event -> `VOLATILITY_CONDITION_NOT_MET`
   (consumed). Volatility becoming high later in the same episode never creates an entry.
8. **Stop.** `ATR14_t` = simple mean of the 14 true ranges of bars `t-13..t` (`TR_k = max(h_k-l_k, |h_k-c_{k-1}|,
   |l_k-c_{k-1}|)`). `d = 1.5 * ATR14_t`, frozen at decision. At the fill: BUY stop `= fill - d`, SELL `= fill + d`.
   Never widened, moved, trailed or replaced (never `cost/0.05`, never `max(...)`). A stop closer than the broker
   `trade_stops_level` -> `STOP_INVALID` (rejected, never repaired).
9. **Entry.** Next M15 bar OPEN (`x_{t+1}`), the engine's `simulate_fill(at="open")` convention. Rejected as
   `STALE_SIGNAL` if `x_{t+1}.time - decision_time > 900` (at most one missing bar).
10. **Exits (only two).** Bars from the fill bar onward: (a) if `bar.time >= entry_time + 14,400` -> close at that
    bar's OPEN (first executable price at/after 4 h, i.e. normally the 17th bar = after 16 completed bars),
    `TIME_EXIT_4H`, checked BEFORE that bar's range; (b) else if the bar's executable side touches the stop ->
    `STOP_LOSS_HIT`, fill `min(stop, open - half_spread) - slippage` (BUY; mirrored for SELL), the engine's
    gap-through convention. The fill bar's own range after its open participates (the position exists then). No
    target, trailing, break-even, partials, scaling or learned/regime exit. Fold end -> `BACKTEST_RANGE_ENDED`.
11. **Conservative exit spread.** Bars with spread 0 have no spread evidence. Every exit uses
    `max(exit bar spread, fill bar spread)` (the fill bar spread is always known for an accepted trade).

## 4. Cost gate -- rejects, never rescues

`round_trip_cost(bar) = (spread(bar)*point + 2*slippage + commission_price + swap) * (1 + 0.10)` with the frozen
BTCUSD assumptions of `config/default.toml` (`slippage_price 11.97` = DEMO p90, commission 0.0/lot, provenance
`BROKER_DEMO_CONFIRMED`, uncertainty margin 10 %). The full spread once = half at entry + half at exit (the fill
model's convention).

- Spread unknown (`spread <= 0`) -> `COST_UNKNOWN` (decision bar) / `COST_UNKNOWN_AT_FILL` (fill bar).
- **Swap.** BTCUSD has NO broker swap evidence (every measured DEMO position was intraday; all 320 BTCUSD deals show
  swap 0). Swap is therefore a known 0 only when the locked hold does not cross the broker rollover (server midnight
  under `UTC+2/US_DST`). If `[entry, entry + 4 h]` contains a server-midnight -> swap is an unknown required cost ->
  `COST_UNKNOWN_SWAP` (decision, planned entry = decision_time) / `COST_UNKNOWN_SWAP_AT_FILL` (actual fill time).
  This is fail-closed, fixed now, and is not a session filter chosen from results.
- `decision_cost_R = round_trip_cost(x_t) / d <= 0.05` else `COST_R_ABOVE_0_05`.
- `fill_cost_R = round_trip_cost(x_{t+1}) / d <= 0.05` else `COST_R_ABOVE_0_05_AT_FILL`.
- Both values are stored per trade.

## 5. Permission, risk and sizing (existing generic gates)

At the decision: one BTCUSD position maximum (`POSITION_OPEN`), daily-loss / drawdown halt (`RISK_HALT`,
`_risk_halt_reason`). At the fill, in order: staleness, news window (the HIGH-impact windows applied to BTCUSD as in
H6), cost/swap/cost_R (section 4), `calculate_safe_volume` (0.25 % of current fold equity on `d`, rounded DOWN, below
`volume_min` -> `SIZING` rejected, never rounded up), `evaluate_risk_gate`, `evaluate_portfolio_risk_gate`.

**Deliberate, documented deviation from `_revalidate_and_open`:** the engine's fill revalidation also runs
`evaluate_cost_gate`, whose expected-edge formula `EV = p*target - (1-p)*stop` treats the V1 heuristic
`raw_confidence` as a calibrated probability (not empirically established; see section 9). H9 has no target and must
not depend on `raw_confidence`, so H9 opens through a research-only wrapper that calls the same staleness, news,
sizing, risk, portfolio and fill primitives and replaces ONLY the EV gate with the cost_R gate above. V1 code and
semantics are unchanged.

Limits (unchanged, asserted): risk/trade 0.25 %, open+pending 0.75 %, daily loss 2.00 %, drawdown 5.00 %, 2 positions,
1 per symbol. No martingale, grid, averaging, pyramiding, recovery sizing.

## 6. One-shot identity and freeze

Trial `v2h9:h9r1:BTCUSD:H9-PRIMARY`, family `v2-H9:BTCUSD`, kind `RESEARCH_V2_H9`. The runner refuses if any
`v2h9:` row exists, if the tree is dirty, if the DB is the production DB, or if the data gate fails (nothing recorded
then). Before the run: code committed, clean tree, tag `h9r1-prerun`, full suite green; source SHA, this document's
SHA, config fingerprint, data checksum, ledger state (261 rows, 0 `v2h9`) and Python 3.13.15 / numpy 2.5.3 /
scipy 1.18.1 recorded. Once evaluation starts it is the single counted run. No ledger row is deleted or replaced; a
genuine implementation defect would be marked INVALID beside the original row and any replacement needs explicit
human authorization.

## 7. Statistics (locked)

Unit: per-trade R (`/ initial_monetary_risk`). Per trade: gross R, entry cost R, exit cost R,
commission/fee/swap R, total cost R, net R, fold, UTC day, episode, direction, t-stat, RV24, P75, ATR14, d,
decision/fill cost_R, holding time, exit reason, `mfe_r_ohlc_bound`, `mae_r_ohlc_bound` (descriptive only).

- **Cluster bootstrap**: 10,000 resamples, seed 20261002, 95 %, percentile; whole clusters resampled; statistic =
  pooled mean per-trade R. Primary cluster = trend episode; secondary (diagnostic) = UTC day.
- **PSR**: `probabilistic_sharpe_ratio` on per-trade NET R, benchmark 0.
- **DSR**: `deflated_sharpe_ratio`; `n_trials` = ledger count of `v2-H6:BTCUSD` trials (39, all COMPLETED, 0
  FAILED, verified 2026-10-02) + 1 (H9) = 40 -- read from the ledger at run time, never hard-coded; cross-trial
  Sharpe variance = sample variance of the completed `v2-H6:BTCUSD` Sharpes with n >= 30 plus H9's own (two-pass,
  as H8). Uncomputable -> criterion FALSE. Other BTCUSD families (H1-H5, independent, selector) are excluded as
  different families, as the request specifies; their count is reported descriptively.
- **PBO**: `probability_of_backtest_overfitting`, 6 groups, 12 x 40 matrix = the 39 immutable H6 BTCUSD
  `fold_net_r` vectors from `data/research/v2h6_BTCUSD_v2r3.json` (sha256
  `7770fc6f5dc125177a20079ed912557e674bf32a735451bda04bcc18c0104645`, verified at run time) + H9's 12-fold net-R
  sums. H6 is not rerun. Missing / unverifiable -> criterion FALSE.
- **Cost stress**: per trade `gross_R - k * total_cost_R`, k = 1.0, 1.2, 1.5, 2.0 (mean reported).
- **Folds**: positive iff `sum(net_R in fold) > 0`.
- **Gross/cost**: `sum(gross_R) >= 3 * sum(total_cost_R)`; non-positive aggregate gross fails.

## 8. Criteria and classification

All 13 REQUIRED, exact names: `trade_count_ge_100`, `gross_mean_positive`, `gross_episode_ci_lower_positive`,
`net_mean_positive`, `net_episode_ci_lower_positive`, `folds_8_of_12_positive`, `psr_ge_095`, `dsr_ge_095`,
`pbo_le_020`, `gross_cost_ratio_ge_3`, `cost_x1_2_net_positive`, `zero_safety_violations`,
`zero_forbidden_data_access`. Missing / None / NaN / inf / uncomputable = FALSE.

- **H9 REJECTED** -- n < 100, gross mean <= 0, net mean <= 0, gross episode-CI lower <= 0, any safety violation, or
  any forbidden access.
- **H9 MARGINAL** -- n >= 100, gross > 0, net > 0, gross episode-CI lower > 0, safety and data access clean, but
  another criterion fails. Not permission to tune.
- **H9 DEVELOPMENT CANDIDATE -- NOT VALIDATED** -- all 13 TRUE. Never "validated", "proven", "production ready".

Safety violations (criterion 12): overlapping BTCUSD positions; an accepted trade without known cost, with either
cost_R > 0.05, or with a swap-exposed hold; risk above 0.25 % of fold equity at entry; a stop not equal to
`fill -/+ d`; any exit reason other than the three above.

## 9. Known V1 limitation (documented, not fixed here)

V1 `raw_confidence` is heuristic (regime confidence x efficiency ratio, acceleration ratio x 2, regime confidence x
discount, proximity terms); `costs/edge.py` uses it as `p` in `EV = p*target - (1-p)*stop`. That treats an
uncalibrated score as a probability. The deployed V1 selector is NOT modified by H9; H9 never uses `raw_confidence`
and measures empirical expectancy directly. No ML is added.

## 10. Descriptive diagnostics (never used to rescue H9)

Trades/day and /week, long vs short, gross/net by direction, UTC session, volatility quartile; t-stat, RV, stop and
cost_R distributions; holding time; stop vs time-exit shares; MFE/MAE OHLC bounds; rejections by reason; episodes;
0.2.7 BTCUSD exit-cost observations (read-only, by economic exit kind, PROVISIONAL/FINAL respected) compared with the
frozen assumptions -- reported, never used to recalibrate; materially higher observed cost gets a sensitivity at the
higher defensible cost. A profitable subgroup inside a failed H9 is NOT a finding and creates no H9b.

## 11. After the run

- REJECTED -> `NO VALIDATED EDGE -- STRATEGY RESEARCH FROZEN`. No forward PAPER, no H10/H9b/variants.
- MARGINAL -> frozen; no OOS, no deployment; forward shadow only if explicitly useful, without parameter changes.
- DEVELOPMENT CANDIDATE -> freeze everything; build a non-trading shadow observer only; forward gate >= 30 calendar
  days AND >= 50 accepted shadow trades (whichever is later), forward pass = mean gross > 0, mean net > 0,
  episode-clustered net CI lower > 0, net > 0 at 1.2x cost, no safety / data / parameter / source changes; pass ->
  `READY FOR HUMAN OOS DECISION` (does not authorize OOS access).
- Never automatic: development -> OOS, forward -> OOS, OOS -> DEMO, DEMO -> REAL. No merge into `release/*` or
  `main`, no DEMO change, no restart, no production schema change, no broker order from H9.
