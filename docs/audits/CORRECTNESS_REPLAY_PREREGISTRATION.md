# Correctness replay — preregistration (2026-10-03) — STATUS: PREPARED, NOT RUN

Purpose: **measure how much the implementation defects D3/D8/D9 (cost horizon, swap) changed the recorded V1
economics.** It is not H10, not a strategy search, and not a rescue. It may be run **exactly once**, only after a
human decision, because each session adds a permanent trial to the ledger and raises the DSR deflation any future
candidate must clear.

## Fixed inputs

- Code: the commit that contains this file (tag it `correctness-replay-prerun` before running).
- Data: the r1 development ranges and bar checksums recorded in `data/research/independent_{BTCUSD,XAUUSD}_r1.json`
  (BTCUSD M5 1759276800–1782777600, checksum `0076f7a4a9e9…`; XAUUSD M5 1748815200–1782777600, `544389698ffe…`).
  The replay must refuse to run if the checksums differ. No bar after 1782777600 (2026-06-30) is read, so the
  sealed OOS interval is never touched.
- Strategies, parameters, regime thresholds, stop/target multiples, adaptive-exit parameters: unchanged V1.
- Costs: the shipped `[costs.*]` values (XAU 0.41, BTC 11.97 per fill; commission 7.03 / 0.00), swap unknown
  (crossing decisions block), uncertainty margin 10 %.
- Edge model: `LEGACY_V1_RAW_SCORE` (the only way to replay V1 selection; labelled, never executable).
- Command (per symbol, research DB only):
  `independent-research --symbol <S> --resolution M5 --start <r1 start> --end <r1 end> --folds 12
   --edge-model LEGACY_V1_RAW_SCORE --research-db data/research/v2_20260929.sqlite3 --tag correctness-replay`

## Fixed outputs (both symbols)

Per session and for the selector: trades, gross R, cost R, net R, PSR, positive folds; selector share by strategy;
expected vs realized net-R quintiles; exits by reason. Reported side by side with r1. No parameter, threshold or
data change is permitted between runs, and the replay is not repeated.

## Interpretation fixed in advance

If net R stays negative (expected: realized costs in r1 were already charged per fill), the defects are recorded
as having had no material effect on the verdict and the result is **accepted**. A positive result would only show
a software effect on V1 development data. It would not be evidence of edge and would not be eligible for anything.
