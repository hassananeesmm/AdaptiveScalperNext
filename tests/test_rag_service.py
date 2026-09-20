"""Tests for the RAG advisory service (rag/service.py) — including the
structural-safety guarantees (advisory only, degrades rather than
crashes, no execution capability)."""

from __future__ import annotations

import inspect

import pytest

from adaptive_scalper.persistence import connect, migrate
from adaptive_scalper.rag.service import DEGRADED, OK, RagService
from adaptive_scalper.rag.store import UnknownMemoryTypeError


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    migrate(conn)
    yield conn
    conn.close()


def test_record_and_query_round_trip(db):
    service = RagService()
    service.record(db, "TRADE_SETUP", "BUY XAUUSD momentum breakout", {"confidence": 0.7}, canonical_symbol="XAUUSD")
    service.rebuild_index(db)

    result = service.query_similar("XAUUSD momentum breakout")
    assert result.status == OK
    assert len(result.matches) == 1


def test_record_never_raises_on_bad_input(db):
    service = RagService()
    result = service.record(db, "NOT_A_TYPE", "content", {})
    assert result is None  # UnknownMemoryTypeError swallowed, not propagated


def test_query_before_rebuild_is_degraded_not_error(db):
    service = RagService()
    result = service.query_similar("anything")
    assert result.status == DEGRADED
    assert result.matches == ()


def test_query_on_empty_db_is_degraded(db):
    service = RagService()
    service.rebuild_index(db)
    result = service.query_similar("anything")
    assert result.status == DEGRADED


def test_status_reports_counts(db):
    service = RagService()
    service.record(db, "SYSTEM_EVENT", "startup", {})
    service.rebuild_index(db)
    status = service.status(db)
    assert status["total_memories"] == 1
    assert status["index_built"] is True
    assert status["index_size"] == 1


def test_rebuild_index_handles_closed_connection_gracefully(db):
    service = RagService()
    db.close()
    result = service.rebuild_index(db)
    assert result == DEGRADED


# --------------------------------------------------------------------------
# Structural safety: RAG is advisory-only, never an execution path.
# --------------------------------------------------------------------------

def test_record_method_takes_no_execution_capable_parameters():
    """No parameter of RagService.record() could plausibly carry a
    Gateway, risk limits, kill-switch state, or final-permission
    authority — this is a structural guarantee (like
    calculate_safe_volume()'s signature-inspection test), not just a
    docstring promise."""
    sig = inspect.signature(RagService.record)
    forbidden_substrings = ("gateway", "risk", "kill_switch", "permission", "authority")
    for name in sig.parameters:
        lowered = name.lower()
        assert not any(f in lowered for f in forbidden_substrings), (
            f"RagService.record() has a suspicious parameter {name!r} — RAG must remain structurally advisory-only"
        )


def test_query_similar_method_takes_no_execution_capable_parameters():
    sig = inspect.signature(RagService.query_similar)
    forbidden_substrings = ("gateway", "risk", "kill_switch", "permission", "authority")
    for name in sig.parameters:
        lowered = name.lower()
        assert not any(f in lowered for f in forbidden_substrings)


def test_rag_service_has_no_order_send_or_gateway_import():
    """Checks actual imports, not prose — the docstrings legitimately
    MENTION Gateway/KillSwitch/order_send while explaining why RAG must
    never touch them; what must be structurally absent is an import of
    any such module, which is what would make the capability reachable."""
    import ast

    for module_path in (
        "adaptive_scalper/rag/service.py", "adaptive_scalper/rag/index.py", "adaptive_scalper/rag/store.py",
    ):
        tree = ast.parse(open(module_path, encoding="utf-8").read())
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        forbidden = {"adaptive_scalper.gateway", "adaptive_scalper.core.kill_switch", "adaptive_scalper.risk"}
        for name in imported_names:
            assert not any(name.startswith(f) for f in forbidden), (
                f"{module_path} imports {name!r} — RAG must never import gateway/kill-switch/risk modules"
            )
