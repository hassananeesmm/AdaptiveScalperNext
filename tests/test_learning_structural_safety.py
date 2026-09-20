"""Structural safety checks for adaptive_scalper/learning/ (directive:
no source self-modification, no eval/exec, models may never raise risk
or touch execution/kill-switch machinery directly)."""

from __future__ import annotations

import ast
from pathlib import Path

LEARNING_DIR = Path(__file__).resolve().parent.parent / "adaptive_scalper" / "learning"


def _learning_modules() -> list[Path]:
    return sorted(LEARNING_DIR.glob("*.py"))


def test_no_eval_or_exec_anywhere_in_learning():
    for path in _learning_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ("eval", "exec"), f"{path} calls {node.func.id}() — forbidden"


def test_no_dynamic_import_or_self_modification_primitives():
    forbidden_calls = {"exec", "eval", "compile", "__import__"}
    for path in _learning_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, f"{path} calls {node.func.id}()"


def test_learning_never_imports_gateway_or_kill_switch_or_execution():
    forbidden_prefixes = (
        "adaptive_scalper.gateway", "adaptive_scalper.core.kill_switch", "adaptive_scalper.execution",
    )
    for path in _learning_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for name in imported:
            assert not any(name.startswith(p) for p in forbidden_prefixes), (
                f"{path} imports {name!r} — learning/ must never reach gateway/kill-switch/execution directly"
            )


def test_registry_module_has_no_risk_sizing_capability():
    """No function in learning/registry.py may compute or accept a
    monetary-risk/volume figure -- sizing remains the sole authority of
    risk.governor.calculate_safe_volume() (directive section 32)."""
    import inspect

    from adaptive_scalper.learning import registry

    for name, func in inspect.getmembers(registry, inspect.isfunction):
        params = set(inspect.signature(func).parameters)
        assert not params & {"monetary_risk", "volume", "risk_per_trade_pct"}, (
            f"learning.registry.{name} has a risk-sizing-shaped parameter — forbidden"
        )
