"""Agent 4 - Risk Analyst: an independent second look at exposure and risk.

It recomputes what it can from the snapshot (exposure counts, drawdown versus
the persisted peak, today's realized + floating loss, live spread versus the
configured cap and the observed spread distribution, cost-evidence depth,
broker-state uncertainty, missing stops, slippage) and compares the result
with the deterministic risk governor's own published decisions.

It publishes warnings only. It cannot lower thresholds, touch broker orders
or override the governor: when its own number disagrees with the governor's
decision the disagreement is recorded as an AUDIT FINDING for a human, and the
governor's decision stands.
"""

from __future__ import annotations

from datetime import UTC, datetime

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft, age_seconds
from adaptive_scalper.ai_desk.contracts import Claim, Disagreement, Snapshot
from adaptive_scalper.config.constants import HARD_RISK_CEILINGS

USD_SENSITIVE = frozenset({"XAUUSD", "BTCUSD"})
MIN_COST_OBSERVATIONS = 30
SEVERITY_ORDER = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


def _w(severity: str, code: str, message: str, evidence: list[str]) -> dict:
    return {"severity": severity, "code": code, "message": message, "evidence_ids": evidence}


class RiskAnalyst(Agent):
    key = "risk_analyst"
    version = "1.0.0"

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        warnings: list[dict] = []
        disagreements: list[Disagreement] = []
        evidence: list[str] = []
        limitations: list[str] = [("the deterministic risk governor and final permission gate are authoritative; "
                                   "this analysis can only warn")]
        findings: dict = {}

        def ev(kind, sym=None):
            item = snapshot.one(kind, sym)
            if item is not None:
                evidence.append(item.evidence_id)
            return item

        ks = ev("KILL_SWITCH")
        tel = ev("ACCOUNT_TELEMETRY")
        limits_rt = ev("RISK_LIMITS_RUNTIME")
        limits_cfg = ev("RISK_LIMITS_CONFIG")
        recon = ev("RECONCILIATION")
        btruth = ev("BROKER_TRUTH")
        peak = ev("PEAK_EQUITY_RUNTIME") or ev("PEAK_EQUITY_HISTORY")
        incidents = ev("EXECUTION_INCIDENTS")
        costs = ev("EXECUTION_COSTS")
        strat_cfg = ev("STRATEGY_CONFIG")
        decisions = ev("ENTRY_DECISIONS")
        trades = ev("DEMO_TRADES")
        shadow = ev("SHADOW_CANDIDATES")
        why = ev("WHY_NO_TRADE")

        # Kill switch (read only; the desk can never change it).
        if ks is not None:
            findings["kill_switch"] = ks.payload
            if ks.payload["status"] != "DISENGAGED":
                warnings.append(_w("INFO", "KILL_SWITCH_BLOCKING",
                                   f"kill switch is {ks.payload['status']}: new entries are blocked "
                                   "(clearing it is a human operator decision)", [ks.evidence_id]))
        else:
            limitations.append("kill-switch state not available in this snapshot (replay or missing)")

        # Risk limits: runtime-published vs configured vs hard ceilings.
        if limits_cfg is not None:
            configured = limits_cfg.payload["configured"]
            audit = {}
            for k, ceiling in HARD_RISK_CEILINGS.items():
                rt = (limits_rt.payload if limits_rt else {}).get(k)
                audit[k] = {"hard_ceiling": ceiling, "configured": configured.get(k), "runtime_published": rt}
                for label, v in (("configured", configured.get(k)), ("runtime", rt)):
                    if v is not None and v > ceiling:
                        warnings.append(_w("CRITICAL", "LIMIT_ABOVE_HARD_CEILING",
                                           f"{label} {k}={v} exceeds the hard ceiling {ceiling}",
                                           [limits_cfg.evidence_id]))
                if rt is not None and configured.get(k) is not None and rt != configured.get(k):
                    disagreements.append(Disagreement(
                        topic=f"risk_limit:{k}", parties=["risk_analyst", "trading_runtime"],
                        positions={"config_file": str(configured.get(k)), "runtime_published": str(rt)},
                        severity="WARNING", resolution="AUDIT_FINDING"))
            findings["limits"] = audit

        # Account mode: the runtime refuses anything but DEMO; the desk only reports what it sees.
        mode = ((tel.payload.get("account") or {}).get("trade_mode") if tel else None)
        findings["account_trade_mode"] = mode
        if mode is not None and mode != "DEMO":
            warnings.append(_w("CRITICAL", "NON_DEMO_ACCOUNT_OBSERVED",
                               f"telemetry shows account trade mode {mode}: REAL-MONEY EXECUTION IS DISABLED and the "
                               "runtime must refuse it - verify the terminal immediately", [tel.evidence_id]))

        # Exposure.
        open_positions = (tel.payload.get("positions") if tel else None) or []
        max_open = (limits_cfg.payload["configured"] if limits_cfg else HARD_RISK_CEILINGS)["max_open_positions"]
        per_sym: dict[str, int] = {}
        for p in open_positions:
            s = p.get("canonical_symbol") or p.get("symbol")
            per_sym[s] = per_sym.get(s, 0) + 1
            if not p.get("sl"):
                warnings.append(_w("CRITICAL", "NO_VISIBLE_STOP",
                                   f"open position on {s} shows no stop-loss in the telemetry", [tel.evidence_id]))
        findings["exposure"] = {"open_positions": len(open_positions), "max_open_positions": max_open,
                                "per_symbol": per_sym}
        if len(open_positions) > max_open:
            warnings.append(_w("CRITICAL", "TOO_MANY_POSITIONS",
                               f"{len(open_positions)} open positions exceed the limit {max_open}", [tel.evidence_id]))
        for s, n in per_sym.items():
            if n > 1:
                warnings.append(_w("CRITICAL", "MULTIPLE_PER_SYMBOL", f"{n} positions open on {s}", [tel.evidence_id]))
        usd = [p for p in open_positions if (p.get("canonical_symbol") or p.get("symbol")) in USD_SENSITIVE]
        if len(usd) >= 2:
            warnings.append(_w("WARNING", "CORRELATED_EXPOSURE",
                               "two USD-sensitive positions (XAUUSD/BTCUSD) open at once: exposure is not "
                               "independent", [tel.evidence_id]))

        # Drawdown versus the persisted peak, compared with the governor.
        equity = (tel.payload.get("account") or {}).get("equity") if tel else None
        balance = (tel.payload.get("account") or {}).get("balance") if tel else None
        peak_value = None
        if peak is not None:
            pv = peak.payload
            peak_value = pv if isinstance(pv, int | float) else (pv or {}).get("peak_equity")
        max_dd = (limits_cfg.payload["configured"] if limits_cfg else HARD_RISK_CEILINGS)["max_drawdown_pct"]
        governor_reasons = " | ".join(f"{v['decision']}: {v['reason']}"
                                      for v in ((decisions.payload.get("latest") or {}).values() if decisions else []))
        governor_blocks_dd = "drawdown" in governor_reasons.lower()
        if equity is not None and peak_value:
            dd_pct = max(0.0, (peak_value - equity) / peak_value * 100.0)
            findings["drawdown"] = {"peak_equity": peak_value, "equity": equity, "drawdown_pct": round(dd_pct, 3),
                                    "limit_pct": max_dd, "governor_latest": governor_reasons or None}
            if dd_pct >= max_dd:
                warnings.append(_w("CRITICAL", "DRAWDOWN_AT_LIMIT",
                                   f"drawdown {dd_pct:.2f}% >= limit {max_dd}% versus the persisted peak",
                                   [peak.evidence_id, tel.evidence_id]))
            elif dd_pct >= 0.8 * max_dd:
                warnings.append(_w("WARNING", "DRAWDOWN_NEAR_LIMIT", f"drawdown {dd_pct:.2f}% within 80% of {max_dd}%",
                                   [peak.evidence_id, tel.evidence_id]))
            if (dd_pct >= max_dd) != governor_blocks_dd and decisions is not None:
                disagreements.append(Disagreement(
                    topic="drawdown_block", parties=["risk_analyst", "risk_governor"],
                    positions={"risk_analyst": f"drawdown {dd_pct:.2f}% vs limit {max_dd}%",
                               "risk_governor": governor_reasons[:300] or "no drawdown block recorded"},
                    severity="WARNING", resolution="AUDIT_FINDING"))
        else:
            limitations.append("drawdown not recomputable (missing equity or persisted peak)")

        # Today's realized + floating loss (approximation; the governor's own accounting is authoritative).
        if trades is not None and balance:
            day_start = int(datetime.fromtimestamp(snapshot.as_of_utc, UTC).replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp())
            realized = sum(t["net_profit"] or 0 for t in trades.payload["trades"] if t["closed_utc"] >= day_start)
            floating = sum((p.get("profit") or 0) for p in open_positions)
            loss = -min(0.0, realized + min(0.0, floating))
            pct = loss / (balance - realized) * 100.0 if balance - realized > 0 else None
            max_daily = (limits_cfg.payload["configured"] if limits_cfg else HARD_RISK_CEILINGS)["max_daily_loss_pct"]
            findings["daily_loss"] = {"realized_today": round(realized, 2), "floating": round(floating, 2),
                                      "loss_pct_approx": None if pct is None else round(pct, 3), "limit_pct": max_daily}
            if pct is not None and pct >= 0.8 * max_daily:
                warnings.append(_w("WARNING" if pct < max_daily else "CRITICAL", "DAILY_LOSS",
                                   f"approximate daily loss {pct:.2f}% vs limit {max_daily}%",
                                   [trades.evidence_id] + ([tel.evidence_id] if tel else [])))

        # Spreads: live versus cap and versus the observed distribution.
        quotes = (tel.payload.get("quotes") or {}) if tel else {}
        cost_cfg = (strat_cfg.payload.get("costs") or {}) if strat_cfg else {}
        cost_obs = costs.payload if costs else {}
        spread_view = {}
        for sym, q in sorted(quotes.items()):
            spread = q.get("spread")
            cap = (cost_cfg.get(sym) or {}).get("max_spread_price")
            p95 = (cost_obs.get(sym) or {}).get("spread_p95")
            spread_view[sym] = {"live_spread": spread, "cap": cap, "observed_p95": p95}
            if spread is not None and cap is not None and spread > cap:
                warnings.append(_w("WARNING", "SPREAD_ABOVE_CAP", f"{sym} live spread {spread} > cap {cap}",
                                   [tel.evidence_id]))
            if spread is not None and p95 is not None and spread > p95 * 1.5:
                warnings.append(_w("WARNING", "ABNORMAL_SPREAD",
                                   f"{sym} live spread {spread} is above 1.5x the observed p95 {p95:.4g}",
                                   [tel.evidence_id, costs.evidence_id]))
        findings["spreads"] = spread_view

        # Cost evidence depth, provenance and slippage.
        cost_view = {}
        for sym, c in sorted(cost_cfg.items()):
            n = (cost_obs.get(sym) or {}).get("n", 0)
            slip_p95 = (cost_obs.get(sym) or {}).get("abs_slippage_p95")
            spread_mean = (cost_obs.get(sym) or {}).get("spread_mean")
            cost_view[sym] = {"provenance": c.get("provenance"), "observations": n, "abs_slippage_p95": slip_p95}
            if c.get("provenance") == "UNVERIFIED_ASSUMPTION":
                warnings.append(_w("WARNING", "COST_UNVERIFIED", f"{sym} configured costs are an UNVERIFIED_ASSUMPTION",
                                   [strat_cfg.evidence_id]))
            if n < MIN_COST_OBSERVATIONS:
                warnings.append(_w("INFO", "COST_EVIDENCE_THIN",
                                   f"{sym}: {n} execution cost observations (< {MIN_COST_OBSERVATIONS})",
                                   [costs.evidence_id] if costs else [strat_cfg.evidence_id]))
            if slip_p95 is not None and spread_mean and slip_p95 > spread_mean:
                warnings.append(_w("WARNING", "SLIPPAGE_HEAVY",
                                   f"{sym} p95 absolute slippage {slip_p95:.4g} exceeds the mean spread "
                                   f"{spread_mean:.4g}", [costs.evidence_id]))
        findings["cost_evidence"] = cost_view

        # Broker-state uncertainty.
        if recon is not None and recon.payload.get("status") != "CLEAN":
            warnings.append(_w("CRITICAL", "RECONCILIATION_NOT_CLEAN",
                               f"reconciliation status {recon.payload.get('status')}", [recon.evidence_id]))
        if btruth is not None and btruth.payload.get("status") != "AVAILABLE":
            warnings.append(_w("CRITICAL", "BROKER_TRUTH_UNAVAILABLE",
                               f"broker truth {btruth.payload.get('status')}", [btruth.evidence_id]))
        open_incidents = sum(v["open"] for v in (incidents.payload.values() if incidents else []))
        findings["open_incidents"] = open_incidents
        if open_incidents:
            warnings.append(_w("WARNING", "OPEN_EXECUTION_INCIDENTS", f"{open_incidents} unresolved execution "
                               "incident(s)", [incidents.evidence_id]))

        # Recently proposed signals (shadow candidates) that look unsuitable.
        recent = []
        if shadow is not None:
            for c in shadow.payload["candidates"][-20:]:
                age = age_seconds(snapshot, c["decision_time_utc"])
                if age is None or age > context.stale_after_seconds:
                    continue
                reasons = []
                if c.get("movement_to_cost") is not None and c["movement_to_cost"] < 2:
                    reasons.append(f"movement-to-cost {c['movement_to_cost']:.2f} < 2: costs dominate")
                if c.get("spread_percentile") is not None and c["spread_percentile"] >= 0.9:
                    reasons.append(f"spread at the {c['spread_percentile']:.0%} percentile")
                if c.get("news_status") not in (None, "ALLOW"):
                    reasons.append(f"news status {c['news_status']}")
                if c.get("cost_provenance") == "UNVERIFIED_ASSUMPTION":
                    reasons.append("cost provenance unverified")
                recent.append({"candidate_id": c["id"], "symbol": c["canonical_symbol"], "strategy": c["strategy_key"],
                               "engine_result": c.get("final_permission_result"),
                               "selector": c.get("selector_disposition"), "unsuitable_because": reasons})
        findings["recent_proposals"] = recent

        if why is not None:
            findings["engine_why_no_trade"] = why.payload.get("global_block")

        live = snapshot.runtime_live
        if not live:
            limitations.append("trading runtime not live: account, exposure and broker state are LAST-KNOWN values")
        status = "COMPLETE" if (live and tel is not None and ks is not None) else "INCOMPLETE"
        warnings.sort(key=lambda w: -SEVERITY_ORDER[w["severity"]])
        findings["warnings"] = warnings
        crit = sum(1 for w in warnings if w["severity"] == "CRITICAL")
        assessment = (f"Risk review: {crit} critical and {len(warnings) - crit} other warning(s); "
                      f"{len(open_positions)} open position(s); "
                      + (f"drawdown {findings['drawdown']['drawdown_pct']}% of limit {max_dd}%; "
                         if "drawdown" in findings else "")
                      + ("kill switch " + ks.payload["status"] if ks else "kill switch unknown")
                      + ". Warnings only - the risk governor decides.")
        claims = [Claim(claim_id=f"risk:{w['code']}:{i}", kind="RISK", statement=w["message"][:600],
                        evidence_ids=w["evidence_ids"], conclusion_key=f"risk:{w['code']}")
                  for i, w in enumerate(warnings[:20])]
        return Draft(status=status, assessment=assessment, evidence_ids=evidence, limitations=limitations,
                     claims=claims, disagreements=disagreements, findings=findings,
                     counterarguments=[("Recomputed figures use the snapshot's last-known telemetry and may lag "
                                        "the governor's fresh broker reads.")])
