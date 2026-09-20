"""Local, advisory-only retrieval-augmented memory (directive: RAG).

SQLite (`rag_memories`) is the sole authoritative store; the TF-IDF
index (`rag.index`) is a derived, rebuildable-from-scratch artifact.
`rag.service.RagService` is the intended public entry point for every
other subsystem — nothing outside this package should import
`rag.store`/`rag.index` directly.
"""
