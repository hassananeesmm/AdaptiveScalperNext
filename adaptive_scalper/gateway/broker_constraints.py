"""Broker-instrument execution constraints (execution-safety review finding #7).

MT5 does not accept `ORDER_FILLING_IOC` unconditionally for every
symbol/broker. The symbol's `filling_mode` bitmask
(`SymbolSpec.filling_mode`, mirroring MT5's `ENUM_SYMBOL_TRADING_MODE_FILLING`)
advertises which fill policies that instrument actually supports. This
module derives a SUPPORTED policy from that bitmask instead of hardcoding
one. If none of the policies this project knows how to handle are
supported, `derive_filling_type()` returns `None` and the caller must
BLOCK_BROKER_CONSTRAINT rather than guess — randomly retrying different
filling modes after an uncertain send is explicitly forbidden.
"""

from __future__ import annotations

FILLING_IOC = "IOC"
FILLING_FOK = "FOK"

_SYMBOL_FILLING_FOK_BIT = 1
_SYMBOL_FILLING_IOC_BIT = 2


def derive_filling_type(symbol_filling_mode: int, *, prefer: str = FILLING_IOC) -> str | None:
    """`None` means no filling policy this project handles is supported —
    the caller must BLOCK_BROKER_CONSTRAINT, never fall back to a guess."""
    supported: list[str] = []
    if symbol_filling_mode & _SYMBOL_FILLING_IOC_BIT:
        supported.append(FILLING_IOC)
    if symbol_filling_mode & _SYMBOL_FILLING_FOK_BIT:
        supported.append(FILLING_FOK)
    if not supported:
        return None
    return prefer if prefer in supported else supported[0]
