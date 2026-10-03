# Profitability root cause — final audit (2026-10-03)

Branch `fix/v1-loss-root-cause-correctness` (PR #7, draft), base `release/0.2.7` (`5be15d8`).
Deployed runtime 0.2.7 (`d9c1bd1`) was **not touched**.

This document keeps three questions apart and never lets an answer to one stand in for another:

- **A. Correctness** — does the software implement its stated rules?
- **B. Signal edge** — do the signals predict anything, gross of costs?
- **C. Executable edge** — does any gross edge survive real costs and execution?

Fixing A is not evidence for B or C.

Labels: **CONFIRMED DEFECT** (reproduced in code, fixed or pinned by a test) · **CONFIRMED ECONOMIC
FAILURE** (measured on stored artifacts or broker deals) · **HYPOTHESIS** (consistent with the evidence, not
proven) · **INSUFFICIENT EVIDENCE**.

## 1. How the facts were checked

- Research facts were recomputed from the stored r1 artifacts `data/research/independent_{BTCUSD,XAUUSD,GBPJPY}_r1.json`
  (development ranges ending 2026-06-30, BACKTEST origin) by `scripts/audit/research_recompute.py`. That script reads
  only those JSON files, never a database or bar data, so it cannot touch the sealed OOS interval.
- DEMO facts were recomputed from the production database, opened read-only, by `scripts/audit/demo_exit_economics.py`.
  It uses broker deals from the runtime `deals` table (profit + commission + swap + fee), one row per closed position,
  and the position's first FULL_CLOSE review reason.
- The sealed BTC OOS interval was not read. One exploratory query this session would have aggregated `MIN/MAX(time)`
  over the whole `bars` table. It failed on a column name before returning anything, and was removed rather than
  fixed (observation 0013 applies).

## 2. Research facts (recomputed, all reproduce the prompt's figures)

| Fact | Recomputed value | Label |
|---|---|---|
| Independent strategy × symbol sessions net-negative | **18 / 18** | CONFIRMED ECONOMIC FAILURE |
| Selector sessions net-negative | 3 / 3 (net R −0.27 BTC, −0.19 XAU, −0.25 GBPJPY) | CONFIRMED ECONOMIC FAILURE |
| Any gross R above its cost R | none (best: pullback XAU +0.076 gross vs 0.174 cost) | CONFIRMED ECONOMIC FAILURE |
| Microstructure share of selections | 92.7 % BTC, 88.9 % XAU, 90.1 % GBPJPY | CONFIRMED |
| Mean raw score of selected microstructure candidates | 0.773 / 0.781 / 0.794 | CONFIRMED |
| Expected vs realized net R by expected-edge quintile | expected +0.10 → +1.40; realized flat −0.17…−0.30 | CONFIRMED (ranking has no predictive value) |
| Microstructure < 5 min holds | gross −0.48 / −0.24 / −0.62 R | CONFIRMED ECONOMIC FAILURE |
| Microstructure 5–15 min holds | gross +0.10 / +0.12 / +0.06 R vs cost 0.21–0.23 R | CONFIRMED ECONOMIC FAILURE |
| "Original strategy thesis invalidated" exits (micro) | 411 / 654 / 490, net R −0.32 / −0.28 / −0.29 | CONFIRMED |
| RANGE share of microstructure entries | 96.9 % / 92.6 % / 95.8 % | CONFIRMED |
| PSR vs zero, all independent sessions | ≤ 0.11; microstructure ≈ 1e-22…1e-17 | CONFIRMED |

## 3. DEMO facts (408 closed positions, broker deals)

Net −234.00 USD (gross −177.44, commission/swap/fee −56.56); win rate 52.2 %; median hold 297 s.

| First exit decision | n | Net USD | Mean net R | Median hold |
|---|---:|---:|---:|---:|
| Thesis invalidated: entry trigger no longer fires | 161 | −759.89 | −0.209 | 301 s |
| Broker stop-loss | 106 | −1,163.17 | −0.481 | 166 s |
| Profit giveback (0.2 R after +0.6 R) | 101 | +1,172.70 | +0.517 | 159 s |
| Early TP at 1.0 R | 18 | +415.59 | +1.046 | 214 s |
| Max hold 600 s | 14 | −52.18 | −0.177 | 600 s |
| Thesis invalidated: remaining edge < 0 | 8 | +152.95 | +0.814 | 84 s |

By strategy: microstructure BTC n=239 −261.50, microstructure XAU n=154 +21.31, statistical reversion n=14 +5.65,
range breakout n=1 +0.54. The broker take-profit (the configured 1.5 R) was reached **once** in 408 positions.
The DEMO sample agrees in sign with the research (Section 2). It is not a validation of anything.

## 4. Root-cause matrix

| ID | Finding | Label | Status on this branch |
|---|---|---|---|
| D1 | DEMO pre-entry cost charged per-fill slippage once (round trip has two fills) | CONFIRMED DEFECT | fixed in PR #7 (`b689490`) |
| D2 | DEMO open-position review re-charged sunk entry costs | CONFIRMED DEFECT | fixed in PR #7 |
| D3 | **Backtest/PAPER** selector and fill-bar revalidation charged slippage once; review re-charged the whole round trip from a mid price (D1+D2 unfixed in simulation) | CONFIRMED DEFECT | fixed `7ba9dce`; DEMO == backtest equivalence tested |
| D4 | `raw_confidence` (a heuristic score) used as P(win) in `p·target − (1−p)·stop` | CONFIRMED DEFECT (issue #6) | fixed `7ba9dce`: evidence-only EV; default FLAT; V1 kept as labelled replay |
| D5 | Holding thesis = "entry trigger still fires" (`strategy.evaluate()` re-run); event triggers vanish one bar after entry | CONFIRMED DEFECT (semantics) | contract made explicit (`shadow/lifecycle.py`); executable exits deliberately unchanged; characterization test pins current behaviour |
| D6 | Selector payoff = configured target/stop, but exits rarely reach either (TP hit 1 / 408) | CONFIRMED DEFECT (model/runtime inconsistency) | removed from executable EV (D4 fix needs realized `LifecyclePayoff`) |
| D7 | Review "remaining net edge" = distance-to-target − cost: no probability, ignores the stop, turns negative near the target | CONFIRMED DEFECT (semantics) | documented; part of D5 (observer contract); not changed executably |
| D8 | Unknown swap silently 0 (`swap_per_lot_per_day = 0.0` default) | CONFIRMED DEFECT (issue #8) | fixed `7ba9dce`: horizon-aware; unknown + crossing → BLOCK_COST |
| D9 | Backtest swap counted at UTC midnight; broker rolls over at server midnight | CONFIRMED DEFECT (no economic effect while swap = 0) | fixed `7ba9dce` (`server_time_rule`) |
| D10 | PAPER simulated unknown commission/slippage as 0 (free fills) | CONFIRMED DEFECT | fixed `7ba9dce`: PAPER BLOCK_COST |
| D11 | `expected_duration_seconds` persisted/displayed but has no decision authority; one global 600 s max hold | CONFIRMED (no authority) | documented; one horizon per lifecycle required for future strategies |
| D12 | Dashboard showed the raw score as `p=` | CONFIRMED DEFECT (display) | fixed `7ba9dce` |
| D13 | `threshold_cross_at_utc` never recorded, so threshold→decision lag is unmeasurable | CONFIRMED (observability gap) | documented |
| E1 | No strategy's gross edge exceeds its cost (Section 2) | CONFIRMED ECONOMIC FAILURE | unchanged by any fix |
| E2 | Microstructure sub-5-minute trades are strongly negative gross | CONFIRMED ECONOMIC FAILURE | — |
| E3 | Microstructure dominates selection because its raw score is high, not because it pays | CONFIRMED (mechanism D4) | D4 fix makes it impossible |
| H1 | Decoupling thesis from trigger would improve the event strategies' realized R | HYPOTHESIS | needs shadow counterfactuals; DEMO thesis exits (−0.21 R) do not show holding would pay |
| H2 | The positive 5–15 min gross is real edge rather than noise | HYPOTHESIS (gross < cost anyway) | — |
| I1 | Calibrated P(win) for any strategy | INSUFFICIENT EVIDENCE | shadow collection |
| I2 | Exit-cost tails per exit kind: FINAL exit observations n = 68 BTC stop-loss (mean slippage 1.03), 38 XAU stop-loss (0.15), 13 / 15 agent closes, 1 take-profit — too few for p90/p95 tails | INSUFFICIENT EVIDENCE | DEMO exit-cost observations continue; configured BTC per-fill 11.97 stays (conservative) |
| L1 | Scheduler lag as a P/L cause | NOT DEMONSTRATED | R lost decision→fill −0.006 R (BTC, n=179) / +0.003 R (XAU, n=122); corr(latency, R lost) −0.05 / +0.12; max scheduler lag 2.4 s. Reliability item only |

## 5. Conclusion

Correctness (A): thirteen defects confirmed, the economically relevant ones fixed and tested.
Signal and executable edge (B, C): **NO VALIDATED POSITIVE NET EDGE.** The corrections remove sources of avoidable
negative expectancy and dishonest ranking. They do not create an edge, and none of them is evidence of one.
The system's executable default is now FLAT until forward evidence exists.
