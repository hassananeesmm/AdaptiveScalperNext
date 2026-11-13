"""Does the desk help? Evaluation that is allowed to answer "no".

Four instruments, all research-only (nothing here changes a trading decision):

1. Forward tracking - every Market Analyst assessment carries a checkable
   volatility statement; `score_forward_checks` scores it once the bars exist.
2. Historical replay - `replay_market_forecasts` re-runs the deterministic
   Market Analyst AS OF each historical bar (only bars at or before that bar),
   then scores it on the following bars against a majority-class baseline. It
   refuses the reserved untouched OOS interval (`research.independent`). A
   deterministic replay is a GENUINE AS-OF reconstruction; an LLM narrative is
   never replayed historically (a present-day model knows the future), so any
   such study must be labelled RETROSPECTIVE.
3. Counterfactual filtering + ablation - `ablation_study` applies the desk's
   PRE-REGISTERED filter rules (code constants below, versioned by
   `PROTOCOL_VERSION`, never fitted to outcomes) to (a) realized DEMO trades
   with broker costs and (b) shadow candidates with estimated net outcomes, for
   baseline / +Market / +Market+Risk / +Skeptic / full desk. The Skeptic rule
   uses only trades closed BEFORE each decision (walk-forward, no leakage).
4. Operational analysis - latency, cost, failures, disagreements and
   model/version comparison from the journal.

Acceptance thresholds are fixed before looking at results. Each evaluation is
recorded with its protocol hash and a trial number; re-running on the same
data range is reported as an additional trial (multiple testing), never as
fresh evidence.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict

from adaptive_scalper.ai_desk.agents.market_analyst import (
    MarketAnalyst,
    bars_from_payload,
)
from adaptive_scalper.ai_desk.contracts import canonical_json, sha
from adaptive_scalper.ai_desk.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    METRICS_VERSION,
    MIN_SAMPLE_DESCRIPTIVE,
    ci_excludes_zero_negative,
    summarize,
)
from adaptive_scalper.ai_desk.snapshot_builder import (
    BAR_SECONDS,
    open_trading_db_readonly,
)
from adaptive_scalper.research.independent import assert_outside_reserved_oos

PROTOCOL_VERSION = "ai-desk-eval/1"
# Pre-registered filter rules (derived from the agents' published logic, not fitted).
MARKET_REJECT_REGIMES = frozenset({"ERRATIC", "VOLATILITY_EXPANSION", "UNKNOWN"})
RISK_MIN_MOVEMENT_TO_COST = 2.0
RISK_MAX_SPREAD_PERCENTILE = 0.9
RISK_MIN_SECONDS_TO_NEWS = 1800
SKEPTIC_MIN_PRIOR_TRADES = MIN_SAMPLE_DESCRIPTIVE
# Pre-registered acceptance thresholds.
ACCEPT_MIN_KEPT = 100
ACCEPT_MIN_REJECTED = 30
SHADOW_HORIZON = "1800"
VOL_RATIO_HIGH = 1.15
VOL_RATIO_LOW = 0.85

ABLATIONS = {
    "baseline": (),
    "baseline+market": ("market",),
    "baseline+market+risk": ("market", "risk"),
    "baseline+skeptic": ("skeptic",),
    "full_desk": ("market", "risk", "skeptic"),
}


def protocol_hash() -> str:
    return sha(canonical_json({
        "v": PROTOCOL_VERSION, "metrics": METRICS_VERSION, "market": sorted(MARKET_REJECT_REGIMES),
        "m2c": RISK_MIN_MOVEMENT_TO_COST, "spread": RISK_MAX_SPREAD_PERCENTILE, "news": RISK_MIN_SECONDS_TO_NEWS,
        "skeptic_n": SKEPTIC_MIN_PRIOR_TRADES, "accept": [ACCEPT_MIN_KEPT, ACCEPT_MIN_REJECTED],
        "vol": [VOL_RATIO_HIGH, VOL_RATIO_LOW], "horizon": SHADOW_HORIZON}), 16)


def wilson_ci(hits: int, n: int, z: float = 1.96) -> list[float] | None:
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(centre - half, 4), round(centre + half, 4)]


def vol_outcome(ratio: float) -> str:
    return "ABOVE_MEDIAN" if ratio > VOL_RATIO_HIGH else "BELOW_MEDIAN" if ratio < VOL_RATIO_LOW else "NEAR"


# --------------------------------------------------------------- forward checks
def score_forward_checks(journal, trading_db_path: str, *, now: int | None = None,
                         unscorable_after_seconds: int = 2 * 86400) -> dict:
    now = int(now or time.time())
    scored = unscorable = waiting = 0
    pending = journal.pending_forward_checks(now)
    if not pending:
        return {"scored": 0, "unscorable": 0, "waiting": 0}
    conn = open_trading_db_readonly(trading_db_path)
    try:
        for chk in pending:
            fc = json.loads(chk["payload_json"])
            secs = int(fc.get("bar_seconds", 300))
            res = next((k for k, v in BAR_SECONDS.items() if v == secs), "M5")
            start = int(fc["from_bar_time_utc"])
            rows = conn.execute("SELECT high - low FROM bars WHERE canonical_symbol=? AND resolution=? AND ts_utc >= ? "
                                "AND ts_utc < ? ORDER BY ts_utc",
                                (chk["symbol"], res, start, start + int(fc["horizon_bars"]) * secs)).fetchall()
            needed = int(fc["horizon_bars"])
            if len(rows) >= max(1, int(0.75 * needed)) and fc["reference_median_range"]:
                ratio = statistics.fmean(r[0] for r in rows) / fc["reference_median_range"]
                outcome = vol_outcome(ratio)
                journal.score_forward_check(chk["check_id"], status="SCORED",
                                            outcome={"ratio": round(ratio, 4), "outcome": outcome, "bars": len(rows)},
                                            hit=outcome == chk["expected"])
                scored += 1
            elif now - chk["due_at_utc"] > unscorable_after_seconds:
                journal.score_forward_check(chk["check_id"], status="UNSCORABLE",
                                            outcome={"reason": f"only {len(rows)}/{needed} bars stored after the "
                                                               "forecast (the runtime does not persist live bars)"},
                                            hit=None)
                unscorable += 1
            else:
                waiting += 1
    finally:
        conn.close()
    return {"scored": scored, "unscorable": unscorable, "waiting": waiting}


def forecast_track_record(journal) -> dict:
    rows = [r for r in journal.forward_check_rows() if r["status"] == "SCORED"]
    if not rows:
        return {"volatility_persistence": {"n": 0}}
    outcomes = Counter(json.loads(r["outcome_json"])["outcome"] for r in rows)
    hits = sum(r["hit"] for r in rows)
    chance = max(outcomes.values()) / len(rows)
    return {"volatility_persistence": {"n": len(rows), "hits": hits, "hit_rate": round(hits / len(rows), 4),
                                       "hit_rate_ci95": wilson_ci(hits, len(rows)),
                                       "chance_rate": round(chance, 4), "baseline": "majority outcome class"}}


# ---------------------------------------------------------------- market replay
def replay_market_forecasts(trading_db_path: str, *, symbol: str, start_utc: int, end_utc: int,
                            resolution: str = "M5", step_bars: int = 12, horizon_bars: int = 12,
                            lookback_bars: int = 300) -> dict:
    """Genuine as-of reconstruction of the deterministic Market Analyst."""
    assert_outside_reserved_oos(start_utc, end_utc)
    secs = BAR_SECONDS[resolution]
    conn = open_trading_db_readonly(trading_db_path)
    try:
        rows = conn.execute("SELECT ts_utc, open, high, low, close, tick_volume, spread, real_volume FROM bars WHERE "
                            "canonical_symbol=? AND resolution=? AND ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc",
                            (symbol, resolution, start_utc - lookback_bars * secs * 3, end_utc)).fetchall()
    finally:
        conn.close()
    bars = bars_from_payload({"bars": [list(r) for r in rows]})
    first = next((i for i, b in enumerate(bars) if b.time >= start_utc), len(bars))
    pairs = []
    for i in range(max(first, lookback_bars), len(bars) - horizon_bars, step_bars):
        window = bars[i - lookback_bars + 1:i + 1]
        if window[-1].time + secs - window[0].time > lookback_bars * secs * 3:
            continue   # a gap (weekend/holiday) inside the window: skip, never stitch across it
        computed = MarketAnalyst._compute(symbol, resolution, window, None)
        pct, median = computed["range_percentile"], computed["median_range"]
        if pct is None or not median:
            continue
        expected = "ABOVE_MEDIAN" if pct >= 0.75 else "BELOW_MEDIAN" if pct <= 0.25 else "NEAR"
        future = bars[i + 1:i + 1 + horizon_bars]
        ratio = statistics.fmean(b.high - b.low for b in future) / median
        pairs.append((expected, vol_outcome(ratio), computed["regime_confidence"]))
    n = len(pairs)
    if n == 0:
        return {"symbol": symbol, "n": 0, "status": "NO_DATA", "reconstruction": "GENUINE_AS_OF"}
    hits = sum(1 for e, o, _ in pairs if e == o)
    outcomes = Counter(o for _, o, _ in pairs)
    majority = outcomes.most_common(1)[0]
    by_expected = {}
    for exp in ("ABOVE_MEDIAN", "NEAR", "BELOW_MEDIAN"):
        sub = [(e, o) for e, o, _ in pairs if e == exp]
        if sub:
            h = sum(1 for e, o in sub if e == o)
            by_expected[exp] = {"n": len(sub), "hit_rate": round(h / len(sub), 4), "ci95": wilson_ci(h, len(sub))}
    hit_ci = wilson_ci(hits, n)
    beats = bool(hit_ci and hit_ci[0] > majority[1] / n)
    return {"symbol": symbol, "resolution": resolution, "range": [start_utc, end_utc], "n": n,
            "reconstruction": "GENUINE_AS_OF (deterministic agent, only bars at or before each decision)",
            "hit_rate": round(hits / n, 4), "hit_rate_ci95": hit_ci,
            "majority_baseline": {"class": majority[0], "rate": round(majority[1] / n, 4)},
            "beats_baseline": beats, "by_expected": by_expected,
            "verdict": ("Volatility statements beat the majority-class baseline (95% CI). This reflects ordinary "
                        "volatility clustering; it is descriptive skill, NOT evidence of a trading edge." if beats else
                        "The Market Analyst's volatility statements have NOT demonstrated skill over the "
                        "majority-class baseline.")}


# --------------------------------------------------------------- ablation study
def _paired_improvement_ci(values: list[float], keep: list[bool]) -> list[float] | None:
    n = len(values)
    if n < 2 or sum(keep) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    diffs = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        idx = [rng.randrange(n) for _ in range(n)]
        kept = [values[i] for i in idx if keep[i]]
        if not kept:
            continue
        diffs.append(statistics.fmean(kept) - statistics.fmean(values[i] for i in idx))
    diffs.sort()
    if len(diffs) < 100:
        return None
    return [round(diffs[int(0.025 * (len(diffs) - 1))], 5), round(diffs[int(0.975 * (len(diffs) - 1))], 5)]


def _skeptic_rejects(history: dict, strategy: str, symbol: str) -> bool:
    past = history.get((strategy, symbol), [])
    if len(past) < SKEPTIC_MIN_PRIOR_TRADES:
        return False
    return ci_excludes_zero_negative(summarize(past))


def _rules(sample: dict, components: tuple[str, ...], history: dict) -> list[str]:
    reasons = []
    if "market" in components and (sample.get("regime") or "UNKNOWN") in MARKET_REJECT_REGIMES:
        reasons.append("market:unsuitable_condition")
    if "risk" in components:
        m2c, sp, news = sample.get("movement_to_cost"), sample.get("spread_percentile"), sample.get("seconds_to_news")
        if m2c is not None and m2c < RISK_MIN_MOVEMENT_TO_COST:
            reasons.append("risk:costs_dominate")
        if sp is not None and sp >= RISK_MAX_SPREAD_PERCENTILE:
            reasons.append("risk:wide_spread")
        if news is not None and 0 <= news < RISK_MIN_SECONDS_TO_NEWS:
            reasons.append("risk:news_proximity")
        if sample.get("news_status") not in (None, "ALLOW"):
            reasons.append("risk:news_status")
    if "skeptic" in components and _skeptic_rejects(history, sample["strategy"], sample["symbol"]):
        reasons.append("skeptic:repeated_underperformer_as_of")
    return reasons


def _evaluate(samples: list[dict], label: str) -> dict:
    """Apply every ablation to chronological samples. The Skeptic's history only
    contains outcomes KNOWN before each decision: a sample's outcome is added at
    its `closed_t` (trade close, or decision time + horizon for shadow
    candidates) - strict walk-forward, no leakage."""
    samples = sorted(samples, key=lambda s: s["t"])
    values = [s["outcome"] for s in samples]
    baseline = summarize(values)
    by_close = sorted(samples, key=lambda s: s["closed_t"])
    out = {"dataset": label, "n": len(samples), "baseline": baseline, "configs": {}}
    for name, components in ABLATIONS.items():
        history: dict = defaultdict(list)
        keep, reasons_count = [], Counter()
        ci = 0
        for s in samples:
            while ci < len(by_close) and by_close[ci]["closed_t"] <= s["t"]:
                history[(by_close[ci]["strategy"], by_close[ci]["symbol"])].append(by_close[ci]["outcome"])
                ci += 1
            reasons = _rules(s, components, history)
            keep.append(not reasons)
            reasons_count.update(reasons)
        kept = [v for v, k in zip(values, keep, strict=True) if k]
        rejected = [v for v, k in zip(values, keep, strict=True) if not k]
        kept_s, rej_s = summarize(kept), summarize(rejected)
        improvement_ci = _paired_improvement_ci(values, keep) if components else None
        false_rejection = round(sum(1 for v in rejected if v > 0) / len(rejected), 4) if rejected else None
        halves = {}
        if len(samples) >= 2 * MIN_SAMPLE_DESCRIPTIVE and components:
            mid = len(samples) // 2
            for part, idx in (("first_half", range(mid)), ("second_half", range(mid, len(samples)))):
                k = [values[i] for i in idx if keep[i]]
                a = [values[i] for i in idx]
                halves[part] = {"kept_expectancy": summarize(k).get("expectancy"),
                                "all_expectancy": summarize(a).get("expectancy"), "kept": len(k), "all": len(a)}
        verdict = "BASELINE"
        if components:
            if len(kept) < ACCEPT_MIN_KEPT or len(rejected) < ACCEPT_MIN_REJECTED:
                short = [f"kept {len(kept)} < {ACCEPT_MIN_KEPT}"] if len(kept) < ACCEPT_MIN_KEPT else []
                short += [f"rejected {len(rejected)} < {ACCEPT_MIN_REJECTED}"] if len(rejected) < ACCEPT_MIN_REJECTED else []
                verdict = f"INSUFFICIENT SAMPLE ({', '.join(short)})"
            elif improvement_ci and improvement_ci[0] > 0 and kept_s.get("max_drawdown", 1e9) <= baseline.get(
                    "max_drawdown", 0):
                verdict = "IMPROVEMENT DEMONSTRATED IN SHADOW RESEARCH (not a deployment permission)"
            else:
                verdict = "IMPROVEMENT NOT DEMONSTRATED"
        out["configs"][name] = {"components": list(components), "kept": kept_s, "rejected": rej_s,
                                "trade_frequency_ratio": round(len(kept) / len(values), 4) if values else None,
                                "false_rejection_rate": false_rejection, "improvement_ci95": improvement_ci,
                                "rejection_reasons": dict(reasons_count), "stability": halves, "verdict": verdict,
                                "walk_forward": "skeptic history = outcomes known before each decision"}
    return out


def _demo_samples(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, p.canonical_symbol, p.strategy_key, p.opened_at_utc, p.closed_at_utc, p.initial_monetary_risk, "
        "e.entry_regime, c.spread_percentile, c.seconds_to_next_news, c.atr, c.spread_price, "
        "(SELECT SUM(d.profit + d.commission + d.swap + COALESCE(d.fee, 0)) FROM deals d "
        " WHERE d.broker_position_id = p.broker_position_id) AS net "
        "FROM positions p LEFT JOIN position_entry_context e ON e.broker_position_id = p.broker_position_id "
        "LEFT JOIN execution_cost_observations c ON c.broker_position_id = p.broker_position_id "
        "WHERE p.status='CLOSED' ORDER BY p.opened_at_utc").fetchall()
    out = []
    for r in rows:
        if r["net"] is None or not r["initial_monetary_risk"]:
            continue
        m2c = (r["atr"] / r["spread_price"]) if r["atr"] and r["spread_price"] else None
        out.append({"t": r["opened_at_utc"], "closed_t": r["closed_at_utc"], "symbol": r["canonical_symbol"],
                    "strategy": r["strategy_key"], "regime": r["entry_regime"], "spread_percentile":
                    r["spread_percentile"], "seconds_to_news": r["seconds_to_next_news"], "movement_to_cost": m2c,
                    "outcome": r["net"] / r["initial_monetary_risk"]})
    return out


def _shadow_samples(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT c.canonical_symbol, c.strategy_key, c.decision_time_utc, c.confirmed_regime, c.spread_percentile, "
        "c.movement_to_cost, c.news_status, o.net_r_estimated FROM shadow_candidates c JOIN shadow_outcomes o "
        "ON o.candidate_id = c.id AND o.status='RESOLVED' AND o.horizon_seconds = ? ORDER BY c.decision_time_utc",
        (int(SHADOW_HORIZON),)).fetchall()
    return [{"t": r["decision_time_utc"], "closed_t": r["decision_time_utc"] + int(SHADOW_HORIZON), "symbol": r["canonical_symbol"], "strategy": r["strategy_key"],
             "regime": r["confirmed_regime"], "spread_percentile": r["spread_percentile"],
             "movement_to_cost": r["movement_to_cost"], "news_status": r["news_status"],
             "outcome": r["net_r_estimated"]} for r in rows if r["net_r_estimated"] is not None]


def ablation_study(trading_db_path: str) -> dict:
    conn = open_trading_db_readonly(trading_db_path)
    try:
        conn.execute("BEGIN")
        demo = _demo_samples(conn)
        shadow = _shadow_samples(conn)
        conn.execute("COMMIT")
    finally:
        conn.close()
    demo_eval = _evaluate(demo, "DEMO (realized net R incl. commission, swap, fees)")
    shadow_eval = _evaluate(shadow, "SHADOW (theoretical net R at 30 min, estimated costs)")
    datasets = {"demo": demo_eval, "shadow": shadow_eval}
    overall = "The AI desk has not demonstrated an improvement over the baseline."
    if any(cfg["verdict"].startswith("IMPROVEMENT DEMONSTRATED") for d in datasets.values()
           for cfg in d["configs"].values()):
        overall = ("At least one desk filter improved shadow-research outcomes under the pre-registered thresholds; "
                   "this is research evidence only and needs the forward-evidence protocol before it means anything.")
    return {"protocol_version": PROTOCOL_VERSION, "protocol_hash": protocol_hash(), "datasets": datasets,
            "thresholds": {"min_kept": ACCEPT_MIN_KEPT, "min_rejected": ACCEPT_MIN_REJECTED,
                           "improvement": "95% bootstrap CI of (kept expectancy - baseline expectancy) > 0 and "
                                          "kept max drawdown <= baseline"},
            "conclusion": overall,
            "labels": {"demo": "DEMO outcomes (realized broker fills and costs)",
                       "shadow": "HISTORICAL SIMULATION (shadow observer, estimated costs)",
                       "status": "UNVERIFIED RESEARCH - shadow comparison, not a deployment permission"}}


# ------------------------------------------------------------ operational stats
def operational_report(journal) -> dict:
    c = journal.conn
    lat = defaultdict(list)
    for agent, ms in c.execute("SELECT agent, duration_ms FROM agent_results"):
        lat[agent].append(ms)
    latency = {a: {"n": len(v), "mean_ms": round(statistics.fmean(v), 1),
                   "p95_ms": sorted(v)[int(0.95 * (len(v) - 1))]} for a, v in sorted(lat.items())}
    statuses = {f"{a}:{s}": n for a, s, n in c.execute(
        "SELECT agent, status, COUNT(*) FROM agent_results GROUP BY agent, status")}
    versions = {f"{a}@{v}/{m}": n for a, v, m, n in c.execute(
        "SELECT agent, agent_version, model_id, COUNT(*) FROM agent_results GROUP BY 1, 2, 3")}
    disagreements = {t: n for t, n in c.execute(
        "SELECT CASE WHEN instr(topic, ':') > 0 THEN substr(topic, 1, instr(topic, ':') - 1) ELSE topic END, COUNT(*) "
        "FROM disagreements GROUP BY 1")}
    providers = [dict(r) for r in c.execute(
        "SELECT provider, model, status, COUNT(*) AS calls, ROUND(AVG(latency_ms), 1) AS mean_latency_ms, "
        "SUM(input_tokens + output_tokens) AS tokens, ROUND(SUM(cost_usd), 6) AS cost_usd FROM provider_calls "
        "GROUP BY provider, model, status")]
    runs = {s: n for s, n in c.execute("SELECT status, COUNT(*) FROM desk_runs GROUP BY status")}
    run_ms = [r[0] for r in c.execute("SELECT (finished_at_utc - started_at_utc) FROM desk_runs "
                                      "WHERE finished_at_utc IS NOT NULL")]
    return {"agent_latency": latency, "agent_status_counts": statuses, "agent_versions": versions,
            "disagreements_by_topic": disagreements, "provider_calls": providers, "runs": runs,
            "run_seconds_mean": round(statistics.fmean(run_ms), 2) if run_ms else None,
            "forecast_track_record": forecast_track_record(journal)}


def run_full_evaluation(journal, trading_db_path: str, *, replay_ranges: dict[str, tuple[int, int]] | None = None) -> dict:
    forward = score_forward_checks(journal, trading_db_path)
    ablation = ablation_study(trading_db_path)
    replays = {}
    for sym, (start, end) in (replay_ranges or {}).items():
        try:
            replays[sym] = replay_market_forecasts(trading_db_path, symbol=sym, start_utc=start, end_utc=end)
        except ValueError as exc:
            replays[sym] = {"status": "REFUSED", "detail": str(exc)}
    result = {"forward_checks": forward, "ablation": ablation, "market_replay": replays,
              "operations": operational_report(journal)}
    data_range = {"replay": {k: list(v) for k, v in (replay_ranges or {}).items()},
                  "demo_n": ablation["datasets"]["demo"]["n"], "shadow_n": ablation["datasets"]["shadow"]["n"]}
    prior = journal.prior_evaluations_on_range("FULL", data_range)
    eid, trial = journal.record_evaluation(kind="FULL", protocol_version=PROTOCOL_VERSION,
                                           protocol_hash=protocol_hash(), data_range=data_range, result=result)
    result["evaluation_id"] = eid
    result["trial_number"] = trial
    result["repeat_on_same_data"] = prior
    if prior:
        result["multiple_testing_note"] = (f"this data range was evaluated {prior} time(s) before; repeated looks are "
                                           "additional trials, not new evidence")
    return result
