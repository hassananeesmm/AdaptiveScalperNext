"""Agent 6 - Learning Analyst: learns from recorded behaviour, changes nothing.

From as-of-bounded evidence only (no future outcomes) it studies:

- blocked opportunities: what the shadow-observed candidates that the engine
  blocked would have earned (theoretical net R), per block reason - did the
  gate avoid losses, or is there a possible false rejection to research?
- losing outcomes by session, regime and cost share;
- deterioration: the latest realized trades versus the ones before (difference
  of expectancy with a bootstrap CI);
- drift: population stability of candidate features (ATR, spread percentile,
  movement-to-cost) recent versus earlier;
- the desk's own forecasts versus what happened (scored by the evaluator);
- recurring execution-cost problems.

Every recommendation is evidence-backed and marked as requiring separate
validation. It writes nothing to the trading system and keeps no competing ML
registry (the project's learning observer stays the model of record).
"""

from __future__ import annotations

from collections import defaultdict

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft
from adaptive_scalper.ai_desk.agents.strategy_analyst import shadow_net_r
from adaptive_scalper.ai_desk.contracts import Claim, Snapshot
from adaptive_scalper.ai_desk.metrics import (
    MIN_SAMPLE_DESCRIPTIVE,
    bootstrap_diff_ci,
    ci_excludes_zero_negative,
    ci_excludes_zero_positive,
    psi,
    summarize,
)

RECENT_TRADES = 100
STUDY_HORIZON = "1800"


def session_of(hour: int) -> str:
    if hour < 7:
        return "ASIA"
    if hour < 12:
        return "LONDON"
    if hour < 16:
        return "LONDON_NY_OVERLAP"
    if hour < 21:
        return "NEW_YORK"
    return "LATE"


class LearningAnalyst(Agent):
    key = "learning_analyst"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        trades_item = snapshot.one("DEMO_TRADES")
        shadow_item = snapshot.one("SHADOW_CANDIDATES")
        decisions_item = snapshot.one("ENTRY_DECISIONS")
        costs_item = snapshot.one("EXECUTION_COSTS")
        evidence = [e.evidence_id for e in (trades_item, shadow_item, decisions_item, costs_item) if e]
        trades = trades_item.payload["trades"] if trades_item else []
        candidates = shadow_item.payload["candidates"] if shadow_item else []
        recommendations: list[dict] = []
        claims: list[Claim] = []
        findings: dict = {}

        # Blocked opportunities.
        blocked: dict[str, list[float]] = defaultdict(list)
        allowed: list[float] = []
        for c in candidates:
            r = shadow_net_r(c, STUDY_HORIZON)
            if r is None:
                continue
            # shadow observer labels: SELECTED reached the final gate (its decision lives in
            # entry_decisions); anything else was blocked earlier (rejection_reason) or not chosen.
            if c.get("selector_disposition") == "SELECTED":
                allowed.append(r)
            else:
                reason = c.get("rejection_reason") or c.get("selector_disposition") or "UNKNOWN"
                blocked[str(reason)].append(r)
        blocked_view = {}
        for reason, rs in sorted(blocked.items()):
            s = summarize(rs)
            blocked_view[reason] = s
            if s.get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE and ci_excludes_zero_positive(s):
                recommendations.append({
                    "id": f"rec:false_rejection:{reason}",
                    "text": f"Candidates blocked as {reason} would have earned {s['expectancy']:+.3f} R (theoretical, "
                            f"n={s['n']}, CI {s['expectancy_ci95']}): research whether this block rejects good "
                            "opportunities. Do NOT relax the gate on this evidence alone.",
                    "requires": "separate validation through the research pipeline and human review"})
        findings["blocked_opportunities"] = {"horizon_seconds": int(STUDY_HORIZON), "by_block": blocked_view,
                                             "selected": summarize(allowed),
                                             "note": "theoretical shadow outcomes with estimated costs"}

        # Losing outcomes by session / regime.
        by_session = defaultdict(list)
        by_regime = defaultdict(list)
        for t in trades:
            by_session[session_of(t["hour_utc"])].append(t["r"])
            by_regime[t["entry_regime"] or "UNKNOWN"].append(t["r"])
        findings["by_session"] = {k: summarize(v) for k, v in sorted(by_session.items())}
        findings["by_regime"] = {k: summarize(v) for k, v in sorted(by_regime.items())}
        for label, table in (("session", findings["by_session"]), ("regime", findings["by_regime"])):
            for k, s in table.items():
                if s.get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE and ci_excludes_zero_negative(s):
                    claims.append(Claim(claim_id=f"edge:negative:{label}:{k}", kind="PROFITABILITY",
                                        sample_size=s["n"],
                                        statement=f"Realized trades in {label} {k} lose: {s['expectancy']:+.3f} R "
                                                  f"(n={s['n']}, CI {s['expectancy_ci95']})",
                                        evidence_ids=[trades_item.evidence_id], conclusion_key=f"loss:{label}:{k}"))
        losers = [t for t in trades if (t["r"] or 0) < 0]
        if losers:
            cost_heavy = sum(1 for t in losers if abs(t["commission_swap_fee"] or 0) >= 0.5 * abs(t["net_profit"] or 1))
            findings["losing_trades"] = {"n": len(losers), "cost_dominated_share": round(cost_heavy / len(losers), 4)}

        # Deterioration: latest trades vs the ones before.
        rs = [t["r"] for t in trades if t["r"] is not None]
        if len(rs) >= 2 * MIN_SAMPLE_DESCRIPTIVE:
            recent = rs[-min(RECENT_TRADES, len(rs) // 2):]
            prior = rs[:len(rs) - len(recent)]
            ci = bootstrap_diff_ci(recent, prior)
            findings["deterioration"] = {"recent": summarize(recent), "prior": summarize(prior),
                                         "diff_ci95": None if ci is None else [round(ci[0], 5), round(ci[1], 5)]}
            if ci and ci[1] < 0:
                findings["deterioration"]["alert"] = "recent expectancy is significantly below the earlier period"
                recommendations.append({"id": "rec:deterioration",
                                        "text": "Performance deteriorated (recent vs earlier expectancy CI below "
                                                "zero): review regime mix and costs before any further research.",
                                        "requires": "human review"})

        # Feature drift among candidates.
        drift = {}
        if len(candidates) >= 40:
            half = len(candidates) // 2
            early, late = candidates[:half], candidates[half:]
            for feat in ("atr", "spread_percentile", "movement_to_cost"):
                value = psi([c.get(feat) for c in early], [c.get(feat) for c in late])
                if value is not None:
                    drift[feat] = {"psi": value, "level": "MAJOR" if value >= 0.25 else "MODERATE" if value >= 0.1
                                   else "STABLE"}
        findings["feature_drift"] = drift
        for feat, d in drift.items():
            if d["level"] == "MAJOR":
                recommendations.append({"id": f"rec:drift:{feat}",
                                        "text": f"Candidate feature {feat} drifted (PSI {d['psi']}): statistics "
                                                "estimated on older data may not apply now.",
                                        "requires": "re-estimation in the research pipeline"})

        # The desk's own forecasts versus outcomes (scored by the evaluator, passed in).
        track = context.options.get("forecast_track_record") or {}
        findings["desk_forecast_track_record"] = track

        # Execution-cost problems.
        costs = costs_item.payload if costs_item else {}
        cost_issues = {sym: c for sym, c in costs.items()
                       if c.get("abs_slippage_p95") and c.get("spread_mean") and c["abs_slippage_p95"] > c["spread_mean"]}
        findings["execution_cost_issues"] = cost_issues
        decisions = decisions_item.payload if decisions_item else {}
        findings["block_reasons_7d"] = decisions.get("last_7d", [])[:10]

        for rec in recommendations:
            claims.append(Claim(claim_id=rec["id"].replace("rec:", "hyp:learning:"), kind="HYPOTHESIS",
                                statement=rec["text"][:600], evidence_ids=evidence[:2]))
        findings["recommendations"] = recommendations
        limitations = ["only outcomes resolved at or before as-of are used (no future leakage)",
                       "recommendations require separate validation; nothing here changes trading rules"]
        if len(trades) < 2 * MIN_SAMPLE_DESCRIPTIVE:
            limitations.append("too few realized trades for a deterioration test")
        status = "COMPLETE" if (trades or candidates) else "INCOMPLETE"
        assessment = (f"Learning review over {len(trades)} realized trade(s) and {len(candidates)} shadow candidate(s): "
                      f"{len(blocked_view)} block reason(s) studied, {len(drift)} feature(s) checked for drift, "
                      f"{len(recommendations)} research recommendation(s).")
        return Draft(status=status, assessment=assessment, evidence_ids=evidence, limitations=limitations,
                     claims=claims[:40], findings=findings,
                     counterarguments=["Shadow outcomes are theoretical; realized trades are few per group."])
