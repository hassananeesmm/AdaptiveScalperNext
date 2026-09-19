"""DEMO hard interlock.

Per MASTER_BUILD_DIRECTIVE.md section 4: immediately before every MT5
order request, positively establish that the connected account is DEMO.
Never infer DEMO merely from configuration — broker/account truth wins.
If DEMO status cannot be positively proven, block order submission.

This module implements the account/terminal/connection slice of that
10-point pre-order checklist (points 1-4): fresh account info, DEMO
status, terminal Algo Trading permission, broker/account trading
permission. The remaining points (gateway/reconciliation/UNKNOWN/kill
switch) belong to the modules that own that state and are composed by the
eventual final permission gate (directive section 36), not duplicated
here.

Callers MUST call this immediately before every order — not once at
startup and cached — because directive section 4 requires detecting an
account switch (DEMO -> real) while the engine is running.
"""

from __future__ import annotations

from dataclasses import dataclass

from adaptive_scalper.gateway.protocol import Gateway
from adaptive_scalper.gateway.types import TradeMode

BLOCK_MT5_DISCONNECTED = "BLOCK_MT5_DISCONNECTED"
BLOCK_TERMINAL_TRADING_DISABLED = "BLOCK_TERMINAL_TRADING_DISABLED"
BLOCK_BROKER_TRADING_DISABLED = "BLOCK_BROKER_TRADING_DISABLED"
BLOCK_ACCOUNT_NOT_DEMO = "BLOCK_ACCOUNT_NOT_DEMO"


@dataclass(frozen=True)
class DemoVerificationResult:
    allowed: bool
    block_reason: str | None
    detail: str


def verify_demo_before_order(gateway: Gateway) -> DemoVerificationResult:
    """Re-fetch fresh account/terminal state and verify it is safe to
    proceed toward an order. Returns allowed=False with a directive-
    vocabulary block_reason on ANY failure to positively prove DEMO —
    there is no default-allow path.
    """
    terminal = gateway.terminal_info()
    if terminal is None or not terminal.connected:
        return DemoVerificationResult(
            allowed=False, block_reason=BLOCK_MT5_DISCONNECTED,
            detail="terminal_info() returned None or connected=False",
        )

    account = gateway.account_info()
    if account is None:
        return DemoVerificationResult(
            allowed=False, block_reason=BLOCK_MT5_DISCONNECTED,
            detail="account_info() returned None",
        )

    if not terminal.trade_allowed:
        return DemoVerificationResult(
            allowed=False, block_reason=BLOCK_TERMINAL_TRADING_DISABLED,
            detail="terminal Algo Trading permission is off",
        )

    if not account.trade_allowed or not account.trade_expert:
        return DemoVerificationResult(
            allowed=False, block_reason=BLOCK_BROKER_TRADING_DISABLED,
            detail=f"account trade_allowed={account.trade_allowed} "
                   f"trade_expert={account.trade_expert}",
        )

    if account.trade_mode != TradeMode.DEMO:
        return DemoVerificationResult(
            allowed=False, block_reason=BLOCK_ACCOUNT_NOT_DEMO,
            detail=f"account.trade_mode={account.trade_mode!r}, not DEMO — "
                   f"real-money orders are permanently prohibited",
        )

    return DemoVerificationResult(
        allowed=True, block_reason=None,
        detail=f"DEMO confirmed: login={account.login} server={account.server!r}",
    )
