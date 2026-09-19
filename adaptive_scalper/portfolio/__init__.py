"""Correlation and portfolio heat/exposure tracking (directive section 35).

Correlation and exposure MEASUREMENT lives here. Hard risk LIMITS
(max_total_open_risk_pct, max_positions_per_symbol, etc.) belong to the
not-yet-built risk governor (directive section 32), which consumes this
module's outputs — this module never enforces a money-risk ceiling
itself.
"""
