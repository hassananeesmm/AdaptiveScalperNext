# Release

## Release definition

The first deployed release means every item below holds, verified **on the Windows laptop**:
- installed locally on Windows;
- works independently of Claude or any cloud AI (nothing at runtime calls an AI service);
- connects only to an MT5 DEMO account, and a REAL account fails closed;
- PAPER, the dashboard, the runtime, the persistent database, the launchers and the kill
  switch all work;
- broker mutations remain DEMO-only;
- packaging is reproducible;
- known limitations are documented (`BUG_BACKLOG.md`, `LOCAL_MT5_HANDOFF.md`).

## Current state

**Cloud release candidate: source complete, laptop verification pending.**

Every cloud-completable item is done and tested (see `docs/QA_REPORT.md`). Release
readiness is **BLOCKED-ON-LOCAL-MT5** until the laptop completes `LOCAL_MT5_HANDOFF.md`
steps A–X. Nothing may be packaged before then (step Y).

## Build (Windows, after local QA)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 0.1.0
powershell -ExecutionPolicy Bypass -File scripts\release_smoke_test.ps1 -Zip dist\AdaptiveScalperNext-0.1.0.zip
```

`build_release.ps1`:
- refuses a dirty working tree;
- runs the test suite and `okf validate`;
- builds `dist\AdaptiveScalperNext-<version>.zip` with **`git archive`**, so only tracked files
  are included (no `.venv`, `data\`, `logs\`, databases, model caches or local secrets);
- rejects the archive if it contains a forbidden file (`*.sqlite3`, `*.db`, `*.key`, `*.pem`,
  `.env`, `secrets*`, `credentials*`, `*.joblib`);
- writes a SHA-256 checksum and a JSON manifest (version, commit, time, file count).

`release_smoke_test.ps1`:
1. Verifies the checksum.
2. Extracts the zip to a temporary folder and creates a fresh venv.
3. Installs the pinned requirements.
4. Runs `doctor` against a throwaway database, then `strategies`, `okf validate`,
   `kill-switch status` and the test suite.

It never starts PAPER or DEMO, and never touches the real database or the kill switch.

## Packaging format

The release is a **source bundle plus a pinned venv**, created on the target machine by
`SETUP.bat`. A PyInstaller single-folder build was considered and deferred:
- the MetaTrader5 package, scikit-learn and uvicorn all need PyInstaller hooks, and that
  build can only be produced and verified on Windows;
- the source bundle already runs with no AI or cloud dependency.

If a frozen build is required later:
1. Produce it on the laptop with `pyinstaller --onedir -n AdaptiveScalperNext adaptive_scalper/cli/__main__.py`, adding the `knowledge/` bundle, `config/` and the SQL migrations as data.
2. Check `--help`, `doctor`, `status`, `health`, `paper`, `demo`, `dashboard` and `backtest`
   against the frozen executable.
3. Re-point the launchers to the frozen executable.

This is a Windows-local item and is not claimed as done.

## Versioning

`pyproject.toml` holds the project version. The release version is passed to
`build_release.ps1` and recorded, together with the commit SHA, in the manifest.
