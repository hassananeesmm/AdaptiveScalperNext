# H8 pre-registration amendment (2026-10-02): causal implementation semantics

Amends `V2_H8_PREREGISTRATION_2026-09-30.md` (commit `64adb24`). Committed ALONE, before any change
to the H8 performance code and before any H8 performance run.

## Status at the time of writing

1. **No H8 performance result has been observed.** The only run attempt (`h8r1`, 2026-10-01,
   `docs/research/V2_H8_RUN_h8r1.md`) was refused by the pre-registered data-completeness gate
   BEFORE any rule was evaluated; no signal, trade, return or statistic exists. No ledger row.
2. This amendment clarifies **causal implementation semantics only**. No parameter changes:
   Donchian N20, 2 M5 counter-closes, 0.50 x ATR14(M5) retracement, 3-bar M1 resumption,
   cost_R <= 0.05, 2.0 R target, 45-minute time stop, the friction floor, the criteria and the
   classification are unchanged.

## Clarifications

3. **Absolute structural invalidation is preserved across the next-bar entry.** At the decision
   (M1 close):
   - BUY: `structural_stop_price = pullback_extreme - decision_spread_price`
   - SELL: `structural_stop_price = pullback_extreme + decision_spread_price`

   At the actual next-bar fill: `structural_distance = |fill_price - structural_stop_price|`.
   A gap therefore can never move the effective stop inside the structural invalidation zone.
4. Volatility floor unchanged: `0.50 x ATR14(M5)` at the decision.
5. Friction floor unchanged: `decision_round_trip_cost / 0.05`.
6. **Initial stop distance at the fill** = max(structural_distance at the fill, volatility floor,
   friction floor) -- the same three components, with the structural one measured from the real fill.
   The stop is NOT widened again to make a higher fill-time cost pass. At the fill the round-trip
   cost is recomputed with the FILL bar's spread and the same configured slippage (entry + exit),
   commission and uncertainty margin; `fill_time_cost_R = fill_time_round_trip_cost /
   initial_stop_distance`; **REQUIRE fill_time_cost_R <= 0.05**, otherwise the entry is REJECTED
   (`COST_R_ABOVE_0_05_AT_FILL`) and its fingerprint stays consumed. The decision-time check
   (section 5.4) also remains. A sudden spread / cost increase can therefore reject an entry.
7. The original friction floor remains part of H8 and is not removed after any result.
8. The report states which component bound each trade's stop (STRUCTURAL / VOLATILITY / FRICTION,
   counts and percentages) and the medians of stop distance, structural distance, volatility floor,
   friction floor, target distance and holding time.
9. MFE / MAE come from M1 OHLC: on an exit bar the full-bar high/low may include movement after the
   exit, and no intrabar ordering is known. They are reported as `mfe_r_ohlc_bound` /
   `mae_r_ohlc_bound`, are descriptive only and are never a pass criterion.

## Further causal semantics made explicit (no rule change)

10. The decision time is the trigger M1 bar's CLOSE (`bar.time + 60`); the pending entry's signal
    time is that close, the entry fills at the next M1 bar's open (never before the decision), and
    fill staleness (<= 2 M1 bars) is measured from the decision close.
11. Section 5.2: the counter-close count and the pullback swing extreme use the M5 bars completed
    AFTER the bar that set the local extreme (the extreme bar itself is neither counted nor used as
    the swing).
12. The STRONG PASS classification requires the 13 named criteria explicitly
    (`trade_count_ge_100`, `gross_mean_positive`, `gross_episode_ci_positive`, `net_mean_positive`,
    `net_episode_ci_positive`, `folds_8_of_12_positive`, `psr_ge_095`, `dsr_ge_095`, `pbo_le_020`,
    `gross_cost_ratio_ge_3`, `cost_x1_2_net_positive`, `zero_safety_violations`,
    `zero_forbidden_data_access`); a missing, None or uncomputable criterion is never a success.
