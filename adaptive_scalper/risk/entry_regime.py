"""Operator directional-momentum entry gate (config `[entry_regime]`).

A NEW entry is allowed only while Wilder ADX(period) of the latest closed
bars is STRICTLY above `min_adx`. Pure function, shared by the runtimes'
per-symbol decision and the final permission gate. It only ever blocks:
exits and position management never consult it. With a threshold
configured, an unknown ADX (too few bars, non-finite) blocks.
"""

from __future__ import annotations

import math

BLOCK_REGIME = "BLOCK_REGIME"


def adx_entry_block(adx: float | None, min_adx: float | None) -> tuple[str, str] | None:
    if min_adx is None:
        return None
    if adx is None or not math.isfinite(adx):
        return BLOCK_REGIME, f"ADX unavailable (need > {min_adx:g}) -- no directional-momentum confirmation"
    if adx > min_adx:
        return None
    return BLOCK_REGIME, f"ADX {adx:.2f} is not above {min_adx:g} -- no directional-momentum confirmation"
