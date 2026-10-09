"""Structural guarantees of the issue #6 fix (raw score is not a probability).

1. End to end, the DEMO and PAPER runtimes are FLAT by default: no validated
   edge evidence exists, so no broker request and no simulated trade happens.
2. The runtimes refuse the legacy V1 raw-score replay provider.
3. Only costs/edge_evidence.py may build expected edge from a raw score, and
   no production module constructs VALIDATED evidence.
4. The evidence module cannot reach risk limits, the kill switch, the broker
   gateway or execution (it imports none of them), so a calibration can never
   raise a limit, enable REAL or send an order.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
from adaptive_scalper.core.operator_authority import OperatorAuthority
from adaptive_scalper.costs.edge_evidence import LEGACY_V1_RAW_SCORE_EVIDENCE
from adaptive_scalper.runtime.engine import RuntimeComponents
from runtime_helpers import STEP, T0, FakeClock, build_engine, step

ROOT = Path(__file__).resolve().parents[1] / "adaptive_scalper"
START_AT = T0 + 60 * STEP + 10


def _boot(conn):
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")


def test_demo_runtime_is_flat_by_default_and_never_sends(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="DEMO", clock=clock)
    _boot(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 6, tick=4)
    assert gateway.calls["order_send"] == 0 and gateway.calls["order_check"] == 0
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    decisions = {r[0] for r in conn.execute("SELECT decision FROM entry_decisions WHERE stage = 'SELECTOR'")}
    assert "BLOCK_EDGE_UNVALIDATED" in decisions, decisions


def test_paper_runtime_is_flat_by_default(tmp_path):
    clock = FakeClock(START_AT)
    engine, conn, gateway = build_engine(tmp_path, mode="PAPER", clock=clock)
    _boot(conn)
    engine.startup()
    step(engine, clock, seconds=STEP * 30, tick=4)
    assert conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0] == 0
    assert gateway.calls["order_send"] == 0


@pytest.mark.parametrize("mode", ["DEMO", "PAPER"])
def test_runtimes_refuse_the_legacy_raw_score_provider(tmp_path, mode):
    clock = FakeClock(START_AT)
    engine, conn, _ = build_engine(tmp_path, mode=mode, clock=clock,
                                   components=RuntimeComponents(edge_evidence=LEGACY_V1_RAW_SCORE_EVIDENCE))
    _boot(conn)
    with pytest.raises(Exception, match="LEGACY_V1_RAW_SCORE"):
        engine.startup()


def _modules():
    for path in ROOT.rglob("*.py"):
        yield path.relative_to(ROOT).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _names(node) -> set[str]:
    return {n.attr if isinstance(n, ast.Attribute) else n.id
            for n in ast.walk(node) if isinstance(n, (ast.Attribute, ast.Name))}


def _raw_score_ev_sites(tree) -> list[int]:
    """Lines where arithmetic mixes the raw score -- directly or through a
    local alias assigned from it (`p = signal.raw_confidence`) -- with the
    configured target/stop distance."""
    hits = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] or [tree]:
        tainted = {"raw_confidence"}
        for node in ast.walk(func):
            if isinstance(node, ast.Assign) and _names(node.value) & tainted:
                tainted |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(func):
            if isinstance(node, ast.BinOp):
                names = _names(node)
                if names & tainted and names & {"target_distance", "stop_distance"}:
                    hits.append(node.lineno)
    return hits


def test_only_the_legacy_provider_combines_raw_score_with_the_configured_payoff():
    """No arithmetic anywhere else mixes raw_confidence with target/stop
    distance -- i.e. nothing outside the labelled replay computes an EV from
    the raw score."""
    offenders = [f"{rel}:{line}" for rel, tree in _modules() if rel != "costs/edge_evidence.py"
                 for line in _raw_score_ev_sites(tree)]
    assert offenders == []


def test_the_raw_score_scan_catches_the_pre_fix_formula():
    """Negative control: the scan must flag the exact pre-fix V1 code."""
    old = (
        "def expected_gross_edge_price(signal):\n"
        "    p = signal.raw_confidence\n"
        "    return p * signal.target_distance - (1 - p) * signal.stop_distance\n"
    )
    assert _raw_score_ev_sites(ast.parse(old)) != []


def test_no_production_module_constructs_validated_evidence():
    allowed = {"costs/edge_evidence.py", "costs/edge.py"}
    offenders = [rel for rel, tree in _modules() if rel not in allowed and "EVIDENCE_VALIDATED" in _names(tree)]
    assert offenders == []


def test_edge_evidence_module_cannot_reach_risk_kill_switch_gateway_or_execution():
    tree = ast.parse((ROOT / "costs/edge_evidence.py").read_text(encoding="utf-8"))
    imported = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    forbidden = ("adaptive_scalper.risk", "adaptive_scalper.core", "adaptive_scalper.gateway",
                 "adaptive_scalper.execution", "adaptive_scalper.config", "MetaTrader5")
    assert not [m for m in imported if m.startswith(forbidden)], imported
