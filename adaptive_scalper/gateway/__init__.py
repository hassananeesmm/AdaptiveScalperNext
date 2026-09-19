from adaptive_scalper.gateway.demo_gate import DemoVerificationResult, verify_demo_before_order
from adaptive_scalper.gateway.fake_gateway import FakeGateway
from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway, Mt5NotAvailableError
from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.symbol_resolver import (
    ResolutionResult,
    load_persisted_mapping,
    persist_all,
    persist_resolution,
    resolve_all,
    resolve_symbol,
)
from adaptive_scalper.gateway.symbol_validation import (
    SymbolValidationResult,
    persist_validation,
    resolve_and_validate,
    validate_resolved_symbol,
)
from adaptive_scalper.gateway.types import (
    AccountSnapshot,
    Bar,
    SymbolSpec,
    SymbolTradeMode,
    TerminalSnapshot,
    Tick,
    TradeMode,
)

__all__ = [
    "DemoVerificationResult",
    "verify_demo_before_order",
    "FakeGateway",
    "Mt5Gateway",
    "Mt5NotAvailableError",
    "Gateway",
    "ResolutionResult",
    "load_persisted_mapping",
    "persist_all",
    "persist_resolution",
    "resolve_all",
    "resolve_symbol",
    "SymbolValidationResult",
    "persist_validation",
    "resolve_and_validate",
    "validate_resolved_symbol",
    "AccountSnapshot",
    "Bar",
    "SymbolSpec",
    "SymbolTradeMode",
    "TerminalSnapshot",
    "Tick",
    "TradeMode",
]
