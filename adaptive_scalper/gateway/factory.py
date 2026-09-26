"""The ONE place a live MT5 gateway is constructed.

Every runtime/CLI path gets its broker connection from
`create_live_gateway()`, which always returns the raw `Mt5Gateway`
wrapped in `SynchronizedGateway` -- so no caller can accidentally hand an
unserialized MetaTrader5 connection to concurrent code, and a process
never holds competing raw gateways. `server_time_rule` is the configured
broker server clock (`[mt5] server_time_rule`, gateway/server_time.py);
every caller passes it explicitly from its config. `tests/test_runtime_architecture.py`
fails the build if `Mt5Gateway(` is constructed anywhere else.
"""

from __future__ import annotations

from adaptive_scalper.gateway.synchronized_gateway import SynchronizedGateway


def create_live_gateway(server_time_rule: str, terminal_path: str | None = None) -> SynchronizedGateway:
    """`terminal_path` (`[mt5] terminal_path`, ASN-010): pin the exact MT5
    terminal on a computer with several installations."""
    from adaptive_scalper.gateway.mt5_gateway import Mt5Gateway  # raises Mt5NotAvailableError off Windows

    return SynchronizedGateway(Mt5Gateway(server_time_rule, terminal_path=terminal_path))
