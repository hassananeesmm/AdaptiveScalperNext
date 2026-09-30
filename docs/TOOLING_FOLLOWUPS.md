# Agent tooling follow-ups (not part of any trading release)

Source: the user-scope task-observer log (`~/.claude/skill-observations/observation-log/`).
Tooling changes live on the `tooling/*` branches, never in a runtime release.

## Applied in this repository (project-owned)

| Obs. | Problem | Project-owned fix |
|---|---|---|
| #3, #10 | GateGuard's first-touch gate denies the first edit of a parallel batch and lets the later, dependent edits through, so the file ends up half-applied (seen 6+ times). | `.claude/hooks/edit_claims.py` (PreToolUse + PostToolUse): a second edit to a file is denied while the first edit in the same session is still unconfirmed. Tested in `tests/test_edit_claims.py`, including an 8-thread race. The plugin cache is **not** modified, because plugin updates overwrite it. |
| #4, #6, #11 | Cross-cutting session discipline. | `CLAUDE.md`, section "Agent session discipline". |

## Staged, not installed (user-scope skill)

| Obs. | Problem | Where |
|---|---|---|
| #1 | The task-observer workspace path is ambiguous when the pinned path already ends in `skill-observations`. | Staged copy under `~/.claude/skill-observations/skill-updates/` (listed in `PENDING.md`). Operator review before install. |

## Deferred: documented for a companion "extras" skill / routing entry

The cached ECC plugin skills are not edited, because updates overwrite them. Candidate content for a
project- or user-owned companion skill that routes alongside them:

| Obs. | Target plugin skill | Rule to carry in the companion |
|---|---|---|
| #7 | `ecc:database-migrations` | A later migration's `CREATE OR REPLACE` can silently revert a parallel branch's fix, and git reports no conflict. Diff object definitions across branches before merging migrations. |
| #8 | `ecc:git-workflow` | Retargeting a stacked PR does not rerun CI. Prove the new merge base and retrigger CI without moving the head. |
| #9 | `ecc:database-migrations` | Before a first CLI migration push, compare the recorded migration history with the objects that actually exist. |

## Deferred: new reusable skills (for skill-creator, separate work)

Not created here, and deliberately not mixed into any trading release:

- `safe-scripted-file-edit` (#2): scripted rewrites on Windows must write UTF-8 atomically (temp file + replace). Never `open(path, 'w')` with the locale codec on the live file.
- `concurrency-regression-testing` (#5, #7): race fixes need a negative control on a real multi-connection database.
- `multi-session-coordination` (#11): peer-session / shared-output checks before autonomous repo mutation.
