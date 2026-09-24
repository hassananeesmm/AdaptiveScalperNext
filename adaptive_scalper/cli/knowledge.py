"""RAG memory and OKF knowledge commands (advisory tooling; nothing here
touches trading state)."""

from __future__ import annotations

import argparse
import time

from adaptive_scalper.cli.common import open_db, print_json
from adaptive_scalper.knowledge.advisor import DEFAULT_BUNDLE
from adaptive_scalper.knowledge.benchmark import format_table, run_benchmark
from adaptive_scalper.knowledge.search import concept_summary, lookup, search
from adaptive_scalper.knowledge.validate import is_valid, validate_bundle
from adaptive_scalper.rag.ingestion import ingest
from adaptive_scalper.rag.service import RagService


def _rag(conn) -> tuple[RagService, str]:
    rag = RagService()
    return rag, rag.rebuild_index(conn)


def cmd_rag_status(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rag, status = _rag(conn)
    marks = {r[0]: {"last_id": r[1], "updated_at_utc": r[2]} for r in conn.execute("SELECT * FROM rag_ingestion_state")}
    out = {"index_rebuild": status, **rag.status(conn), "ingestion_watermarks": marks}
    conn.close()
    print_json(out)
    return 0 if status == "OK" or out["total_memories"] == 0 else 1


def cmd_rag_stats(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rows = conn.execute("SELECT memory_type, COALESCE(origin, 'UNLABELLED') AS origin, COUNT(*) AS n "
                        "FROM rag_memories GROUP BY memory_type, origin ORDER BY memory_type, origin").fetchall()
    conn.close()
    print_json([dict(r) for r in rows])
    return 0


def cmd_rag_similar(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rag, _ = _rag(conn)
    result = rag.query_similar(args.text, top_k=args.top_k)
    conn.close()
    print_json({"status": result.status, "detail": result.detail, "matches": [
        {"score": round(m.score, 4), "id": m.memory.id, "type": m.memory.memory_type, "symbol": m.memory.canonical_symbol,
         "text": m.memory.content_text} for m in result.matches]})
    return 0


def cmd_rag_rebuild(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    rag, status = _rag(conn)
    out = {"status": status, **rag.status(conn)}
    conn.close()
    print_json(out)
    return 0 if status == "OK" or out["total_memories"] == 0 else 1


def cmd_rag_verify(args: argparse.Namespace) -> int:
    """Index covers every stored memory; every ingested memory's source row
    still exists (SQLite stays authoritative)."""
    _, conn = open_db(args.config)
    rag, status = _rag(conn)
    state = rag.status(conn)
    total = state["total_memories"]
    tables = {"journal": "journal_events", "paper_trade": "paper_trades", "incident": "execution_incidents",
              "runtime_event": "runtime_events"}
    dangling = []
    for row in conn.execute("SELECT id, source_key FROM rag_memories WHERE source_key IS NOT NULL"):
        kind, _, ident = row["source_key"].partition(":")
        if kind == "trial":
            exists = conn.execute("SELECT 1 FROM research_trials WHERE trial_id = ?", (ident,)).fetchone()
        elif kind in tables:
            exists = conn.execute(f"SELECT 1 FROM {tables[kind]} WHERE id = ?", (int(ident),)).fetchone()  # nosec B608 - fixed table map
        else:
            exists = None
        if exists is None:
            dangling.append(row["source_key"])
    unlabelled = conn.execute("SELECT COUNT(*) FROM rag_memories WHERE origin IS NULL").fetchone()[0]
    conn.close()
    ok = (total == 0 or (status == "OK" and state["index_size"] == total)) and not dangling
    print_json({"ok": ok, "index_status": status, "memories": total, "dangling_source_keys": dangling[:50],
                "memories_without_origin": unlabelled})
    return 0 if ok else 1


def cmd_rag_ingest(args: argparse.Namespace) -> int:
    _, conn = open_db(args.config)
    report = ingest(conn, now_utc=int(time.time()))
    conn.close()
    print_json({"inserted": report.inserted, "scanned": report.scanned})
    return 0


def cmd_okf_status(args: argparse.Namespace) -> int:
    bundle = validate_bundle(args.bundle)
    by_type: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for concept in bundle.concepts.values():
        by_type[concept.type] = by_type.get(concept.type, 0) + 1
        tiers[concept.trust_tier] = tiers.get(concept.trust_tier, 0) + 1
    print_json({"bundle": str(args.bundle), "okf_version": bundle.okf_version, "checksum": bundle.checksum,
                "concepts": len(bundle.concepts), "by_type": by_type, "trust_tiers": tiers,
                "errors": sum(1 for i in bundle.issues if i.severity == "ERROR"),
                "warnings": sum(1 for i in bundle.issues if i.severity == "WARNING")})
    return 0 if is_valid(bundle) else 1


def cmd_okf_validate(args: argparse.Namespace) -> int:
    bundle = validate_bundle(args.bundle)
    print_json({"valid": is_valid(bundle), "issues": [i.__dict__ for i in bundle.issues]})
    return 0 if is_valid(bundle) else 1


def cmd_okf_search(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    bundle = validate_bundle(args.bundle)
    now = datetime.now(timezone.utc)
    direct = lookup(bundle, args.query)
    hits = [direct] if direct is not None else [h.concept for h in search(bundle, args.query, top_k=args.top_k)]
    print_json([{**concept_summary(c, now), "description": c.description} for c in hits])
    return 0


def cmd_okf_benchmark(args: argparse.Namespace) -> int:
    print(format_table(run_benchmark(args.bundle, rag_documents=args.rag_documents)))
    return 0


def register(sub) -> None:
    rag = sub.add_parser("rag", help="SQLite RAG memory (advisory)")
    rsub = rag.add_subparsers(dest="rag_command", required=True)
    rsub.add_parser("status", help="index and ingestion state").set_defaults(func=cmd_rag_status)
    rsub.add_parser("stats", help="memories by type and origin").set_defaults(func=cmd_rag_stats)
    similar = rsub.add_parser("similar", help="similar past memories for a text")
    similar.add_argument("text")
    similar.add_argument("--top-k", type=int, default=5)
    similar.set_defaults(func=cmd_rag_similar)
    rsub.add_parser("rebuild-index", help="rebuild the derived TF-IDF index").set_defaults(func=cmd_rag_rebuild)
    rsub.add_parser("verify-index", help="index health and source-row integrity").set_defaults(func=cmd_rag_verify)
    rsub.add_parser("ingest", help="run journal -> RAG ingestion now").set_defaults(func=cmd_rag_ingest)

    okf = sub.add_parser("okf", help="OKF knowledge bundle (curated, Git-tracked)")
    okf.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    osub = okf.add_subparsers(dest="okf_command", required=True)
    osub.add_parser("status", help="bundle summary").set_defaults(func=cmd_okf_status)
    osub.add_parser("validate", help="OKF conformance + project policy").set_defaults(func=cmd_okf_validate)
    s = osub.add_parser("search", help="direct lookup by id/path, else keyword search")
    s.add_argument("query")
    s.add_argument("--top-k", type=int, default=5)
    s.set_defaults(func=cmd_okf_search)
    b = osub.add_parser("benchmark", help="direct OKF vs TF-IDF vs FTS5/BM25")
    b.add_argument("--rag-documents", type=int, default=2000)
    b.set_defaults(func=cmd_okf_benchmark)
