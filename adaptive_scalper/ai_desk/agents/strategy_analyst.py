"""Agent 2 - Strategy Analyst: evaluates existing strategies and hypotheses.

Evidence: realized DEMO trades (net of commission, swap and fees, in R of the
initial monetary risk), theoretical shadow outcomes of the same strategies,
the research-trial ledger, and the strategy configuration. It reports, per
strategy, win rate / expectancy / profit factor / drawdown with 95 % CIs and
sample sizes; the gap between theoretical and realized results; performance
by regime against today's conditions; repeated underperformers; signs of
overfitting (in-sample vs purged-CV divergence, number of trials); and
proposes research EXPERIMENTS.

Retired strategies are reported only as history and never proposed; suspended
strategies are marked suspended. Nothing here activates, modifies, retrains
or deploys a strategy: every idea is an UNTESTED HYPOTHESIS for the human-run
research pipeline.
"""

from __future__ import annotations

from collections import defaultdict

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft
from adaptive_scalper.ai_desk.contracts import Claim, Snapshot
from adaptive_scalper.ai_desk.metrics import (
    MIN_SAMPLE_DESCRIPTIVE,
    ci_excludes_zero_negative,
    ci_excludes_zero_positive,
    summarize,
)
from adaptive_scalper.config.constants import RETIRED_STRATEGY_KEYS

SHADOW_HORIZONS = ("300", "900", "1800")   # resolved horizons the shadow observer records (seconds)


def shadow_net_r(candidate: dict, horizon: str) -> float | None:
    o = (candidate.get("outcomes") or {}).get(horizon)
    return None if o is None else o.get("net_r")


class StrategyAnalyst(Agent):
    key = "strategy_analyst"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        trades_item = snapshot.one("DEMO_TRADES")
        shadow_item = snapshot.one("SHADOW_CANDIDATES")
        research_item = snapshot.one("RESEARCH_TRIALS")
        cfg_item = snapshot.one("STRATEGY_CONFIG")
        evidence = [e.evidence_id for e in (trades_item, shadow_item, research_item, cfg_item) if e]
        trades = trades_item.payload["trades"] if trades_item else []
        candidates = shadow_item.payload["candidates"] if shadow_item else []
        suspended = set((cfg_item.payload.get("entry_suspended") if cfg_item else []) or [])
        costs_cfg = (cfg_item.payload.get("costs") if cfg_item else {}) or {}

        current_regime = {}
        for r in context.of("market_analyst"):
            if r.symbol and r.findings.get("regime"):
                current_regime[r.symbol] = r.findings["regime"]
        if not current_regime:
            for e in snapshot.by_kind("RUNTIME_REGIME"):
                current_regime[e.symbol] = e.payload.get("confirmed")

        groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for t in trades:
            groups[(t["strategy"], t["symbol"])].append(t)
        shadow_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for c in candidates:
            shadow_groups[(c["strategy_key"], c["canonical_symbol"])].append(c)

        strategies = {}
        claims: list[Claim] = []
        claim_stats: dict[str, dict] = {}
        hypotheses: list[dict] = []
        underperformers: list[str] = []
        for key in sorted(set(groups) | set(shadow_groups)):
            strat, sym = key
            ts = groups.get(key, [])
            realized = summarize([t["r"] for t in ts])
            money = summarize([t["net_profit"] for t in ts])
            cost_share = None
            gross_abs = sum(abs((t["net_profit"] or 0) - (t["commission_swap_fee"] or 0)) for t in ts)
            if gross_abs > 0:
                cost_share = round(sum(abs(t["commission_swap_fee"] or 0) for t in ts) / gross_abs, 4)
            by_regime = {reg: summarize([t["r"] for t in ts if t["entry_regime"] == reg])
                         for reg in sorted({t["entry_regime"] for t in ts if t["entry_regime"]})}
            theoretical = {h: summarize([shadow_net_r(c, h) for c in shadow_groups.get(key, [])])
                           for h in SHADOW_HORIZONS}
            status = ("RETIRED" if strat in RETIRED_STRATEGY_KEYS else "ENTRY_SUSPENDED" if strat in suspended
                      else "CONFIGURED")
            entry: dict = {"strategy": strat, "symbol": sym, "status": status, "realized_r": realized,
                           "realized_money": money, "commission_swap_fee_share_of_gross": cost_share,
                           "by_entry_regime": by_regime, "theoretical_shadow_net_r": theoretical,
                           "cost_provenance": (costs_cfg.get(sym) or {}).get("provenance")}
            best_h = max((h for h in SHADOW_HORIZONS if theoretical[h].get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE),
                         key=lambda h: theoretical[h]["n"], default=None)
            if best_h and realized.get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE:
                entry["theory_vs_realized"] = {
                    "shadow_horizon_seconds": int(best_h),
                    "shadow_expectancy_r": theoretical[best_h]["expectancy"],
                    "realized_expectancy_r": realized["expectancy"],
                    "gap_r": round(realized["expectancy"] - theoretical[best_h]["expectancy"], 5),
                    "caveat": "shadow outcomes use fixed horizons and estimated costs; realized trades use the "
                              "live exit logic and broker costs - the gap mixes both effects"}
            reg_now = current_regime.get(sym)
            if reg_now and reg_now in by_regime:
                s = by_regime[reg_now]
                entry["current_regime_fit"] = {"regime": reg_now, **{k: s.get(k) for k in ("n", "expectancy",
                                                                                             "expectancy_ci95")}}
            strategies[f"{strat}:{sym}"] = entry
            if strat in RETIRED_STRATEGY_KEYS:
                continue      # history only: no claims, no hypotheses, never proposed

            if realized.get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE:
                positive = realized["expectancy"] > 0
                cid = f"edge:{'positive' if positive else 'negative'}:{strat}:{sym}"
                claims.append(Claim(
                    claim_id=cid, kind="PROFITABILITY", sample_size=realized["n"],
                    statement=f"{strat} on {sym}: realized net expectancy {realized['expectancy']:+.3f} R over "
                              f"{realized['n']} DEMO trades (95% CI {realized['expectancy_ci95']})",
                    evidence_ids=[trades_item.evidence_id], conclusion_key=f"strategy:{strat}:{sym}:edge"))
                claim_stats[cid] = {**realized, "symbol": sym, "source": "DEMO", "costs_included": True}
                if ci_excludes_zero_negative(realized):
                    underperformers.append(f"{strat}:{sym}")
                worst = [(reg, s) for reg, s in by_regime.items() if s.get("n", 0) >= MIN_SAMPLE_DESCRIPTIVE
                         and ci_excludes_zero_negative(s)]
                for reg, s in worst:
                    hypotheses.append({
                        "id": f"hyp:{strat}:{sym}:exclude_regime:{reg}",
                        "hypothesis": f"{strat} on {sym} loses in regime {reg} (realized {s['expectancy']:+.3f} R, "
                                      f"n={s['n']}); test a regime filter excluding {reg}",
                        "status": "UNTESTED_HYPOTHESIS",
                        "required_validation": "purged CV on design data, untouched OOS once, then the "
                                               "preregistered forward-evidence protocol; human review",
                        "risk_of_overfitting": "selected after looking at realized outcomes: treat as one of many "
                                               "implicit trials"})
                if cost_share is not None and cost_share > 0.5:
                    hypotheses.append({
                        "id": f"hyp:{strat}:{sym}:cost_dominance",
                        "hypothesis": f"commission/swap/fees are {cost_share:.0%} of gross P&L for {strat} on {sym}; "
                                      "test a minimum movement-to-cost requirement",
                        "status": "UNTESTED_HYPOTHESIS",
                        "required_validation": "research pipeline with realistic costs; human review"})
            elif ts:
                entry["note"] = f"only {len(ts)} realized trades (< {MIN_SAMPLE_DESCRIPTIVE}): no performance claim"
            if ci_excludes_zero_positive(realized) and realized.get("n", 0) < 100:
                entry["note"] = "positive CI on a small sample: likely noise or regime luck"

        # Overfitting signals from the research ledger.
        overfit = []
        if research_item:
            fam: dict[str, dict] = defaultdict(dict)
            counts: dict[str, int] = defaultdict(int)
            for t in research_item.payload["trials"]:
                fam[t["family"]][t["kind"]] = t
                counts[t["family"]] += 1
            for family, kinds in sorted(fam.items()):
                cv = kinds.get("PURGED_CV")
                if cv and cv.get("sharpe") is not None and cv["sharpe"] < 0 and "BACKTEST" in kinds:
                    overfit.append({"family": family, "purged_cv_sharpe": cv["sharpe"], "trials": counts[family],
                                    "note": "purged cross-validation is negative: in-sample results do not "
                                            "generalize"})
        for h in hypotheses:
            claims.append(Claim(claim_id=h["id"], kind="HYPOTHESIS", statement=h["hypothesis"][:600],
                                evidence_ids=[trades_item.evidence_id] if trades_item else []))

        limitations = ["DEMO realized trades are few per strategy and regime; per-group statistics are noisy",
                       "the strategy analyst never activates, modifies, retrains or deploys a strategy"]
        if not trades:
            limitations.append("no realized trades available")
        status = "COMPLETE" if trades or candidates else "INCOMPLETE"
        n_trades = len(trades)
        assessment = (f"Reviewed {len(strategies)} strategy/symbol group(s) over {n_trades} realized DEMO trade(s) "
                      f"and {len(candidates)} shadow candidate(s); {len(underperformers)} repeated "
                      f"underperformer(s) (95% CI of net expectancy below zero); {len(overfit)} research "
                      f"family(ies) with negative purged-CV; {len(hypotheses)} untested hypothesis(es) for research.")
        return Draft(status=status, assessment=assessment, evidence_ids=evidence, limitations=limitations,
                     claims=claims[:60],
                     counterarguments=[("Strategy statistics are conditional on the regimes that occurred; a "
                                        "different regime mix changes them.")],
                     findings={"strategies": strategies, "underperformers": underperformers,
                               "overfitting_signals": overfit, "hypotheses": hypotheses,
                               "claim_stats": claim_stats, "suspended": sorted(suspended),
                               "retired": sorted(RETIRED_STRATEGY_KEYS), "current_regimes": current_regime})
