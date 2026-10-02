# H8 results, tag h8r1 (2026-10-02): FAIL -- H8 REJECTED

**Classification: FAIL. H8 REJECTED.** BACKTEST evidence on development data (2024-06-01 .. 2026-06-30).
11 of 13 pre-registered criteria fail; only the trade count and the two safety / data-access criteria
pass. Research status: **NO VALIDATED EDGE YET.** No tuning, no OOS access, no DEMO activation follows.

## Lineage

| Item | Value |
|---|---|
| Trial | `v2h8:h8r1:XAUUSD:H8-PRIMARY`, family `v2-H8:XAUUSD`, ONE parameterization, run once (ledger 260 -> 261) |
| Pre-registration | `V2_H8_PREREGISTRATION_2026-09-30.md`, commit `64adb24` (before any H8 code; unchanged) |
| Amendment (causal semantics only, before any result) | `V2_H8_PREREGISTRATION_AMENDMENT_2026-10-02.md`, commit `c61b69d` |
| Source | `28271dc` (clean tree), tag `h8r1-prerun`; implementation `435b24e` + lookahead control |
| Config fingerprint | `00c72347246ecdd2...` (full value in the report) |
| Data (research copy `v2_20260929.sqlite3`) | M15 49,099 (`973c4fc3ea7c1718...`), M5 147,280 (`15a64b255fc0c5b3...`), M1 735,599 (`944443aa9016b091...`) |
| Report | `data/research/v2h8_XAUUSD_h8r1_run.json` (sha256 prefix `9140631f98215bbc`) |
| Prior refusals (no rule evaluated) | 2026-10-01 and 2026-10-02 07:xx: data gate failed (terminal `maxbars` 100,000) |

## Data acquisition and gate

Operator raised MT5 "Max bars in chart" to Unlimited (`maxbars` 100,000,000; terminal restarted
2026-10-02 07:48). Read-only bounded import into the research copy (DEMO, ICMarketsSC-Demo, symbol
XAUUSD, every request and stored bar within 2024-06-01 00:00:00 .. 2026-06-30 23:59:59 UTC, 0 outside):
M5 +63,186, M1 +715,027. Series: first 2024-06-02 22:00, last 2026-06-30 23:45/23:55/23:59 UTC;
0 duplicates; 0 misaligned timestamps; 432 intraday gaps > 1 h in every resolution (the daily XAU
maintenance break), largest gap 73 h (weekend/holiday). Gate: all 12 folds pass (M5 coverage
0.999-1.000, M1 0.998-0.999).

## Results

| Metric | Value |
|---|---|
| Decisions / accepted trades | 8,812 / 4,368 |
| Rejections | POSITION_OPEN 2,667; COST_R_ABOVE_0_05_AT_FILL 1,006; RISK_HALT 581; BLOCK_RISK_SIZING 92; COST_UNKNOWN 50; COST_UNKNOWN_AT_FILL 48 |
| Breakout episodes / trades per episode | 799 / 5.47 |
| Gross R mean / aggregate | -0.0010 / -4.30 |
| Gross R 95 % CI, episode-clustered / day-clustered | [-0.0126, +0.0097] / [-0.0129, +0.0107] |
| Net R mean / aggregate | -0.0463 / -202.37 |
| Net R 95 % CI, episode-clustered / day-clustered | [-0.0579, -0.0356] / [-0.0583, -0.0346] |
| Cost R mean (entry / exit / commission+swap) | 0.0453 (0.0210 / 0.0210 / 0.0033); aggregate 198.06 |
| Aggregate gross / cost | -0.02 (criterion >= 3.0) |
| Per-trade Sharpe / PSR / DSR (N = 40) | -0.113 / 9.1e-13 / 3.0e-55 |
| PBO (CSCV, 39 H6 XAUUSD trials + H8) | 0.85 |
| Mean net R at cost x1.2 / x1.5 / x2.0 | -0.0554 / -0.0690 / -0.0917 |
| Net-positive episodes / days | 168 of 799 / 148 of 537 |

**Folds (trades / gross R / cost R / net R):** 0: 437 / -0.91 / 19.87 / -20.78; 1: 434 / -3.88 / 19.76 /
-23.64; 2: 409 / -2.68 / 18.61 / -21.28; 3: 345 / -2.91 / 15.67 / -18.58; 4: 292 / +1.37 / 13.23 /
-11.86; 5: 385 / +8.70 / 17.52 / -8.82; 6: 363 / -3.87 / 16.49 / -20.37; 7: 364 / +4.65 / 16.58 /
-11.93; 8: 266 / -11.51 / 12.09 / -23.59; 9: 442 / +6.05 / 19.86 / -13.81; 10: 244 / -12.56 / 10.82 /
-23.38; 11: 387 / +13.24 / 17.57 / -4.34. **Net-positive folds: 0 of 12.**

**Exits:** TIME_STOP_45M 4,126 (mean net -0.011 R); STOP_LOSS_HIT 210 (-1.023); TAKE_PROFIT_HIT 29
(+1.997); range end 3. Holding time: p25 = p50 = p75 = p90 = 45 min (mean 60 min, incl. weekend-spanning
time stops).

**Stop binding (amendment item 8):** FRICTION 4,344 (99.45 %), STRUCTURAL 23 (0.53 %), VOLATILITY 1
(0.02 %). Medians: stop 20.69, friction floor 20.69, structural 2.77, volatility floor 1.37, target 41.37
(price units), decision and fill-time cost_R 0.05.

**Session (descriptive only):** ASIA 1,342 (-0.063), LONDON 915 (-0.051), LONDON_NEW_YORK 731 (-0.035),
NEW_YORK 993 (-0.038), LATE 387 (-0.022). Every session is net-negative; no session filter is implied.

**MFE / MAE (M1 OHLC bounds, descriptive only):** mean 0.24 R / 0.28 R.

**Safety violations:** 0. **Forbidden data access:** 0 (only bars inside the window loaded; OOS and H7
holdout untouched). News windows applied: 0 (no cached HIGH-impact events in the research copy for this
window; same as the engine's input for H6).

## Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | trade_count_ge_100 | PASS (4,368) |
| 2 | gross_mean_positive | FAIL (-0.0010) |
| 3 | gross_episode_ci_positive | FAIL (lower -0.0126) |
| 4 | net_mean_positive | FAIL (-0.0463) |
| 5 | net_episode_ci_positive | FAIL (upper -0.0356) |
| 6 | folds_8_of_12_positive | FAIL (0/12) |
| 7 | psr_ge_095 | FAIL |
| 8 | dsr_ge_095 | FAIL |
| 9 | pbo_le_020 | FAIL (0.85) |
| 10 | gross_cost_ratio_ge_3 | FAIL (-0.02) |
| 11 | cost_x1_2_net_positive | FAIL |
| 12 | zero_safety_violations | PASS |
| 13 | zero_forbidden_data_access | PASS |

## Interpretation (no rescue)

- There is no gross edge: the episode-clustered gross interval is centred on zero. Costs (~0.045 R per
  trade) make every fold net-negative.
- As the operator anticipated, the friction floor (20 x round-trip cost, ~20.7 price units) set the stop in
  99.45 % of trades; the 2 R target (~41 units) was almost never reached, and 94 % of trades ended at the
  45-minute time stop. Economically H8 behaved as "hold 45 minutes after an M1 resumption in an M15
  breakout context", and that has no gross edge on this data.
- This is the pre-registered outcome: **H8 REJECTED.** Not done, per the result policy: changing N20, the
  0.50 ATR, ATR14, the 3-bar M1 trigger, the 5 % cost gate (or its friction floor), the 2 R target or the
  45-minute exit. Any future idea is a NEW pre-registered hypothesis (H9), never a re-tune of H8.
- Inference limits: Donchian N20 came from H6 on this same development window (post-selection); H8 was
  designed after seeing H6. Even a positive result here would not have been independent validation.
- Known limitation: the fill-time cost uses the fill bar's MT5 `spread` aggregate (the engine's existing
  convention); the lookahead control proves no other fill-bar future field is used.

## Next

Per the pre-registration: the next family is H9 (preferred direction: BTCUSD volatility / liquidity-
conditioned momentum-vs-reversal), pre-registered before any code; BTC inherits no XAU Donchian
assumption. The sealed OOS stays sealed.
