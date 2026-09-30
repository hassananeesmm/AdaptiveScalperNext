"""Tests for the project-owned edit-claim guard (.claude/hooks/edit_claims.py).

Negative control for task-observer observations #3/#10: when another hook
denies the FIRST edit of a parallel batch, the later edits to the same file
must be denied too, instead of landing on top of the missing edit.
"""
import json
import subprocess
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = PROJECT_ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
import edit_claims  # noqa: E402


def test_first_edit_takes_claim_and_is_allowed(tmp_path):
    decision, _ = edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)
    assert decision == edit_claims.ALLOW


def test_second_edit_while_first_unconfirmed_is_denied(tmp_path):
    # edit 1 allowed here but denied by another hook -> no PostToolUse
    edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)
    decision, reason = edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.2)
    assert decision == edit_claims.DENY
    assert "has not landed" in reason


def test_edit_after_confirmed_edit_is_allowed(tmp_path):
    edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)
    edit_claims.post_edit(tmp_path, "C:/repo/a.py")
    assert edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.1)[0] == edit_claims.ALLOW
    assert edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.2)[0] == edit_claims.ALLOW


def test_stale_pending_claim_is_taken_over_once(tmp_path):
    edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)          # denied elsewhere, never landed
    later = 100.0 + edit_claims.PENDING_TTL_SECONDS + 1
    assert edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=later)[0] == edit_claims.ALLOW
    assert edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=later + 0.1)[0] == edit_claims.DENY


def test_other_files_are_independent(tmp_path):
    edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)
    assert edit_claims.pre_edit(tmp_path, "C:/repo/b.py", now=100.1)[0] == edit_claims.ALLOW


def test_path_spelling_variants_share_one_claim(tmp_path):
    edit_claims.pre_edit(tmp_path, str(tmp_path / "x" / "a.py"), now=100.0)
    variant = str(tmp_path / "x" / "." / "a.py")
    assert edit_claims.pre_edit(tmp_path, variant, now=100.1)[0] == edit_claims.DENY


def test_sessions_are_isolated(tmp_path):
    s1 = edit_claims.session_dir("session-1", root=tmp_path)
    s2 = edit_claims.session_dir("session-2", root=tmp_path)
    edit_claims.pre_edit(s1, "C:/repo/a.py", now=100.0)
    assert edit_claims.pre_edit(s2, "C:/repo/a.py", now=100.1)[0] == edit_claims.ALLOW


def test_session_id_cannot_escape_state_root(tmp_path):
    d = edit_claims.session_dir("../../etc/x", root=tmp_path)
    assert d.parent == tmp_path


def test_concurrent_first_edits_only_one_takes_the_claim(tmp_path):
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(edit_claims.pre_edit(tmp_path, "C:/repo/a.py", now=100.0)[0])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(edit_claims.ALLOW) == 1
    assert results.count(edit_claims.DENY) == 7


def _run_hook(payload, tmp_path) -> subprocess.CompletedProcess:
    env = {"TEMP": str(tmp_path), "TMP": str(tmp_path), "TMPDIR": str(tmp_path), "SYSTEMROOT": "C:\\Windows"}
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOKS_DIR / "edit_claims.py")], input=text,
                          capture_output=True, text=True, env=env, timeout=30)


def test_hook_protocol_partial_batch_is_denied_then_recovers(tmp_path):
    base = {"session_id": "s-proto", "tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "f.py")}}
    assert _run_hook({**base, "hook_event_name": "PreToolUse"}, tmp_path).stdout.strip() == ""   # allow = silent
    denied = json.loads(_run_hook({**base, "hook_event_name": "PreToolUse"}, tmp_path).stdout)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    _run_hook({**base, "hook_event_name": "PostToolUse"}, tmp_path)                              # first edit landed
    assert _run_hook({**base, "hook_event_name": "PreToolUse"}, tmp_path).stdout.strip() == ""


def test_hook_ignores_non_edit_tools_and_fails_open_on_bad_input(tmp_path):
    bash = _run_hook({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
                      "tool_input": {"command": "ls"}}, tmp_path)
    assert bash.returncode == 0 and bash.stdout == ""
    bad = _run_hook("not json", tmp_path)
    assert bad.returncode == 0 and bad.stdout == ""
    assert "failed open" in bad.stderr


def test_settings_register_pre_and_post_hooks():
    settings = json.loads((PROJECT_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = [h["command"] for m in settings["hooks"]["PreToolUse"] if "Edit" in m["matcher"] for h in m["hooks"]]
    post = [h["command"] for m in settings["hooks"]["PostToolUse"] if "Edit" in m["matcher"] for h in m["hooks"]]
    assert any("run-edit-claims.sh" in c for c in pre)
    assert any("run-edit-claims.sh" in c for c in post)
    assert any("run-guardrails.sh" in c for c in pre)                                           # existing gate kept
    template = json.loads((PROJECT_ROOT / ".claude" / "hookify-templates" / "settings.json").read_text("utf-8"))
    assert template == settings
    for name in ("edit_claims.py", "run-edit-claims.sh"):
        assert (PROJECT_ROOT / ".claude" / "hookify-templates" / name).read_bytes() == \
            (HOOKS_DIR / name).read_bytes()
