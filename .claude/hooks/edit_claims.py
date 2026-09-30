#!/usr/bin/env python3
"""Project-owned edit-claim guard (PreToolUse + PostToolUse, stdlib only).

Problem (task-observer observations #3 and #10, recurring): a plugin's
first-touch gate denies the FIRST edit of a file in a parallel batch and
lets the later edits of the same batch through. The later edits usually
depend on the first one (it defines what they use), so the file is left
half-applied and a test run in the same batch exercises a broken module.

Rule enforced here, per Claude Code session and per file:

  * An edit (Edit/Write/MultiEdit/NotebookEdit) to a file this session has
    already edited successfully is always allowed by this hook.
  * The first edit to a not-yet-confirmed file takes a PENDING claim and
    is allowed by this hook (other hooks still decide for themselves).
  * PostToolUse (the edit really landed) turns the claim into CONFIRMED.
  * Any further edit to the same file while an earlier claim is still
    PENDING and fresh is DENIED: the earlier edit is in flight or was
    denied elsewhere, so this one could land on top of a missing edit.
    Resend it once the earlier edit has landed.
  * A PENDING claim older than PENDING_TTL_SECONDS is stale (the earlier
    edit was denied or failed and a later turn is retrying) and is taken
    over by exactly one caller.

The decision for one file is serialized with an O_EXCL lock file, so two
edits of the same batch can never both take the claim. The hook fails
OPEN on any unexpected error: it narrows a known failure mode and must
never make ordinary editing impossible. This file lives in the project
(.claude/hooks), not in a plugin cache that updates overwrite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
PENDING_TTL_SECONDS = 5.0
LOCK_WAIT_SECONDS = 2.0
LOCK_STALE_SECONDS = 10.0
STATE_ROOT = Path(tempfile.gettempdir()) / "asn-edit-claims"

ALLOW = "allow"
DENY = "deny"


def target_path(tool_input: dict) -> str | None:
    value = tool_input.get("file_path") or tool_input.get("notebook_path")
    return str(value) if value else None


def file_key(path: str) -> str:
    normalized = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def session_dir(session_id: str, root: Path = STATE_ROOT) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "no-session")[:128]
    return root / safe


class _FileLock:
    def __init__(self, path: Path, clock=time.time, sleep=time.sleep) -> None:
        self.path, self.clock, self.sleep = path, clock, sleep
        self.held = False

    def __enter__(self) -> "_FileLock":
        deadline = self.clock() + LOCK_WAIT_SECONDS
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, repr(self.clock()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
                        self.path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if self.clock() >= deadline:
                    raise TimeoutError(f"edit-claim lock busy: {self.path}")
                self.sleep(0.02)

    def __exit__(self, *exc) -> None:
        if self.held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def pre_edit(state: Path, path: str, *, now: float) -> tuple[str, str]:
    state.mkdir(parents=True, exist_ok=True)
    key = file_key(path)
    confirmed = state / f"{key}.ok"
    pending = state / f"{key}.pending"
    with _FileLock(state / f"{key}.lock"):
        if confirmed.exists():
            return ALLOW, "file already edited successfully in this session"
        if pending.exists():
            try:
                age = now - float(pending.read_text(encoding="utf-8").strip() or 0)
            except ValueError:
                age = PENDING_TTL_SECONDS + 1
            if age <= PENDING_TTL_SECONDS:
                return DENY, (
                    f"edit-claim guard: an earlier edit to {path} in this batch has not landed yet (it is in flight "
                    f"or was denied by another hook). Applying this edit now could leave the file half-changed. "
                    f"Wait for the earlier edit's result, re-read the file if it was denied, then resend this edit "
                    f"on its own. (Project hook .claude/hooks/edit_claims.py; observations #3/#10.)"
                )
        pending.write_text(repr(now), encoding="utf-8")
        return ALLOW, "first edit to this file in this session: pending claim taken"


def post_edit(state: Path, path: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    key = file_key(path)
    (state / f"{key}.ok").write_text("ok", encoding="utf-8")
    try:
        (state / f"{key}.pending").unlink()
    except FileNotFoundError:
        pass


def _emit_pre(decision: str, reason: str) -> None:
    if decision == DENY:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason,
        }}))
    # allow: print nothing, so this hook never overrides another hook's decision


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        tool = payload.get("tool_name")
        path = target_path(payload.get("tool_input") or {})
        if tool not in EDIT_TOOLS or not path:
            return 0
        state = session_dir(str(payload.get("session_id") or ""))
        event = payload.get("hook_event_name")
        if event == "PostToolUse":
            post_edit(state, path)
        elif event == "PreToolUse":
            _emit_pre(*pre_edit(state, path, now=time.time()))
    except Exception as exc:  # fail open, visibly
        print(f"edit_claims.py failed open: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
