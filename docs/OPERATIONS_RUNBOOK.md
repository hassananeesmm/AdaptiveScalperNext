# Operations and Recovery Runbook

## Normal startup

1. Start MetaTrader 5 and verify the selected account is DEMO.
2. Run `.venv\Scripts\python.exe -m adaptive_scalper.cli preflight`.
3. `READY_FOR_PAPER`: start `START PAPER.bat`, then `START DASHBOARD.bat`.
4. `READY_FOR_DEMO`: this label is necessary but not permission. The human operator still
   decides whether to clear/bootstrap the kill switch and start DEMO.
5. `BLOCKED`: do not start a runtime; correct the listed PAPER blockers.

Never run PAPER and DEMO concurrently against the same database. A fresh runtime heartbeat
causes a second runtime to refuse startup.

## Stop new entries

Run `STOP TRADING.bat`. It engages the persistent kill switch. It does not close positions;
position management continues. Clearing or bootstrapping is an explicit human command only.

## Database backup and integrity

For a running WAL database, use SQLite's online backup API. Do not copy only the main file.
Verify both source and backup with `PRAGMA integrity_check`. For a stopped runtime, copying
the main file plus any `-wal` and `-shm` files is acceptable. Never delete the database to
resolve migration trouble.

Recovery sequence:

1. Engage the kill switch if state is uncertain.
2. Stop the trading runtime cleanly; leave the dashboard optional.
3. Preserve the main/WAL/SHM files and take an online backup where readable.
4. Run `doctor`, then `preflight`.
5. Restore only from a verified backup, to a separate path first; run integrity and schema
   checks before replacing any file.
6. Start PAPER and verify state continuity before considering DEMO.

## Common blocks

- Stale/no quote: wait for market data; do not force a signal.
- Clock mismatch: verify the configured broker server-time rule; new exposure stays blocked.
- News unavailable/stale: restore the provider/cache; do not interpret outage as no news.
- UNKNOWN order or reconciliation mismatch: compare broker truth with local truth; never
  blindly resend or fabricate a fill.
- Cost unknown: collect evidence or exclude the symbol; never assume zero.
- Kill switch UNINITIALIZED/ENGAGED/INVALID: only the human operator resolves it.
- Dashboard offline: trading remains independent; restore localhost monitoring before DEMO.

## Restart and shutdown

Ctrl+C stops the runtime without closing positions; broker-side stops remain. After a laptop
restart, start MT5, run preflight, reconcile through the DEMO startup path, and verify the
dashboard before allowing new exposure. The system cannot operate while the laptop is off.

## Release

Only after applicable QA:

`powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 0.1.0`

`powershell -ExecutionPolicy Bypass -File scripts\release_smoke_test.ps1 -Zip dist\AdaptiveScalperNext-0.1.0.zip`

Review the branch for secrets before pushing. Do not merge to `main` without approval.

