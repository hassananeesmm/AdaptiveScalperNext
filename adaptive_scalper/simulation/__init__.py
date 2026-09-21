"""Shared simulation primitives (directive sections 80, 82, "PAPER
ENGINE") — fill/cost simulation and evidence-provenance labeling used by
BOTH `adaptive_scalper/backtest/` (historical replay) and the PAPER
engine (real-time, no `order_send`). Keeping this one shared module is
what makes directive section 80's "never mix BACKTEST/SIMULATION/PAPER/
BROKER_DEMO_CONFIRMED silently" actually enforceable: every simulated
fill anywhere in this codebase goes through the same, single,
consistently-labeled code path.
"""

from __future__ import annotations
