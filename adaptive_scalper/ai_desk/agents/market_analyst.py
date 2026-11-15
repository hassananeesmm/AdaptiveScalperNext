"""Agent 1 - Market Analyst: describes market CONDITIONS, never direction.

Reuses the project's causal feature engine, regime classifier, Wilder ADX,
EMA and rolling VWAP over CLOSED bars from the snapshot, cross-checks them
with the runtime's own published regime and microstructure observer, lists
contradicting indicators, states data freshness, and records why the
assessment changed since the desk's previous one. It does not infer order-book
liquidity or institutional activity (no such data exists here); the spread is
the only liquidity proxy and is labelled as such.

Each assessment carries a forward-checkable statement (does the next hour's
realized range stay above/below the recent median?) that the evaluator scores
later, so the agent's usefulness is measured, not assumed.
"""

from __future__ import annotations

import statistics

from adaptive_scalper.ai_desk.agents.base import Agent, AgentContext, Draft, age_seconds
from adaptive_scalper.ai_desk.contracts import Claim, Snapshot
from adaptive_scalper.features.adx import wilder_adx
from adaptive_scalper.features.bar_features import compute_bar_features
from adaptive_scalper.features.ema import ema_last
from adaptive_scalper.features.vwap import rolling_vwap_bands
from adaptive_scalper.gateway.types import Bar
from adaptive_scalper.regimes.classifier import classify_regime

CONDITION_OF_REGIME = {
    "TRENDING_UP": "TRENDING", "TRENDING_DOWN": "TRENDING", "RANGE": "RANGING", "COMPRESSION": "COMPRESSION",
    "BREAKOUT": "BREAKOUT", "VOLATILITY_EXPANSION": "HIGH_VOLATILITY", "ERRATIC": "HIGH_VOLATILITY",
    "UNKNOWN": "UNCERTAIN",
}
FORWARD_HORIZON_BARS = 12


def bars_from_payload(payload: dict) -> list[Bar]:
    return [Bar(time=int(b[0]), open=b[1], high=b[2], low=b[3], close=b[4], tick_volume=int(b[5]),
                spread=int(b[6]), real_volume=int(b[7])) for b in payload.get("bars", [])]


def range_percentile(bars: list[Bar], window: int = 20) -> tuple[float | None, float | None, float | None]:
    """(current mean range, its percentile among rolling means, median rolling mean)."""
    ranges = [b.high - b.low for b in bars]
    if len(ranges) < window * 3:
        return None, None, None
    rolling = [statistics.fmean(ranges[i - window:i]) for i in range(window, len(ranges) + 1)]
    current = rolling[-1]
    history = rolling[:-1]
    pct = sum(1 for r in history if r <= current) / len(history)
    return current, pct, statistics.median(history)


def quote_age_seconds(snapshot: Snapshot, sym: str) -> int | None:
    """Age of the runtime's last published quote for `sym` (None if absent)."""
    tel = snapshot.one("ACCOUNT_TELEMETRY")
    quote = ((tel.payload.get("quotes") or {}) if tel else {}).get(sym) or {}
    ts = quote.get("time_utc")
    return None if not isinstance(ts, (int, float)) else age_seconds(snapshot, int(ts))


def freshness_report(bar_age: int | None, observer_age: int | None, quote_age: int | None, stale_after: int,
                     runtime_live: bool) -> dict:
    """Classify where current market data could come from and what restores it.

    The desk never connects to MT5 itself (the runtime is the only MT5 client),
    so freshness can only be restored by the runtime or by an operator action.
    """
    def fresh(a):
        return a is not None and 0 <= a <= stale_after

    if fresh(bar_age):
        state, fix = "FRESH_BARS", None
    elif runtime_live and fresh(observer_age):
        state = "RUNTIME_OBSERVER_ONLY"
        fix = ("bars table is not updated by the runtime; closed-bar statistics (volatility percentile, VWAP) "
               "need a bar refresh - see docs/AI_DESK.md 'Market-data freshness'")
    elif runtime_live:
        state = "RUNTIME_LIVE_NO_OBSERVER"
        fix = "the runtime is live but has not yet published a microstructure observation for this symbol"
    else:
        state = "RUNTIME_DOWN"
        fix = ("the trading runtime is not running: start it only through the normal operator procedure; "
               "the desk never starts it and never opens MT5")
    return {"state": state, "bars_age_seconds": bar_age, "runtime_observer_age_seconds": observer_age,
            "quote_age_seconds": quote_age, "stale_after_seconds": stale_after, "runtime_live": runtime_live,
            "remediation": fix}


class MarketAnalyst(Agent):
    key = "market_analyst"
    version = "1.0.0"
    per_symbol = True

    def analyze(self, snapshot: Snapshot, context: AgentContext, symbol: str | None = None) -> Draft:
        sym = symbol or "XAUUSD"
        stale_after = context.stale_after_seconds
        bars_item = snapshot.one("MARKET_BARS", sym)
        m1_item = snapshot.one("MARKET_BARS_M1", sym)
        rt_regime = snapshot.one("RUNTIME_REGIME", sym)
        rt_micro = snapshot.one("RUNTIME_MICROSTRUCTURE", sym)
        prior = snapshot.one("PRIOR_DESK_ASSESSMENT", sym)
        evidence = [e.evidence_id for e in (bars_item, m1_item, rt_regime, rt_micro) if e is not None]
        limitations: list[str] = ["no order-book or institutional-flow data; the spread is the only liquidity proxy"]
        if sym == "GBPJPY":
            limitations.append("GBPJPY is observed only; its execution restrictions in the trading engine apply")
        conflicts: list[str] = []
        findings: dict = {"symbol": sym}

        bar_age = age_seconds(snapshot, bars_item.data_timestamp_utc) if bars_item else None
        rt_age = age_seconds(snapshot, rt_micro.data_timestamp_utc) if rt_micro else None
        quote_age = quote_age_seconds(snapshot, sym)
        bars_fresh = bar_age is not None and bar_age <= stale_after
        runtime_fresh = rt_age is not None and rt_age <= stale_after and snapshot.runtime_live
        findings["data_freshness"] = freshness_report(bar_age, rt_age, quote_age, stale_after, snapshot.runtime_live)

        computed = None
        if bars_item is not None:
            bars = bars_from_payload(bars_item.payload)
            computed = self._compute(sym, bars_item.payload["resolution"], bars, m1_item)
            findings["computed"] = computed

        runtime_view = None
        if rt_micro is not None or rt_regime is not None:
            micro = rt_micro.payload if rt_micro else {}
            runtime_view = {"confirmed_regime": (rt_regime.payload if rt_regime else {}).get("confirmed"),
                            "adx14": micro.get("adx14"), "close_vs_ema20": micro.get("close_vs_ema"),
                            "candle": micro.get("candle"), "bar_time_utc": micro.get("bar_time_utc")}
            findings["runtime_observer"] = runtime_view

        # Which view describes "now"?
        if bars_fresh and computed is not None:
            basis, status = "closed_bars", "COMPLETE"
            regime, adx, trend = computed["regime"], computed["adx14"], computed["trend"]
        elif runtime_fresh and runtime_view is not None:
            basis, status = "runtime_observer", "INCOMPLETE"
            regime = runtime_view["confirmed_regime"] or "UNKNOWN"
            adx = runtime_view["adx14"]
            trend = {"ABOVE": "UP_BIAS", "BELOW": "DOWN_BIAS"}.get(runtime_view["close_vs_ema20"], "NEUTRAL")
            limitations.append("stored closed bars are stale; condition taken from the runtime observer only "
                               "(no volatility percentile or VWAP from bars)")
        else:
            basis, status = "last_known", "STALE"
            # Describe the MOST RECENT of the two stale sources, never an older one.
            observer_newer = runtime_view is not None and (
                computed is None or (rt_age is not None and (bar_age is None or rt_age < bar_age)))
            if observer_newer:
                findings["last_known_source"] = "runtime_observer"
                regime = runtime_view["confirmed_regime"] or "UNKNOWN"
                adx = runtime_view["adx14"]
                trend = {"ABOVE": "UP_BIAS", "BELOW": "DOWN_BIAS"}.get(runtime_view["close_vs_ema20"], "UNKNOWN")
            else:
                findings["last_known_source"] = "stored_bars" if computed else None
                regime = (computed or {}).get("regime") or "UNKNOWN"
                adx = (computed or {}).get("adx14")
                trend = (computed or {}).get("trend", "UNKNOWN")
            limitations.append(f"no fresh market data (bars age {bar_age}s, runtime observer age {rt_age}s, "
                               f"limit {stale_after}s): this is a LAST-KNOWN description, not current conditions")

        condition = CONDITION_OF_REGIME.get(regime, "UNCERTAIN")
        # Bar-derived statistics only describe the view when the bars ARE the chosen source.
        use_bars = basis == "closed_bars" or findings.get("last_known_source") == "stored_bars"
        vol_pct = (computed or {}).get("range_percentile") if use_bars else None
        if vol_pct is not None and vol_pct >= 0.9 and condition not in ("HIGH_VOLATILITY", "BREAKOUT"):
            conflicts.append(f"regime says {regime} but the 20-bar mean range is at the {vol_pct:.0%} percentile")
            condition = "HIGH_VOLATILITY"
        vol_bucket = None if vol_pct is None else ("HIGH" if vol_pct >= 0.75 else "LOW" if vol_pct <= 0.25 else "NORMAL")

        if adx is not None and adx > 25 and regime in ("RANGE", "COMPRESSION"):
            conflicts.append(f"ADX(14) {adx:.1f} indicates directional movement while the regime is {regime}")
        if adx is not None and adx < 20 and regime in ("TRENDING_UP", "TRENDING_DOWN"):
            conflicts.append(f"regime {regime} while ADX(14) {adx:.1f} is weak")
        if computed and use_bars:
            if computed["trend"] == "UP" and (computed["momentum"] or 0) < 0:
                conflicts.append("EMA structure points up but 20-bar momentum is negative")
            if computed["trend"] == "DOWN" and (computed["momentum"] or 0) > 0:
                conflicts.append("EMA structure points down but 20-bar momentum is positive")
            vw = computed.get("vwap")
            if vw and vw.get("position") in ("ABOVE_UPPER", "BELOW_LOWER"):
                conflicts.append(f"price is outside the 10-minute VWAP +/-2 sigma band ({vw['position']}): stretched")
        if (runtime_view and computed and runtime_fresh and bars_fresh and runtime_view["confirmed_regime"]
                and runtime_view["confirmed_regime"] != computed["regime"]):
            conflicts.append(f"runtime confirmed regime {runtime_view['confirmed_regime']} differs from the "
                             f"desk's single-bar classification {computed['regime']} (hysteresis lag is expected)")

        confidence = basis_text = None
        if status != "STALE" and computed:
            confidence = max(0.0, min(1.0, computed["regime_confidence"] - 0.15 * len(conflicts)))
            basis_text = ("HEURISTIC, not calibrated: regime-classifier confidence minus 0.15 per indicator "
                          "conflict; calibration is measured by `ai-desk evaluate`")

        change = None
        if prior is not None:
            p = prior.payload
            if p.get("condition") != condition or p.get("trend") != trend or p.get("vol_bucket") != vol_bucket:
                reasons = []
                if p.get("regime") != regime:
                    reasons.append(f"regime {p.get('regime')} -> {regime}")
                if p.get("trend") != trend:
                    reasons.append(f"trend {p.get('trend')} -> {trend}")
                if p.get("vol_bucket") != vol_bucket:
                    reasons.append(f"volatility {p.get('vol_bucket')} -> {vol_bucket}")
                change = {"changed_from": p.get("condition"), "changed_to": condition, "reasons": reasons,
                          "previous_as_of": p.get("as_of")}
                evidence.append(prior.evidence_id)
        findings.update({"basis": basis, "condition": condition, "regime": regime, "trend": trend,
                         "adx14": adx, "vol_bucket": vol_bucket, "conflicts": conflicts, "change": change})
        if computed and computed.get("median_range") is not None and status == "COMPLETE":
            findings["forward_check"] = {
                "metric": "mean_bar_range_next_bars_vs_median", "horizon_bars": FORWARD_HORIZON_BARS,
                "reference_median_range": computed["median_range"],
                "from_bar_time_utc": bars_item.data_timestamp_utc,
                "bar_seconds": bars_item.payload["bar_seconds"],
                "expected": "ABOVE_MEDIAN" if vol_bucket == "HIGH" else "BELOW_MEDIAN" if vol_bucket == "LOW" else "NEAR",
                "rationale": "volatility clustering: recent range level tends to persist over short horizons"}

        adx_txt = "n/a" if adx is None else f"{adx:.1f}"
        assessment = (f"{sym}: condition {condition} (regime {regime}, trend {trend}, ADX(14) {adx_txt}, "
                      f"volatility {vol_bucket or 'n/a'}); basis {basis}"
                      + (f" from {findings['last_known_source']}" if findings.get("last_known_source") else "")
                      + f"; data {findings['data_freshness']['state']}"
                      + (f"; {len(conflicts)} indicator conflict(s)" if conflicts else "")
                      + ". Describes conditions only - not a price-direction forecast.")
        from_observer = basis == "runtime_observer" or findings.get("last_known_source") == "runtime_observer"
        cited = ([e.evidence_id for e in (rt_regime, rt_micro) if e is not None] if from_observer else evidence[:2])
        claims = [Claim(claim_id=f"market:{sym}:condition", kind="DESCRIPTIVE",
                        statement=f"{sym} market condition is {condition}", evidence_ids=cited or evidence[:2],
                        conclusion_key=f"market:{sym}:{condition}")]
        if "forward_check" in findings:
            claims.append(Claim(claim_id=f"market:{sym}:volatility_persistence", kind="PREDICTIVE",
                                statement=f"next {FORWARD_HORIZON_BARS} bars' mean range expected "
                                          f"{findings['forward_check']['expected']} the recent median",
                                evidence_ids=[bars_item.evidence_id], conclusion_key=f"market:{sym}:vol"))
        counter = [f"Contradiction: {c}" for c in conflicts] or [
            "Regime labels come from a single-bar heuristic classifier with hysteresis in the runtime; they lag turns."]
        return Draft(status=status, assessment=assessment, symbol=sym, confidence=confidence,
                     confidence_basis=basis_text, evidence_ids=evidence, counterarguments=counter,
                     limitations=limitations, claims=claims, findings=findings)

    @staticmethod
    def _compute(sym: str, resolution: str, bars: list[Bar], m1_item) -> dict:
        snap = compute_bar_features(sym, resolution, bars, lookback=20)
        reg = classify_regime(snap)
        closes = [b.close for b in bars]
        ema20, ema50 = ema_last(closes, 20), ema_last(closes, 50)
        adx = wilder_adx(bars, 14)
        close = closes[-1]
        if ema20 is not None and ema50 is not None:
            trend = "UP" if ema20 > ema50 and close > ema20 else "DOWN" if ema20 < ema50 and close < ema20 else "MIXED"
        else:
            trend = "UNKNOWN"
        current_range, pct, median_range = range_percentile(bars)
        vwap = None
        if m1_item is not None:
            m1 = bars_from_payload(m1_item.payload)
            bands = rolling_vwap_bands(m1, bar_seconds=60, window_seconds=600, band_std=2.0) if m1 else None
            if bands is not None:
                last = m1[-1].close
                position = "ABOVE_UPPER" if last > bands.upper else "BELOW_LOWER" if last < bands.lower else "INSIDE"
                vwap = {"vwap": round(bands.vwap, 5), "upper": round(bands.upper, 5), "lower": round(bands.lower, 5),
                        "last_close": last, "position": position, "bar_count": bands.bar_count}
        return {
            "regime": reg.regime, "regime_confidence": round(reg.confidence, 4), "regime_reason": reg.reason,
            "adx14": None if adx is None else round(adx, 3),
            "ema20": None if ema20 is None else round(ema20, 5), "ema50": None if ema50 is None else round(ema50, 5),
            "close": close, "trend": trend, "momentum": snap.momentum, "efficiency_ratio": snap.efficiency_ratio,
            "realized_volatility": snap.realized_volatility, "atr": snap.atr, "session": snap.session,
            "spread_points": snap.spread_current, "spread_percentile": snap.spread_percentile,
            "range_mean_20": current_range, "range_percentile": None if pct is None else round(pct, 4),
            "median_range": median_range, "vwap": vwap, "bars_used": len(bars), "last_bar_open_utc": bars[-1].time,
        }
