"""Evidence provenance (directive section 82).

Every piece of evidence this codebase produces or consumes — a trade
result, a feature snapshot, a model prediction, a journal entry — must be
traceable to exactly one of these origins, and different origins must
NEVER be silently pooled together as if they were equally trustworthy
(directive section 80: "Never mix them silently"; section 100's
dashboard performance tabs: "Separate evidence tabs... Never mix them").

`BACKTEST` and `SIMULATED` are similar but distinct: `BACKTEST` is the
label this codebase's own `adaptive_scalper/backtest/` engine uses for a
causal historical replay (directive section 80); `SIMULATED` is the
broader/generic bucket for any other synthetic-outcome evidence
(fixtures, what-if research) that isn't a formal backtest run.
"""

from __future__ import annotations

from enum import Enum


class EvidenceOrigin(str, Enum):
    BROKER_DEMO_CONFIRMED = "BROKER_DEMO_CONFIRMED"      # a real DEMO broker fill/close, reconciled
    PAPER_LIVE_DATA = "PAPER_LIVE_DATA"                  # PAPER engine, real-time live market data, no order_send
    BROKER_ACCOUNT_HISTORY = "BROKER_ACCOUNT_HISTORY"    # imported broker order/deal history, strategy=UNKNOWN/EXTERNAL
    HISTORICAL_MT5_REPLAY = "HISTORICAL_MT5_REPLAY"      # raw historical bar/tick data itself (not yet a trade outcome)
    BACKTEST = "BACKTEST"                                # this codebase's causal backtest engine
    SIMULATED = "SIMULATED"                              # generic synthetic/what-if evidence, not a formal backtest
    IMPORTED = "IMPORTED"                                # any other externally-sourced record
    UNVERIFIED = "UNVERIFIED"                             # provenance genuinely unknown -- never silently upgraded


# Origins that may NEVER be presented as, or silently promoted to, a
# claim of real broker-confirmed performance (directive: "Do not claim
# profitability", "never mix them silently").
NON_LIVE_ORIGINS = frozenset({
    EvidenceOrigin.BACKTEST, EvidenceOrigin.SIMULATED, EvidenceOrigin.HISTORICAL_MT5_REPLAY,
    EvidenceOrigin.IMPORTED, EvidenceOrigin.UNVERIFIED,
})
