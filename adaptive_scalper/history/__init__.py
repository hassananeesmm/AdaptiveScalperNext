"""Historical MT5 data bootstrap (MASTER_BUILD_DIRECTIVE.md sections 46-52).

Chunked, resumable download of bar and tick history for the three
canonical symbols, with idempotent storage and derived coverage summaries.
Nothing in this package submits orders or makes trading decisions — it is
purely data acquisition feeding the not-yet-built feature engine.
"""
