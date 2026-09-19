"""Risk governor — the SOLE authority for monetary position sizing and
hard portfolio risk ceilings (directive section 32).

No strategy, model, RAG, or selector may decide volume or override these
limits — see `governor.py`'s module docstring for how sizing is made
structurally immune to martingale/grid/revenge-sizing, and
`risk_limits_from_config()`'s docstring for why learning can never raise
these ceilings.
"""
