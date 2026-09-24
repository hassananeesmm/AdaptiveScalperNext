"""OKF knowledge layer (completion directive Phase 5).

- the Git-tracked `knowledge/` bundle is OKF v0.2 conformant and passes
  the project policy with zero errors;
- conformance and policy failures are reported, never raised, and a bad
  concept is quarantined from every consumer;
- the advisor is evidence only (`influence: NONE`), separates unverified
  findings from approved knowledge and never resurrects retired
  strategies;
- isolation: nothing in the trading path imports the knowledge package,
  and the knowledge package imports nothing that can trade, size, permit
  or touch the kill switch;
- the retrieval benchmark runs and reports every retriever.
"""

from __future__ import annotations

import ast
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS
from adaptive_scalper.knowledge.advisor import DEFAULT_BUNDLE, KnowledgeAdvisor
from adaptive_scalper.knowledge.benchmark import run_benchmark
from adaptive_scalper.knowledge.loader import load_bundle, split_frontmatter
from adaptive_scalper.knowledge.model import (
    TRUST_HUMAN_REVIEWED,
    TRUST_MACHINE_CONFIRMED,
    TRUST_UNVERIFIED,
    trust_tier,
)
from adaptive_scalper.knowledge.search import filter_concepts, lookup, search
from adaptive_scalper.knowledge.validate import is_valid, scan_text, validate_bundle
from adaptive_scalper.strategies import build_active_registry

REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)

GOOD = """\
---
type: Safety Procedure
title: Example
description: An example concept.
generated:
  by: claude-code/1
  at: 2026-09-24T00:00:00Z
{extra}
---
Body text about {body}.
"""


def _bundle(tmp_path, files: dict[str, str], *, index: str | None = '---\nokf_version: "0.2"\n---\n# Index\n'):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if index is not None:
        (tmp_path / "index.md").write_text(index)
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    return tmp_path


def _codes(bundle, path=None):
    return {i.code for i in bundle.issues if i.severity == "ERROR" and (path is None or i.path == path)}


def _concept(extra: str = "", body: str = "safety") -> str:
    return GOOD.format(extra=extra, body=body)


# ---------------------------------------------------------------------------
# the real bundle
# ---------------------------------------------------------------------------

def test_the_tracked_bundle_is_conformant_and_policy_clean():
    bundle = validate_bundle(DEFAULT_BUNDLE, now=NOW)
    errors = [i for i in bundle.issues if i.severity == "ERROR"]
    assert errors == []
    assert bundle.okf_version == "0.2"
    assert len(bundle.concepts) >= 15
    assert DEFAULT_BUNDLE == REPO / "knowledge"


def test_every_active_strategy_has_a_matching_definition_and_retired_ones_stay_retired():
    bundle = validate_bundle(DEFAULT_BUNDLE, now=NOW)
    for strategy in build_active_registry().all_active():
        concept = lookup(bundle, f"strategy/{strategy.key}")
        assert concept is not None, strategy.key
        assert concept.frontmatter["strategy_version"] == strategy.version, "definition drifted from code"
        assert concept.status == "stable" and concept.frontmatter["retired"] is False
    for key in RETIRED_STRATEGY_KEYS:
        concept = lookup(bundle, f"strategy/{key}")
        assert concept.status == "deprecated" and concept.frontmatter["retired"] is True


def test_evidence_concepts_in_the_tracked_bundle_are_never_stable_without_human_review():
    bundle = validate_bundle(DEFAULT_BUNDLE, now=NOW)
    for concept in bundle.concepts.values():
        if concept.type in ("Research Finding", "Model Card", "Lesson Learned") and concept.status == "stable":
            assert concept.trust_tier == TRUST_HUMAN_REVIEWED, concept.path


# ---------------------------------------------------------------------------
# OKF conformance (spec section 11)
# ---------------------------------------------------------------------------

def test_missing_or_unparseable_frontmatter_and_missing_type_are_conformance_errors(tmp_path):
    root = _bundle(tmp_path, {
        "a.md": "# no frontmatter\n",
        "b.md": "---\ntype: [unclosed\n---\n",
        "c.md": "---\ntitle: no type\n---\n",
        "d.md": "---\n- a list\n---\n",
    })
    bundle = load_bundle(root)
    assert _codes(bundle, "a.md") == {"OKF_FRONTMATTER_MISSING"}
    assert _codes(bundle, "b.md") == {"OKF_FRONTMATTER_INVALID"}
    assert _codes(bundle, "c.md") == {"OKF_TYPE_MISSING"}
    assert _codes(bundle, "d.md") == {"OKF_FRONTMATTER_INVALID"}
    assert bundle.concepts == {}


def test_reserved_files_follow_their_structure(tmp_path):
    root = _bundle(tmp_path, {
        "log.md": "---\ntype: Log\n---\n",
        "sub/index.md": "---\nokf_version: \"0.2\"\n---\n",
    }, index="---\nokf_version: \"0.2\"\ntitle: nope\n---\n")
    bundle = load_bundle(root)
    assert _codes(bundle, "log.md") == {"OKF_RESERVED_LOG"}
    assert _codes(bundle, "sub/index.md") == {"OKF_RESERVED_INDEX"}
    assert _codes(bundle, "index.md") == {"OKF_RESERVED_INDEX"}
    assert "log.md" not in bundle.concepts and "sub/index.md" not in bundle.concepts


def test_the_loader_is_permissive_about_unknown_types_and_keys(tmp_path):
    root = _bundle(tmp_path, {"x.md": _concept("custom_key: 1").replace("Safety Procedure", "Something New")})
    bundle = validate_bundle(root, now=NOW)
    assert is_valid(bundle)
    assert {i.code for i in bundle.issues} == {"POLICY_TYPE_UNKNOWN"}  # a warning, not a rejection


def test_yaml_is_loaded_safely(tmp_path):
    root = _bundle(tmp_path, {"x.md": "---\ntype: !!python/object/apply:os.system ['echo pwned']\n---\n"})
    bundle = load_bundle(root)
    assert _codes(bundle, "x.md") == {"OKF_FRONTMATTER_INVALID"}


def test_oversized_and_symlinked_files_are_refused(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(_concept())
    root = _bundle(tmp_path / "b", {"big.md": _concept(body="x" * 300_000)})
    (root / "link.md").symlink_to(outside)
    bundle = load_bundle(root)
    assert _codes(bundle, "big.md") == {"LOAD_FILE_TOO_LARGE"}
    assert _codes(bundle, "link.md") == {"LOAD_SYMLINK_REFUSED"}


def test_frontmatter_split_handles_bom_and_body():
    fm, body = split_frontmatter("﻿---\ntype: X\n---\nhello\n")
    assert fm == "type: X\n" and body == "hello\n"
    assert split_frontmatter("no fm") == (None, "no fm")


# ---------------------------------------------------------------------------
# provenance / verification / lifecycle policy
# ---------------------------------------------------------------------------

def test_trust_tiers_are_derived_from_verifiers():
    assert trust_tier({}) == TRUST_UNVERIFIED
    assert trust_tier({"verified": {"by": "process:nightly", "at": "2026-01-01T00:00:00Z"}}) == TRUST_MACHINE_CONFIRMED
    assert trust_tier({"verified": [{"by": "process:x", "at": "t"}, {"by": "human:ops", "at": "t"}]}) == TRUST_HUMAN_REVIEWED


def test_provenance_fields_are_required_and_well_formed(tmp_path):
    root = _bundle(tmp_path, {
        "no_generated.md": "---\ntype: Runbook\ntitle: t\ndescription: d\n---\n",
        "bad_actor.md": _concept("verified:\n  - by: bob\n    at: 2026-01-01T00:00:00Z"),
        "naive_ts.md": _concept().replace("2026-09-24T00:00:00Z", "2026-09-24T00:00:00"),
        "bad_source.md": _concept("sources:\n  - title: no resource"),
        "bad_status.md": _concept("status: approved"),
    })
    bundle = validate_bundle(root, now=NOW)
    assert "POLICY_GENERATED_MISSING" in _codes(bundle, "no_generated.md")
    assert "POLICY_ACTOR_INVALID" in _codes(bundle, "bad_actor.md")
    assert "POLICY_TIMESTAMP_INVALID" in _codes(bundle, "naive_ts.md")
    assert "POLICY_SOURCE_RESOURCE_MISSING" in _codes(bundle, "bad_source.md")
    assert "POLICY_STATUS_INVALID" in _codes(bundle, "bad_status.md")


def test_an_unverified_research_finding_can_never_be_stable(tmp_path):
    finding = _concept().replace("Safety Procedure", "Research Finding")
    root = _bundle(tmp_path, {
        "stable_unverified.md": finding,
        "stable_machine.md": finding.replace("---\nBody", "verified:\n  by: process:ci\n  at: 2026-09-24T00:00:00Z\n---\nBody"),
        "draft.md": finding.replace("---\nBody", "status: draft\n---\nBody"),
        "reviewed.md": finding.replace("---\nBody", "verified:\n  by: human:ops\n  at: 2026-09-24T00:00:00Z\n---\nBody"),
    })
    bundle = validate_bundle(root, now=NOW)
    assert "POLICY_UNVERIFIED_EVIDENCE_STABLE" in _codes(bundle, "stable_unverified.md")
    assert "POLICY_UNVERIFIED_EVIDENCE_STABLE" in _codes(bundle, "stable_machine.md")
    assert not _codes(bundle, "draft.md") and not _codes(bundle, "reviewed.md")


def test_retired_strategies_can_only_be_documented_as_retired(tmp_path):
    root = _bundle(tmp_path, {
        "resurrect.md": _concept("applies_to:\n  strategies: [failed_breakout_fade]"),
        "definition.md": _concept("strategy_key: support_resistance_reaction\nstatus: deprecated")
        .replace("Safety Procedure", "Strategy Definition"),
        "ok.md": _concept("strategy_key: failed_breakout_fade\nretired: true\nstatus: deprecated")
        .replace("Safety Procedure", "Strategy Definition"),
    })
    bundle = validate_bundle(root, now=NOW)
    assert "POLICY_RETIRED_STRATEGY" in _codes(bundle, "resurrect.md")
    assert "POLICY_RETIRED_STRATEGY" in _codes(bundle, "definition.md")
    assert not _codes(bundle, "ok.md")


def test_only_the_three_canonical_symbols_may_be_in_scope(tmp_path):
    root = _bundle(tmp_path, {"x.md": _concept("applies_to:\n  symbols: [XAUUSD, EURUSD]")})
    assert "POLICY_SYMBOL_NOT_ALLOWED" in _codes(validate_bundle(root, now=NOW), "x.md")


def test_knowledge_can_never_carry_runtime_control_keys(tmp_path):
    root = _bundle(tmp_path, {
        "risk.md": _concept("risk_per_trade_pct: 5.0"),
        "ks.md": _concept("kill_switch: DISENGAGED"),
        "promote.md": _concept("promote: true"),
    })
    bundle = validate_bundle(root, now=NOW)
    for path in ("risk.md", "ks.md", "promote.md"):
        assert "POLICY_CONTROL_KEY" in _codes(bundle, path)


def test_supersession_must_resolve_and_agree(tmp_path):
    root = _bundle(tmp_path, {
        "old.md": _concept("status: deprecated\nsuperseded_by: /new.md"),
        "new.md": _concept("supersedes: [/old.md]"),
        "dangling.md": _concept("supersedes: [/ghost.md]"),
        "live_old.md": _concept("superseded_by: /new.md"),
    })
    bundle = validate_bundle(root, now=NOW)
    assert not _codes(bundle, "old.md") and not _codes(bundle, "new.md")
    assert "POLICY_SUPERSEDES_UNRESOLVED" in _codes(bundle, "dangling.md")
    assert "POLICY_SUPERSEDES_INCONSISTENT" in _codes(bundle, "live_old.md")


def test_stale_concepts_are_flagged(tmp_path):
    root = _bundle(tmp_path, {"x.md": _concept("stale_after: 2026-01-01T00:00:00Z")})
    bundle = validate_bundle(root, now=NOW)
    assert "POLICY_STALE" in {i.code for i in bundle.issues if i.severity == "WARNING"}


def test_duplicate_ids_are_rejected(tmp_path):
    root = _bundle(tmp_path, {"a.md": _concept("id: same"), "b.md": _concept("id: same")})
    assert "POLICY_ID_DUPLICATE" in _codes(validate_bundle(root, now=NOW))


# ---------------------------------------------------------------------------
# secret and raw-record hygiene
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("leak", [
    "MT5 password: hunter2secret",
    "api_key = abcd1234efgh",
    "login: 51234567",
    "-----BEGIN RSA PRIVATE KEY-----",
    "token AKIAABCDEFGHIJKLMNOP",
])
def test_credentials_are_detected(leak):
    assert any(i.code.startswith("SECRET_") for i in scan_text("x.md", f"text\n{leak}\n"))


@pytest.mark.parametrize("record", [
    '{"broker_position_id": 123456, "profit": 3.2}',
    "client_request_id = 998877",
    "payload_json: {...}",
    "```csv\ntime,open,high\n```",
])
def test_raw_runtime_records_are_detected(record):
    assert scan_text("x.md", record)


def test_ordinary_safety_prose_is_not_a_false_positive():
    prose = ("Credentials never go into the repository. The operator enters the password in the MT5 "
             "terminal. Reconcile by broker_position_id in the History tab.")
    assert scan_text("x.md", prose) == []


def test_a_leaking_concept_is_quarantined_from_search_and_advice(tmp_path):
    root = _bundle(tmp_path, {
        "clean.md": _concept("applies_to:\n  strategies: [range_breakout]", body="breakout guidance"),
        "leak.md": _concept("applies_to:\n  strategies: [range_breakout]", body="breakout login: 51234567"),
    })
    bundle = validate_bundle(root, now=NOW)
    assert [h.concept.path for h in search(bundle, "breakout")] == ["clean.md"]
    advice = KnowledgeAdvisor(bundle, clock=lambda: NOW.timestamp()).advise(
        canonical_symbol="XAUUSD", strategy_key="range_breakout")
    assert [c["path"] for c in advice["approved"]] == ["clean.md"]
    assert advice["quarantined_files"] == 1


# ---------------------------------------------------------------------------
# search and the advisor
# ---------------------------------------------------------------------------

def test_direct_lookup_and_filters():
    bundle = validate_bundle(DEFAULT_BUNDLE, now=NOW)
    assert lookup(bundle, "safety/kill-switch").path == "safety/kill-switch.md"
    assert lookup(bundle, "/safety/kill-switch.md").id == "safety/kill-switch"
    runbooks = filter_concepts(bundle, concept_type="Runbook")
    assert runbooks and all(c.type == "Runbook" for c in runbooks)
    assert search(bundle, "kill switch bootstrap operator")[0].concept.id == "safety/kill-switch"
    assert not any(c.frontmatter.get("retired") for c in filter_concepts(bundle))  # deprecated hidden by default


def test_the_advisor_is_evidence_only_and_separates_unverified_findings():
    advisor = KnowledgeAdvisor.from_path(clock=lambda: NOW.timestamp())
    advice = advisor.advise(canonical_symbol="XAUUSD", strategy_key="range_breakout")
    assert advice["influence"] == "NONE"
    assert advice["okf_version"] == "0.2" and len(advice["bundle_checksum"]) == 16
    assert "strategy/range_breakout" in [c["id"] for c in advice["approved"]]
    assert "research/no-validated-edge-yet" in [c["id"] for c in advice["unverified"]]
    assert all(c["trust_tier"] != TRUST_HUMAN_REVIEWED for c in advice["unverified"])
    for retired in RETIRED_STRATEGY_KEYS:
        advice = advisor.advise(canonical_symbol="XAUUSD", strategy_key=retired)
        assert advice["approved"] == [] and advice["unverified"] == []


def test_the_advisor_payload_contains_no_authority_fields():
    advice = KnowledgeAdvisor.from_path(clock=lambda: NOW.timestamp()).advise(
        canonical_symbol="GBPJPY", strategy_key="momentum_continuation")
    flat = repr(advice).lower()
    for forbidden in ("allow", "volume", "risk_per_trade", "kill_switch", "order"):
        assert forbidden not in flat


def test_a_missing_bundle_degrades_the_engine_knowledge_health_only(tmp_path, monkeypatch):
    import adaptive_scalper.knowledge.advisor as advisor_module
    from runtime_helpers import STEP, T0, FakeClock, build_engine, step

    monkeypatch.setattr(advisor_module, "DEFAULT_BUNDLE", tmp_path / "does-not-exist")
    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, _ = build_engine(tmp_path / "db", mode="PAPER", clock=clock)
    engine.startup()
    step(engine, clock, seconds=STEP * 2, tick=4)
    assert engine.component_health["okf"]["status"] == "DEGRADED"
    assert engine.okf is None


def test_the_engine_loads_the_tracked_bundle_by_default(tmp_path):
    from runtime_helpers import STEP, T0, FakeClock, build_engine

    engine, _, _ = build_engine(tmp_path, mode="PAPER", clock=FakeClock(T0 + 60 * STEP + 10))
    engine.startup()
    assert isinstance(engine.okf, KnowledgeAdvisor)
    assert engine.component_health["okf"]["status"] == "OK"


# ---------------------------------------------------------------------------
# isolation audit
# ---------------------------------------------------------------------------

def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    return names


def test_the_knowledge_package_imports_nothing_that_can_trade_or_change_safety_state():
    forbidden = ("adaptive_scalper.gateway", "adaptive_scalper.execution", "adaptive_scalper.core",
                 "adaptive_scalper.risk", "adaptive_scalper.portfolio", "adaptive_scalper.selector",
                 "adaptive_scalper.position_management", "adaptive_scalper.learning", "adaptive_scalper.runtime",
                 "adaptive_scalper.persistence", "MetaTrader5")
    for path in (REPO / "adaptive_scalper" / "knowledge").glob("*.py"):
        for name in _imports(path):
            assert not name.startswith(forbidden), (path.name, name)


def test_no_decision_or_execution_module_imports_the_knowledge_package():
    consumers = set()
    for path in (REPO / "adaptive_scalper").rglob("*.py"):
        rel = path.relative_to(REPO / "adaptive_scalper").as_posix()
        if rel.startswith("knowledge/"):
            continue
        if any(n.startswith("adaptive_scalper.knowledge") for n in _imports(path)):
            consumers.add(rel)
    # Only the engine (to hand the advisor to the evidence-only panel) and the operator CLI.
    assert consumers <= {"runtime/engine.py", "cli/knowledge.py", "cli/runtime.py", "dashboard/panels.py"}, consumers


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------

def test_the_retrieval_benchmark_reports_every_retriever():
    results = run_benchmark(DEFAULT_BUNDLE, rag_documents=300)
    assert {(r.corpus, r.retriever) for r in results} == {
        ("okf", "okf_direct"), ("okf", "tfidf"), ("okf", "fts5_bm25"), ("rag", "tfidf"), ("rag", "fts5_bm25"),
    }
    for r in results:
        assert 0.0 <= r.precision_at_k <= 1.0 and 0.0 <= r.mrr <= 1.0 and r.latency_p95_us >= r.latency_p50_us
    okf_direct = next(r for r in results if r.retriever == "okf_direct")
    assert okf_direct.mrr >= 0.8  # the labelled operator questions are answered by the curated bundle


def test_demo_journals_okf_evidence_with_no_authority(tmp_path):
    import json

    from adaptive_scalper.core.kill_switch import bootstrap as bootstrap_kill_switch
    from adaptive_scalper.core.operator_authority import OperatorAuthority
    from runtime_helpers import STEP, T0, FakeClock, build_engine, step

    clock = FakeClock(T0 + 60 * STEP + 10)
    engine, conn, _ = build_engine(tmp_path, mode="DEMO", clock=clock)
    bootstrap_kill_switch(conn, OperatorAuthority("test-operator"), reason="test setup")
    engine.startup()
    step(engine, clock, seconds=STEP * 4, tick=4)
    payloads = [json.loads(r[0]) for r in conn.execute(
        "SELECT payload_json FROM journal_events WHERE event_type = 'RAG_USED'")]
    okf = [p for p in payloads if p["source"] == "OKF"]
    assert okf and all(p["status"] == "OK" for p in okf)
    assert all(p["evidence"]["influence"] == "NONE" and p["authority"].startswith("NONE") for p in okf)
