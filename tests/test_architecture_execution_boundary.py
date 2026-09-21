"""Architectural regression test (execution-safety review, "BUILD ONE
EXECUTION SERVICE"): `execution/service.py` must be the ONLY module that
calls `Gateway.order_send()` for a NEW entry. Strategies, the selector,
learning/RAG, and the dashboard must only ever produce PROPOSALS.

This scans source text for `.order_send(` rather than doing real
call-graph analysis — deliberately conservative (a textual match is a
false-negative-free, if occasionally false-positive, guard) so a future
contributor cannot quietly reintroduce a second calling path without
this test forcing an explicit decision.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTIVE_SCALPER = REPO_ROOT / "adaptive_scalper"

# Modules allowed to reference `.order_send(` in source text: the
# execution service itself (the sole real caller), the gateway layer
# (which DEFINES order_send, and FakeGateway's own simulation of it),
# and execution/close.py (the safe close path, execution-safety review
# finding #2 — closing an EXISTING position is not a "new entry").
ALLOWED_DIRS = {
    ADAPTIVE_SCALPER / "execution",
    ADAPTIVE_SCALPER / "gateway",
}

FORBIDDEN_DIRS = [
    ADAPTIVE_SCALPER / "strategies",
    ADAPTIVE_SCALPER / "selector",
    ADAPTIVE_SCALPER / "learning",
    ADAPTIVE_SCALPER / "rag",
    ADAPTIVE_SCALPER / "dashboard",
    ADAPTIVE_SCALPER / "cli",
    ADAPTIVE_SCALPER / "backtest",
    ADAPTIVE_SCALPER / "paper",
]


def _is_allowed(path: Path) -> bool:
    return any(path.is_relative_to(allowed) for allowed in ALLOWED_DIRS)


def test_only_execution_and_gateway_modules_reference_order_send():
    offenders = []
    for py_file in ADAPTIVE_SCALPER.rglob("*.py"):
        if _is_allowed(py_file):
            continue
        text = py_file.read_text(encoding="utf-8")
        if ".order_send(" in text:
            offenders.append(str(py_file.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"only adaptive_scalper/execution/ and adaptive_scalper/gateway/ may call "
        f".order_send() for a new entry; found direct references in: {offenders}"
    )


def test_forbidden_directories_are_covered_by_the_allowlist_check():
    """Sanity check that the forbidden dirs list stays meaningful even
    before those packages exist yet (learning/, rag/, dashboard/, cli/) —
    this test passes trivially today and starts actually scanning the
    moment those directories are created, with no test file changes
    needed."""
    for forbidden in FORBIDDEN_DIRS:
        assert not _is_allowed(forbidden)
