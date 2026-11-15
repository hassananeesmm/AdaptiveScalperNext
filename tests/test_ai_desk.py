"""AI Trading Desk: functional tests over a synthetic trading database.

Covers: every agent on meaningful data, offline deterministic mode, missing and
stale data, idempotent journaling, restart recovery, leakage-free snapshots,
preserved disagreements, provider budgets / fallback / circuit breaker,
time-bounded agents, the event-driven service, evaluation and its OOS guard,
the CLI and the dashboard endpoint.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from ai_desk_fixtures import NOW, build_trading_db

from adaptive_scalper.ai_desk.agents.base import Agent, Draft
from adaptive_scalper.ai_desk.configuration import AiDeskConfig, ProviderConfig
from adaptive_scalper.ai_desk.contracts import (
    AgentResult,
    ContractViolation,
    validate_result,
)
from adaptive_scalper.ai_desk.journal import Journal, JournalError
from adaptive_scalper.ai_desk.orchestration import DeskOrchestrator
from adaptive_scalper.ai_desk.providers.base import (
    Provider,
    ProviderError,
    ProviderResponse,
    ProviderRouter,
)
from adaptive_scalper.ai_desk.security import NarrativeRejected, parse_model_response
from adaptive_scalper.ai_desk.snapshot_builder import SnapshotBuilder
from adaptive_scalper.config.loader import AppConfig


@pytest.fixture
def desk(tmp_path):
    db = build_trading_db(tmp_path / "trading.sqlite3")
    cfg = AiDeskConfig(journal_path=str(tmp_path / "ai_desk.sqlite3"), stale_after_seconds=1800)
    journal = Journal(cfg.journal_path, trading_db_path=db)
    orch = DeskOrchestrator(cfg, trading_db_path=db, journal=journal, trading_config=AppConfig())
    yield cfg, db, journal, orch
    journal.close()


def by_agent(report):
    out = {}
    for r in report.results:
        out.setdefault(r.agent, []).append(r)
    return out


# --------------------------------------------------------------------- agents
def test_every_agent_processes_meaningful_data(desk):
    _, _, _, orch = desk
    report = orch.run_cycle(trigger="test", as_of_utc=NOW)
    assert report.status == "COMPLETED", report.error
    res = by_agent(report)
    assert set(res) == {"market_analyst", "news_analyst", "risk_analyst", "strategy_analyst", "learning_analyst",
                        "skeptic", "coordinator"}
    assert not [r for rs in res.values() for r in rs if r.status == "FAILED"]

    xau = next(r for r in res["market_analyst"] if r.symbol == "XAUUSD")
    assert xau.status == "COMPLETE" and xau.findings["basis"] == "closed_bars"
    assert xau.findings["computed"]["adx14"] is not None and xau.findings["condition"]
    assert xau.findings["forward_check"]["horizon_bars"] == 12
    assert "not a price-direction forecast" in xau.assessment

    news = res["news_analyst"][0]
    assert "XAUUSD" in news.findings["imminent_high_impact"]          # FOMC at +30 min, USD -> XAUUSD/BTCUSD
    assert any(f["category"] == "CENTRAL_BANK" for f in news.findings["facts"])

    risk = res["risk_analyst"][0]
    assert risk.status == "COMPLETE" and risk.findings["kill_switch"]["status"] == "DISENGAGED"
    assert risk.findings["drawdown"]["peak_equity"] == 10500.0

    strat = res["strategy_analyst"][0]
    assert "momentum_continuation:XAUUSD" in strat.findings["underperformers"]
    assert strat.findings["overfitting_signals"], "negative purged-CV must be reported"
    assert all(h["status"] == "UNTESTED_HYPOTHESIS" for h in strat.findings["hypotheses"])

    learn = res["learning_analyst"][0]
    assert learn.findings["blocked_opportunities"]["by_block"]
    assert all("requires" in r for r in learn.findings["recommendations"])

    skeptic = res["skeptic"][0]
    assert sum(skeptic.findings["verdict_counts"].values()) >= 5
    assert any("not evidence of a trading edge" in o for o in skeptic.counterarguments)

    coord = res["coordinator"][0]
    lines = coord.findings["summary"]["lines"]
    assert lines[0].startswith("ADVISORY ONLY") and any("edge" in line.lower() for line in lines)


def test_profitability_claims_need_samples_and_a_ci_that_excludes_zero(desk):
    _, _, _, orch = desk
    res = by_agent(orch.run_cycle(trigger="test", as_of_utc=NOW))
    verdicts = {v.claim_id: v for v in res["skeptic"][0].verdicts}
    neg = verdicts["edge:negative:momentum_continuation:XAUUSD"]
    assert neg.verdict == "WEAK_EVIDENCE" and any("small sample" in o for o in neg.objections)  # n=80 < 100
    for v in verdicts.values():
        if v.claim_id.startswith("hyp:"):
            assert v.verdict == "INSUFFICIENT_DATA"


def test_retired_strategies_are_history_only(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3")
    conn = sqlite3.connect(db)
    conn.execute("UPDATE positions SET strategy_key='failed_breakout_fade' WHERE canonical_symbol='BTCUSD'")
    conn.commit()
    conn.close()
    cfg = AiDeskConfig(journal_path=str(tmp_path / "j.sqlite3"))
    j = Journal(cfg.journal_path)
    r = DeskOrchestrator(cfg, trading_db_path=db, journal=j).run_cycle(as_of_utc=NOW, agents={"strategy_analyst"})
    strat = by_agent(r)["strategy_analyst"][0]
    assert strat.findings["strategies"]["failed_breakout_fade:BTCUSD"]["status"] == "RETIRED"
    assert not [c for c in strat.claims if "failed_breakout_fade" in c.claim_id]
    assert not [h for h in strat.findings["hypotheses"] if "failed_breakout_fade" in h["id"]]
    j.close()


# --------------------------------------------------------- missing and stale
def test_missing_market_data_gives_a_safe_incomplete_result(desk):
    _, _, _, orch = desk
    res = by_agent(orch.run_cycle(as_of_utc=NOW))
    gbp = next(r for r in res["market_analyst"] if r.symbol == "GBPJPY")    # no bars, no runtime data
    assert gbp.status == "STALE" and gbp.confidence is None
    assert any("LAST-KNOWN" in lim or "no fresh market data" in lim for lim in gbp.limitations)


def test_stale_runtime_is_marked_and_the_assessment_is_incomplete(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3", runtime_live=False, bars_age_seconds=10 * 86400)
    cfg = AiDeskConfig(journal_path=str(tmp_path / "j.sqlite3"))
    j = Journal(cfg.journal_path)
    res = by_agent(DeskOrchestrator(cfg, trading_db_path=db, journal=j).run_cycle(as_of_utc=NOW))
    assert all(r.status == "STALE" for r in res["market_analyst"])
    assert res["risk_analyst"][0].status == "INCOMPLETE"
    summary = res["coordinator"][0].findings["summary"]
    assert summary["complete"] is False and summary["overall"].startswith("INCOMPLETE ASSESSMENT")
    j.close()


# ------------------------------------------------------- journal guarantees
def test_retries_do_not_duplicate_research_records(desk):
    _, _, journal, orch = desk
    first = orch.run_cycle(as_of_utc=NOW)
    count = journal.conn.execute("SELECT COUNT(*) FROM agent_results").fetchone()[0]
    second = orch.run_cycle(as_of_utc=NOW)
    assert first.snapshot_id == second.snapshot_id
    assert second.inserted == 0 and second.duplicates == first.inserted
    assert journal.conn.execute("SELECT COUNT(*) FROM agent_results").fetchone()[0] == count
    assert journal.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_restart_recovery_preserves_completed_evidence(tmp_path, desk):
    cfg, db, journal, orch = desk
    report = orch.run_cycle(as_of_utc=NOW)
    dangling = journal.start_run(trigger="crash", mode="CURRENT", config_fingerprint="x", agents=[])
    journal.close()
    reopened = Journal(cfg.journal_path, trading_db_path=db)          # "restart"
    assert reopened.recover_incomplete_runs() == 1
    status = reopened.conn.execute("SELECT status FROM desk_runs WHERE run_id=?", (dangling,)).fetchone()[0]
    assert status == "ABANDONED"
    kept = reopened.conn.execute("SELECT COUNT(*) FROM agent_results WHERE run_id=?", (report.run_id,)).fetchone()[0]
    assert kept == report.inserted > 0
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        reopened.conn.execute("UPDATE agent_results SET status='COMPLETE'")
    reopened.close()


def test_the_journal_refuses_to_be_the_trading_database(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3")
    with pytest.raises(JournalError):
        Journal(db, trading_db_path=db)


# ------------------------------------------------------------------ leakage
def test_snapshots_contain_no_future_information(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3")
    snap = SnapshotBuilder(db, symbols=["XAUUSD", "BTCUSD"]).build(as_of_utc=NOW, mode="CURRENT")
    assert snap.future_items() == []
    trades = snap.one("DEMO_TRADES").payload["trades"]
    assert all(t["closed_utc"] <= NOW for t in trades)                       # the FUTURE trade is excluded
    cands = snap.one("SHADOW_CANDIDATES").payload["candidates"]
    assert all(o["resolved_utc"] <= NOW for c in cands for o in c["outcomes"].values())
    assert any(not c["outcomes"] for c in cands)                             # late outcomes are hidden
    bars = snap.one("MARKET_BARS", "XAUUSD").payload["bars"]
    assert bars[-1][0] + 300 <= NOW                                          # only CLOSED bars
    replay = SnapshotBuilder(db, symbols=["XAUUSD"]).build(as_of_utc=NOW - 20 * 86400, mode="REPLAY")
    assert replay.future_items() == [] and replay.one("KILL_SWITCH") is None
    assert all(t["closed_utc"] <= NOW - 20 * 86400 for t in replay.one("DEMO_TRADES").payload["trades"])


def test_snapshot_ids_are_content_hashes_and_detect_mutation(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3")
    b = SnapshotBuilder(db, symbols=["XAUUSD"])
    s1, s2 = b.build(as_of_utc=NOW), b.build(as_of_utc=NOW)
    assert s1.snapshot_id == s2.snapshot_id and s1.verify()
    object.__setattr__(s1, "limitations", ("tampered",))
    assert not s1.verify()


def test_account_identity_never_enters_a_snapshot(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3")
    snap = SnapshotBuilder(db, symbols=["XAUUSD"]).build(as_of_utc=NOW)
    blob = json.dumps([e.payload_json for e in snap.evidence])
    assert "123456" not in blob and "Synthetic-Demo" not in blob and "Synthetic Ltd" not in blob


# ------------------------------------------------------------ disagreements
def test_disagreement_is_preserved_not_averaged(tmp_path):
    db = build_trading_db(tmp_path / "t.sqlite3", runtime_risk_per_trade=0.2)   # runtime differs from config
    cfg = AiDeskConfig(journal_path=str(tmp_path / "j.sqlite3"))
    j = Journal(cfg.journal_path)
    DeskOrchestrator(cfg, trading_db_path=db, journal=j, trading_config=AppConfig()).run_cycle(as_of_utc=NOW)
    rows = j.conn.execute("SELECT positions_json, resolution FROM disagreements WHERE topic=?",
                          ("risk_limit:risk_per_trade_pct",)).fetchall()
    assert rows and rows[0][1] == "AUDIT_FINDING"
    positions = json.loads(rows[0][0])
    assert positions == {"config_file": "0.25", "runtime_published": "0.2"}       # both sides kept verbatim
    j.close()


# ---------------------------------------------------------------- providers
class FakeProvider(Provider):
    kind = "openai_compatible"

    def __init__(self, cfg, *, text=None, error=None):
        super().__init__(cfg)
        self.text, self.error, self.calls = text, error, 0

    def complete(self, messages, *, max_tokens, timeout):
        self.calls += 1
        if self.error:
            raise self.error
        return ProviderResponse(self.text, 100, 50, "fake-1")


def _external_cfg(tmp_path, **budget):
    return AiDeskConfig(
        journal_path=str(tmp_path / "j.sqlite3"), provider="a", fallback_providers=["b", "deterministic"],
        llm_agents=["coordinator"], allowed_external_data=["risk_summary"],
        budget={"daily_token_budget": budget.get("tokens", 100_000), "daily_cost_budget_usd": budget.get("usd", 1.0)},
        providers=[ProviderConfig(name="a", kind="openai_compatible", model="m-a", min_seconds_between_requests=0,
                                  backoff_seconds=0, max_retries=1, circuit_breaker_failures=2),
                   ProviderConfig(name="b", kind="openai_compatible", model="m-b", min_seconds_between_requests=0,
                                  backoff_seconds=0, max_retries=0),
                   ProviderConfig()])


def test_deterministic_mode_works_offline(desk, monkeypatch):
    import socket

    def no_network(*a, **k):
        raise AssertionError("the deterministic desk must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    _, _, _, orch = desk
    report = orch.run_cycle(as_of_utc=NOW)
    assert report.status == "COMPLETED"
    assert {r.llm_narrative_status for r in report.results} <= {"DETERMINISTIC_ONLY", "NOT_PERMITTED"}
    assert all(r.llm_narrative is None for r in report.results)


def test_unavailable_provider_falls_back_and_the_circuit_opens(tmp_path):
    cfg = _external_cfg(tmp_path)
    j = Journal(cfg.journal_path)
    ok_text = json.dumps({"narrative": "The desk found mostly uncertain evidence.", "cited_evidence_ids": [],
                          "counterarguments": []})
    a = FakeProvider(cfg.provider_config("a"), error=ProviderError("HTTP 503"))
    b = FakeProvider(cfg.provider_config("b"), text=ok_text)
    router = ProviderRouter(cfg, j, providers={"a": a, "b": b, "deterministic": Provider(cfg.provider_config(
        "deterministic"))}, sleep=lambda s: None)
    out = router.narrate(agent="coordinator", category="risk_summary", digest={"x": 1},
                         allowed_evidence_ids=frozenset())
    assert out.status == "OK" and out.provider == "b" and a.calls == 2         # retried once, then failed over
    assert router.status()["a"]["circuit"] == "OPEN"
    out2 = router.narrate(agent="coordinator", category="risk_summary", digest={"x": 2},
                          allowed_evidence_ids=frozenset())
    assert out2.provider == "b" and a.calls == 2                              # open circuit: a not called again
    b.error = ProviderError("down")
    out3 = router.narrate(agent="coordinator", category="risk_summary", digest={"x": 3},
                          allowed_evidence_ids=frozenset())
    assert out3.narrative is None and out3.attempts[-1]["status"] == "DETERMINISTIC_FALLBACK"
    j.close()


def test_provider_budget_limits_block_calls(tmp_path):
    cfg = _external_cfg(tmp_path, tokens=50)
    j = Journal(cfg.journal_path)
    a = FakeProvider(cfg.provider_config("a"), text="{}")
    b = FakeProvider(cfg.provider_config("b"), text="{}")
    router = ProviderRouter(cfg, j, providers={"a": a, "b": b}, sleep=lambda s: None)
    out = router.narrate(agent="coordinator", category="risk_summary", digest={"big": "x" * 1000},
                         allowed_evidence_ids=frozenset())
    assert out.narrative is None and a.calls == 0 and b.calls == 0
    assert {x["status"] for x in out.attempts} >= {"BUDGET_EXHAUSTED"}
    paid = _external_cfg(tmp_path / "p", usd=0.0)
    paid.providers[0] = paid.providers[0].model_copy(update={"input_cost_per_1k_tokens_usd": 1.0})
    (tmp_path / "p").mkdir()
    j2 = Journal(paid.journal_path)
    a2 = FakeProvider(paid.provider_config("a"), text="{}")
    out2 = ProviderRouter(paid, j2, providers={"a": a2, "b": b}, sleep=lambda s: None).narrate(
        agent="coordinator", category="risk_summary", digest={}, allowed_evidence_ids=frozenset())
    assert a2.calls == 0 and out2.attempts[0]["status"] == "BUDGET_EXHAUSTED"
    j.close()
    j2.close()


def test_external_calls_need_every_explicit_opt_in(tmp_path):
    assert AiDeskConfig().external_calls_permitted() is False
    cfg = _external_cfg(tmp_path)
    assert cfg.external_calls_permitted() is True
    assert cfg.model_copy(update={"allowed_external_data": []}).external_calls_permitted() is False
    router = ProviderRouter(cfg.model_copy(update={"allowed_external_data": ["market_features"]}), None,
                            providers={}, sleep=lambda s: None)
    assert router.narrate(agent="coordinator", category="risk_summary", digest={},
                          allowed_evidence_ids=frozenset()).status == "CATEGORY_NOT_PERMITTED"


@pytest.mark.parametrize("text", [
    "not json at all",
    '{"narrative": "ok", "cited_evidence_ids": ["ev-unknown"], "counterarguments": []}',
    '{"narrative": "ok", "cited_evidence_ids": [], "counterarguments": [], "execute": "order_send"}',
    '{"narrative": "Buy XAUUSD now at market.", "cited_evidence_ids": [], "counterarguments": []}',
    '{"narrative": "Please disable the kill switch to allow trading.", "cited_evidence_ids": []}',
    '{"narrative": "Increase the risk to 1% per trade.", "cited_evidence_ids": []}',
    '{"narrative": 42}',
])
def test_invalid_model_responses_are_rejected(text):
    with pytest.raises(NarrativeRejected):
        parse_model_response(text, frozenset({"ev-known"}))


def test_a_valid_model_response_is_accepted_but_stays_untrusted(desk):
    cfg, db, journal, _ = desk
    ext = _external_cfg(Path(cfg.journal_path).parent)
    good = json.dumps({"narrative": "Evidence is incomplete; no edge is supported.", "cited_evidence_ids": [],
                       "counterarguments": ["small samples"]})
    router = ProviderRouter(ext, journal, providers={"a": FakeProvider(ext.provider_config("a"), text=good)},
                            sleep=lambda s: None)
    orch = DeskOrchestrator(ext.model_copy(update={"journal_path": cfg.journal_path}), trading_db_path=db,
                            journal=journal, router=router, trading_config=AppConfig())
    coord = by_agent(orch.run_cycle(as_of_utc=NOW))["coordinator"][0]
    assert coord.llm_narrative.startswith("[UNTRUSTED MODEL TEXT")
    assert coord.model_provider == "deterministic"            # structured findings stay deterministic
    calls = journal.conn.execute("SELECT status, input_tokens, output_tokens FROM provider_calls").fetchall()
    assert [tuple(c) for c in calls] == [("OK", 100, 50)]


# ------------------------------------------------------------- time bounds
def test_a_hung_agent_is_time_bounded(desk, monkeypatch):
    cfg, db, journal, _ = desk
    from adaptive_scalper.ai_desk import orchestration

    class Hung(Agent):
        key = "news_analyst"

        def analyze(self, snapshot, context, symbol=None):
            time.sleep(5)
            return Draft(status="COMPLETE", assessment="late")

    monkeypatch.setitem(orchestration.AGENT_CLASSES, "news_analyst", Hung)
    orch = DeskOrchestrator(cfg.model_copy(update={"agent_timeout_seconds": 0.3}), trading_db_path=db, journal=journal)
    started = time.monotonic()
    report = orch.run_cycle(as_of_utc=NOW, agents={"news_analyst", "risk_analyst"})
    assert time.monotonic() - started < 4.5
    news = by_agent(report)["news_analyst"][0]
    assert news.status == "FAILED" and "timed out" in news.limitations[0]
    assert report.status == "COMPLETED"


def test_a_crashing_agent_yields_failed_not_a_crash(desk, monkeypatch):
    cfg, db, journal, _ = desk
    from adaptive_scalper.ai_desk import orchestration

    class Boom(Agent):
        key = "risk_analyst"

        def analyze(self, snapshot, context, symbol=None):
            raise RuntimeError("boom")

    monkeypatch.setitem(orchestration.AGENT_CLASSES, "risk_analyst", Boom)
    report = DeskOrchestrator(cfg, trading_db_path=db, journal=journal).run_cycle(as_of_utc=NOW)
    res = by_agent(report)
    assert res["risk_analyst"][0].status == "FAILED"
    assert res["coordinator"][0].findings["summary"]["complete"] is False


def test_contract_rejects_unknown_evidence_and_execution_shaped_findings(desk):
    _, db, _, _ = desk
    snap = SnapshotBuilder(db, symbols=["XAUUSD"]).build(as_of_utc=NOW)
    base = {"analysis_id": "an-x", "agent": "market_analyst", "agent_version": "1", "symbol": "XAUUSD",
            "snapshot_id": snap.snapshot_id, "as_of_utc": "2027-01-15T08:00:00Z", "status": "COMPLETE",
            "assessment": "x"}
    with pytest.raises(ContractViolation):
        validate_result(AgentResult(**base, evidence_ids=["ev-not-in-snapshot"]), snap)
    with pytest.raises(ContractViolation):
        validate_result(AgentResult(**base, findings={"nested": {"order_request": {"volume": 1}}}), snap)
    with pytest.raises(ContractViolation):
        validate_result(AgentResult(**base, confidence=0.9), snap)          # confidence without a basis
    with pytest.raises(ValueError):
        AgentResult(**base, direction="BUY")                                # extra fields are forbidden


# ---------------------------------------------------------------- service
def test_the_service_is_event_driven_deduplicated_and_rate_limited(desk):
    from adaptive_scalper.ai_desk.scheduler import DeskDisabledError, DeskService

    cfg, db, journal, orch = desk
    clock = [NOW]
    svc = DeskService(cfg.model_copy(update={"enabled": True}), trading_db_path=db, journal=journal,
                      orchestrator=orch, clock=lambda: clock[0], sleep=lambda s: None)
    first = svc.tick()
    assert first["status"] == "OK" and first["cycle"]["status"] == "COMPLETED"
    assert any(k.startswith("bar:XAUUSD") for k in first["new_events"])
    clock[0] += 30
    second = svc.tick()
    assert second["new_events"] == [] and "cycle" not in second                # nothing new: no cycle
    assert journal.get_state("service")["heartbeat_utc"] == clock[0]
    disabled = DeskService(cfg, trading_db_path=db, journal=journal, orchestrator=orch, sleep=lambda s: None)
    with pytest.raises(DeskDisabledError):
        disabled.serve(max_ticks=1)


def test_min_intervals_keep_agents_from_running_every_tick(desk):
    from adaptive_scalper.ai_desk.event_bus import DeskEvent
    from adaptive_scalper.ai_desk.scheduler import DeskService

    cfg, db, journal, orch = desk
    svc = DeskService(cfg, trading_db_path=db, journal=journal, orchestrator=orch)
    journal.set_watermark("last_run:risk_analyst", str(NOW - 10))
    agents, _ = svc.due_agents([DeskEvent("RISK_STATE_CHANGE", "risk:x")], NOW)
    assert agents == set()                                                     # 10 s < 120 s minimum interval
    agents, _ = svc.due_agents([DeskEvent("RISK_STATE_CHANGE", "risk:y")], NOW + 200)
    assert agents == {"risk_analyst"}


# -------------------------------------------------------------- evaluation
def test_ablation_study_reports_honestly_with_thresholds(tmp_path):
    from adaptive_scalper.ai_desk.evaluator import ablation_study

    db = build_trading_db(tmp_path / "t.sqlite3")
    result = ablation_study(db)
    demo = result["datasets"]["demo"]
    assert demo["n"] == 140          # the open-at-as-of FUTURE trade has no deals and set(demo["configs"]) == set(ABLATIONS)
    for name, c in demo["configs"].items():
        assert c["verdict"] == "BASELINE" if name == "baseline" else c["verdict"].split(" (")[0] in (
            "INSUFFICIENT SAMPLE", "IMPROVEMENT NOT DEMONSTRATED",
            "IMPROVEMENT DEMONSTRATED IN SHADOW RESEARCH")
    assert "deployment permission" in result["labels"]["status"]


def test_skeptic_filter_is_walk_forward(tmp_path):
    from adaptive_scalper.ai_desk.evaluator import _evaluate

    # 40 losing trades that all CLOSE after the last decision: the skeptic must never see them.
    samples = [{"t": i, "closed_t": 10_000 + i, "symbol": "XAUUSD", "strategy": "s", "outcome": -1.0}
               for i in range(40)]
    out = _evaluate(samples, "x")
    assert out["configs"]["baseline+skeptic"]["rejected"]["n"] == 0
    early = [{**s, "closed_t": s["t"]} for s in samples]                   # outcomes known immediately
    assert _evaluate(early, "y")["configs"]["baseline+skeptic"]["rejected"]["n"] > 0


def test_historical_replay_obeys_the_reserved_oos(tmp_path):
    from adaptive_scalper.ai_desk.evaluator import replay_market_forecasts
    from adaptive_scalper.research.independent import (
        RESERVED_OOS_INTERVALS,
        ReservedOosOverlapError,
    )

    db = build_trading_db(tmp_path / "t.sqlite3")
    oos_start, _ = RESERVED_OOS_INTERVALS[0]
    with pytest.raises(ReservedOosOverlapError):
        replay_market_forecasts(db, symbol="XAUUSD", start_utc=oos_start - 86400, end_utc=oos_start + 86400)
    rep = replay_market_forecasts(db, symbol="XAUUSD", start_utc=NOW - 2 * 86400, end_utc=NOW)
    assert rep["reconstruction"].startswith("GENUINE_AS_OF") and rep["n"] > 0
    assert "majority_baseline" in rep


def test_forward_checks_are_scored_from_later_bars(desk):
    from adaptive_scalper.ai_desk.evaluator import (
        forecast_track_record,
        score_forward_checks,
    )

    _, db, journal, orch = desk
    orch.run_cycle(as_of_utc=NOW - 2 * 3600)          # forecasts whose horizon has fully elapsed
    assert journal.conn.execute("SELECT COUNT(*) FROM forward_checks").fetchone()[0] >= 1
    out = score_forward_checks(journal, db, now=NOW)
    assert out["scored"] >= 1
    rec = forecast_track_record(journal)["volatility_persistence"]
    assert rec["n"] >= 1 and rec["baseline"] == "majority outcome class"


def test_full_evaluation_counts_repeated_trials(desk):
    from adaptive_scalper.ai_desk.evaluator import run_full_evaluation

    _, db, journal, _ = desk
    first = run_full_evaluation(journal, db)
    second = run_full_evaluation(journal, db)
    assert first["trial_number"] == 1 and second["trial_number"] == 2
    assert second["repeat_on_same_data"] == 1 and "multiple_testing_note" in second


# ---------------------------------------------------------- CLI + dashboard
def _cli_env(tmp_path):
    db = build_trading_db(tmp_path / "trading.sqlite3")
    trading = tmp_path / "trading.toml"
    trading.write_text(f'mode = "PAPER"\n[database]\npath = "{db.replace(chr(92), "/")}"\n', encoding="utf-8")
    desk = tmp_path / "ai_desk.toml"
    desk.write_text(f'journal_path = "{(tmp_path / "ai.sqlite3").as_posix()}"\n', encoding="utf-8")
    return str(trading), str(desk)


def test_cli_doctor_run_once_report_and_serve_refusal(tmp_path, capsys, monkeypatch):
    from adaptive_scalper.cli import main

    trading, desk = _cli_env(tmp_path)
    monkeypatch.setenv("FAKE_AI_KEY", "sk-test-SECRETVALUE1234567890")
    assert main(["--config", trading, "ai-desk", "--ai-config", desk, "doctor"]) == 0
    assert "SECRETVALUE" not in capsys.readouterr().out
    assert main(["--config", trading, "ai-desk", "--ai-config", desk, "run-once"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETED"
    assert main(["--config", trading, "ai-desk", "--ai-config", desk, "report"]) == 0
    assert "ADVISORY ONLY" in capsys.readouterr().out
    assert main(["--config", trading, "ai-desk", "--ai-config", desk, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "DISABLED"
    assert main(["--config", trading, "ai-desk", "--ai-config", desk, "serve", "--max-ticks", "1"]) == 1
    assert "disabled" in capsys.readouterr().err


def test_dashboard_ai_desk_endpoint_is_read_only(tmp_path):
    from fastapi.testclient import TestClient

    from adaptive_scalper.dashboard.app import create_app

    db = build_trading_db(tmp_path / "trading.sqlite3")
    client = TestClient(create_app(db, ai_desk_journal_path=tmp_path / "missing.sqlite3"))
    empty = client.get("/api/ai-desk").json()["panels"]
    assert {p["status"] for p in empty.values()} == {"NO_DATA"}
    cfg = AiDeskConfig(journal_path=str(tmp_path / "ai.sqlite3"))
    j = Journal(cfg.journal_path)
    DeskOrchestrator(cfg, trading_db_path=db, journal=j).run_cycle(as_of_utc=NOW)
    before = "\n".join(j.conn.iterdump())
    client = TestClient(create_app(db, ai_desk_journal_path=cfg.journal_path))
    panels = client.get("/api/ai-desk").json()["panels"]
    assert set(panels) == {"ai_overview", "ai_market", "ai_research", "ai_learning", "ai_health"}
    assert panels["ai_research"]["engine_decision"]["label"].startswith("ACTUAL TRADING-ENGINE DECISION")
    assert panels["ai_research"]["ai_advice"]["label"].startswith("AI ADVICE")
    assert "XAUUSD" in panels["ai_market"]["symbols"]
    assert client.post("/api/ai-desk").status_code == 405
    assert "\n".join(j.conn.iterdump()) == before                 # the dashboard wrote nothing
    j.close()
