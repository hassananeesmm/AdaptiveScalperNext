# H9 h9r1 results -- H9 REJECTED -- NO VALIDATED EDGE; STRATEGY RESEARCH FROZEN (2026-10-02)

BACKTEST evidence on **EXPOSED DEVELOPMENT DATA** (the H6 BTCUSD M15 set). Not validation. Run exactly once.
Reserved OOS 2026-07-01..2026-09-18 **sealed** (see "Data access" for the disclosed pre-registration incident).

## Reproducibility

| Item | Value |
|---|---|
| Pre-registration | `docs/research/V2_H9_PREREGISTRATION_2026-10-02.md`, commit `eb2f9ae` (alone, before any code) |
| Implementation | `eb9fb74` = tag `h9r1-prerun` (clean tree enforced by the runner) |
| Trial | `v2h9:h9r1:BTCUSD:H9-PRIMARY`, family `v2-H9:BTCUSD`, ledger row id 262 (ledger 261 -> 262) |
| Data | BTCUSD M15, 92,660 bars, bar opens 2023-11-01 00:00 .. 2026-06-30 00:00 UTC, checksum `ede3898a45df163d66207fb5ad6ed745fde3853d358010da4e648de3315d03a8` (= H6); data gate PASSED (all 10 checks) |
| Folds | H6's 12 index folds of 7,721 bars (pre-registration section 2.1) |
| Config fingerprint | `f787763f08d9140d1fe3456e06e98be9444dfab1d2b462425d839aaac3c16296` |
| Costs (frozen) | slippage 11.97/fill (DEMO p90), commission 0, margin 10 %, spread from the bar; swap unknown -> rollover holds rejected |
| H6 PBO universe | `data/research/v2h6_BTCUSD_v2r3.json` sha256 `7770fc6f...0104645` verified, 39 vectors |
| Environment | Python 3.13.15, numpy 2.5.3, scipy 1.18.1; runtime 16.8 s |
| Machine-readable | `data/research/v2h9_BTCUSD_h9r1_run.json` (sha256 `6510c0e926bb9fe2a98befb72c59bb9985dad99caf30195600f8ec1bad6141ee`), ledger row `docs/research/V2_H9_LEDGER_h9r1.json` |

## Funnel

1,019 threshold-cross events (episodes), every one consumed once:

| Outcome | Count |
|---|---|
| `VOLATILITY_CONDITION_NOT_MET` | 757 |
| `COST_UNKNOWN` (decision bar spread 0 = no evidence) | 100 |
| `COST_R_ABOVE_0_05` | 85 |
| `NO_SIGNAL_INSUFFICIENT_VOL_HISTORY` | 33 |
| `COST_UNKNOWN_SWAP` (4 h hold crosses the broker rollover) | 27 |
| `COST_UNKNOWN_AT_FILL` / `COST_R_ABOVE_0_05_AT_FILL` | 2 / 2 |
| **Accepted trades** | **13** (13 episodes, 0.09 trades/week) |

News windows applied: 0 (the research copy holds no HIGH-impact event applicable to BTCUSD, as in H6).

## Primary metrics

| Metric | Value |
|---|---|
| Trades | 13 |
| Gross R mean | **-0.293** (sum -3.81) |
| Cost R mean | 0.037 (entry 0.018, exit 0.019, commission/fee/swap 0) (sum 0.49) |
| Net R mean | **-0.330** (sum -4.29) |
| Gross 95 % CI, episode clusters | [-0.713, +0.172] |
| Net 95 % CI, episode clusters | [-0.751, +0.134] |
| Gross / net 95 % CI, UTC-day clusters (diagnostic) | [-0.710, +0.174] / [-0.748, +0.137] |
| Folds net-positive | 2 / 12 (6 folds had no trade): [-1.20, 0, -1.02, 0, 0, 0, 0, +1.45, +0.75, 0, -3.88, -0.40] |
| Sharpe per trade (net) | -0.400 (skew 0.92, kurtosis 2.47) |
| PSR (vs 0) | 0.123 |
| DSR | 0.048 (N = 40 = 39 `v2-H6:BTCUSD` + H9, from the ledger; cross-trial Sharpe variance 0.00637) |
| PBO (CSCV, 6 groups, 40 members) | 0.20 |
| Aggregate gross / cost | -7.81 (gross is negative) |
| Cost stress, mean net R | x1.0 -0.330, x1.2 -0.338, x1.5 -0.349, x2.0 -0.368 |
| Exits | STOP_LOSS_HIT 6 (mean gross -0.98 R), TIME_EXIT_4H 7 (mean gross +0.29 R) |
| Safety violations | 0 |
| Forbidden data access (runner) | 0 |

## The 13 pre-registered criteria

| Criterion | Result |
|---|---|
| `trade_count_ge_100` | FALSE (13) |
| `gross_mean_positive` | FALSE (-0.293) |
| `gross_episode_ci_lower_positive` | FALSE (-0.713) |
| `net_mean_positive` | FALSE (-0.330) |
| `net_episode_ci_lower_positive` | FALSE (-0.751) |
| `folds_8_of_12_positive` | FALSE (2) |
| `psr_ge_095` | FALSE (0.123) |
| `dsr_ge_095` | FALSE (0.048) |
| `pbo_le_020` | TRUE (0.20; uninformative with 13 trades and 6 empty folds) |
| `gross_cost_ratio_ge_3` | FALSE (gross negative) |
| `cost_x1_2_net_positive` | FALSE (-0.338) |
| `zero_safety_violations` | TRUE |
| `zero_forbidden_data_access` | TRUE (runner) |

**Classification: H9 REJECTED** (fundamental failures: n < 100, gross <= 0, net <= 0, gross episode-CI lower <= 0).

## Descriptive diagnostics (never used to rescue H9)

- Direction: BUY 8 trades, mean gross -0.31 R; SELL 5, -0.26 R. Sessions: every session negative.
- Volatility quartiles of the 13 entries (RV24): Q1 4 trades +0.31 R gross, Q2-Q4 negative. With 3-4 trades per cell
  this is noise; it is **not** a finding and creates no H9b.
- t-stat at entry: median 2.02 (range -2.46..+2.30). RV rank inside its 30-day baseline: median 0.96.
- Stop d = 1.5 ATR14: median 940 USD (650..1,327). Decision cost_R median 0.040, fill cost_R median 0.041 (all <= 0.05
  by construction); events rejected for cost had decision cost_R median 0.093.
- Holding: median 4 h; 6/13 stopped. MFE/MAE (M15 OHLC bounds, descriptive): mean 0.63 R / 0.74 R.

## What it means

1. **The signal rarely survives honest friction.** 74 % of threshold crossings happen without high recent volatility;
   most high-volatility crossings are rejected because the frozen cost is above 5 % of a 1.5-ATR stop, the spread
   evidence is missing, or the hold would cross an unpriced swap rollover. 13 trades in 2.7 years cannot support any
   claim of edge.
2. **Where it did trade, it lost before costs** (gross -0.29 R; costs only 0.04 R). H9 did not die of friction; it
   showed no gross edge in the cases it was allowed to take.
3. The volatility-conditioned continuation mechanism described for crypto does not show up on this broker's BTCUSD
   CFD at the 4 h horizon with these gates. Nothing here justifies another parameterization on the same data.

## 0.2.7 exit-cost observations (read-only, descriptive)

Production DB opened read-only; BTCUSD economic exit events, FINAL, owned positions, INOUT/OUT_BY/conflicts
excluded (deals 2026-09-25..2026-10-02):

| Exit kind | n | Exit slippage median / p90 (USD, + = adverse) | Reference |
|---|---|---|---|
| STOP_LOSS | 64 | 0.67 / 8.41 | deal-comment trigger |
| AGENT_CLOSE | 7 | 0.00 / 0.74 (quote spread 5.00) | close quote |
| TAKE_PROFIT | 1 | 50.17 (n = 1, unusable) | deal-comment trigger |
| UNKNOWN | 166 | not computed (no provable reference) | none |

Against the frozen 11.97 USD per fill, the observed stop and agent-close slippage is lower: the assumption appears
**conservative**, not understated (small samples; no recalibration). It cannot change the verdict: H9's gross mean is
negative before any cost.

## Data access

- The H9 runner loaded only the SQL-bounded H6 range (last bar 2026-06-30 00:00 UTC); `forbidden_bars` = 0; the data
  gate passed before any rule. H7 not used; H8 not rerun.
- **Disclosed incident (pre-registration section 1.1):** during the pre-implementation audit a read-only
  `COUNT(*)` of BTCUSD M15 rows after 2026-06-30 00:00 returned 7,760, which counts rows inside the sealed OOS. No
  value of any such row was read; it cannot have informed H9 (the rule was fixed by the request). For human review.
- The research copy received: ledger row 262 and table `h9_consumed_fingerprints` (1,019 rows); its schema is now 31
  (`open_research_db` applies pending migrations; the last earlier runner, H8, predates migration 0031). The
  production database was only read (exit-cost query).

## Decision

**H9 REJECTED -- NO VALIDATED EDGE; STRATEGY RESEARCH FROZEN.** No forward PAPER, no H9b / H10 / variants, no OOS
access, no deployment. The deployed 0.2.7 DEMO safety runtime, its observability, the research history and the
sealed OOS are preserved. Reopening strategy research needs new independent data accumulated after this freeze,
materially new information, or an explicit human decision to start a new pre-registered programme with
multiple-testing accounting.
