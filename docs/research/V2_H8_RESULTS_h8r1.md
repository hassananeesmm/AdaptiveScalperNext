# H8 results, tag h8r1 (2026-10-02): H8 DATA BLOCKED

**Outcome: H8 DID NOT RUN. No H8 rule was evaluated; no trade, return or statistic exists.
Classification: none (not FAIL / MARGINAL / STRONG PASS). Research status: NO VALIDATED EDGE YET.**

## Lineage

| Item | Value |
|---|---|
| Pre-registration | `docs/research/V2_H8_PREREGISTRATION_2026-09-30.md`, commit `64adb24` (before any H8 code) |
| Amendment (causal semantics only, before any result) | `docs/research/V2_H8_PREREGISTRATION_AMENDMENT_2026-10-02.md`, commit `c61b69d` |
| Implementation + tests | `435b24e` (74 H8 tests; full suite 1901 passed / 10 skipped / 0 failed; compileall OK) |
| Runner | `scripts/research_v2_h8.py --tag h8r1`, clean tree, exit code 3 (REFUSED_DATA_GATE) |
| Config fingerprint | `00c72347246ecdd2...` (full value in the gate report) |
| Gate report | `data/research/v2h8_XAUUSD_h8r1_gate_20261002.json` (earlier attempt: `v2h8_XAUUSD_h8r1.json`, 2026-10-01) |
| Research DB | `data/research/v2_20260929.sqlite3` (copy; backup before import attempts: `v2_20260929.pre_h8_import.sqlite3`) |
| Ledger | 260 rows before and after; 0 `v2h8:*` rows; `v2h8:h8r1:XAUUSD:H8-PRIMARY` NOT written |

## Data acquisition (operator-approved, read-only, research copy only)

MT5 DEMO account on `ICMarketsSC-Demo`, terminal build 6230; `copy_rates_range` only, XAUUSD, every
request inside 2024-06-01 00:00:00 .. 2026-06-30 23:59:59 UTC; every returned bar re-checked against
those bounds (0 outside), de-duplicated by `(symbol, resolution, ts_utc)`; no order call, no account
change.

| Resolution | Fetched (2026-10-02) | Inserted | Stored in window | First .. last stored (UTC epoch) | Checksum (prefix) |
|---|---|---|---|---|---|
| M15 | (not re-imported) | - | 49,099 | 1717365600 .. 1782863100 | `973c4fc3ea7c1718` |
| M5 | 81,633 | 0 | 84,094 | 1745427600 .. 1782863700 | `1ee13aac03470f0f` |
| M1 | 7,981 | 0 | 20,572 | 1781029260 .. 1782863940 | `0c2576b673adde4f` |

Cause: the terminal's "Max bars in chart" is **100,000** (`terminal_info().maxbars`); MT5 serves at most
that many bars per timeframe, i.e. M5 back to about 2025-04 and M1 only for the most recent ~10 weeks
(almost all of it after the window). A bounded probe for 2024-06-03 returned 1 M1 and 1 M5 bar.

## Pre-registered data gate (every fold needs M5 >= 0.90 x 3 x M15 and M1 >= 0.90 x 15 x M15)

| Fold | Span (UTC) | M15 | M5 | M1 | M5 cov. | M1 cov. |
|---|---|---|---|---|---|---|
| 0 | 2024-06-02 .. 2024-08-02 | 4,091 | 0 | 0 | 0.000 | 0.000 |
| 1 | 2024-08-02 .. 2024-10-04 | 4,091 | 0 | 0 | 0.000 | 0.000 |
| 2 | 2024-10-04 .. 2024-12-05 | 4,091 | 0 | 0 | 0.000 | 0.000 |
| 3 | 2024-12-05 .. 2025-02-10 | 4,091 | 0 | 0 | 0.000 | 0.000 |
| 4 | 2025-02-10 .. 2025-04-14 | 4,091 | 0 | 0 | 0.000 | 0.000 |
| 5 | 2025-04-14 .. 2025-06-16 | 4,091 | 10,452 | 0 | 0.852 | 0.000 |
| 6 | 2025-06-16 .. 2025-08-18 | 4,091 | 12,273 | 0 | 1.000 | 0.000 |
| 7 | 2025-08-18 .. 2025-10-20 | 4,091 | 12,273 | 0 | 1.000 | 0.000 |
| 8 | 2025-10-20 .. 2025-12-19 | 4,091 | 12,268 | 0 | 1.000 | 0.000 |
| 9 | 2025-12-19 .. 2026-02-24 | 4,091 | 12,273 | 0 | 1.000 | 0.000 |
| 10 | 2026-02-24 .. 2026-04-29 | 4,091 | 12,266 | 0 | 0.999 | 0.000 |
| 11 | 2026-04-29 .. 2026-06-30 | 4,091 | 12,268 | 20,467 | 1.000 | 0.334 |

**DATA GATE FAILED in 12 of 12 folds.** The signal engine was never called. Not done (forbidden by the
pre-registration and the operator prompt): lowering the 90 % gate, shortening the window, another
symbol, resampling M15 into M5/M1, another data provider, synthetic bars.

## Data safety

Read: only XAUUSD bars with open time in 2024-06-01 .. 2026-06-30 (bounded queries and bounded broker
requests). Reserved OOS 2026-07-01 .. 2026-09-18: not read. H7 holdout 2022-06-23 .. 2024-05-31: not
read. Production DB: not opened. One earlier diagnostic (2026-10-01) asked the terminal for bars from
position 0 without a window bound; it returned an error and no data (recorded in WORKLOG).

## Known implementation limitation (recorded before any result)

The fill-time cost gate and the engine's fill model use the FILL bar's MT5 `spread` field. MT5 stores one
spread value per bar (an aggregate over the bar), so at the bar's open it is not strictly known yet. This is
the engine's existing fill convention (also used by H6); the 2026-10-02 lookahead negative control
(`test_fill_decision_ignores_the_fill_bar_future_high_low_close_volume`) proves the fill decision uses no
other fill-bar future field (high, low, close, volume). Tick data would remove this limitation.

## Inference status (unchanged)

Donchian N20 is H6-derived / post-selection context on this same window; even a future H8 result on
this window is hypothesis-development evidence, not independent validation.

## What would unblock it (operator decisions)

1. Raise MT5 "Max bars in chart" to Unlimited (requires a terminal restart, i.e. the DEMO runtime's
   terminal), then re-run the bounded import and `h8r1` once (the tag is not consumed); or
2. evaluate H8 only on future forward PAPER / shadow data; or
3. approve an alternate data source as a separate decision (not taken here).
