# Cleanup manifest (2026-09-26)

Scope: `C:\AdaptiveScalperNext` only (the legacy `C:\AdaptiveScalper` is never touched).
Method: `git status --ignored`, an AST scan of `adaptive_scalper/` for unused top-level
imports, and checks of launcher, CLI registration and test references. Nothing listed as
"approval required" has been removed.

## Verified clean

| Check | Result |
|---|---|
| Unused top-level imports in `adaptive_scalper/` (AST scan) | none found |
| Untracked source files | none (all new work committed on `feature/strategy-lab-attribution`) |
| Generated artifacts (`.venv`, caches, `data/`, `logs/`, `dist/`, `.playwright-mcp/`) | all gitignored; none are in the release zip |
| `joblib` pin in `requirements.txt` | **kept**: scikit-learn requires it; the project no longer uses it for model artifacts (ASN-009) |
| `ruff` | not installed in `.venv`; the AST scan was used instead |

## Candidates: approval required (not removed)

| Item | Why it is a candidate | Risk / recommendation |
|---|---|---|
| Dashboard panels `strategy_attribution` and `strategy_performance` (`dashboard/panels.py`) | Superseded by the Strategy Lab view. They sum local `deals` only (no broker-history reconciliation), and `strategy_performance` ships up to 900 rows on every 2 s push. | Still covered by `tests/test_dashboard_strategy_lab.py` and reachable under "All panels". Recommend removing them from the push after you approve. Kept for now (preserve existing panels). |
| Process `pwsh.exe` PID 17756 (started 2026-09-25 14:02) | A Codex migration race experiment on a temporary database; not a trading component. | Not started by this session; ask before terminating. |
| `data/backups/pre_strategy_lab_attribution_…sqlite3-shm/-wal` | Created by read-only analysis of the backup copy. | Harmless; they can be deleted when no process has the backup open. |
| `.playwright-mcp/` (≈1 MB screenshots/snapshots) | Browser verification output. | Safe to delete; kept as QA evidence for this session. |
| `dist/AdaptiveScalperNext-0.1.0 … 0.1.2` | Older release archives. | Release evidence; keep unless you want them archived elsewhere. |
| `data/backups/` (≈2.5 GB, 10 backups) | Pre-change safety copies. | Evidence; prune only by explicit operator decision. |

## Preserved by rule

Migrations, journal history, broker evidence, databases, backups, research runs and user
data are never cleanup candidates.
