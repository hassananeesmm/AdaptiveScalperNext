# H8 run h8r1 (2026-10-01): REFUSED by the pre-registered data gate

**Outcome: NOT RUN. No H8 rule was evaluated. NO VALIDATED EDGE (none tested).**
Pre-registration `V2_H8_PREREGISTRATION_2026-09-30.md` (commit `64adb24`); implementation
`0319e40` (clean tree); runner `scripts/research_v2_h8.py --tag h8r1`, research DB copy
`data/research/v2_20260929.sqlite3`; report `data/research/v2h8_XAUUSD_h8r1.json`; exit code 3.

## What was read

Only XAUUSD bars with open time in 2024-06-01 00:00 .. 2026-06-30 23:59:59 UTC (query bounds
1717200000..1782863999; the loaded series were re-checked against the same bounds): M15 49,099,
M5 84,094, M1 20,572. Reserved OOS (2026-07-01 .. 2026-09-18): not read. H7 holdout
(2022-06-23 .. 2024-05-31): not read. No tick, no production DB, no broker call.

## Data gate (pre-registered: M5 >= 0.90 x 3 x M15 and M1 >= 0.90 x 15 x M15 in every fold)

| Fold | Span (UTC) | M15 | M5 | M1 | M5 cov. | M1 cov. |
|---|---|---|---|---|---|---|
| 0 | 2024-06-02 .. 2024-08-02 | 4,091 | 0 | 0 | 0.00 | 0.00 |
| 1 | 2024-08-02 .. 2024-10-04 | 4,091 | 0 | 0 | 0.00 | 0.00 |
| 2 | 2024-10-04 .. 2024-12-05 | 4,091 | 0 | 0 | 0.00 | 0.00 |
| 3 | 2024-12-05 .. 2025-02-10 | 4,091 | 0 | 0 | 0.00 | 0.00 |
| 4 | 2025-02-10 .. 2025-04-14 | 4,091 | 0 | 0 | 0.00 | 0.00 |
| 5 | 2025-04-14 .. 2025-06-16 | 4,091 | 10,452 | 0 | 0.85 | 0.00 |
| 6 | 2025-06-16 .. 2025-08-18 | 4,091 | 12,273 | 0 | 1.00 | 0.00 |
| 7 | 2025-08-18 .. 2025-10-20 | 4,091 | 12,273 | 0 | 1.00 | 0.00 |
| 8 | 2025-10-20 .. 2025-12-19 | 4,091 | 12,268 | 0 | 1.00 | 0.00 |
| 9 | 2025-12-19 .. 2026-02-24 | 4,091 | 12,273 | 0 | 1.00 | 0.00 |
| 10 | 2026-02-24 .. 2026-04-29 | 4,091 | 12,266 | 0 | 1.00 | 0.00 |
| 11 | 2026-04-29 .. 2026-06-30 | 4,091 | 12,268 | 20,467 | 1.00 | 0.33 |

All 12 folds fail (M1 everywhere; M5 in folds 0-5). Per the pre-registration the run was refused
before any rule was evaluated; no trial was recorded (ledger unchanged at 260 rows, 0 `v2h8:*`).
Because nothing was evaluated, `h8r1` is not consumed: the same pre-registered rules can run once
when complete data exists. No substitution (other resolutions, a shorter window, another rule) is
made under the H8 name.

## Operator decision needed (not taken here)

1. Supply XAUUSD M5 and M1 history for 2024-06-01 .. 2026-06-30 into a research DB COPY (e.g. a
   read-only MT5 history import bounded to that window, run while the DEMO terminal is in use only
   if the operator accepts that), then run `h8r1` once; or
2. Leave H8 unrun on history and evaluate it only on FUTURE forward PAPER / shadow data; or
3. Pre-register a different hypothesis (a NEW name) that fits the stored data.
