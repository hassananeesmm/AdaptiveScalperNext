# H6 and H7 results (2026-09-29, evening)

**Result: NO VALIDATED EDGE YET.** Pre-registration: `V2_H6_H7_PREREGISTRATION_2026-09-29.md`
(committed `78f0491` before any H6/H7 code). BACKTEST evidence on development data.
Reserved OOS 2026-07-01..2026-09-18 not read. Nothing deployed or configured.

## Reproducibility

| Item | Value |
|---|---|
| H6 code | `2c98b9a` (clean tree enforced), `scripts/research_v2_h6.py`, tag `v2r3` |
| H6 data | XAUUSD M15 2024-06-01..2026-06-30 (49,008 bars); BTCUSD M15 2023-11-01..2026-06-30 (92,660 bars); 12 folds |
| H6 outputs | `data/research/v2h6_{XAUUSD,BTCUSD}_v2r3.json` |
| H7 code | `f9b56a6`, `scripts/research_v2_h7.py` (one-shot: fixed range, refuses a second run) |
| H7 data | XAUUSD M15 holdout 2022-06-23..2024-05-31 (45,758 bars) -- **now CONSUMED** |
| Ledger | +78 H6 trials (`v2-H6:*`), +2 H7 trials (`v2-H7:XAUUSD`), 0 FAILED; total 260 |
| Replay fidelity (V1 exit re-played on M15) | 100 % in all 14 cohorts |

## H6: slower, cost-efficient entries

**Screen passers: none (0 of 78).** Screen = 95 % CI of gross R above 0 AND gross >= 3x cost
AND n >= 100.

What moved:

- **Cost fell as intended.** Moving to M15 (wider ATR stops) cut mean cost from 0.11-0.24 R
  per trade (M5) to 0.04-0.17 R; the H6b gate at 5 % cost-to-stop brings it to 0.04-0.06 R.
- **Gross edge mostly did not appear.** V1 entry logic on M15 (H6a) has gross R between
  -0.15 and +0.18 per trade with every 95 % interval containing 0; the selector cohorts are
  +0.03 (XAUUSD) and -0.02 (BTCUSD). Net stays negative (selector -0.10 / -0.11 R).
- **One real, but too small, gross edge: XAUUSD Donchian breakout (H6c).** N20: gross
  +0.072 R, CI [+0.026, +0.118], n 3683; N48: +0.074 R, CI [+0.018, +0.131], n 2401 -- the
  first gross edge in this programme whose interval excludes 0. Its cost is 0.077 R, so
  gross/cost is 0.94-0.96 and net is -0.005 / -0.003 R (6/12 and 5/12 positive folds). It
  fails the pre-registered 3x screen by a wide margin. The same rule on BTCUSD has no
  gross edge (-0.011 / -0.024 R).
- Small positive cells (e.g. XAUUSD `volatility_expansion` gated at 10 %, n 28, net +0.26;
  BTCUSD `pullback_continuation` gated at 5 %, n 64, net +0.20) have n < 100 and intervals
  spanning 0; with 78 trials, a few such cells are expected by chance. Family PBO: XAUUSD
  0.75 (rankings not stable), BTCUSD 0.20. Every DSR is < 1e-10.

DEMO-evidence cost scenario (pre-registered, labelled, never a decision basis): observed
per-fill slippage (entry p90 and stop-exit p90, averaged) is 1.01x the assumption for
XAUUSD and 0.49x for BTCUSD. XAUUSD Donchian stays at net -0.006 / -0.004 R; BTCUSD
Donchian improves to -0.043 / -0.053 R, the BTCUSD selector to -0.073 R. Nothing turns
positive.

## H7: XAUUSD London/NY overlap (POST-HOC origin), one shot on the unseen holdout

| Trial | Trades | Gross R [95 % CI] | Cost R | Net R | PF | +folds | PSR |
|---|---|---|---|---|---|---|---|
| H7-SESSION (fills 12-16 UTC) | 84 | -0.009 [-0.261, +0.242] | 0.288 | -0.297 | 0.61 | 2/12 | 0.02 |
| H7-ALL (control) | 467 | -0.005 [-0.109, +0.098] | 0.442 | -0.447 | 0.49 | 0/12 | 0.00 |

**FAIL** on every criterion except "session beats control" (which only reflects the lower
cost of that session). The M5 finding does not replicate on unseen data: it was a
data-mined subgroup, as the POST-HOC label anticipated. The holdout is consumed and cannot
be reused for this or any related hypothesis.

## What this adds to the root cause

1. Slower entries fix the cost side (cost R roughly halves) but not the entry side: the V1
   signal logic has no gross edge on M15 either.
2. A plain trend-following breakout on XAUUSD shows a small, statistically real gross edge
   (about +0.07 R) that is almost exactly consumed by current friction. On BTCUSD it does
   not exist.
3. That suggests the next hypothesis should widen the gap between gross edge and cost
   further along the same axis (longer-horizon breakout/trend on XAUUSD with wider stops
   and holds, cost at <= 5 % of risk), pre-registered as a new family, not a re-tune of N,
   the ATR multiples or the exits on this data.

## Status

NO VALIDATED EDGE YET. No candidate locked. OOS sealed. H7 holdout consumed.
