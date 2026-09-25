# Performance Report

Target: Windows 11, Intel CPU, approximately 16 GB RAM, no GPU requirement.

## Measurements

| Item | Observation |
|---|---|
| Full test suite | 260.97 s initial; 115.05 s final warm-cache run |
| Dashboard `/api/panels` (19 KB, five samples) | median 0.148 s, max 0.170 s |
| PAPER worker resident memory | about 127 MB |
| Dashboard worker resident memory | about 133 MB |
| PAPER entry-cycle max duration | about 0.49 s |
| Scheduler max observed lag | about 1.04 s |
| News refresh max duration | about 1.23 s, network fetch isolated from scheduler |
| CLI `status` | 4.487 s |
| CLI `health` | 5.576 s |

Windows venv launcher parent processes show about 4 MB and must not be double-counted as
independent runtimes; the child processes hold the working set.

## Optimizations already verified

- Dashboard full integrity checks moved off the request path and run at most every ten
  minutes on a read-only connection.
- News HTTP fetch runs in one bounded background worker; broker and SQLite operations remain
  on the serialized scheduler.
- RAG rebuild/ingest is incremental and low duration in the current database.
- WebSocket push has polling fallback and the page does not query MT5 directly.

## Remaining performance item

`status` and `health` synchronously scan the full database. This does not delay trading, but
operator feedback is slow. Preserve full integrity checking in `doctor` and preflight;
consider age-labelled cached evidence for the fast commands.

No GPU or paid/cloud service is needed.
