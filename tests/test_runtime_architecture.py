"""Architecture audits (completion directive Phase 12), enforced on every
run by walking the real source ASTs -- not by convention:

- the MT5 gateway is constructed in exactly one place (`gateway/factory.py`,
  which always wraps it in `SynchronizedGateway`);
- `order_send` / `order_check` are called only by the three safe execution
  services and the gateway implementations themselves -- never by the
  runtime, PAPER, backtest, strategies, selector, learning, RAG, research
  or dashboard code;
- the kill switch can only be bootstrapped or cleared from the operator
  CLI, and `OperatorAuthority` is only constructed there;
- runtime/advisory/learning/RAG/research/dashboard code never imports the
  operator authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "adaptive_scalper"


def _sources():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path.relative_to(PACKAGE).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                owner = func.value.id if isinstance(func.value, ast.Name) else None
                yield func.attr, owner
            elif isinstance(func, ast.Name):
                yield func.id, None


def _callers(name: str, owner: str | None = None) -> set[str]:
    found = set()
    for rel, tree in _sources():
        for called, called_owner in _calls(tree):
            if called == name and (owner is None or called_owner == owner):
                found.add(rel)
    return found


def _imports_of(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_the_live_mt5_gateway_is_constructed_in_exactly_one_place():
    assert _callers("Mt5Gateway") == {"gateway/factory.py"}


def test_only_the_safe_execution_services_send_or_check_orders():
    gateways = {"gateway/fake_gateway.py", "gateway/mt5_gateway.py", "gateway/synchronized_gateway.py"}
    services = {"execution/service.py", "execution/close.py", "execution/stop_modification.py"}
    assert _callers("order_send") <= gateways | services
    assert _callers("order_check") <= gateways | services
    assert "execution/service.py" in _callers("order_send")


def test_the_runtime_enters_positions_only_through_the_execution_service():
    runtime_calls = set()
    for rel, tree in _sources():
        if rel.startswith(("runtime/", "paper/", "backtest/")):
            runtime_calls |= {name for name, _ in _calls(tree)}
    assert "order_send" not in runtime_calls and "order_check" not in runtime_calls
    assert "submit_new_entry" in runtime_calls  # DEMO entries go through the one guarded path


def test_kill_switch_bootstrap_and_clear_are_operator_cli_only():
    callers = set()
    for rel, tree in _sources():
        imports = _imports_of(tree)
        for name, owner in _calls(tree):
            if owner == "kill_switch" and name in ("clear", "bootstrap"):
                callers.add(rel)
            if name in ("clear", "bootstrap") and owner is None and any(
                imp in imports for imp in (f"adaptive_scalper.core.kill_switch.{name}",)
            ):
                callers.add(rel)
    assert callers <= {"cli/operator.py"}


def test_operator_authority_is_only_constructed_by_the_operator_cli():
    assert _callers("OperatorAuthority") <= {"cli/operator.py"}


def test_non_operator_subsystems_never_import_operator_authority():
    restricted = ("runtime/", "learning/", "rag/", "research/", "dashboard/", "strategies/", "selector/",
                  "risk/", "portfolio/", "paper/", "backtest/", "knowledge/")
    for rel, tree in _sources():
        if rel.startswith(restricted):
            assert "adaptive_scalper.core.operator_authority" not in _imports_of(tree), rel


def test_the_runtime_never_mutates_the_kill_switch():
    for rel, tree in _sources():
        if rel.startswith("runtime/"):
            for name, owner in _calls(tree):
                assert not (name in ("engage", "clear", "bootstrap") and owner in (None, "kill_switch")), rel


def test_there_is_no_live_or_real_execution_mode():
    from adaptive_scalper.config.constants import ALLOWED_MODES

    assert ALLOWED_MODES == frozenset({"PAPER", "DEMO"})
    for rel, tree in _sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value.upper() not in ("LIVE", "REAL_MONEY", "LIVE_TRADING"), (rel, node.value)
