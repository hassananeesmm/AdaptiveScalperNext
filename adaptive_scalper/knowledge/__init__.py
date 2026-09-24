"""Curated, version-controlled project knowledge in Open Knowledge Format
(OKF v0.2) -- the hybrid half of the knowledge architecture.

    OKF (Git, `knowledge/`)             SQLite (authoritative runtime data)
    ---------------------------------   ------------------------------------
    architecture / engineering decisions trade records, journal events
    strategy definitions                 execution incidents, positions
    approved research findings           runtime state, kill switch
    model cards, lessons learned         RAG memories (derived from the above)
    safety procedures, runbooks

OKF is read-only, advisory evidence. It never controls order execution,
changes risk limits, clears the kill switch, re-activates retired
strategies or promotes unverified research (see `validate.py` for the
enforced policy and `tests/test_knowledge.py` for the isolation audit).
"""
