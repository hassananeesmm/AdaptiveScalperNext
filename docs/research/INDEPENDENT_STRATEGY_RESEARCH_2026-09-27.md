# Independent strategy research, loss investigation and selector study (2026-09-27)

**Evidence class: BACKTEST (historical broker bars, simulated fills) plus read-only DEMO ledger.**
Nothing here is live evidence of profitability, and nothing here changes live configuration.
No strategy is claimed profitable. No parameter was tuned. The reserved untouched OOS interval
(2026-07-01..2026-09-18) was not read: the research code refuses it by construction.

## 1. Method

- **Data.** A read-only online SQLite backup of the production database
  (`data/research/snapshot_20260927T080325Z.sqlite3`, quick_check ok, row counts identical to
  the source). All research writes (runs, trades, trials) went to that copy, never to production.
- **Ranges (development data only).** The same ranges as the earlier walk-forward:
  BTCUSD 2025-10-01..2026-06-30 (77,486 M5 bars), XAUUSD 2025-06-01..2026-06-30 (76,349 bars),
  GBPJPY 2025-06-01..2026-06-30 (80,305 bars). These ranges were already recorded as VALIDATION /
  WALK_FORWARD_FOLD data, so they are **development** data, not untouched test data.
- **Sessions.** For each symbol: one independent session per active strategy (6) plus the
  combined selector session (the live logic, all six strategies competing), 7 sessions in all.
  Each session has its own positions, risk state, equity (10,000 per fold), drawdown, costs,
  trade history and configuration fingerprint. Sessions are never pooled. Every session saw the
  identical bars: the bar checksum is recorded per session and `inputs_identical` is true for
  all three symbols.
- **Folds.** 12 sequential fixed-configuration folds per session. Each fold starts flat at the
  initial equity under the unchanged live risk limits (0.25 % per trade, 0.75 % aggregate,
  2 % daily loss, 5 % drawdown). `RiskLimits` cannot be raised, so no "unhalted" variant exists.
- **Costs.** BTCUSD and XAUUSD use `BROKER_DEMO_CONFIRMED` cost evidence. GBPJPY uses
  `UNVERIFIED_ASSUMPTION` (slippage evidence missing, ASN-007); its results are indicative only.
- **Engine.** The production causal engine (`backtest/engine.py`): fills at the next bar's open,
  intrabar stop-first ambiguity resolution, and every cost itemised once. No-lookahead is
  regression-tested by truncation invariance (`tests/test_independent_research.py`).
- **Trials.** 21 trials recorded in the research database's append-only ledger
  (18 `independent:<symbol>` + 3 `selector:<symbol>`), in addition to the 9 earlier trials.

Reproduce (from the release or the `feature/independent-strategy-research` branch; the research
DB must be a separate copy of the production DB):

```
python -m adaptive_scalper.cli research-snapshot --out data\research\<new>.sqlite3
python -m adaptive_scalper.cli independent-research --symbol BTCUSD --start 2025-10-01 --end 2026-06-30 ^
    --folds 12 --research-db data\research\<new>.sqlite3 --tag r1 --out data\research\independent_BTCUSD_r1.json
(same for XAUUSD and GBPJPY with --start 2025-06-01)
```

## 2. Reproduction of the earlier findings

Recomputed from the stored walk-forward trades (`backtest_trades`, run ids `wf:*`) with
`research/trade_analysis.py`: 1,505 trades; `microstructure_acceleration` 1,377 = **91.5 %**;
its average gross result **+0.005 R**, average cost **0.227 R**, net −0.222 R. All three
figures reproduce exactly.

## 3. Independent results per strategy (net after all simulated costs)

R values are per trade, relative to each trade's own initial risk. "Halted" = folds that hit a
risk halt (the sample is truncated by the live risk rule, not by the data).

| Symbol | Strategy | Trades | Win % | Gross | Costs | Net | Gross R | Cost R | Net R | PF | +folds | Halted | PSR |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| BTCUSD | momentum_continuation | 231 | 39.0 | +114.1 | 588.3 | −474.2 | +0.026 | 0.109 | −0.083 | 0.73 | 5/12 | 0 | 0.045 |
| BTCUSD | pullback_continuation | 85 | 40.0 | +101.5 | 299.6 | −198.1 | +0.051 | 0.147 | −0.097 | 0.66 | 5/12 | 0 | 0.081 |
| BTCUSD | range_breakout | 322 | 31.7 | −201.7 | 856.0 | −1,057.6 | −0.024 | 0.114 | −0.137 | 0.54 | 2/12 | 0 | 0.000 |
| BTCUSD | statistical_reversion | 1,150 | 43.9 | +150.5 | 5,887.2 | −5,736.7 | +0.006 | 0.216 | −0.210 | 0.52 | 0/12 | 11 | 0.000 |
| BTCUSD | volatility_expansion | 62 | 38.7 | −110.5 | 176.9 | −287.3 | −0.077 | 0.122 | −0.199 | 0.33 | 3/12 | 0 | 0.001 |
| BTCUSD | microstructure_acceleration | 860 | 27.4 | −873.8 | 4,959.7 | −5,833.5 | −0.043 | 0.242 | −0.285 | 0.39 | 0/12 | 12 | 0.000 |
| BTCUSD | *selector (live logic)* | 906 | 27.9 | −631.6 | 5,129.3 | −5,760.9 | −0.031 | 0.238 | −0.269 | 0.41 | 0/12 | 12 | 0.000 |
| XAUUSD | momentum_continuation | 325 | 40.3 | +433.8 | 1,235.4 | −801.5 | +0.057 | 0.170 | −0.114 | 0.67 | 4/12 | 0 | 0.003 |
| XAUUSD | pullback_continuation | 125 | 52.0 | +169.2 | 485.3 | −316.1 | +0.076 | 0.174 | −0.099 | 0.61 | 3/12 | 0 | 0.045 |
| XAUUSD | range_breakout | 265 | 37.4 | +197.6 | 630.3 | −432.6 | +0.034 | 0.114 | −0.079 | 0.73 | 3/12 | 0 | 0.031 |
| XAUUSD | statistical_reversion | 1,122 | 42.3 | −70.0 | 5,791.1 | −5,861.0 | −0.004 | 0.230 | −0.235 | 0.49 | 0/12 | 12 | 0.000 |
| XAUUSD | volatility_expansion | 49 | 32.7 | −76.0 | 107.2 | −183.2 | −0.077 | 0.105 | −0.182 | 0.40 | 3/12 | 0 | 0.013 |
| XAUUSD | microstructure_acceleration | 1,323 | 32.7 | +949.5 | 6,740.7 | −5,791.2 | +0.032 | 0.227 | −0.195 | 0.54 | 0/12 | 12 | 0.000 |
| XAUUSD | *selector (live logic)* | 1,326 | 33.5 | +654.8 | 6,476.2 | −5,821.4 | +0.025 | 0.218 | −0.193 | 0.53 | 0/12 | 12 | 0.000 |
| GBPJPY* | momentum_continuation | 261 | 40.2 | −2.4 | 824.6 | −827.0 | 0.000 | 0.128 | −0.128 | 0.61 | 1/12 | 0 | 0.001 |
| GBPJPY* | pullback_continuation | 83 | 45.8 | +155.9 | 319.8 | −163.9 | +0.076 | 0.156 | −0.080 | 0.70 | 4/12 | 0 | 0.109 |
| GBPJPY* | range_breakout | 127 | 29.9 | −64.1 | 289.6 | −353.7 | −0.021 | 0.093 | −0.114 | 0.59 | 2/12 | 0 | 0.014 |
| GBPJPY* | statistical_reversion | 1,377 | 44.3 | +1,003.9 | 6,744.6 | −5,740.8 | +0.030 | 0.202 | −0.172 | 0.58 | 0/12 | 11 | 0.000 |
| GBPJPY* | volatility_expansion | 28 | 39.3 | −84.9 | 67.6 | −152.5 | −0.123 | 0.099 | −0.222 | 0.28 | 3/12 | 0 | 0.006 |
| GBPJPY* | microstructure_acceleration | 898 | 29.2 | −1,428.1 | 4,544.7 | −5,972.8 | −0.066 | 0.209 | −0.275 | 0.39 | 0/12 | 12 | 0.000 |
| GBPJPY* | *selector (live logic)* | 1,019 | 31.2 | −1,015.7 | 5,028.4 | −6,044.1 | −0.041 | 0.204 | −0.245 | 0.44 | 0/12 | 12 | 0.000 |

\* GBPJPY costs are an unverified assumption; GBPJPY stays disabled.

**All 18 independent strategy/symbol sessions lose money after costs.** None has a positive
net R, and no PSR exceeds 0.11. DSR (deflated for the trials in each family) is ≤ 0.01
everywhere. (DSR's cross-trial Sharpe variance excludes trials with fewer than 30 trades; they
still count in the number of trials.) PBO of "pick the best strategy in-sample" is 0.00
(BTCUSD), 0.15 (XAUUSD) and 0.00 (GBPJPY). A low PBO here only means the in-sample ranking
stays consistent out of sample. It is **consistently negative**, so it is not evidence of an
edge.

## 4. Gross versus net: why the system loses

1. **No strategy's gross edge covers its costs.** The best gross results are +0.05..+0.08 R
   per trade (pullback / momentum / range_breakout on XAUUSD), while their costs are
   0.11..0.17 R. The two high-frequency strategies (`microstructure_acceleration`,
   `statistical_reversion`) have gross results of −0.07..+0.03 R and costs of 0.20..0.24 R.
2. **In aggregate the losses are cost-driven.** Summed over every trade, gross is near zero
   while costs are large (microstructure XAUUSD: gross +950, costs 6,741). Per trade, most
   losing trades are genuine adverse moves: stop-loss exits dominate (for example 301 stop exits
   on XAUUSD, gross −4,782). Winners and losers roughly cancel, and costs are the net drag.
   Both statements hold together. The loss class counts per session are in the JSON reports
   (`COST_ONLY_LOSS`, `COST_DOMINATED_LOSS`, `ADVERSE_MARKET_LOSS`).
3. **Short holding makes it worse.** For `microstructure_acceleration`, trades held under
   5 minutes are strongly negative gross (−0.24 R XAUUSD, −0.48 R BTCUSD, −0.62 R GBPJPY).
   Trades held 5–15 minutes are slightly positive gross (+0.06..+0.12 R) but pay about 0.22 R
   in costs. Trades exited at the 600 s maximum hold are net positive on every symbol.
   "Original strategy thesis invalidated" exits (411–654 trades per symbol) lose about
   −0.06..−0.09 R gross before costs.
4. **Symbol, session and direction do not rescue it.** Every session bucket and both directions
   are net negative for the dominant strategies on every symbol. `RANGE` regime entries are
   93–97 % of microstructure trades.

## 5. The selector: does expected net edge pick the better trade?

`selector.select_proposal` ranks candidates by `p·target − (1−p)·stop − cost` in price units,
where `p` is the strategy's raw confidence taken at face value.

| Symbol | microstructure share of selections | stated p (selected) | confidence capped at 1.0 | expected net R (selected) | realized net R (selected, own session) | expected cost R | realized cost R |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSD | 92.7 % | 0.77 | 17 % | +0.76 | −0.28 | 0.17 | 0.24 |
| XAUUSD | 88.9 % | 0.78 | 20 % | +0.81 | −0.20 | 0.14 | 0.22 |
| GBPJPY | 90.1 % | 0.79 | 19 % | +0.75 | −0.28 | 0.23 | 0.21 |

Findings:

- **The confidence is not a probability.** `microstructure_acceleration` sets
  `raw_confidence = min(1, 2 × |acceleration| / ATR)`. Across all confidence buckets its
  realized gross hit rate is flat at 0.38–0.48, including the bucket that claims 1.00.
- **Expected edge does not predict realized edge.** Pooled over every qualifying candidate
  matched to its own strategy's outcome, the expected net R rises from about +0.1 to +1.4 across
  the five quintiles, while realized net R stays flat at −0.17..−0.30 in every quintile.
- **The bias is systematic.** The strategy with the most inflated confidence (and 1.5 ATR
  targets) wins 77–84 % of contested bars (BTCUSD 48/57, XAUUSD 86/105, GBPJPY 59/77). Most
  wins come against `statistical_reversion`.
- **Cost is underestimated, but it is the smaller error.** The selector's cost estimate is
  about 28–36 % below realized simulated cost on BTCUSD and XAUUSD. The dominant error is the
  roughly 1 R overstatement of gross edge.
- **Replacing the winner would not have helped on this data.** The candidates that lost the
  ranking are also negative when taken in their own sessions (for example XAUUSD
  statistical_reversion −0.34 R, momentum −0.38 R). The selector amplifies the concentration,
  but it is not what turns a profitable system into a losing one.

**Implication (hypothesis for review, not implemented):** a selector that ranks on calibrated
probabilities (for example by per-strategy reliability bins from out-of-fold data) and on
measured rather than estimated cost would stop the concentration. It would not by itself create
an edge, because no strategy is net-positive on its own. The live selector is unchanged.

## 6. Actual DEMO results (read-only ledger, snapshot 2026-09-27 11:43 UTC)

- Attributed closed trades: **70**. `microstructure_acceleration` 68 (33 W / 35 L, gross −106.75,
  commission −1.62, **net −108.37 USD**, PF 0.75, avg R −0.068). `statistical_reversion` 2
  (1 W / 1 L, net +9.53 USD). The other four strategies: 0 orders submitted. In total, attributed
  net is −98.84 USD.
- Exits: 52 adaptive exits net +111.51; 18 broker stop-loss exits net −210.35.
- BTCUSD has no recorded commission (spread-only pricing), so DEMO "gross" already includes
  spread and slippage. Recorded costs understate real friction.
- Reconciliation: 2,362 deals; SQL total = ledger total = 9,609.01 USD = broker balance and
  equity; discrepancy 0. Unattributed: 1,101 EXTERNAL_EXPERT (−4,576.72) and 8 UNKNOWN_SOURCE
  (+3,267.89) positions; 4 non-trade balance deals (+11,016.68).
- The DEMO sample is small and short-horizon. It agrees in sign with the research and is not
  independent evidence of anything else.

## 7. Limitations

- No point-in-time historical news calendar: no news block is applied in research.
- M5 OHLC bars; the intrabar stop-first rule is conservative.
- The 5 % drawdown halt truncates 11–12 of 12 folds for the two high-frequency strategies, so
  their samples are shorter than the data.
- GBPJPY costs are unverified.
- The research ranges are development data (previously used for validation). The single
  untouched OOS run has **not** been spent.
- The research trial ledger lives in the research database copy, not in production.

## 8. What a strategy would need before any promotion (unchanged policy)

1. A net-positive result on development data, with gross R clearly above measured cost R.
2. Purged CV stability, PSR and DSR across **every** trial in its family, and an acceptable PBO.
3. One untouched OOS run on the reserved interval, used once.
4. `BROKER_DEMO_CONFIRMED` costs for its symbol.
5. Human review recorded as a verified OKF research finding.

**No strategy meets step 1 today.**
